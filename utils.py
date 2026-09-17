# -*- coding: utf-8 -*-
import re
from datetime import datetime

DATE_PATTERN = re.compile(r'(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})')
URL_DATE_PATTERN = re.compile(r'/(\d{4})/(\d{2})(\d{2})/')

# 优先级 1：时间：2026年09月16日
DETAIL_DATE_PATTERN = re.compile(r'时间[:：]\s*(\d{4})年(\d{1,2})月(\d{1,2})日')
# 优先级 2：发布时间 / 时间 + 连字符 / 斜杠 / 点
PUBDATE_PATTERN = re.compile(
    r'(?:发布时间|发布日期|时间)[:：]\s*(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})'
)
# 优先级 3：meta PubDate
META_PUBDATE_PATTERN = re.compile(
    r'<meta[^>]+name=["\']PubDate["\'][^>]+content=["\'](\d{4})[-/](\d{1,2})[-/](\d{1,2})'
)


def _parse_match(m):
    if not m:
        return None
    y, mo, d = m.groups()
    try:
        return datetime(int(y), int(mo), int(d)).date()
    except ValueError:
        return None


def parse_date_text(text: str):
    if not text:
        return None
    return _parse_match(DATE_PATTERN.search(text))


def parse_detail_date(html: str):
    if not html:
        return None
    # 优先级 1
    d = _parse_match(DETAIL_DATE_PATTERN.search(html))
    if d:
        return d
    # 优先级 2
    d = _parse_match(PUBDATE_PATTERN.search(html))
    if d:
        return d
    # 优先级 3
    d = _parse_match(META_PUBDATE_PATTERN.search(html))
    if d:
        return d
    # 优先级 4：全文兜底
    return parse_date_text(html)


def extract_date(a_tag, site: dict):
    sel = site.get("date_selector")
    if sel:
        node = a_tag.find(sel)
        if not node and a_tag.parent:
            node = a_tag.parent.find(sel)
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

    for sib in [a_tag.find_next_sibling(), a_tag.find_previous_sibling()]:
        if sib:
            d = parse_date_text(sib.get_text(" ", strip=True))
            if d:
                return d

    href = a_tag.get("href", "") or ""
    m = URL_DATE_PATTERN.search(href)
    if m:
        y, mo, d_ = m.groups()
        try:
            return datetime(int(y), int(mo), int(d_)).date()
        except ValueError:
            pass

    return None
