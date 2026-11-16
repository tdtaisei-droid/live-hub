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
