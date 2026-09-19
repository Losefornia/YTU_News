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


async def fetch(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text


def parse_list(html: str, site: dict, existing_urls: set = None, stop_after_known: int = 2):
    """连续 N 条已存在就停，避免全量解析。"""
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

        # 连续 N 条已存在 → 认为后面都是旧的，停止
        if existing_urls and full_url in existing_urls:
            known_streak += 1
            if known_streak >= stop_after_known:
                logger.debug(f"[{site['name']}] 连续 {stop_after_known} 条已存在，停止解析")
                break
            continue

        known_streak = 0  # 遇到新的，重置

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

    # 按日期排序，有日期的在前，无日期的在后
    items.sort(
        key=lambda x: (x["date"] is None, x["date"] or datetime.min.date()),
        reverse=False,
    )
    return items


async def enrich_dates(client, items, max_fetch_per_site: int = 10):
    """缺日期的补详情页；可疑日期的核对详情页。同一个 URL 只抓一次，且限制总数。"""
    fetched = {}
    fetched_count = 0

    need_fetch = [it for it in items if it["date"] is None]

    dates = Counter(it["date"] for it in items if it["date"])
    suspicious = {d for d, c in dates.items() if c >= 3}
    for it in items:
        if it["date"] in suspicious and it not in need_fetch:
            need_fetch.append(it)

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
        if it["url"] in fetched and fetched[it["url"]]:
            it["date"] = fetched[it["url"]]
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
            headers={**HEADERS, "Referer": site.get("referer", "")},
            timeout=20,
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
    async with httpx.AsyncClient() as client:
        existing = db.get_all_urls()   # 只查一次
        tasks = [crawl_site(client, s, existing) for s in SITES]
        results = await asyncio.gather(*tasks)
    return [it for sub in results for it in sub]
