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
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(events)")}
        if "baseline" not in cols:
            # baseline=1: 情報源を初めて巡回したときに見つけた公演(前から決まっていた可能性があるので「新着」にしない)
            self.db.execute("ALTER TABLE events ADD COLUMN baseline INTEGER NOT NULL DEFAULT 0")
            # この仕組みより前に集めたものは、すべて各情報源の初回巡回で見つけたもの
            self.db.execute("UPDATE events SET baseline=1")
            self.db.commit()

    def has_crawled(self, source_id: str) -> bool:
        """その情報源を、これまでに正常に巡回したことがあるか。"""
        return self.db.execute("SELECT 1 FROM runs WHERE source_id=? AND status='ok' LIMIT 1", (source_id,)).fetchone() is not None

    def upsert(self, events: Iterable[Event], now: datetime, baseline: bool = False) -> int:
        """新規件数を返す。baseline=True は情報源の初回巡回(新着扱いにしない)。"""
        new = 0
        for ev in events:
            row = self.db.execute("SELECT data, sources FROM events WHERE fingerprint=?", (ev.fingerprint,)).fetchone()
            d = ev.to_dict()
            if row is None:
                self.db.execute(
                    "INSERT INTO events (fingerprint, data, sources, first_seen, last_seen, baseline) VALUES (?,?,?,?,?,?)",
                    (ev.fingerprint, json.dumps(d, ensure_ascii=False), json.dumps([ev.source_id]), now.isoformat(), now.isoformat(),
                     1 if baseline else 0),
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
            old["house"] = old.get("house") or d.get("house")
            if old.get("kind") != "ticket" and d.get("kind") == "ticket":
                old["kind"] = "ticket"
            old["performers"] = list(dict.fromkeys(old.get("performers", []) + d.get("performers", [])))[:30]
            old["special"] = old.get("special") or d.get("special", False)
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
        for data, sources, first_seen, baseline in self.db.execute("SELECT data, sources, first_seen, baseline FROM events"):
            d = json.loads(data)
            # 今月より前に始まった公演は載せない(運営方針)
            if d.get("start_date") and date.fromisoformat(d["start_date"][:10]) < today.replace(day=1):
                continue
            last = d.get("end_date") or d.get("start_date")
            if last and date.fromisoformat(last[:10]) < date.fromordinal(today.toordinal() - keep_past_days):
                continue
            # 開催日の分からないニュースは90日で掲載終了
            if not last and datetime.fromisoformat(first_seen).date().toordinal() < today.toordinal() - 90:
                continue
            d["sources"] = json.loads(sources)
            d["first_seen"] = first_seen
            if baseline:
                d["baseline"] = True  # 初回巡回で見つけたもの: 新着には出さない
            out.append(d)
        return out
