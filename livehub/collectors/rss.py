"""RSS 2.0 / RSS 1.0(RDF) / Atom フィード。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Optional
from urllib.parse import urljoin

from . import RawItem

_TAG = re.compile(r"<[^>]+>")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, *names: str) -> Optional[ET.Element]:
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _text(el: ET.Element, *names: str) -> str:
    c = _child(el, *names)
    return (c.text or "").strip() if c is not None else ""


def _date(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", s or ""))).strip()


def parse_feed(text: str, base_url: str = "") -> list[RawItem]:
    root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    items = [e for e in root.iter() if _local(e.tag) in ("item", "entry")]
    out = []
    for it in items:
        title = strip_html(_text(it, "title"))
        link = _text(it, "link")
        if not link:
            le = _child(it, "link")
            if le is not None:
                link = le.get("href", "")
        if not title or not link:
            continue
        summary = strip_html(_text(it, "description", "summary", "content", "encoded"))
        pub = _date(_text(it, "pubDate", "published", "updated", "date"))
        out.append(RawItem(title=title, url=urljoin(base_url, link), summary=summary[:1000], published_at=pub))
    return out
