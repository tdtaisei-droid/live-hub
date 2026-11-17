"""巡回の本体: 情報源ごとに取得 → 解析 → 判定 → 重複排除 → 保存。"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from .classify import is_schedule_item, ENDED, STORE_LIVE, is_news_post, is_special, classify_genre_with_default, classify_kind, is_venue_target, is_broadcast_target, is_free, is_hall_target, is_relevant
from .collectors import RawItem
from .collectors.html import page_identity, find_feeds, find_schedule_pages, page_text, parse_jsonld_events, parse_schedule_blocks
from .collectors.ics import parse_ics
from .collectors.rss import parse_feed
from .fetch import BlockedByRobots, Fetcher
from .geo import detect_location, region_of
from .models import Event
from .sources import URL_CHECK_PATH, Source, load_candidates, load_sources
from .store import Store
from .textutil import strip_ticket_words, clean_title, extract_performers, extract_schedule, find_dates, normalize, quoted_titles

log = logging.getLogger("livehub")
DATA = Path(__file__).resolve().parent.parent / "data"


# 公演スケジュール専用の情報源(ページ上の日付つき項目は基本的に公演)
SCHEDULE_CATEGORIES = {"venues_music", "theaters", "promoters", "stage_producers"}

def month_start(d: date) -> date:
    """その月の1日。これより前に始まる公演は掲載しない(運営方針)。"""
    return d.replace(day=1)


# 月送りのリンク(「2026年 11月」「10月 11月 12月」)など、年月だけの見出し
NAV_ONLY = re.compile(r"[\d\s年月/.\-〜~・|｜<>＜＞«»前次へ]+")
# 公演名ではない一語だけの見出し・販売所名
GENERIC_TITLE = re.compile(
    r"(EVENING|NIGHT|DAY|AFTERNOON|CLUB|LIVE|OPEN|START|SCHEDULE|カレンダー形式|リスト形式|一覧|窓口.*|"
    r"【?公演詳細】?|【?公演情報】?|【?イベント情報】?|イープラス|eplus|ローソンチケット|セブンチケット|チケットぴあ|ぴあ|楽天チケット|LINEチケット|PassMarket)", re.I)

# ---------- RawItem → Event ----------
# 取れなかった理由の記録(巡回ログに要約を残し、読み取りの改善に使う。ページ本文は保存しない)
TRACE: list[str] = []
DROPS: Counter = Counter()


class HttpRefused(Exception):
    def __init__(self, status: int):
        super().__init__(status)
        self.status = status


def _drop(reason: str):
    DROPS[reason] += 1
    return None


def _trace(msg: str) -> None:
    if len(TRACE) < 12:
        TRACE.append(msg)

def to_event(item: RawItem, src: Source, ref: date, now: datetime) -> Optional[Event]:
    text = f"{item.title} {item.summary}"
    if ENDED.search(text):
        return _drop("終了")  # 終了済みの公演
    if not item.structured and is_news_post(item.title):
        return _drop("お知らせ記事")  # 写真公開・コメント到着などのお知らせ記事
    schedule_src = src.category in SCHEDULE_CATEGORIES and not item.site_feed
    if not item.structured and schedule_src:
        if not is_schedule_item(text):
            return _drop("公演以外")
    elif not item.structured and not is_relevant(text):
        return _drop("対象外")
    if item.structured and not is_relevant(text + " 公演"):
        return _drop("構造化対象外")  # 映画イベント等の除外は構造化データにも適用
    if src.category == "halls" and not is_hall_target(text):
        return _drop("会館の対象外")  # 公立ホールは発表会・式典などが多いので、対象ジャンルの公演だけ残す
    if src.category in ("venues_music", "theaters") and not item.structured and not schedule_src \
            and not is_venue_target(text):
        return _drop("会場の対象外")  # 会場の一覧に混ざる展示会・スポーツ・フリマ等を除く
    if src.category == "broadcasters" and not is_broadcast_target(text):
        return _drop("放送局の対象外")  # 放送局は物産展・スポーツ等が多いので、ライブ・公演・公開収録だけ残す
    sched = extract_schedule(text, ref)
    start = item.start_date or sched.start_date
    end = item.end_date or sched.end_date
    sale = item.ticket_sale_at or sched.ticket_sale_at
    if src.category == "stores":
        # 店舗イベントはライブを含むものだけ。CDの発売日をチケット発売と誤認しないよう発売日は扱わない
        if not STORE_LIVE.search(text):
            return _drop("店舗ライブ以外")
        start, end, sale = _store_date(text, ref) or start, None, None

    # 場所: 会場・劇場の情報源ならその所在地が最優先
    venue, pref = item.venue, item.prefecture
    if src.is_location and not item.site_feed:
        venue, pref = venue or src.name, src.prefecture
    if src.category == "stores" and not venue:
        m = re.search(r"場所\s*([^\s]{1,20}店)", text)
        if m:
            venue = f"{src.operator or ''}{m.group(1)}".strip()
    if not pref:
        v2, p2, _ = detect_location(f"{venue or ''} {text}")
        venue, pref = venue or v2, p2
    region = region_of(pref) if pref else detect_location(text)[2]

    title = item.title if item.structured else clean_title(item.title)
    # 「会員先行 抽選 〜」のように、公演名が無く販売期間だけのブロックは公演として扱わない
    if len(normalize(title)) < 4 or re.match(r"^(\S*先行|一般発売|一般販売|抽選|受付|料金|チケット)", title) \
            or re.fullmatch(r"[【\[]?(公演詳細|公演情報|イベント情報|詳細|お知らせ|NEWS|INFORMATION)[】\]]?", normalize(title), re.I):
        return _drop("題名なし")
    if start and start < month_start(ref):
        return _drop("先月以前")  # 今月より前に始まった公演は載せない
    if len(title) < 2 or any(normalize(title) in normalize(n) for n in (src.name, item.venue or "") if n):
        return _drop("題名なし")  # チケット文言などを外したら会場名しか残らない
    if NAV_ONLY.fullmatch(normalize(title)) or GENERIC_TITLE.fullmatch(title.strip()):
        return _drop("見出しだけ")  # 「2026年 11月」などの月送りや、「CLUB」「イープラス」などの見出し・販売所名
    ev = Event(
        title=title,
        url=item.url,
        source_id=src.id,
        genre=classify_genre_with_default(text, src.genre_default),
        kind=classify_kind(text, sale is not None, item.structured),
        performers=[p for p in (strip_ticket_words(x) for x in (item.performers or extract_performers(text))) if len(p) >= 2],
        venue=venue,
        prefecture=pref,
        region=region,
        start_date=start,
        end_date=end,
        ticket_sale_at=sale,
        ticket_sale_label=(sched.ticket_sale_label if not item.ticket_sale_at else "発売") if sale else None,
        free=is_free(text),
        house=src.house,
        special=bool(src.house) and is_special(text),
        published_at=item.published_at,
        fetched_at=now,
    )
    ev.fingerprint = fingerprint(ev)
    return ev


def _store_date(text: str, ref: date) -> Optional[date]:
    """「開催日時」欄の直後の日付を開催日とする。"""
    i = text.find("開催日")
    if i < 0:
        return None
    hits = find_dates(text[i:i + 60], ref)
    return hits[0].value.date() if hits else None


def fingerprint(ev: Event) -> str:
    """同一公演の判定キー。『』「」内の作品名があればそれを、無ければタイトル全体を使う。"""
    core = (quoted_titles(ev.title) or [ev.title])[0]
    key = normalize(core)[:40]
    day = ev.start_date.isoformat() if ev.start_date else ""
    place = ev.prefecture or ""
    return f"{key}|{day}|{place}" if day else f"{key}|news"


# ---------- 取得 ----------
MAX_DETAILS_PER_RUN = 60


def _with_details(items: list[RawItem], fetcher: Fetcher, seen_urls: set[str]) -> list[RawItem]:
    """一覧で見つけた各イベントの詳細ページを読む。読んだことのあるページは読まない(負担を減らす)。"""
    out = []
    for it in items:
        if it.url in seen_urls or len(out) >= MAX_DETAILS_PER_RUN:
            continue
        try:
            r = fetcher.get(it.url)
        except BlockedByRobots:
            continue
        if r.status < 400 and r.text:
            it.summary = page_text(r.text)
        out.append(it)
    return out


def _items_for(src: Source, fetcher: Fetcher, ref: date, seen_urls: Optional[set[str]] = None) -> list[RawItem]:
    if src.feed_url:
        r = fetcher.get(src.feed_url)
        _trace(f"feed:{r.status}{'(304)' if r.not_modified else ''}")
        if r.not_modified or r.status >= 400:
            return []
        if src.method == "ics" or src.feed_url.endswith(".ics") or "calendar" in r.content_type:
            return parse_ics(r.text, src.feed_url)
        return parse_feed(r.text, src.feed_url)

    if src.schedule_url:
        return _read_schedule_page(src, src.schedule_url, fetcher, ref, seen_urls)

    # スケジュールページ未登録: トップページから 構造化データ / メニューの「スケジュール」等 / RSS を自動で探す
    r = fetcher.get(src.url, use_cache=False)
    _trace(f"top:{r.status}")
    if r.status >= 400:
        raise HttpRefused(r.status)
    if not r.text:
        return []
    items = parse_jsonld_events(r.text, r.url)
    if items:
        _trace(f"top-jsonld:{len(items)}")
        return items
    pages = find_schedule_pages(r.text, r.url)
    _trace(f"pages:{len(pages)}")
    # 会場はスケジュールページを優先(サイト全体のRSSは他会場のニュースも混ざるため)
    for page in pages:
        found = _read_schedule_page(src, page, fetcher, ref, seen_urls)
        if found:
            return found
    for ftype, furl in find_feeds(r.text, r.url):
        fr = fetcher.get(furl)
        if fr.status < 400 and fr.text:
            try:
                got = parse_ics(fr.text, furl) if "calendar" in ftype else parse_feed(fr.text, furl)
            except Exception as exc:  # 壊れたフィードは無視
                log.info("feed parse failed %s: %s", furl, exc)
                continue
            _trace(f"feed:{len(got)}")
            for it in got:
                it.site_feed = True  # 会場の所在地を当てはめない(本文から判定する)
            return got
    return []


def _read_schedule_page(src: Source, page: str, fetcher: Fetcher, ref: date,
                        seen_urls: Optional[set[str]]) -> list[RawItem]:
    r = fetcher.get(page)
    path = urlsplit(page).path[-30:] or "/"
    if r.not_modified or r.status >= 400:
        _trace(f"{path}:{'304' if r.not_modified else r.status}")
        return []
    if src.method == "html_detail":
        return _with_details(parse_schedule_blocks(r.text, page, ref), fetcher, seen_urls or set())
    items = parse_jsonld_events(r.text, page)
    if items or src.method == "jsonld":
        _trace(f"{path}:jsonld{len(items)}")
        return items
    blocks = parse_schedule_blocks(r.text, page, ref)
    _trace(f"{path}:blocks{len(blocks)}/dates{len(find_dates(page_text(r.text), ref))}/len{len(r.text)//1000}k")
    return blocks


def run(sources: Optional[list[Source]] = None, only: Optional[set[str]] = None,
        db_path: Path = DATA / "livehub.sqlite3", now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    ref = now.date()
    sources = sources if sources is not None else load_sources()
    store = Store(db_path)
    fetcher = Fetcher(DATA / "http_cache.json")
    stats = Counter()
    performers = Counter()
    for src in sources:
        if only and src.id not in only:
            continue
        if not src.crawlable:
            stats["skipped"] += 1
            continue
        if not only and not src.due(ref):
            stats["not_today"] += 1  # 週1回巡回の情報源で、今日は担当日でない
            continue
        TRACE.clear(); DROPS.clear()
        try:
            raw = _items_for(src, fetcher, ref, store.urls_for(src.id) if src.method == "html_detail" else None)
            events = [e for e in (to_event(i, src, ref, now) for i in raw) if e]
            new = store.upsert(events, now, baseline=not store.has_crawled(src.id))
            msg = " ".join(TRACE) + (f" raw{len(raw)}" if raw else "") + \
                ("" if not DROPS else " drop:" + ",".join(f"{k}{v}" for k, v in DROPS.most_common()))
            store.log_run(src.id, now, "ok", len(events), msg[:300])
            stats["ok"] += 1
            stats["new"] += new
            for e in events:
                performers.update(e.performers)
        except BlockedByRobots:
            store.log_run(src.id, now, "robots", 0, "robots.txt で拒否")
            stats["robots"] += 1
        except HttpRefused as exc:
            # 403等: こちらからのアクセスを受け付けていない。回避はせず記録だけ残す
            store.log_run(src.id, now, f"http{exc.status}", 0, f"HTTP {exc.status}(アクセス拒否)")
            stats["refused"] += 1
        except Exception as exc:  # 1つの情報源の失敗で全体を止めない
            log.warning("source %s failed: %s", src.id, exc)
            store.log_run(src.id, now, "error", 0, repr(exc))
            stats["error"] += 1
    fetcher.save_cache()
    _suggest_organizers(performers, sources)
    return dict(stats)


def _suggest_organizers(counter: Counter, sources: list[Source]) -> None:
    """よく出てくる出演者のうち、organizers.csv に未登録のものを候補として書き出す。"""
    known = {normalize(n) for s in sources for n in [s.name, *s.aliases]}
    rows = [(n, c) for n, c in counter.most_common(200) if c >= 2 and normalize(n) not in known]
    if rows:
        DATA.mkdir(exist_ok=True)
        path = DATA / "organizer_candidates.csv"
        path.write_text("name,count\n" + "".join(f"{n},{c}\n" for n, c in rows), encoding="utf-8")


# ---------- 情報源の自動調査 ----------
def discover(sources: Optional[list[Source]] = None, out: Path = DATA / "source_status.json") -> dict:
    """各サイトを1回だけ訪問し、robots.txt の可否・フィード・構造化データの有無を記録する。
    結果を見て人が CSV の feed_url / method を埋める。"""
    sources = sources if sources is not None else load_sources()
    fetcher = Fetcher()
    result = {}
    for src in sources:
        target = src.schedule_url or src.url
        if not target or src.method == "manual":
            continue
        info: dict = {"checked": datetime.now().isoformat(timespec="seconds")}
        try:
            if not fetcher.allowed(target):
                info["status"] = "robots_disallow"
            else:
                r = fetcher.get(target)
                info["status"] = r.status
                info["feeds"] = find_feeds(r.text, r.url)
                info["jsonld_events"] = len(parse_jsonld_events(r.text, r.url))
                info["schedule_pages"] = [] if src.schedule_url else find_schedule_pages(r.text, r.url)
                info["suggested_method"] = (
                    "rss" if any("rss" in t or "atom" in t for t, _ in info["feeds"]) else
                    "ics" if info["feeds"] else
                    "jsonld" if info["jsonld_events"] else "html"
                )
        except Exception as exc:
            info["status"] = f"error: {exc!r}"[:200]
        result[src.id] = info
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


# ---------- 候補URLの自動確認 ----------
_GENERIC = re.compile(r"(株式会社|有限会社|\(.*?\)|（.*?）|公式|オフィシャル|サイト|ホームページ|劇場$|ホール$)")


def name_keys(src: Source) -> list[str]:
    keys = []
    for n in [src.name, *src.aliases]:
        for v in (n, _GENERIC.sub("", n)):
            k = normalize(v)
            if (len(k) >= 3 or (len(k) == 2 and not k.isascii())) and k not in keys:
                keys.append(k)
    return keys


def check_candidate(src: Source, url: str, fetcher: Fetcher) -> dict:
    """候補ページを1回だけ開き、題名・サイト名・見出しに名前(別名)が入っていれば合格。"""
    info = {"url": url, "checked": datetime.now().isoformat(timespec="seconds"), "ok": False}
    try:
        if not fetcher.allowed(url):
            from urllib.parse import urlsplit
            state = getattr(fetcher, "robots_state", {}).get("{0.scheme}://{0.netloc}".format(urlsplit(url)), "")
            info["reason"] = "robots.txtを確認できず(安全のため見送り)" if state.startswith("unreachable") else "robots.txtで拒否"
            info["robots"] = state
            return info
        r = fetcher.get(url, use_cache=False)
        if r.status >= 400 or not r.text:
            info["reason"] = f"HTTP {r.status}"
            return info
        ident = page_identity(r.text)
        info["identity"] = ident[:200]
        hit = next((k for k in name_keys(src) if k in normalize(ident)), None)
        info["ok"] = hit is not None
        info["matched"] = hit
        if not hit:
            info["reason"] = "ページの名乗りに名前が見つからない"
        if r.url != url:
            info["final_url"] = r.url
    except Exception as exc:
        info["reason"] = f"error: {exc!r}"[:200]
    return info


def verify_candidates(out: Path = URL_CHECK_PATH) -> dict:
    srcs = {s.id: s for s in load_sources(use_checked_candidates=False)}
    fetcher = Fetcher()
    result = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    for sid, url in load_candidates().items():
        src = srcs.get(sid)
        if not src or src.url:  # 正式に登録済みなら確認不要
            continue
        if result.get(sid, {}).get("url") == url and result[sid].get("ok"):
            continue  # 合格済み
        result[sid] = check_candidate(src, url, fetcher)
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


# ---------- 蓄積データの再整理 ----------
def reclean(db_path: Path = DATA / "livehub.sqlite3", today: Optional[date] = None) -> dict:
    """読み取りルールを改良したときに、蓄積済みの公演にも同じルールを当て直す。
    公演名の整え直し・ノイズ除去・今月より前の公演の削除をし、同一公演は1件にまとめる。"""
    import json
    from .classify import CINEMA_EVENT, NOT_EVENT, SITE_NOISE, VENUE_NOISE
    today = today or date.today()
    SRC_NAMES = {x.id: x.name for x in load_sources()}
    store = Store(db_path)
    db = store.db
    rows = db.execute("SELECT fingerprint, data, sources, first_seen, last_seen, baseline FROM events").fetchall()
    stats = Counter()
    for fp, data, sources, first_seen, last_seen, baseline in rows:
        d = json.loads(data)
        start = date.fromisoformat(d["start_date"][:10]) if d.get("start_date") else None
        title = clean_title(d.get("title") or "")
        names = {normalize(n) for n in (d.get("venue") or "", *(SRC_NAMES.get(x, "") for x in json.loads(sources)))} - {""}
        perf = [strip_ticket_words(p) for p in d.get("performers", [])]
        perf = [p for p in perf if len(p) >= 2]
        if perf != d.get("performers", []):
            d["performers"] = perf
            d["title"] = None  # 下で書き戻させる
        bad = (
            (start and start < month_start(today)) and "先月以前" or
            (len(title) < 2 or any(normalize(title) in n for n in names)) and "題名なし" or
            (NAV_ONLY.fullmatch(normalize(title)) or GENERIC_TITLE.fullmatch(title)) and "見出しだけ" or
            (CINEMA_EVENT.search(title) or NOT_EVENT.search(title) or VENUE_NOISE.search(title)
             or SITE_NOISE.search(title)) and "公演以外" or None
        )
        if bad:
            db.execute("DELETE FROM events WHERE fingerprint=?", (fp,))
            stats[bad] += 1
            continue
        if title == d.get("title"):
            continue
        d["title"] = title
        ev = Event(title=title, url=d.get("url", ""), source_id="", start_date=start, prefecture=d.get("prefecture"))
        new_fp = fingerprint(ev)
        db.execute("DELETE FROM events WHERE fingerprint=?", (fp,))
        row = db.execute("SELECT sources, first_seen FROM events WHERE fingerprint=?", (new_fp,)).fetchone()
        if row:  # 整え直した結果、同じ公演がすでにある → 出典をまとめる
            merged = list(dict.fromkeys(json.loads(row[0]) + json.loads(sources)))
            db.execute("UPDATE events SET sources=?, first_seen=min(first_seen, ?) WHERE fingerprint=?",
                       (json.dumps(merged), first_seen, new_fp))
            stats["統合"] += 1
        else:
            db.execute("INSERT INTO events (fingerprint, data, sources, first_seen, last_seen, baseline) VALUES (?,?,?,?,?,?)",
                       (new_fp, json.dumps(d, ensure_ascii=False), sources, first_seen, last_seen, baseline))
            stats["公演名を整えた"] += 1
    db.commit()
    return dict(stats)
