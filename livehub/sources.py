"""情報源リスト(config/sources/*.csv)の読み込み。"""
from __future__ import annotations

import csv
import zlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "config" / "sources"

CATEGORIES = {
    "venues_music": "音楽ライブ会場",
    "promoters": "イベンター",
    "stage_producers": "舞台制作・配給",
    "theaters": "劇場",
    "agencies": "芸能事務所",
    "organizers": "主催者(アーティスト・劇団など)",
    "stores": "店舗イベント(インストアライブ)",  # 店頭が会場のためキャパ条件なし
    "halls": "公立文化ホール(市民会館・文化会館)",
}

# 何日に1回巡回するか(CSVの interval_days 列で個別に上書き可)
DEFAULT_INTERVAL = {"halls": 7}

MIN_MUSIC_CAPACITY = 200   # 音楽ライブ会場
MIN_COMEDY_CAPACITY = 100  # お笑いライブの会場(劇場・寄席など)
MIN_HALL_CAPACITY = 1000   # 公立文化ホール(まず1,000席以上から。段階的に下げる)


def min_capacity(category: str, genre: Optional[str]) -> Optional[int]:
    """キャパの下限。下限未満の会場は登録しても読み込まない。"""
    if category == "venues_music":
        return MIN_MUSIC_CAPACITY
    if category == "theaters" and genre == "comedy":
        return MIN_COMEDY_CAPACITY
    if category == "halls":
        return MIN_HALL_CAPACITY
    return None


@dataclass
class Source:
    id: str
    category: str
    name: str
    aliases: list[str] = field(default_factory=list)
    prefecture: Optional[str] = None
    city: Optional[str] = None
    capacity: Optional[int] = None
    url: Optional[str] = None
    schedule_url: Optional[str] = None
    feed_url: Optional[str] = None
    method: str = "auto"
    genre_default: Optional[str] = None
    operator: Optional[str] = None
    notes: str = ""
    verified: bool = False
    interval_days: int = 1

    @property
    def is_location(self) -> bool:
        return self.category in ("venues_music", "theaters", "halls")

    def due(self, day: "date") -> bool:
        """今日巡回する日か。interval_days=7 なら、IDごとに曜日を散らして週1回。"""
        if self.interval_days <= 1:
            return True
        slot = zlib.crc32(self.id.encode()) % self.interval_days
        return day.toordinal() % self.interval_days == slot

    @property
    def crawlable(self) -> bool:
        return self.method != "manual" and bool(self.feed_url or self.schedule_url or self.url)


def _opt(v: str) -> Optional[str]:
    v = (v or "").strip()
    return v or None


def load_sources(directory: Path = SOURCES_DIR) -> list[Source]:
    out: list[Source] = []
    seen: set[str] = set()
    for cat in CATEGORIES:
        path = directory / f"{cat}.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                sid = (row.get("id") or "").strip()
                if not sid:
                    continue
                if sid in seen:
                    raise ValueError(f"情報源IDが重複しています: {sid} ({path.name})")
                seen.add(sid)
                cap = (row.get("capacity") or "").strip()
                s = Source(
                    id=sid,
                    category=cat,
                    name=row["name"].strip(),
                    aliases=[a.strip() for a in (row.get("aliases") or "").split("|") if a.strip()],
                    prefecture=_opt(row.get("prefecture")),
                    city=_opt(row.get("city")),
                    capacity=int(cap) if cap.isdigit() else None,
                    url=_opt(row.get("url")),
                    schedule_url=_opt(row.get("schedule_url")),
                    feed_url=_opt(row.get("feed_url")),
                    method=(row.get("method") or "auto").strip() or "auto",
                    genre_default=_opt(row.get("genre_default")),
                    operator=_opt(row.get("operator")),
                    notes=(row.get("notes") or "").strip(),
                    verified=(row.get("verified") or "").strip() == "1",
                    interval_days=int(row["interval_days"]) if (row.get("interval_days") or "").strip().isdigit()
                    else DEFAULT_INTERVAL.get(cat, 1),
                )
                # 音楽会場は200以上、お笑いの会場は100以上のみ(不明は確認待ちとして残す)
                low = min_capacity(cat, s.genre_default)
                if low and s.capacity is not None and s.capacity < low:
                    continue
                out.append(s)
    return out


def validate(sources: list[Source]) -> list[str]:
    """リストの不備を人間向けメッセージで返す。"""
    problems = []
    for s in sources:
        if s.is_location and not s.prefecture:
            problems.append(f"{s.id}: 都道府県が空です")
        low = min_capacity(s.category, s.genre_default)
        if low and s.capacity is None:
            problems.append(f"{s.id}: キャパが空です({low}以上か確認してください)")
        if s.method != "manual" and not (s.url or s.schedule_url or s.feed_url):
            problems.append(f"{s.id}: URLが未登録のため巡回できません")
        if s.genre_default and s.genre_default not in ("music", "comedy", "musical", "stage"):
            problems.append(f"{s.id}: genre_default が不正です ({s.genre_default})")
    return problems
