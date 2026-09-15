# -*- coding: utf-8 -*-
import asyncio
import re
from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .sites import SITES
from .utils import extract_date, parse_detail_date

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


async def fetch(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text


def parse_list(html: str, site: dict):
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(site["container"]) or soup

    items = []
    seen = set()
    for a in container.select(f'a[href*="{site["link_filter"]}"]'):
        title = a.get_text(" ", strip=True)
        title = re.sub(r'^\d{4}[-./年]\d{1,2}[-./月]\d{1,2}日?\s*', '', title).strip()
        if not title or len(title) < 4:
            continue
        href = a.get("href")
        if not href:
            continue
        full_url = urljoin(site["list_url"], href)
        if full_url in seen:
            continue
        seen.add(full_url)

        date = extract_date(a, site)
        items.append({
            "title": title,
            "url": full_url,
            "date": date,
            "site": site["name"],
            "category": site.get("category", "其他"),
        })
    return items


async def fill_missing_dates(client, items):
    for it in items:
        if it["date"] is not None:
            continue
        try:
            html = await fetch(client, it["url"])
            d = parse_detail_date(html)
            if d:
                it["date"] = d
        except Exception as e:
            print(f"[WARN] 详情页失败 {it['url']}: {e}")
        await asyncio.sleep(0.3)
    return items


async def crawl_site(client, site):
    if site.get("type") == "json":
        return await crawl_json_site(client, site)

    try:
        html = await fetch(client, site["list_url"])
    except Exception as e:
        print(f"[ERR] {site['name']} 抓取失败: {e}")
        return []
    items = parse_list(html, site)

    if any(it["date"] is None for it in items):
        items = await fill_missing_dates(client, items)

    print(f"[OK] {site['name']}: 抓到 {len(items)} 条，"
          f"日期缺失 {sum(1 for i in items if i['date'] is None)} 条")
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
        print(f"[ERR] {site['name']} JSON 抓取失败: {e}")
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

    print(f"[OK] {site['name']}: 抓到 {len(items)} 条（JSON）")
    return items


async def crawl_all():
    async with httpx.AsyncClient() as client:
        tasks = [crawl_site(client, s) for s in SITES]
        results = await asyncio.gather(*tasks)
    all_items = [it for sub in results for it in sub]
    return all_items
