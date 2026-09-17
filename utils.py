# -*- coding: utf-8 -*-
import re
from datetime import date, datetime
from typing import Optional

DATE_PATTERN = re.compile(r'(\d{4})\s*[-./年]\s*(\d{1,2})\s*[-./月]\s*(\d{1,2})\s*日?')

URL_DATE_PATTERN = re.compile(
    r'/(\d{4})/(\d{2})(\d{2})/|'
    r'/(\d{4})/(\d{2})/(\d{2})/|'
    r'/(\d{4})(\d{2})(\d{2})/'
)

DETAIL_DATE_PATTERN = re.compile(
    r'(?:时间|发布时间|发布日期|发表时间|更新日期)\s*[:：]\s*'
    r'(\d{4})\s*[-./年]\s*(\d{1,2})\s*[-./月]\s*(\d{1,2})\s*日?'
)

META_PUBDATE_PATTERN = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:PubDate|pubdate|article:published_time|'
    r'og:published_time|publishdate)["\'][^>]+content=["\']'
    r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})',
    re.IGNORECASE,
)

MIN_YEAR = 2000


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    if not (MIN_YEAR <= y <= date.today().year + 1):
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _parse_match(m) -> Optional[date]:
    if not m:
        return None
    groups = m.groups()
    if len(groups) == 9:
        for i in range(0, 9, 3):
            if groups[i]:
                return _safe_date(int(groups[i]), int(groups[i + 1]), int(groups[i + 2]))
        return None
    y, mo, d = groups[:3]
    return _safe_date(int(y), int(mo), int(d))


def parse_date_text(text: str) -> Optional[date]:
    if not text:
        return None
    for m in DATE_PATTERN.finditer(text):
        d = _parse_match(m)
        if d:
            return d
    return None


def parse_detail_date(html: str) -> Optional[date]:
    if not html:
        return None
    d = _parse_match(DETAIL_DATE_PATTERN.search(html))
    if d:
        return d
    d = _parse_match(META_PUBDATE_PATTERN.search(html))
    if d:
        return d
    return parse_date_text(html[:20000])


def extract_date(a_tag, site: dict) -> Optional[date]:
    sel = site.get("date_selector")
    if sel:
        for scope in (a_tag, a_tag.parent):
            if scope is None:
                continue
            node = scope.find(sel)
            if node:
                d = parse_date_text(node.get_text(" ", strip=True))
                if d:
                    return d

    d = parse_date_text(a_tag.get_text(" ", strip=True))
    if d:
        return d

    if a_tag.parent:
        d = parse_date_text(a_tag.parent.get_text(" ", strip=True))
        if d:
            return d

    for sib in (a_tag.find_previous_sibling(), a_tag.find_next_sibling()):
        if sib:
            d = parse_date_text(sib.get_text(" ", strip=True))
            if d:
                return d

    href = a_tag.get("href", "") or ""
    m = URL_DATE_PATTERN.search(href)
    if m:
        return _parse_match(m)

    return None
