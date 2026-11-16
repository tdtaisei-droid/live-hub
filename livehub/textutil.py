"""日本語テキストの正規化と、日付・チケット発売情報の抽出。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

_SYMBOLS = re.compile(r"[\s　「」『』【】\[\]()（）〈〉《》<>\"'“”‘’・･、。,.!！?？~〜～\-ー―‐/／:：;；#＃&＆*＊+＋=|｜♪☆★◆◇■□●○※]")


def normalize(text: str) -> str:
    """表記ゆれ吸収用。全角半角統一・小文字化・記号と空白を除去。"""
    t = unicodedata.normalize("NFKC", text or "").lower()
    return _SYMBOLS.sub("", t)


def clean_space(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()


# 2027年1月10日 / 2027/1/10 / 2027.1.10 / 1月10日 / 1/10(土)
_DATE = re.compile(
    r"(?:(?P<y>20\d{2})\s*[年/.]\s*)?"
    r"(?P<m>1[0-2]|0?[1-9])\s*(?:月|/|\.)\s*(?P<d>3[01]|[12]\d|0?[1-9])\s*日?"
    r"(?:\s*[(（][^)）]{1,4}[)）])?"
    r"(?:\s*(?P<h>[01]?\d|2[0-3])\s*(?::|時)\s*(?P<mi>[0-5]\d)?)?"
)
_RANGE_SEP = re.compile(r"^\s*(?:〜|~|～|-|－|から|より)\s*$")

TICKET_WORDS = re.compile(
    r"(最速先行|オフィシャル先行|ファンクラブ先行|FC先行|会員先行|先行抽選|先行受付|先行販売|プレリザーブ|プレオーダー|"
    r"抽選受付|一般発売|一般販売|一般受付|チケット発売|発売開始|受付開始|販売開始|先行)"
)
TICKET_AFTER = re.compile(r"^\s*(?:より|から|〜|~)?(?:発売|販売|受付|抽選)")


@dataclass
class DateHit:
    value: datetime
    has_time: bool
    start: int
    end: int
    explicit_year: bool = False


def _infer_year(m: int, d: int, ref: date) -> int:
    """年が省略された日付は、基準日から見て直近の未来(または30日以内の過去)とみなす。"""
    for y in (ref.year, ref.year + 1):
        try:
            cand = date(y, m, d)
        except ValueError:
            continue
        if (cand - ref).days >= -30:
            return y
    return ref.year + 1


def find_dates(text: str, ref: date) -> list[DateHit]:
    t = unicodedata.normalize("NFKC", text or "")
    hits = []
    for mt in _DATE.finditer(t):
        raw = mt.group(0)
        # 「1/10」形式は誤検出が多いので、年か曜日か時刻が付いているときだけ採用
        if "月" not in raw and not (mt.group("y") or "(" in raw or mt.group("h")):
            continue
        m, d = int(mt.group("m")), int(mt.group("d"))
        y = int(mt.group("y")) if mt.group("y") else _infer_year(m, d, ref)
        try:
            day = date(y, m, d)
        except ValueError:
            continue
        hh = int(mt.group("h")) if mt.group("h") else 0
        mi = int(mt.group("mi")) if mt.group("mi") else 0
        hits.append(DateHit(datetime.combine(day, time(hh, mi)), mt.group("h") is not None, mt.start(), mt.end(),
                            explicit_year=mt.group("y") is not None))
    return hits


@dataclass
class Extracted:
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    ticket_sale_at: Optional[datetime] = None
    ticket_sale_label: Optional[str] = None


def extract_schedule(text: str, ref: date) -> Extracted:
    """本文やタイトルから、公演日程とチケット発売日を切り分けて取り出す。"""
    t = unicodedata.normalize("NFKC", text or "")
    hits = find_dates(t, ref)
    out = Extracted()
    show_days: list[date] = []
    for i, h in enumerate(hits):
        before = t[max(0, h.start - 18):h.start]
        after = t[h.end:h.end + 10]
        kw = TICKET_WORDS.search(before)
        if kw or TICKET_AFTER.match(after):
            if out.ticket_sale_at is None or h.value < out.ticket_sale_at:
                out.ticket_sale_at = h.value
                out.ticket_sale_label = (kw.group(1) if kw else "発売")
            continue
        show_days.append(h.value.date())
        # 「1月10日〜2月3日」の範囲。終わりの年が省略されていれば、始まりの年を基準にする
        if i + 1 < len(hits) and _RANGE_SEP.match(t[h.end:hits[i + 1].start]):
            nxt = hits[i + 1]
            end_d = nxt.value.date()
            if not nxt.explicit_year:
                try:
                    end_d = end_d.replace(year=h.value.year)
                    if end_d < h.value.date():
                        end_d = end_d.replace(year=h.value.year + 1)
                except ValueError:
                    pass
                nxt.value = datetime.combine(end_d, nxt.value.time())
            show_days.append(end_d)
    if show_days:
        out.start_date, out.end_date = min(show_days), max(show_days)
        if out.end_date == out.start_date:
            out.end_date = None
    return out


_QUOTED = re.compile(r"[「『]([^」』]{2,60})[」』]")


def quoted_titles(text: str) -> list[str]:
    return _QUOTED.findall(text or "")


# ---------- タイトルの整形 ----------
_LEAD_NOISE = re.compile(
    r"^(\s|上演中|公演中|開催中|NEW|new|New|PICK ?UP|[0-9０-９]{1,2}\.[0-9]{1,2}\s*\[[A-Z]{3}\]|"
    r"(開催日|公演日|日時|日程)\s*[:：]?|20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|"
    r"(20\d{2}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*日|\d{1,2}/\d{1,2}|"
    r"[(（][^)）]{1,4}[)）]|[~〜～\-–]|℗|・|\||｜|コンサート|演劇|ダンス|主催公演|PARCO PRODUCE( 20\d{2})?)+"
)
_CUT_AT = re.compile(
    r"\s(出演|料金|開場|開演|OPEN|START|入場料|お問い?合わせ|問合せ|会場|チケット|一般発売|発売日|主催|"
    r"終了しました|詳細|※|TEL|全席|S席|A席)[\s:：/]")


_DATE_IN_TITLE = re.compile(r"\s(20\d{2}\s*[年/.\-]\s*)?\d{1,2}\s*[月/.]\s*\d{1,2}\s*日?\s*[(（]")


def clean_title(raw: str) -> str:
    """一覧ブロックの文字列から、公演名らしい部分だけを取り出す。"""
    t = clean_space(raw)
    t = _LEAD_NOISE.sub("", t).strip()
    t = re.sub(r"^((一般|関係者|一般&関係者|音楽・芸能|音楽|芸能|提携公演|主催公演|共催公演|発売中|販売中|受付中|上演中|"
               r"シアタートラム|コンサート|演劇|ダンス|お笑い)\s+)+", "", t)
    t = re.sub(r"(\s*(公演ページ|作品ページ|詳細|詳しくはこちら|more|MORE))+$", "", t)
    # 公演名のあとに続く日付以降(会期・曜日など)は切り落とす
    m = _DATE_IN_TITLE.search(t)
    if m and m.start() >= 4:
        t = t[:m.start()]
    t = re.sub(r"\s*(公演日|日時|日程|会期)\s*[:：]?\s*$", "", t).strip()
    m = _CUT_AT.search(" " + t)
    if m and m.start() > 4:
        t = t[:m.start() - 1]
    return t[:90].strip(" ・-|｜")
