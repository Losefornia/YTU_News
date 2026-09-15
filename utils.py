# -*- coding: utf-8 -*-
import re
from datetime import datetime

DATE_PATTERN = re.compile(r'(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})')
URL_DATE_PATTERN = re.compile(r'/(\d{4})/(\d{2})(\d{2})/')
DETAIL_DATE_PATTERN = re.compile(r'时间[:：]\s*(\d{4})年(\d{1,2})月(\d{1,2})日')


def parse_date_text(text: str):
    if not text:
        return None
    m = DATE_PATTERN.search(text)
    if not m:
        return None
    y, mo, d = m.groups()
    try:
        return datetime(int(y), int(mo), int(d)).date()
    except ValueError:
        return None


def parse_detail_date(html: str):
    if not html:
        return None
    m = DETAIL_DATE_PATTERN.search(html)
    if m:
        y, mo, d = m.groups()
        try:
            return datetime(int(y), int(mo), int(d)).date()
        except ValueError:
            pass
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
