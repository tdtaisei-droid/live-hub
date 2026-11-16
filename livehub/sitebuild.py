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
    write_split_data(events, meta, out_dir)

    from .japanmap import svg as japan_map
    html = (TEMPLATES / "index.html").read_text(encoding="utf-8").replace("<!--MAP-->", japan_map())
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


# ---------- データを都道府県ごとのファイルに分ける ----------
PREFS = ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県", "埼玉県",
         "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県", "静岡県",
         "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県",
         "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県", "熊本県", "大分県",
         "宮崎県", "鹿児島県", "沖縄県"]
PREF_CODE = {p: f"p{i + 1:02d}" for i, p in enumerate(PREFS)}  # 都道府県コード(JIS)の順


def file_code(e: dict) -> str:
    if e.get("prefecture") in PREF_CODE:
        return PREF_CODE[e["prefecture"]]
    return "online" if e.get("region") == "オンライン" else "other"


def write_split_data(events: list[dict], meta: dict, out_dir: Path) -> None:
    """data/index.json(目次と件数)と、data/p13.json(東京都)のような都道府県ごとのファイルを書く。
    ページは選ばれたエリアのファイルだけを読むので、データが増えても開くのが遅くならない。"""
    data_dir = out_dir / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    groups: dict[str, list[dict]] = {}
    for e in events:
        groups.setdefault(file_code(e), []).append(e)
    files = {}
    for code, evs in groups.items():
        (data_dir / f"{code}.json").write_text(json.dumps(evs, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        files[code] = {"count": len(evs), "pref": next((p for p, c in PREF_CODE.items() if c == code), None)}
    region_counts: dict[str, int] = {}
    pref_counts: dict[str, int] = {}
    for e in events:
        region_counts[e.get("region") or "不明"] = region_counts.get(e.get("region") or "不明", 0) + 1
        if e.get("prefecture"):
            pref_counts[e["prefecture"]] = pref_counts.get(e["prefecture"], 0) + 1
    index = {"meta": meta, "files": files, "prefCode": PREF_CODE, "regionCounts": region_counts, "prefCounts": pref_counts}
    (data_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    old = out_dir / "events.json"
    if old.exists():
        old.unlink()
