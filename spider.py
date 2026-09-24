# -*- coding: utf-8 -*-
import asyncio
import re
from collections import Counter
from datetime import datetime
from urllib.parse import urljoin, urlparse

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

SKIP_TITLES = {
    "首页", "上页", "下页", "尾页", "返回", "更多",
    "上一页", "下一页", "网站首页", "校医院简介", "荣誉资质",
    "保健专栏", "医保中心", "党建工作", "交流合作", "文件下载",
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


async def fetch(client, url):
    r = await client.get(url)
    r.raise_for_status()
    if not r.encoding:
        r.encoding = "utf-8"
    return r.text


def parse_list(html, site, existing_urls=None):
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(site["container"]) or soup
    allow_external = site.get("allow_external", False)
    base_netloc = urlparse(site["base"]).netloc

    link_filter = site.get("link_filter")
    if link_filter:
        links = container.select(f'a[href*="{link_filter}"]')
    else:
        links = container.find_all("a", href=True)

    items = []
    seen = set()
    for a in links:
        href = a.get("href")
        if not href or href == "#" or "javascript" in href.lower():
            continue

        full_url = urljoin(site["base"], href)

        if not allow_external and urlparse(full_url).netloc != base_netloc:
            continue

        if full_url in seen:
            continue
        seen.add(full_url)

        title = a.get_text(" ", strip=True)
        if not title or len(title) < 4:
            continue
        if title in SKIP_TITLES:
            continue

        title = re.sub(r'^\d{4}[-./年]\d{1,2}[-./月]\d{1,2}日?\s*', '', title).strip()
        title = re.sub(r'\s*\d{4}[-./]\d{1,2}[-./]\d{1,2}\s*$', '', title).strip()
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


async def enrich_dates(client, items, max_fetch_per_site=30):
    if not items:
        return items

    dates = Counter(it["date"] for it in items if it["date"])
    suspicious = {d for d, c in dates.items() if c >= 3}

    need_fetch = {}
    for it in items:
        if it["url"].startswith("https://mp.weixin.qq.com"):
            continue
        if it["date"] is None or it["date"] in suspicious:
            need_fetch[it["url"]] = it
    need_fetch = list(need_fetch.values())

    fetched = {}
    count = 0
    for it in need_fetch:
        if count >= max_fetch_per_site:
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
        count += 1
        await asyncio.sleep(0.3)

    for it in items:
        d = fetched.get(it["url"])
        if d:
            it["date"] = d
    return items


async def crawl_site(client, site, existing_urls=None):
    if site.get("type") == "json":
        return await crawl_json_site(client, site)

    site_name = site["name"]
    full_fetched = db.get_kv(f"full_fetched_{site_name}") == "1"

    if full_fetched:
        max_pages = 1
    else:
        max_pages = site.get("max_pages", 50)

    page_pattern = site.get("page_pattern")
    all_items = []
    seen = set()

    for page in range(1, max_pages + 1):
        if page == 1:
            url = site["list_url"]
        else:
            if not page_pattern:
                break
            url = page_pattern.format(page=page)

        try:
            html = await fetch(client, url)
        except Exception as e:
            logger.warning(f"[{site_name}] 第 {page} 页失败: {e}")
            break

        items = parse_list(html, site, existing_urls)
        new_items = [it for it in items if it["url"] not in seen]
        for it in new_items:
            seen.add(it["url"])
        all_items.extend(new_items)

        logger.debug(
            f"[{site_name}] 第 {page} 页，共 {len(items)} 条，新 {len(new_items)} 条"
        )

        if not items:
            break

        await asyncio.sleep(0.3)

    if all_items:
        all_items = await enrich_dates(client, all_items, max_fetch_per_site=30)

    if not full_fetched:
        db.set_kv(f"full_fetched_{site_name}", "1")
        logger.info(f"[{site_name}] 首次全量抓取完成，后续只抓第一页")

    logger.info(
        f"[OK] {site_name}: 新增 {len(all_items)} 条，"
        f"日期缺失 {sum(1 for i in all_items if i['date'] is None)} 条"
    )
    return all_items


async def crawl_json_site(client, site):
    try:
        r = await client.get(
            site["api_url"],
            params=site.get("params", {}),
            headers={**HEADERS, "Referer": site.get("referer", "")},
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
