"""画面確認用の架空データ。実際の解析処理(to_event)を通して作る。
出演者・公演名はすべて架空。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .collectors import RawItem
from .pipeline import to_event
from .sources import load_sources


def sample_events(today: date) -> list[dict]:
    srcs = {s.id: s for s in load_sources()}
    now = datetime.combine(today, datetime.min.time()) + timedelta(hours=6)

    def md(n: int) -> str:
        d = today + timedelta(days=n)
        return f"{d.month}月{d.day}日"

    specs = [
        # (情報源ID, タイトル, 要約, 構造化日付オフセット, 何日前に発見)
        ("diskgarage", f"架空バンド・ネオンラグーン 全国ツアー「夜光虫」開催決定 {md(70)}Zepp Haneda(TOKYO)公演ほか",
         f"オフィシャル先行 {md(2)} 12:00より受付", None, 0),
        ("kyodo-tokai", f"〈架空〉シロツメクサ楽団 ホールツアー名古屋公演 {md(40)} Niterra日本特殊陶業市民会館 フォレストホール",
         f"一般発売 {md(5)} 10:00", None, 0),
        ("yumebanchi", f"架空SSW・朝霧ルカ ワンマンライブ 大阪 BIGCAT {md(25)}", f"最速先行 {md(1)} 18:00から", None, 1),
        ("kyodo-west", f"架空ロックバンド・ハチドリ号 九州ツアー追加公演決定 DRUM LOGOS {md(55)}",
         f"一般発売 {md(9)} 10:00", None, 0),
        ("wess", f"〈架空〉北の三重奏 札幌公演 {md(33)} Zepp Sapporo", "", None, 2),
        ("lumine-yoshimoto", "架空コンビ「メトロノーム」単独ライブ『反復横跳び』", "", 12, 0),
        ("ngk", "〈架空〉漫才トーナメント特別公演", "", 6, 1),
        ("ohtapro", f"架空トリオ・ささくれ団 単独ライブ全国5都市 福岡公演 {md(48)}",
         f"チケット一般発売 {md(3)} 10:00", None, 0),
        ("toho-stage", f"〈架空〉ミュージカル『灯台守のワルツ』上演決定 {md(120)}〜{md(150)} 日生劇場",
         f"プレイガイド最速先行 {md(13)}", None, 0),
        ("umeda-arts-main", "〈架空〉ミュージカル『星降る駅舎』大阪公演", "", 20, 1),
        ("shinkansen", f"〈架空〉劇団の新作『鉄と牡丹』 {md(90)}〜{md(110)} 博多座公演",
         f"一般発売 {md(16)} 10:00", None, 0),
        ("honda-gekijo", "〈架空〉劇団ひだまり座 第12回公演『午後四時の手紙』", "", 4, 3),
        ("suehirotei", "〈架空〉十二月上席 夜の部", "", 14, 1),
        ("rohm-theatre", "架空ピアニスト・水無月ソラ リサイタル", "", 30, 0),
        ("okinawa-arena", "〈架空〉南風フェス2027 開催決定", "", 150, 0),
        ("tower-records", "〈架空〉ガラスの金魚 新曲発売記念インストアイベント",
         f"開催日時 {(today + timedelta(days=4)).year}年{md(4)} 19:00 場所 渋谷店 5Fイベントスペース ※ミニライブは観覧フリーです。", None, 0),
    ]
    out = []
    for sid, title, summary, offset, ago in specs:
        src = srcs[sid]
        start = today + timedelta(days=offset) if offset is not None else None
        item = RawItem(title=title, url=src.url or "https://example.com/", summary=summary,
                       start_date=start, structured=start is not None)
        ev = to_event(item, src, today, now)
        if not ev:
            continue
        d = ev.to_dict()
        d["sources"] = [sid]
        d["first_seen"] = (now - timedelta(days=ago)).isoformat()
        out.append(d)
    return out
