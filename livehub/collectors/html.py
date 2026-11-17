"""HTMLページから情報を取り出す。

1. schema.org の Event 構造化データ(JSON-LD)があれば最優先で使う(最も正確で、
   サイト側が機械可読にする意思で公開しているデータ)
2. 無ければスケジュールページを「日付を含むリンク付きブロック」単位で切り出す
3. フィード(RSS/Atom/iCal)へのリンクを見つける(discover 用)
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import urljoin

from . import RawItem
from ..textutil import clean_space, find_dates

EVENT_TYPES = {
    "Event", "MusicEvent", "TheaterEvent", "ComedyEvent", "DanceEvent", "Festival",
    "PerformingArtsEvent", "SocialEvent", "EntertainmentEvent",
}


# ---------- JSON-LD ----------
_LD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def _walk(node: Any):
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "subEvent", "itemListElement", "item"):
            if key in node:
                yield from _walk(node[key])


def _types(node: dict) -> set[str]:
    t = node.get("@type", [])
    return set(t if isinstance(t, list) else [t])


def _parse_dt(v: Any) -> Optional[datetime]:
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(v[:10]), datetime.min.time())
        except ValueError:
            return None


def _names(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [v["name"]] if v.get("name") else []
    if isinstance(v, list):
        return [n for x in v for n in _names(x)]
    return []


def parse_jsonld_events(html_text: str, base_url: str = "") -> list[RawItem]:
    out = []
    for block in _LD.findall(html_text):
        try:
            data = json.loads(unescape(block.strip()))
        except json.JSONDecodeError:
            continue
        for node in _walk(data):
            if not (_types(node) & EVENT_TYPES) or not node.get("name"):
                continue
            start = _parse_dt(node.get("startDate"))
            end = _parse_dt(node.get("endDate"))
            loc = node.get("location") or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            addr = loc.get("address") if isinstance(loc, dict) else None
            pref = addr.get("addressRegion") if isinstance(addr, dict) else None
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            sale = _parse_dt(offers.get("validFrom")) if isinstance(offers, dict) else None
            out.append(RawItem(
                title=clean_space(node["name"]),
                url=urljoin(base_url, node.get("url") or base_url),
                summary=clean_space(str(node.get("description", "")))[:1000],
                start_date=start.date() if start else None,
                end_date=end.date() if end and (not start or end.date() > start.date()) else None,
                venue=(loc.get("name") if isinstance(loc, dict) else None) or None,
                prefecture=pref,
                performers=_names(node.get("performer"))[:10],
                ticket_sale_at=sale.replace(tzinfo=None) if sale else None,
                structured=True,
            ))
    return out


# ---------- フィード検出 + ブロック抽出 ----------
class _Scanner(HTMLParser):
    BLOCK = {"li", "article", "tr", "dl", "section", "div"}
    SKIP = {"script", "style", "noscript", "header", "footer", "nav"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.feeds: list[tuple[str, str]] = []
        self.blocks: list[tuple[str, list[str]]] = []
        self.link_texts: dict[str, str] = {}
        self._stack: list[tuple[list[str], list[str]]] = []
        self._skip = 0
        self._a: Optional[str] = None
        self._abuf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and "alternate" in (a.get("rel") or "").lower():
            t = (a.get("type") or "").lower()
            if any(x in t for x in ("rss", "atom", "calendar")) and a.get("href"):
                self.feeds.append((t, a["href"]))
        if tag == "a" and a.get("href", "").lower().split("?")[0].endswith(".ics"):
            self.feeds.append(("text/calendar", a["href"]))
        if tag in self.SKIP:
            self._skip += 1
        if tag in self.BLOCK:
            # カード型レイアウト(<a href><div>日付</div><div>公演名</div></a>)では、外側のリンクを内側の枠にも持たせる
            self._stack.append(([], [self._a] if self._a else []))
        if tag == "a" and a.get("href") and self._stack:
            self._stack[-1][1].append(a["href"])
        if tag == "a" and a.get("href"):
            self._a, self._abuf = a["href"], []

    def handle_endtag(self, tag):
        if tag == "a" and self._a is not None:
            txt = clean_space(" ".join(self._abuf))
            if len(txt) > len(self.link_texts.get(self._a, "")):
                self.link_texts[self._a] = txt
            self._a = None
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self.BLOCK and self._stack:
            texts, links = self._stack.pop()
            text = clean_space(" ".join(texts))
            if self._stack:  # 親ブロックにも本文とリンクを伝える
                self._stack[-1][0].append(text)
                self._stack[-1][1].extend(links)
            self.blocks.append((text, links))

    def handle_data(self, data):
        if self._a is not None and data.strip():
            self._abuf.append(data.strip())
        if not self._skip and self._stack and data.strip():
            self._stack[-1][0].append(data.strip())


def find_feeds(html_text: str, base_url: str) -> list[tuple[str, str]]:
    sc = _Scanner()
    sc.feed(html_text)
    return [(t, urljoin(base_url, h)) for t, h in sc.feeds]


_WEEKDAY = re.compile(r"[\s()（）\[\]・.〜~\-/:：]|[月火水木金土日祝]|SUN|MON|TUE|WED|THU|FRI|SAT", re.I)


def _has_date_and_name(text: str, ref: date) -> bool:
    """日付があり、かつ日付・曜日以外の文字(公演名など)もある枠か。"""
    hits = find_dates(text, ref)
    if not hits:
        return False
    rest = text
    for h in sorted(hits, key=lambda h: -h.start):
        rest = rest[:h.start] + " " + rest[h.end:]
    # 時刻・開場/開演・会期の見出し・括弧書き(臨時バスの案内など)だけの枠は、公演名がない
    rest = re.sub(r"[(（][^)）]*[)）]", " ", rest)
    rest = re.sub(r"(ジャンル|カテゴリー?|会場|場所|ホール)\s*[:：]\s*\S+", " ", rest)
    rest = re.sub(r"\d{1,2}\s*[:：]\s*\d{2}|開場|開演|終演|開始|終了|会期中|初日|最終日|時間|OPEN|START|CLOSE|予定", " ", rest, flags=re.I)
    return len(_WEEKDAY.sub("", rest)) >= 2


def parse_schedule_blocks(html_text: str, base_url: str, ref: date) -> list[RawItem]:
    """日付を1つ以上含み、リンクを持つ、適度な長さのブロックを公演とみなす。
    入れ子の場合は最も内側(短い)ブロックを採用する。"""
    sc = _Scanner()
    sc.feed(html_text)
    cands = [(t, l) for t, l in sc.blocks if 8 <= len(t) <= 400 and l and _has_date_and_name(t, ref)]
    cands.sort(key=lambda b: len(b[0]))
    chosen: list[tuple[str, list[str]]] = []
    for text, links in cands:
        if any(text in c[0] or c[0] in text for c in chosen):
            continue
        chosen.append((text, links))
    out = []
    for text, links in chosen:
        # 1つの枠(カレンダーの1日など)に複数の公演リンクがある場合は、リンクごとに分ける
        named = []
        for h in dict.fromkeys(links):
            lt = sc.link_texts.get(h, "")
            if len(lt) >= 4 and not _GENERIC_LINK.search(lt):
                named.append((h, lt))
        if len(named) >= 2:
            hits = find_dates(text, ref)
            head = text[:hits[0].end] if hits else ""  # 枠の共通部分は日付だけ(他の催しの語を混ぜない)
            for h, lt in named:
                out.append(RawItem(title=lt[:200], url=urljoin(base_url, h), summary=f"{head} {lt}"))
        else:
            out.append(RawItem(title=text[:200], url=urljoin(base_url, links[0]), summary=text))
    return out


_GENERIC_LINK = re.compile(r"^(詳細|詳しく|more|MORE|もっと見る|チケット|購入|予約|公演ページ|作品ページ|一覧|次へ|前へ|>|＞|»|TOP|HOME|PAGE ?TOP)", re.I)

_DROP = re.compile(r"<(script|style|noscript|nav|header|footer)\b.*?</\1>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")


def page_text(html_text: str, limit: int = 4000) -> str:
    """詳細ページの本文テキスト(ナビ・ヘッダ・スクリプトを除く)。判定にだけ使い、保存しない。"""
    t = _DROP.sub(" ", html_text)
    t = re.sub(r"<br\s*/?>|</(p|div|li|dt|dd|tr|h\d)>", "\n", t, flags=re.I)
    return clean_space(unescape(_TAGS.sub(" ", t)))[:limit]


# ---------- スケジュールページの自動発見 ----------
# メニューの文字(またはURL)に含まれる語と、その優先度(小さいほど優先)
_SCHEDULE_WORDS = [
    (0, re.compile(r"(スケジュール|schedule|公演情報|公演スケジュール|公演一覧|イベントスケジュール|ライブスケジュール)", re.I)),
    (1, re.compile(r"(ライブ情報|live\s*info|ライブ・イベント|イベント情報|event\s*info|ツアー情報|tour\s*info|上演情報)", re.I)),
    (2, re.compile(r"^(live|ライブ|ライヴ|event|events|イベント|tour|ツアー|公演|calendar|カレンダー|stage|ステージ)$", re.I)),
]
_SCHEDULE_PATH = re.compile(r"/(schedule|live|lives|event|events|tour|calendar|stage|performance|kouen)(/|\.html?|$|\?)", re.I)
_NOT_SCHEDULE = re.compile(r"(配信|アーカイブ|archive|report|レポート|過去|past|goods|グッズ|ticket\.|fc|fanclub|"
                           r"番組|timetable|time-table|program_?table|programtable|weeklyprogram|tv-prog|bangumi|epg|\.pdf$)", re.I)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: Optional[str] = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._href, self._buf = a["href"], []
        elif tag == "img" and self._href is not None and a.get("alt"):
            self._buf.append(a["alt"])  # 画像メニューの代替テキスト

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, clean_space(" ".join(self._buf))))
            self._href = None


def find_schedule_pages(html_text: str, base_url: str, limit: int = 2) -> list[str]:
    """トップページのリンクから、公演スケジュールらしいページを優先度順に返す(同じサイト内のみ)。"""
    from urllib.parse import urlsplit
    host = urlsplit(base_url).netloc.removeprefix("www.")
    p = _Links()
    p.feed(html_text)
    scored: dict[str, tuple[int, int]] = {}
    for order, (href, text) in enumerate(p.links):
        url = urljoin(base_url, href).split("#")[0]
        if not url.startswith("http") or urlsplit(url).netloc.removeprefix("www.") != host:
            continue
        if url.rstrip("/") == base_url.rstrip("/") or _NOT_SCHEDULE.search(text + " " + url):
            continue
        rank = next((r for r, pat in _SCHEDULE_WORDS if pat.search(text.strip())), None)
        if rank is None and _SCHEDULE_PATH.search(urlsplit(url).path + ("?" + urlsplit(url).query if urlsplit(url).query else "")):
            rank = 3
        if rank is not None and (url not in scored or scored[url] > (rank, order)):
            scored[url] = (rank, order)
    return [u for u, _ in sorted(scored.items(), key=lambda kv: kv[1])][:limit]


_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_META = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:site_name|og:title|description)["\'][^>]*content=["\']([^"\']*)', re.I)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)


def page_identity(html_text: str) -> str:
    """サイトの名乗り(題名・サイト名・説明・見出し)。本文は見ない(同名の言及で誤判定しないため)。"""
    parts = _TITLE.findall(html_text)[:1] + _META.findall(html_text)[:3] + _H1.findall(html_text)[:2]
    return clean_space(unescape(re.sub(r"<[^>]+>", " ", " ".join(parts))))[:600]
