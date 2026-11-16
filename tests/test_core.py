from datetime import date, datetime
from pathlib import Path

from livehub.classify import classify_genre, is_relevant
from livehub.collectors import RawItem
from livehub.collectors.html import find_feeds, parse_jsonld_events, parse_schedule_blocks
from livehub.collectors.ics import parse_ics
from livehub.collectors.rss import parse_feed
from livehub.geo import detect_location
from livehub.pipeline import to_event
from livehub.sources import Source, load_sources, validate
from livehub.store import Store
from livehub.textutil import extract_schedule

REF = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 6)
FX = Path(__file__).parent / "fixtures"


def src(**kw):
    base = dict(id="t", category="promoters", name="テスト", url="https://example.com/")
    base.update(kw)
    return Source(**base)


# ---- 日付・発売日 ----
def test_ticket_vs_show_dates():
    x = extract_schedule("全国ツアー開催決定 2月14日(土)Zepp Nagoya 一般発売 12月6日(土)10:00", REF)
    assert x.start_date == date(2027, 2, 14)
    assert x.ticket_sale_at == datetime(2026, 12, 6, 10, 0)
    assert x.ticket_sale_label == "一般発売"


def test_range_and_year_rollover():
    x = extract_schedule("1月10日〜2月3日 上演", REF)
    assert (x.start_date, x.end_date) == (date(2027, 1, 10), date(2027, 2, 3))


def test_sale_marked_by_suffix():
    x = extract_schedule("11月20日 18:00より発売 公演は12月24日", REF)
    assert x.ticket_sale_at == datetime(2026, 11, 20, 18, 0)
    assert x.start_date == date(2026, 12, 24)


def test_slash_date_requires_context():
    assert extract_schedule("ver 1/2 リリース", REF).start_date is None
    assert extract_schedule("2027/1/2 公演", REF).start_date == date(2027, 1, 2)


# ---- 地域 ----
def test_location_from_venue_list():
    v, p, r = detect_location("ワンマン at 梅田CLUB QUATTRO")
    assert (v, p, r) == ("梅田CLUB QUATTRO", "大阪府", "近畿")


def test_surname_not_treated_as_place():
    assert detect_location("山口一郎 ソロ公演")[1] is None
    assert detect_location("ツアー 山口公演 決定")[1] == "山口県"
    assert detect_location("福岡・大阪で開催")[1] == "福岡県"


# ---- ジャンル・関連性 ----
def test_genres():
    assert classify_genre("ミュージカル『X』上演決定") == "musical"
    assert classify_genre("漫才師Yの単独ライブ") == "comedy"
    assert classify_genre("劇団Zの新作舞台") == "stage"
    assert classify_genre("全国ツアー開催") == "music"


def test_cinema_stage_greeting_excluded():
    assert not is_relevant("映画『X』公開記念舞台挨拶 開催決定")
    assert not is_relevant("劇場版アニメ 完成披露上映会")
    assert is_relevant("舞台『X』上演決定 チケット一般発売")


def test_non_event_news_excluded():
    assert not is_relevant("ライブBlu-ray発売、ツアーファイナルを収録")
    assert is_relevant("ライブBlu-ray発売 さらに追加公演が決定")


# ---- パーサ ----
def test_rss_and_atom():
    rss = parse_feed((FX / "feed.rss").read_text(encoding="utf-8"), "https://example.com/")
    assert [i.title for i in rss][0].startswith("架空バンド")
    assert rss[0].url == "https://example.com/news/1"
    atom = parse_feed((FX / "feed.atom").read_text(encoding="utf-8"))
    assert atom[0].url == "https://example.com/a/1" and atom[0].published_at.year == 2026


def test_ics():
    items = parse_ics((FX / "venue.ics").read_text(encoding="utf-8"), "https://example.com/")
    assert len(items) == 2
    assert items[0].start_date == date(2026, 12, 5) and items[0].venue == "Zepp Fukuoka"
    assert items[1].end_date == date(2026, 12, 20)  # 終日予定のDTENDは翌日指定


def test_jsonld_event():
    html = (FX / "event_jsonld.html").read_text(encoding="utf-8")
    ev = parse_jsonld_events(html, "https://example.com/live/")
    assert len(ev) == 1
    e = ev[0]
    assert e.start_date == date(2027, 1, 20) and e.prefecture == "愛知県"
    assert e.performers == ["架空バンドA", "架空バンドB"]
    assert e.ticket_sale_at == datetime(2026, 12, 1, 10, 0)
    assert find_feeds(html, "https://example.com/live/") == [("application/rss+xml", "https://example.com/feed.xml")]


def test_schedule_blocks():
    html = (FX / "schedule.html").read_text(encoding="utf-8")
    items = parse_schedule_blocks(html, "https://example.com/schedule/", REF)
    titles = [i.title for i in items]
    assert len(items) == 2, titles
    assert items[0].url.startswith("https://example.com/schedule/")


