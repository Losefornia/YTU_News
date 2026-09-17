# -*- coding: utf-8 -*-
import asyncio
import logging
import re
from collections import Counter
from datetime import date, datetime
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .sites import SITES
from .utils import extract_date, parse_detail_date

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

DETAIL_CONCURRENCY = 8
DETAIL_DELAY = 0.2
REQUEST_TIMEOUT = 20
MAX_RETRIES = 2

DATE_PREFIX_RE = re.compile(
    r'^\s*(?:\[|\(|【)?'
    r'\d{4}[-./年]\d{1,2}[-./月]\d{1,2}日?'
    r'(?:\]|\)|】)?\s*[:\-—]?\s*'
)


def _clean_title(raw: str) -> str:
    return re.sub(r'\s+', ' ', DATE_PREFIX_RE.sub('', raw).strip())


def _is_valid_url(url: str) -> bool:
    if not url:
        return False
    return urlparse(url).scheme in ("http", "https")


async def fetch(client: httpx.AsyncClient, url: str) -> str:
    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = await client.get(
                url, headers=HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True
            )
            r.raise_for_status()
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                await asyncio.sleep(0.5 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def parse_list(html: str, site: dict) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(site["container"]) or soup

    items: list[dict] = []
    seen: set[str] = set()

    link_filter = site["link_filter"]
    min_len = site.get("min_title_len", 4)
    max_items = site.get("max_items")
    url_contains = site.get("url_contains") or []
    exclude_kw = site.get("exclude_href_kw") or []

    for a in container.select(f'a[href*="{link_filter}"]'):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        if any(kw in href.lower() for kw in exclude_kw):
            continue

        full_url = urljoin(site["list_url"], href)
        if not _is_valid_url(full_url) or full_url in seen:
            continue
        if url_contains and not all(kw in full_url for kw in url_contains):
            continue

        title = _clean_title(a.get_text(" ", strip=True))
        if len(title) < min_len:
            continue

        seen.add(full_url)
        items.append({
            "title": title,
            "url": full_url,
            "date": extract_date(a, site),
            "site": site["name"],
            "category": site.get("category", "其他"),
        })
        if max_items and len(items) >= max_items:
            break

    return items


def find_suspicious(items: list[dict], threshold: int = 3, min_total: int = 8) -> list[dict]:
    by_site: dict[str, list[dict]] = {}
    for it in items:
        by_site.setdefault(it["site"], []).append(it)

    suspicious: list[dict] = []
    for site_items in by_site.values():
        dated = [it for it in site_items if it["date"]]
        if len(dated) < min_total:
            continue
        dates = Counter(it["date"] for it in dated)
        for d, c in dates.items():
            if c >= threshold and c / len(dated) >= 0.5:
                suspicious.extend(it for it in dated if it["date"] == d)
    return suspicious


async def _fetch_detail_date(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
) -> Optional[date]:
    async with sem:
        try:
            html = await fetch(client, url)
            return parse_detail_date(html)
        except Exception as e:
            logger.warning("详情页失败 %s: %s", url, e)
            return None
        finally:
            await asyncio.sleep(DETAIL_DELAY)


async def enrich_dates(
    client: httpx.AsyncClient,
    items: list[dict],
    only_suspicious: bool = False,
) -> list[dict]:
    if only_suspicious:
        targets = find_suspicious(items)
    else:
        targets = [it for it in items if it["date"] is None]

    if not targets:
        return items

    sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
    results = await asyncio.gather(
        *(_fetch_detail_date(client, sem, it["url"]) for it in targets)
    )
    for it, d in zip(targets, results):
        if only_suspicious:
            it["date"] = d or it["date"]
        elif d is not None:
            it["date"] = d
    return items


async def crawl_site(client: httpx.AsyncClient, site: dict) -> list[dict]:
    if site.get("type") == "json":
        return await crawl_json_site(client, site)

    try:
        html = await fetch(client, site["list_url"])
    except Exception as e:
        logger.error("%s 抓取失败: %s", site["name"], e)
        return []

    items = parse_list(html, site)
    if not items:
        return items

    await enrich_dates(client, items, only_suspicious=False)
    await enrich_dates(client, items, only_suspicious=True)

    missing = sum(1 for i in items if i["date"] is None)
    logger.info("[OK] %s: 抓到 %d 条，日期缺失 %d 条", site["name"], len(items), missing)
    return items


async def crawl_json_site(client: httpx.AsyncClient, site: dict) -> list[dict]:
    try:
        r = await client.get(
            site["api_url"],
            params=site.get("params", {}),
            headers={**HEADERS, "Referer": site.get("referer", "")},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.error("%s JSON 抓取失败: %s", site["name"], e)
        return []

    rows = data.get("rows", [])
    items: list[dict] = []
    for row in rows:
        title = (row.get(site["title_field"]) or "").strip()
        if not title:
            continue

        date_val: Optional[date] = None
        date_str = row.get(site["date_field"])
        if date_str:
            try:
                date_val = datetime.fromisoformat(date_str).date()
            except (ValueError, TypeError):
                pass

        url = row.get(site.get("fileurl_field", "fileurl")) or site["url_template"].format(
            id=row.get(site["id_field"])
        )

        items.append({
            "title": title,
            "url": url,
            "date": date_val,
            "site": site["name"],
            "category": site.get("category", "其他"),
        })

    logger.info("[OK] %s: 抓到 %d 条（JSON）", site["name"], len(items))
    return items


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        transport=httpx.AsyncHTTPTransport(retries=2),
        headers=HEADERS,
    )


async def crawl_all(sites: Iterable[dict] = SITES) -> list[dict]:
    sites = list(sites)
    async with _make_client() as client:
        results = await asyncio.gather(
            *(crawl_site(client, s) for s in sites),
            return_exceptions=True,
        )

    all_items: list[dict] = []
    for site, res in zip(sites, results):
        if isinstance(res, Exception):
            logger.error("%s 整体失败: %s", site.get("name"), res)
            continue
        all_items.extend(res)
    return all_items
