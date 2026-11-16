"""公演情報のデータモデル。

方針: 保持するのは「事実情報」(公演名・日時・会場・出演者・発売日・出典URL)だけ。
記事本文や画像は保存・転載しない(著作権・利用規約への配慮)。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Optional

GENRES = {
    "music": "音楽ライブ",
    "comedy": "お笑いライブ",
    "musical": "ミュージカル",
    "stage": "舞台・演劇",
}

# 情報の種類
KINDS = {
    "announce": "新規発表",     # 公演・ツアー・追加公演の発表
    "ticket": "チケット発売",   # 先行・一般発売の開始
    "event": "公演情報",       # 開催日が分かっている公演そのもの
}

REGIONS = ["北海道", "東北", "関東", "中部", "近畿", "中国", "四国", "九州・沖縄", "オンライン", "不明"]


@dataclass
class Event:
    title: str
    url: str                       # 出典(一次情報)URL。必ず出典に誘導する
    source_id: str                 # sources.json の id
    genre: Optional[str] = None    # GENRES のキー
    kind: str = "announce"         # KINDS のキー
    performers: list[str] = field(default_factory=list)
    venue: Optional[str] = None
    prefecture: Optional[str] = None
    region: str = "不明"
    start_date: Optional[date] = None      # 公演開始日
    end_date: Optional[date] = None        # 公演最終日(ツアー・ロングラン)
    ticket_sale_at: Optional[datetime] = None  # 発売開始(先行・一般)
    ticket_sale_label: Optional[str] = None    # "最速先行" "一般発売" など
    free: bool = False                     # 観覧無料(フリーライブ)
    published_at: Optional[datetime] = None    # 情報源での公開日時
    fetched_at: Optional[datetime] = None
    fingerprint: str = ""                  # 重複判定キー(pipeline で付与)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, (date, datetime)):
                d[k] = v.isoformat()
        return d