# ---- 統合: 変換と重複排除 ----
def test_dedupe_across_sources(tmp_path):
    venue = src(id="zepp-fukuoka", category="venues_music", name="Zepp Fukuoka", prefecture="福岡県", genre_default="music")
    promoter = src(id="kyodo-west", genre_default="music")
    a = to_event(RawItem("架空バンド『夜光虫』ツアー", "https://v/1", start_date=date(2026, 12, 5), structured=True), venue, REF, NOW)
    b = to_event(RawItem("架空バンド ツアー『夜光虫』福岡公演 12月5日 Zepp Fukuoka",
                         "https://p/1", summary="一般発売 11月22日 10:00"), promoter, REF, NOW)
    assert a.fingerprint == b.fingerprint
    st = Store(tmp_path / "t.db")
    assert st.upsert([a], NOW) == 1
    assert st.upsert([b], NOW) == 0
    [row] = st.export(REF)
    assert row["sources"] == ["zepp-fukuoka", "kyodo-west"]
    assert row["ticket_sale_at"].startswith("2026-11-22T10:00")
    assert row["kind"] == "ticket"


def test_past_events_dropped(tmp_path):
    s = src(id="x", category="theaters", name="本多劇場", prefecture="東京都", genre_default="stage")
    old = to_event(RawItem("劇団A 公演", "https://x/1", start_date=date(2026, 10, 1), structured=True), s, REF, NOW)
    st = Store(tmp_path / "t.db")
    st.upsert([old], NOW)
    assert st.export(REF) == []


# ---- 情報源リスト ----
def test_source_registry_is_valid():
    srcs = load_sources()
    assert len(srcs) > 300
    music = [s for s in srcs if s.category == "venues_music"]
    assert all(s.capacity is None or s.capacity >= 200 for s in music)
    assert all(s.prefecture for s in srcs if s.is_location)
    bad = [m for m in validate(srcs) if "genre_default" in m or "都道府県" in m]
    assert bad == []


