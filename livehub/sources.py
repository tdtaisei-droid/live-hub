"""情報源リスト(config/sources/*.csv)の読み込み。"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
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
}

MIN_MUSIC_CAPACITY = 200


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

    @property
    def is_location(self) -> bool:
        return self.category in ("venues_music", "theaters")

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
                )
                # 音楽会場はキャパ200以上のみ(不明は確認待ちとして残す)
                if cat == "venues_music" and s.capacity is not None and s.capacity < MIN_MUSIC_CAPACITY:
                    continue
                out.append(s)
    return out


def validate(sources: list[Source]) -> list[str]:
    """リストの不備を人間向けメッセージで返す。"""
    problems = []
    for s in sources:
        if s.is_location and not s.prefecture:
            problems.append(f"{s.id}: 都道府県が空です")
        if s.category == "venues_music" and s.capacity is None:
            problems.append(f"{s.id}: キャパが空です(200以上か確認してください)")
        if s.method != "manual" and not (s.url or s.schedule_url or s.feed_url):
            problems.append(f"{s.id}: URLが未登録のため巡回できません")
        if s.genre_default and s.genre_default not in ("music", "comedy", "musical", "stage"):
            problems.append(f"{s.id}: genre_default が不正です ({s.genre_default})")
    return problems
