"""各形式のパーサ。どれも (本文テキスト, 取得元URL) → list[RawItem] の純粋関数。
ネットワークに触れないのでテストしやすい。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class RawItem:
    title: str
    url: str
    summary: str = ""                 # 判定にだけ使い、保存・表示はしない
    published_at: Optional[datetime] = None
    # 構造化データ(iCal / JSON-LD)から直接取れた場合
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    venue: Optional[str] = None
    prefecture: Optional[str] = None
    performers: list[str] = field(default_factory=list)
    ticket_sale_at: Optional[datetime] = None
    structured: bool = False
    site_feed: bool = False           # サイト全体のRSS(複数会場の情報が混ざる)から来た
