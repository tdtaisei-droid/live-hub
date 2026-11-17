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
    r"(TOUR|EVENT|LIVE|INFO|NEWS)\s+(SUN|MON|TUE|WED|THU|FRI|SAT)\b|\d{1,2}\s*[:：]\s*\d{2}\s*[~〜～\-–]\s*(\d{1,2}\s*[:：]\s*\d{2})?|\d{1,2}\s*[:：]\s*\d{2}(\s*(開演|開場|開始|START|OPEN))?|"
    r"[(（][^)）]*(終演|臨時バス|開場|開演|夜公演|昼公演)[^)）]*[)）]|(ジャンル|カテゴリー?)\s*[:：]\s*\S+|(会期中|初日|最終日)\s*[:：]?|\d{1,2}\s*[:：]\s*\d{2}\s*(開演|開場|開始|START|OPEN)|\d部|開場\s*\d{1,2}\s*[:：]\s*\d{2}|開演\s*\d{1,2}\s*[:：]\s*\d{2}|"
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
    t = strip_dates(t)
    return t[:90].strip(" ・-|｜")


# 公演名の中に残った日付・曜日・日付の見出し(「公演日:」など)
_DATE_TOKEN = re.compile(
    r"((開催日|公演日|日時|日程|会期|期間)\s*[:：]\s*)?"
    r"((20\d{2}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*日|20\d{2}\s*[/.\-]\s*\d{1,2}\s*[/.\-]\s*\d{1,2}|"
    r"(?<![\d/])\d{1,2}/\d{1,2}(?=\s*[(（]))"
    r"(\s*[(（][^)）]{1,5}[)）])?"
    r"(\s*[~〜～\-–]\s*(?=[\d(（]))?")
# 一覧で同じ日の別公演が続くときの区切り(東京国際フォーラムなどの「一般」「一般&関係者」)
_NEXT_ITEM = re.compile(r"\s(一般&関係者|一般|関係者)\s")
_TAIL_NOISE = re.compile(r"(\s*(好評発売中|発売中|完売御礼|SOLD ?OUT|詳細はこちら|開催|前売券販売中|\[開催日\]|#ステージ)!*)+$", re.I)


# チケット・先行の文言(このサイトは販売情報を載せないため、公演名からも外す)
TICKET_WORDS = re.compile(
    r"((最速|オフィシャル|会員|ネット会員|ファンクラブ|FC|プレイガイド)?\s*先行(予約|抽選|販売|受付)?|"
    r"インターネット予約|ネット予約|一般発売|一般販売|チケット発売|チケット(情報|購入|販売)|"
    r"(半館|全館)貸切|好評発売中|発売中|予約受付中)", re.I)


def strip_ticket_words(t: str) -> str:
    t = t.strip()
    for _ in range(5):
        m = TICKET_WORDS.search(t)
        if not m:
            break
        # 先頭にあれば文言だけ外し、途中にあればそこから後ろ(販売の案内)を切る
        t = t[m.end():].strip(" /／・、,|｜:：") if m.start() < 2 else t[:m.start()]
    return t.strip(" /／・、,|｜")


def strip_dates(t: str) -> str:
    """公演名から日付とチケット文言を取り除く(日付は別の欄で表示し、販売情報は載せないため)。"""
    t = strip_ticket_words(t)
    t = _DATE_TOKEN.sub(" ", t)
    # 公演名のあとに続く時刻・終演案内・ジャンル表記も外す
    t = re.sub(r"[(（][^)）]*(終演|臨時バス|開場|開演|夜公演|昼公演)[^)）]*[)）]?", " ", t)
    t = re.sub(r"(ジャンル|カテゴリー?)\s*[:：]\s*\S+", " ", t)
    t = re.sub(r"\s\d{1,2}\s*[:：]\s*\d{2}(\s*[~〜～\-–]\s*(\d{1,2}\s*[:：]\s*\d{2})?)?", " ", t)
    m = _NEXT_ITEM.search(" " + t)
    if m and m.start() > 4:
        t = t[:m.start() - 1]
    t = re.sub(r"\s{2,}", " ", t).strip(" ~〜～-–・、,")
    t = _TAIL_NOISE.sub("", t).strip()
    return t


# ---------- 出演者 ----------
_PERFORMERS = re.compile(
    r"(?:出演者?|出\s*演|キャスト|CAST|ゲスト|MC)\s*[:：／/]?\s*(.+?)"
    r"(?=\s*(?:料金|入場料|開場|開演|OPEN|START|チケット|前売|当日|※|お問|問合|会場|日時|公演日|主催|詳細|$))",
    re.I)
_SPLIT = re.compile(r"\s*[、,，/／\n]\s*|\s{2,}|　")
_NOT_NAME = re.compile(r"(ほか|他|など|予定|未定|順不同|五十音順|変更|日替わり|調整中|後日|発表|^\d|^[(（])")


def extract_performers(text: str, limit: int = 30) -> list[str]:
    """「出演: A、B、C ほか」から名前を取り出す。名前の中の「・」は区切りにしない(コンビ名に使われるため)。"""
    out: list[str] = []
    for m in _PERFORMERS.finditer(clean_space(text)):
        for raw in _SPLIT.split(m.group(1)):
            name = re.sub(r"[(（][^)）]*[)）]", "", raw).strip(" ・:：「」『』")
            name = re.sub(r"\s*(ほか|他|など)$", "", name).strip()
            if 2 <= len(name) <= 24 and not _NOT_NAME.search(name) and name not in out:
                out.append(name)
            if len(out) >= limit:
                return out
    return out
