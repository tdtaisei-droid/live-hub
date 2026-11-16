"""SQLite に公演情報を蓄積する。同じ公演が複数の情報源から来たら1件にまとめる。"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from .models import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    fingerprint TEXT PRIMARY KEY,
    data        TEXT NOT NULL,      -- Event.to_dict() のJSON
    sources     TEXT NOT NULL,      -- 情報源IDのJSON配列
    first_seen  TEXT NOT NULL,      -- 初めて見つけた日時(「新着」判定に使う)
    last_seen   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_first_seen ON events(first_seen);
CREATE TABLE IF NOT EXISTS runs (
    source_id TEXT, run_at TEXT, status TEXT, items INTEGER, message TEXT
);
"""

_MERGE_FIELDS = ("venue", "prefecture", "start_date", "end_date", "ticket_sale_at", "ticket_sale_label", "genre")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def upsert(self, events: Iterable[Event], now: datetime) -> int:
        """新規件数を返す。"""
        new = 0
        for ev in events:
            row = self.db.execute("SELECT data, sources FROM events WHERE fingerprint=?", (ev.fingerprint,)).fetchone()
            d = ev.to_dict()
            if row is None:
                self.db.execute(
                    "INSERT INTO events VALUES (?,?,?,?,?)",
                    (ev.fingerprint, json.dumps(d, ensure_ascii=False), json.dumps([ev.source_id]), now.isoformat(), now.isoformat()),
                )
                new += 1
                continue
            old, sources = json.loads(row[0]), json.loads(row[1])
            # 既存の空欄だけを新しい情報で埋める(先に来た一次情報を尊重)
            for k in _MERGE_FIELDS:
                if not old.get(k) and d.get(k):
                    old[k] = d[k]
                    if k == "prefecture":
                        old["region"] = d.get("region", old.get("region"))
            old["free"] = old.get("free") or d.get("free", False)
            if old.get("kind") != "ticket" and d.get("kind") == "ticket":
                old["kind"] = "ticket"
            old["performers"] = list(dict.fromkeys(old.get("performers", []) + d.get("performers", [])))[:10]
            if ev.source_id not in sources:
                sources.append(ev.source_id)
            self.db.execute(
                "UPDATE events SET data=?, sources=?, last_seen=? WHERE fingerprint=?",
                (json.dumps(old, ensure_ascii=False), json.dumps(sources), now.isoformat(), ev.fingerprint),
            )
        self.db.commit()
        return new

    def urls_for(self, source_id: str) -> set[str]:
        """その情報源から既に取り込んだ出典URL。"""
        out = set()
        for data, sources in self.db.execute("SELECT data, sources FROM events"):
            if source_id in json.loads(sources):
                out.add(json.loads(data).get("url"))
        return out

    def log_run(self, source_id: str, now: datetime, status: str, items: int, message: str = "") -> None:
        self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?)", (source_id, now.isoformat(), status, items, message[:300]))
        self.db.commit()

    def export(self, today: date, keep_past_days: int = 3) -> list[dict]:
        """サイト用に、終了済み公演を除いた一覧を返す。"""
        out = []
        for data, sources, first_seen in self.db.execute("SELECT data, sources, first_seen FROM events"):
            d = json.loads(data)
            last = d.get("end_date") or d.get("start_date")
            if last and date.fromisoformat(last[:10]) < date.fromordinal(today.toordinal() - keep_past_days):
                continue
            # 開催日の分からないニュースは90日で掲載終了
            if not last and datetime.fromisoformat(first_seen).date().toordinal() < today.toordinal() - 90:
                continue
            d["sources"] = json.loads(sources)
            d["first_seen"] = first_seen
            out.append(d)
        return out