def test_capacity_rules(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    head = "id,name,aliases,prefecture,city,capacity,url,schedule_url,feed_url,method,genre_default,operator,notes,verified\n"
    (d / "venues_music.csv").write_text(head + "m1,大ハコ,,東京都,,250,,,,auto,music,,,0\nm2,小ハコ,,東京都,,150,,,,auto,music,,,0\n", encoding="utf-8")
    (d / "theaters.csv").write_text(head + "c1,お笑い劇場A,,東京都,,120,,,,auto,comedy,,,0\nc2,お笑い劇場B,,東京都,,80,,,,auto,comedy,,,0\n", encoding="utf-8")
    ids = {s.id for s in load_sources(d)}
    assert ids == {"m1", "c1"}  # 音楽は200以上、お笑いは100以上


# ---- 店舗イベント(タワーレコード) ----
TOWER_DETAIL = """架空バンドX 新曲発売記念インストアイベント 開催日時 2026年12月05日(土) 18:30
場所 渋谷店 5Fイベントスペース 出演 架空バンドX 参加方法 ※ミニライブは観覧フリーです。
対象商品 入荷日:既発(12/2発売) イベント当日までに対象商品をご購入の方に特典券"""


def _tower():
    return src(id="tower-records", category="stores", name="タワーレコード 店舗イベント",
               operator="タワーレコード", genre_default="music", method="html_detail")


def test_store_live_event():
    ev = to_event(RawItem("架空バンドX インストアイベント", "https://t/1", summary=TOWER_DETAIL), _tower(), REF, NOW)
    assert ev.start_date == date(2026, 12, 5)
    assert ev.ticket_sale_at is None          # CD発売日をチケット発売と誤認しない
    assert ev.free is True
    assert ev.venue == "タワーレコード渋谷店" and ev.prefecture == "東京都"
    assert ev.genre == "music"


def test_store_signing_only_is_excluded():
    text = "架空歌手Y サイン会 開催日時 2026年12月06日(日) 14:00 場所 新宿店 参加方法 特典券"
    assert to_event(RawItem("架空歌手Y サイン会", "https://t/2", summary=text), _tower(), REF, NOW) is None


def test_details_fetched_once():
    from livehub.pipeline import _with_details
    from livehub.fetch import Response

    class Stub:
        calls = []
        def get(self, url):
            self.calls.append(url)
            return Response(url, 200, "<html><body><p>" + TOWER_DETAIL + "</p></body></html>", "text/html")

    f = Stub()
    items = [RawItem("a", "https://t/1"), RawItem("b", "https://t/2")]
    out = _with_details(items, f, seen_urls={"https://t/1"})
    assert f.calls == ["https://t/2"] and "観覧フリー" in out[0].summary


# ---- スケジュールページの自動発見 ----
def test_find_schedule_pages():
    from livehub.collectors.html import find_schedule_pages
    html = """<nav><a href="/">HOME</a><a href="/news/">NEWS</a><a href="/live/">LIVE</a>
    <a href="/schedule/">SCHEDULE</a><a href="https://other.example.org/schedule">外部</a>
    <a href="/live/report/">ライブレポート</a><a href="/goods/">GOODS</a></nav>"""
    assert find_schedule_pages(html, "https://www.example.com/") == [
        "https://www.example.com/schedule/", "https://www.example.com/live/"]


def test_collect_via_top_page_menu():
    from livehub.fetch import Response
    from livehub.pipeline import _items_for
    pages = {
        "https://v.example.com/": '<a href="/schedule/">スケジュール</a>',
        "https://v.example.com/schedule/": (FX / "schedule.html").read_text(encoding="utf-8"),
    }

    class Stub:
        def get(self, url, use_cache=True):
            return Response(url, 200, pages.get(url, ""), "text/html")

    s = src(id="v", category="venues_music", name="会場V", prefecture="東京都", url="https://v.example.com/")
    items = _items_for(s, Stub(), REF)
    assert len(items) == 2 and items[0].url.startswith("https://v.example.com/schedule/detail/")


# ---- 公立ホール ----
def test_hall_filter():
    from livehub.classify import is_hall_target
    assert is_hall_target("爆笑!お笑いエンタメライブ in岐阜 岐阜市民会館 大ホール")
    assert is_hall_target("ミュージカル『X』全国ツアー 倉敷市民会館")
    assert not is_hall_target("第30回 ピアノ発表会")
    assert not is_hall_target("市民合唱団 定期演奏会")
    assert not is_hall_target("成人式")
    assert not is_hall_target("市民ミュージカル公演")       # アマチュア公演
    assert not is_hall_target("講演会「まちづくりの未来」")


def test_weekly_rotation():
    s = src(id="hall-x", category="halls", name="会館X", prefecture="岐阜県", interval_days=7)
    days = [date(2026, 11, 16 + i) for i in range(14)]
    assert sum(s.due(d) for d in days) == 2          # 2週間で2回
    assert src(id="daily").due(days[0])              # 既定は毎日


def test_halls_load_weekly():
    halls = [s for s in load_sources() if s.category == "halls"]
    assert len(halls) >= 90
    assert all(s.interval_days == 7 and (s.capacity or 0) >= 1000 for s in halls)


def test_shared_site_feed_does_not_force_venue():
    item = RawItem("Zepp Nagoya 公演追加決定 1月10日", "https://z/1", site_feed=True)
    item2 = RawItem("会場限定ワンマン 1月11日", "https://z/2", site_feed=True)
    s = src(id="zepp-fukuoka", category="venues_music", name="Zepp Fukuoka", prefecture="福岡県", genre_default="music")
    ev = to_event(item, s, REF, NOW)
    assert ev.prefecture == "愛知県"            # 本文の会場名から判定(福岡にしない)
    assert to_event(item2, s, REF, NOW).prefecture is None


def test_crawl_frequency_by_category():
    by = {}
    for s in load_sources():
        by.setdefault(s.category, set()).add(s.interval_days)
    assert by["promoters"] == {1} and by["agencies"] == {1} and by["organizers"] == {1}
    assert by["venues_music"] == {3} and by["theaters"] == {3} and by["halls"] == {7}


def test_broadcaster_filter():
    from livehub.classify import is_broadcast_target
    assert is_broadcast_target("FM802主催 年末ライブイベント 開催決定")
    assert is_broadcast_target("人気番組の公開収録 観覧募集")
    assert not is_broadcast_target("北海道物産展 開催")
    assert not is_broadcast_target("○○マラソン 中継")
    assert not is_broadcast_target("映画『X』試写会プレゼント")
    s = src(id="fm802", category="broadcasters", name="FM802", genre_default="music")
    assert to_event(RawItem("番組の公開収録 12月20日 観覧無料", "https://f/1"), s, REF, NOW).free


def test_calendar_cell_with_multiple_events_is_split():
    html = (FX / "calendar_multi.html").read_text(encoding="utf-8")
    items = parse_schedule_blocks(html, "https://h.example.com/cal/", REF)
    titles = [i.title for i in items]
    assert "架空歌手 CONCERT TOUR 2026" in titles and "架空学会 学術大会" in titles
    s = src(id="hall", category="venues_music", name="会場H", prefecture="東京都", genre_default="music")
    kept = [to_event(i, s, REF, NOW) for i in items]
    names = [e.title for e in kept if e]
    assert "架空歌手 CONCERT TOUR 2026" in names        # 同じ枠の学会と一緒に消えない
    assert all("学会" not in n for n in names)
    assert "架空バンド ワンマンライブ" in names


def test_news_posts_dropped():
    s = src(id="t", category="theaters", name="劇場T", prefecture="東京都", genre_default="stage")
    assert to_event(RawItem("劇団X『Y』開幕コメント、舞台写真を公開しました 12月1日", "https://t/1"), s, REF, NOW) is None
    assert to_event(RawItem("劇団X『Y』追加公演決定 12月20日", "https://t/2"), s, REF, NOW) is not None
