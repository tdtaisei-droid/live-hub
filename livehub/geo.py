"""会場名・地名から都道府県と地方を推定する。"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from .textutil import normalize

PREF_REGION = {
    "北海道": "北海道",
    **{p: "東北" for p in ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"]},
    **{p: "関東" for p in ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県"]},
    **{p: "中部" for p in ["新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県", "静岡県", "愛知県"]},
    **{p: "近畿" for p in ["三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"]},
    **{p: "中国" for p in ["鳥取県", "島根県", "岡山県", "広島県", "山口県"]},
    **{p: "四国" for p in ["徳島県", "香川県", "愛媛県", "高知県"]},
    **{p: "九州・沖縄" for p in ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"]},
}

# 都市名・通称 → 都道府県(会場名辞書に無い場合のフォールバック)
CITY_PREF = {
    "札幌": "北海道", "仙台": "宮城県", "新潟": "新潟県", "金沢": "石川県",
    "さいたま": "埼玉県", "横浜": "神奈川県", "川崎": "神奈川県", "幕張": "千葉県",
    "渋谷": "東京都", "新宿": "東京都", "池袋": "東京都", "六本木": "東京都", "日比谷": "東京都",
    "下北沢": "東京都", "有楽町": "東京都", "銀座": "東京都", "お台場": "東京都",
    "名古屋": "愛知県", "静岡": "静岡県", "浜松": "静岡県",
    "梅田": "大阪府", "難波": "大阪府", "なんば": "大阪府", "心斎橋": "大阪府",
    "神戸": "兵庫県", "京都": "京都府", "奈良": "奈良県",
    "岡山": "岡山県", "広島": "広島県", "高松": "香川県", "松山": "愛媛県",
    "福岡": "福岡県", "博多": "福岡県", "天神": "福岡県", "熊本": "熊本県",
    "鹿児島": "鹿児島県", "那覇": "沖縄県",
}

_PREF_SHORT = {p.rstrip("都府県") if p != "北海道" else p: p for p in PREF_REGION}
_ONLINE = re.compile(r"(配信|オンライン|生配信|ライブビューイング)")


@lru_cache(maxsize=1)
def _venues() -> list[tuple[str, str, str]]:
    """(正規化した会場名, 表示名, 都道府県) のリスト。長い名前から照合する。
    音楽会場・劇場の情報源リストをそのまま会場辞書として使う。"""
    from .sources import load_sources
    rows = []
    for s in load_sources():
        if not s.is_location or not s.prefecture:
            continue
        display = s.name.split("(")[0].strip()
        for name in [s.name, display, *s.aliases]:
            key = normalize(name)
            # "WWW" "duo" など短い英字名は誤爆するので除外(「博多座」「南座」等の漢字名は採用)
            if len(key) >= 4 or (len(key) >= 2 and not key.isascii()):
                rows.append((key, display, s.prefecture))
    rows.sort(key=lambda r: -len(r[0]))
    return rows


def detect_location(text: str) -> tuple[Optional[str], Optional[str], str]:
    """テキストから (会場名, 都道府県, 地方) を推定する。"""
    n = normalize(text)
    for key, display, pref in _venues():
        if key and key in n:
            return display, pref, PREF_REGION.get(pref, "不明")
    # 地名は本文中で最初に出てくるものを採用(ツアーなら初日の地域になりやすい)
    hits: list[tuple[int, str]] = []
    for pref in PREF_REGION:
        i = text.find(pref)
        if i >= 0:
            hits.append((i, pref))
    # 「山口」「福岡」「千葉」などは人名にも多いので、地名らしい文脈のときだけ採用する
    for name, pref in [*((s, p) for s, p in _PREF_SHORT.items() if s not in CITY_PREF), *CITY_PREF.items()]:
        m = re.search(rf"{re.escape(name)}{_PLACE_AFTER}", text)
        if m:
            hits.append((m.start(), pref))
    if hits:
        pref = min(hits)[1]
        return None, pref, PREF_REGION[pref]
    if _ONLINE.search(text):
        return None, None, "オンライン"
    return None, None, "不明"


_PLACE_AFTER = r"(?:公演|会場|店|市|区|・|、|,|/|／|\)|）|\]|】|$|\s|で|にて|より|から|追加|編|ほか|他|ライブ|LIVE|初日|千秋楽|凱旋)"


def _place_like(name: str, text: str) -> bool:
    # 人名は直後に名(漢字)が続くことが多いので、直後が区切りや「公演」等のときだけ地名とみなす
    return re.search(rf"{re.escape(name)}{_PLACE_AFTER}", text) is not None


def region_of(prefecture: Optional[str]) -> str:
    return PREF_REGION.get(prefecture or "", "不明")
