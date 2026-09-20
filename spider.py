# -*- coding: utf-8 -*-
import asyncio
import re
from collections import Counter
from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from astrbot.api import logger

from .sites import SITES
from . import db
from .utils import extract_date, parse_detail_date

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Encoding": "gzip, deflate",
}

_client = None


def get_client():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            headers=HEADERS,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=60,
            ),
            follow_redirects=True,
            timeout=20,
        )
    return _client


async def close_client():
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None


async def fetch(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url)
    r.raise_for_status()
    if not r.encoding:
        r.encoding = "utf-8"
    return r.text


def parse_list(html: str, site: dict, existing_urls: set = None, stop_after_known: int = 2):
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(site["container"]) or soup

    items = []
    seen = set()
    known_streak = 0

    for a in container.select(f'a[href*="{site["link_filter"]}"]'):
        href = a.get("href")
        if not href:
            continue
        full_url = urljoin(site["list_url"], href)
        if full_url in seen:
            continue
        seen.add(full_url)

        if existing_urls and full_url in existing_urls:
            known_streak += 1
            if known_streak >= stop_after_known:
                logger.debug(f"[{site['name']}] 连续 {stop_after_known} 条已存在，停止解析")
                break
            continue

        known_streak = 0

        title = a.get_text(" ", strip=True)
        title = re.sub(r'^\d{4}[-./年]\d{1,2}[-./月]\d{1,2}日?\s*', '', title).strip()
        if not title or len(title) < 4:
            continue

        date = extract_date(a, site)
        items.append({
            "title": title,
            "url": full_url,
            "date": date,
            "site": site["name"],
            "category": site.get("category", "其他"),
        })

    items.sort(
        key=lambda x: (x["date"] is None, x["date"] or datetime.min.date()),
        reverse=False,
    )
    return items


async def enrich_dates(client, items, max_fetch_per_site: int = 10):
    if not items:
        return items

    dates = Counter(it["date"] for it in items if it["date"])
    suspicious = {d for d, c in dates.items() if c >= 3}

    need_fetch = [
        it for it in items
        if it["date"] is None or it["date"] in suspicious
    ]

    fetched = {}
    fetched_count = 0

    for it in need_fetch:
        if fetched_count >= max_fetch_per_site:
            break
        url = it["url"]
        if url in fetched:
            continue
        try:
            html = await fetch(client, url)
            fetched[url] = parse_detail_date(html)
        except Exception as e:
            logger.warning(f"[WARN] 详情页失败 {url}: {e}")
            fetched[url] = None
        fetched_count += 1
        await asyncio.sleep(0.3)

    for it in items:
        d = fetched.get(it["url"])
        if d:
            it["date"] = d
    return items


async def crawl_site(client, site, existing_urls=None):
    if site.get("type") == "json":
        return await crawl_json_site(client, site)

    try:
        html = await fetch(client, site["list_url"])
    except Exception as e:
        logger.error(f"[ERR] {site['name']} 抓取失败: {e}")
        return []

    stop = site.get("stop_after_known", 2)
    items = parse_list(html, site, existing_urls, stop_after_known=stop)

    if items:
        items = await enrich_dates(client, items, max_fetch_per_site=10)

    logger.info(
        f"[OK] {site['name']}: 新增 {len(items)} 条，"
        f"日期缺失 {sum(1 for i in items if i['date'] is None)} 条"
    )
    return items


async def crawl_json_site(client, site):
    try:
        r = await client.get(
            site["api_url"],
            params=site.get("params", {}),
            headers={"Referer": site.get("referer", "")},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.error(f"[ERR] {site['name']} JSON 抓取失败: {e}")
        return []

    rows = data.get("rows", [])
    items = []
    for row in rows:
        title = (row.get(site["title_field"]) or "").strip()
        if not title:
            continue

        date_str = row.get(site["date_field"])
        date = None
        if date_str:
            try:
                date = datetime.fromisoformat(date_str).date()
            except ValueError:
                pass

        fileurl = row.get(site.get("fileurl_field", "fileurl"))
        if fileurl:
            url = fileurl
        else:
            url = site["url_template"].format(id=row.get(site["id_field"]))

        items.append({
            "title": title,
            "url": url,
            "date": date,
            "site": site["name"],
            "category": site.get("category", "其他"),
        })

    logger.info(f"[OK] {site['name']}: 抓到 {len(items)} 条（JSON）")
    return items


async def crawl_all():
    client = get_client()
    existing = db.get_all_urls()
    tasks = [crawl_site(client, s, existing) for s in SITES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out = []
    for r in results:
        if isinstance(r, Exception):
            logger.error(f"[ytunews] 站点抓取异常: {r}")
            continue
        out.extend(r)
    return out
