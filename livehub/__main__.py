"""使い方:
  python -m livehub check       情報源リストの不備を表示
  python -m livehub discover    各サイトのフィード・構造化データ・robots.txt を調査
  python -m livehub verify      候補URL(config/url_candidates.csv)が本物か、ページの名乗りで確認
  python -m livehub collect     巡回して data/livehub.sqlite3 に蓄積
  python -m livehub build       site/ に静的サイトを書き出す
  python -m livehub daily       collect → build(毎日の定期実行用)
  python -m livehub sample      架空データで画面確認用サイトを書き出す
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from . import pipeline, sitebuild
from .sources import load_sources, validate
from .store import Store


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="livehub")
    p.add_argument("command", choices=["check", "discover", "verify", "collect", "build", "daily", "sample"])
    p.add_argument("--only", help="カンマ区切りの情報源IDだけを対象にする")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    only = set(args.only.split(",")) if args.only else None

    if args.command == "check":
        srcs = load_sources()
        probs = validate(srcs)
        by_cat = {}
        for s in srcs:
            by_cat[s.category] = by_cat.get(s.category, 0) + 1
        print(f"情報源 {len(srcs)}件: " + ", ".join(f"{k}={v}" for k, v in by_cat.items()))
        print(f"巡回可能 {sum(s.crawlable for s in srcs)}件 / URL確認済 {sum(s.verified for s in srcs)}件")
        print(f"要対応 {len(probs)}件(先頭20件):")
        for m in probs[:20]:
            print("  -", m)
        return 0
    if args.command == "discover":
        srcs = [s for s in load_sources() if not only or s.id in only]
        res = pipeline.discover(srcs)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if args.command == "verify":
        res = pipeline.verify_candidates()
        ok = [k for k, v in res.items() if v.get("ok")]
        print(f"合格 {len(ok)}件 / 確認 {len(res)}件")
        for k, v in res.items():
            print(("OK " if v.get("ok") else "NG ") + k, v.get("url"), v.get("matched") or v.get("reason", ""))
        return 0
    if args.command in ("collect", "daily"):
        print(pipeline.run(only=only))
    if args.command in ("build", "daily"):
        events = Store(pipeline.DATA / "livehub.sqlite3").export(date.today())
        out = sitebuild.build(events)
        print(f"{len(events)}件を {out} に書き出しました")
    if args.command == "sample":
        from .sample import sample_events
        out = sitebuild.build(sample_events(date.today()), sample=True)
        print(f"サンプルサイトを {out} に書き出しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
