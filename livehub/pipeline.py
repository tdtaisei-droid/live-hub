"""巡回の本体: 情報源ごとに取得 → 解析 → 判定 → 重複排除 → 保存。"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .classify import ENDED, STORE_LIVE, is_news_post, is_special, classify_genre_with_default, classify_kind, is_venue_target, is_broadcast_target, is_free, is_hall_target, is_relevant
from .collectors import RawItem
from .collectors.html import page_identity, find_feeds, find_schedule_pages, page_text, parse_jsonld_events, parse_schedule_blocks
from .collectors.ics import parse_ics
from .collectors.rss import parse_feed
from .fetch import BlockedByRobots, Fetcher
from .geo import detect_location, region_of
from .models import Event
from .sources import URL_CHECK_PATH, Source, load_candidates, load_sources
from .store import Store
from .textutil import clean_title, extract_performers, extract_schedule, find_dates, normalize, quoted_titles

log = logging.getLogger("livehub")
DATA = Path(__file__).resolve().parent.parent / "data"


# ---------- RawItem → Event ----------
def to_event(item: RawItem, src: Source, ref: date, now: datetime) -> Optional[Event]:
    text = f"{item.title} {item.summary}"
    if ENDED.search(text):
        return None  # 終了済みの公演
    if not item.structured and is_news_post(item.title):
        return None  # 写真公開・コメント到着などのお知らせ記事
    if not item.structured and not is_relevant(text):
        return None
    if item.structured and not is_relevant(text + " 公演"):
        return None  # 映画イベント等の除外は構造化データにも適用
    if src.category == "halls" and not is_hall_target(text):
        return None  # 公立ホールは発表会・式典などが多いので、対象ジャンルの公演だけ残す
    if src.category in ("venues_music", "theaters") and not item.structured and not is_venue_target(text):
        return None  # 会場の一覧に混ざる展示会・スポーツ・フリマ等を除く
    if src.category == "broadcasters" and not is_broadcast_target(text):
        return None  # 放送局は物産展・スポーツ等が多いので、ライブ・公演・公開収録だけ残す
    sched = extract_schedule(text, ref)
    start = item.start_date or sched.start_date
    end = item.end_date or sched.end_date
    sale = item.ticket_sale_at or sched.ticket_sale_at
    if src.category == "stores":
        # 店舗イベントはライブを含むものだけ。CDの発売日をチケット発売と誤認しないよう発売日は扱わない
        if not STORE_LIVE.search(text):
            return None
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
        return None
    ev = Event(
        title=title,
        url=item.url,
        source_id=src.id,
        genre=classify_genre_with_default(text, src.genre_default),
        kind=classify_kind(text, sale is not None, item.structured),
        performers=item.performers or extract_performers(text),
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
        if r.not_modified or r.status >= 400:
            return []
        if src.method == "ics" or src.feed_url.endswith(".ics") or "calendar" in r.content_type:
            return parse_ics(r.text, src.feed_url)
        return parse_feed(r.text, src.feed_url)

    if src.schedule_url:
        return _read_schedule_page(src, src.schedule_url, fetcher, ref, seen_urls)

    # スケジュールページ未登録: トップページから 構造化データ / メニューの「スケジュール」等 / RSS を自動で探す
    r = fetcher.get(src.url, use_cache=False)
    if r.status >= 400 or not r.text:
        return []
    items = parse_jsonld_events(r.text, r.url)
    if items:
        return items
    # 会場はスケジュールページを優先(サイト全体のRSSは他会場のニュースも混ざるため)
    for page in find_schedule_pages(r.text, r.url):
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
            for it in got:
                it.site_feed = True  # 会場の所在地を当てはめない(本文から判定する)
            return got
    return []


def _read_schedule_page(src: Source, page: str, fetcher: Fetcher, ref: date,
                        seen_urls: Optional[set[str]]) -> list[RawItem]:
    r = fetcher.get(page)
    if r.not_modified or r.status >= 400:
        return []
    if src.method == "html_detail":
        return _with_details(parse_schedule_blocks(r.text, page, ref), fetcher, seen_urls or set())
    items = parse_jsonld_events(r.text, page)
    if items or src.method == "jsonld":
        return items
    return parse_schedule_blocks(r.text, page, ref)


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
        try:
            raw = _items_for(src, fetcher, ref, store.urls_for(src.id) if src.method == "html_detail" else None)
            events = [e for e in (to_event(i, src, ref, now) for i in raw) if e]
            new = store.upsert(events, now, baseline=not store.has_crawled(src.id))
            store.log_run(src.id, now, "ok", len(events))
            stats["ok"] += 1
            stats["new"] += new
            for e in events:
                performers.update(e.performers)
        except BlockedByRobots:
            store.log_run(src.id, now, "robots", 0, "robots.txt で拒否")
            stats["robots"] += 1
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
