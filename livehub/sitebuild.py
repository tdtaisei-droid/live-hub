"""静的サイトを書き出す(GitHub Pages / Cloudflare Pages などにそのまま置ける)。"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path

from .models import GENRES, KINDS, REGIONS
from .sources import CATEGORIES, load_houses, load_sources

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = Path(__file__).resolve().parent / "templates"


def build(events: list[dict], out_dir: Path = ROOT / "site", today: date | None = None,
          sample: bool = False) -> Path:
    today = today or date.today()
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = load_sources()
    src_names = {s.id: s.name for s in sources}
    for e in events:
        e["source_names"] = [src_names.get(i, i) for i in e.get("sources", [e.get("source_id")])]
        # チケットの発売・先行情報は公開しない(最速の先行を網羅できず、誤解を招くため)。
        # 日付の読み分けには内部で使うので、データベースには残す。
        e.pop("ticket_sale_at", None)
        e.pop("ticket_sale_label", None)
        if e.get("kind") == "ticket":
            e["kind"] = "announce"
    meta = {
        "generated_at": datetime.now().isoformat(timespec="minutes"),
        "today": today.isoformat(),
        "genres": GENRES,
        "kinds": KINDS,
        "regions": REGIONS,
        "sample": sample,
        "houses": {k: v["name"] for k, v in load_houses().items()},
        "count": len(events),
    }
    (out_dir / "events.json").write_text(
        json.dumps({"meta": meta, "events": events}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    from .japanmap import svg as japan_map
    html = (TEMPLATES / "index.html").read_text(encoding="utf-8").replace("<!--MAP-->", japan_map())
    # 初回表示を速くするため、データをページにも埋め込む(events.json は API 的に別途公開)
    payload = json.dumps({"meta": meta, "events": events}, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("/*__DATA__*/null", payload)
    # オープニング演出(架空の会場500種類)
    from .opening import build_variants, compact
    opening = (TEMPLATES / "opening.html").read_text(encoding="utf-8").replace(
        "/*__OPENING__*/[]", json.dumps(compact(build_variants()), ensure_ascii=False, separators=(",", ":")))
    html = html.replace("</body>", opening + "\n</body>", 1)
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    rows = "".join(
        f"<tr><td>{CATEGORIES[s.category]}</td><td>{s.name}</td><td>{s.prefecture or ''}</td>"
        f"<td>{s.capacity or ''}</td><td>{'確認済' if s.verified else '未確認'}</td></tr>"
        for s in sources
    )
    about = (TEMPLATES / "about.html").read_text(encoding="utf-8").replace("<!--SOURCES-->", rows)
    about = about.replace("<!--COUNT-->", str(len(sources)))
    (out_dir / "about.html").write_text(about, encoding="utf-8")
    shutil.copy(TEMPLATES / "robots.txt", out_dir / "robots.txt")
    return out_dir
