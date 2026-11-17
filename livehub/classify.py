"""ジャンル判定・情報種別判定・関連性フィルタ。

ニュースサイトのフィードには公演以外(CD発売、TV出演など)も混ざるため、
公演に関係する語を含む記事だけを残す。
"""
from __future__ import annotations

import re
from typing import Optional

# 映画の舞台挨拶・上映イベントは対象外(「舞台」の語で演劇と誤判定されるのを防ぐ)
CINEMA_EVENT = re.compile(r"(舞台挨拶|舞台あいさつ|初日挨拶|公開記念|完成披露|ティーチイン|応援上映|上映会|試写会|劇場版|映画[「『])")

GENRE_RULES: list[tuple[str, re.Pattern]] = [
    ("musical", re.compile(r"(ミュージカル|musical|宝塚|劇団四季|2\.5次元|ミューステ)", re.I)),
    ("comedy", re.compile(r"(お笑い|漫才|コント|落語|トークライブ|独演会|単独ライブ|ルミネtheよしもと|なんばグランド花月|NGK|芸人|M-1|キングオブコント|R-1)", re.I)),
    ("stage", re.compile(r"(舞台|演劇|公演.*(劇場|座)|劇団|朗読劇|ストレートプレイ|歌舞伎|バレエ|オペラ|戯曲|上演)")),
    ("music", re.compile(r"(ライブ|LIVE|ツアー|TOUR|コンサート|フェス|ワンマン|対バン|リサイタル|アリーナ|ドーム公演|ホール公演|歌唱)", re.I)),
]

RELEVANT = re.compile(
    r"(ライブ|LIVE|ツアー|TOUR|公演|コンサート|フェス|ワンマン|開催|上演|独演会|単独|"
    r"チケット|先行|一般発売|追加公演|再演|初演|開幕|千秋楽|全国ツアー|主催|公開収録|公開放送)",
    re.I,
)
# 公演情報ではない記事(配信・TV出演・円盤・レポートのみ等)
NOT_EVENT = re.compile(r"(Blu-ray|DVD|配信開始|地上波|TV出演|ラジオ出演|オンエア|レポート|ライブ写真|インタビュー|写真集)", re.I)

ANNOUNCE = re.compile(r"(決定|発表|開催へ|追加公演|新作|再演|上演決定|開催決定|ツアー.*(開催|決定)|始動)")


def classify_genre(text: str, default: Optional[str] = None) -> Optional[str]:
    for genre, pat in GENRE_RULES:
        if pat.search(text):
            return genre
    return default


def is_relevant(text: str) -> bool:
    if CINEMA_EVENT.search(text):
        return False
    if not RELEVANT.search(text):
        return False
    # 「ライブレポート」などで、日程・チケット・決定の語が無いものは除外
    if NOT_EVENT.search(text) and not re.search(r"(決定|開催|チケット|先行|一般発売|公演日)", text):
        return False
    return True


def classify_kind(text: str, has_ticket: bool, structured: bool) -> str:
    if has_ticket and re.search(r"(先行|発売|受付|抽選|チケット)", text):
        return "ticket"
    if ANNOUNCE.search(text):
        return "announce"
    return "event" if structured else "announce"


FREE = re.compile(r"(観覧フリー|観覧無料|観覧自由|入場無料|入場フリー|フリーライブ|フリーライヴ|無料ライブ|参加無料|チャージフリー)")
# 店舗イベントは「ライブ」を含むものだけ(サイン会・握手会のみは対象外)
STORE_LIVE = re.compile(r"(ミニライブ|ミニ・ライブ|ミニライヴ|ミニ・ライヴ|ライブ|ライヴ|LIVE|アコースティック|演奏)", re.I)


def is_free(text: str) -> bool:
    return FREE.search(text) is not None


# 公立ホールで多い、対象外の催し(アマチュアの発表・式典・講演など)
HALL_NOISE = re.compile(
    r"(発表会|発表公演|おさらい会|合唱祭|合唱団|合唱コンクール|吹奏楽|定期演奏会|コンクール|講演会|講演|"
    r"シンポジウム|セミナー|説明会|研修|式典|成人式|入学式|卒業式|表彰式|記念式典|総会|大会|カラオケ|"
    r"民謡|詩吟|日本舞踊|フラダンス|展示|展覧会|作品展|上映会|映画会|健康|"
    r"(市民|区民|町民|村民)(音楽祭|文化祭|芸術祭|まつり|祭|合唱|オーケストラ|劇団|ミュージカル)|"
    r"子ども会|PTA|中学校|高等学校|高校|小学校|保育|幼稚園)"
)
# ホールで残すのは、対象ジャンルの語を含むもの
HALL_TARGET = re.compile(
    r"(お笑い|漫才|コント|落語|爆笑|芸人|ライブ|LIVE|ツアー|TOUR|コンサート|ミュージカル|舞台|演劇|公演|劇団|"
    r"ものまね|リサイタル|新喜劇)", re.I)


def is_hall_target(text: str) -> bool:
    return HALL_TARGET.search(text) is not None and HALL_NOISE.search(text) is None


# 放送局のイベントで多い、対象外のもの
BROADCAST_NOISE = re.compile(
    r"(物産展|展示|展覧会|美術展|博覧会|スポーツ|マラソン|ゴルフ|野球|サッカー|駅伝|プレゼント|キャンペーン|"
    r"採用|求人|番組審議|旅行|\d+日間|授業|個展|JLPGA|PGA|お詫び|訂正|天気|防災|セミナー|講演|フェア|グルメ|マルシェ|花火|映画|試写会|舞台挨拶)"
)
BROADCAST_TARGET = re.compile(r"(公開収録|公開放送|公開生放送)")


def is_broadcast_target(text: str) -> bool:
    if BROADCAST_NOISE.search(text):
        return False
    return HALL_TARGET.search(text) is not None or BROADCAST_TARGET.search(text) is not None


# 会場(アリーナ・ホール)で開かれる、公演ではない催し
VENUE_NOISE = re.compile(
    r"(フリーマーケット|フリマ|見本市|展示会|展覧会|博覧会|スタンプラリー|自由見学|見学会|物産|即売会|"
    r"説明会|就職|入学試験|試験|式典|総会|スポーツ|大会|競技|プロレス|格闘|ボクシング|バスケ|バレーボール|"
    r"フィギュアスケート|ゴルフ|JLPGA|個展|授業|eスポーツ|マルシェ|フェア|グルメ|ビアガーデン|イベントカレンダーは)"
)
ENDED = re.compile(r"(終了しました|公演終了|終演しました|販売終了しました|受付は全て終了)")
# 会場の告知に多い、対象ジャンルの公演であることを示す語
VENUE_TARGET = re.compile(
    r"(コンサート|ライブ|LIVE|ライヴ|ツアー|TOUR|公演|ワンマン|フェス|リサイタル|ミュージカル|舞台|演劇|"
    r"お笑い|漫才|落語|寄席|開演|OPEN|START)", re.I)


# 公演スケジュール専用ページ(会場・主催社・劇場)の項目は、公演名が出演者名だけのことが多い。
# そのため「ライブ」等の語を必須にせず、明らかな公演以外(お知らせ・休館・募集など)だけを除く
SITE_NOISE = re.compile(
    r"(休館|臨時休業|メンテナンス|営業時間|駐車場|アクセス|募集|求人|採用|プレゼント|キャンペーン|"
    r"プライバシー|利用規約|会員登録|ログイン|お問い合わせ|よくある質問|FAQ|定休日|学会|学術|骨董市|農業祭|"
    r"スタンプラリー|見学|出発式|式典|入学式|卒業式|成人式|入社式|SUMMIT|サミット|Conference|カンファレンス|ファンフェスタ|野球)", re.I)


def is_schedule_item(text: str) -> bool:
    return not (CINEMA_EVENT.search(text) or NOT_EVENT.search(text) or VENUE_NOISE.search(text)
                or SITE_NOISE.search(text))


def is_venue_target(text: str) -> bool:
    return VENUE_TARGET.search(text) is not None and VENUE_NOISE.search(text) is None


# 「ライブ」「LIVE」だけで音楽と判定された場合は、情報源の既定ジャンル(お笑い事務所ならお笑い)を優先
_GENERIC_MUSIC = re.compile(r"(ライブ|LIVE)", re.I)
_STRONG_MUSIC = re.compile(r"(ツアー|TOUR|コンサート|フェス|ワンマン|対バン|リサイタル|アリーナ|ドーム公演|ホール公演|歌唱|バンド)", re.I)


# 会場サイトの「ジャンル:音楽」のような表記
_SITE_GENRE = [(re.compile(r"ジャンル\s*[:：]\s*(音楽|コンサート|ライブ|ポップス|ロック|ジャズ)"), "music"),
               (re.compile(r"ジャンル\s*[:：]\s*(お笑い|演芸|寄席|落語)"), "comedy"),
               (re.compile(r"ジャンル\s*[:：]\s*ミュージカル"), "musical"),
               (re.compile(r"ジャンル\s*[:：]\s*(演劇|舞台|ステージ)"), "stage")]


def classify_genre_with_default(text: str, default: Optional[str]) -> Optional[str]:
    g = classify_genre(text, default)
    if g is None:  # 公演名から決まらないときは、会場サイトのジャンル表記を使う
        for pat, gname in _SITE_GENRE:
            if pat.search(text):
                return gname
    if g == "music" and default and default != "music" and not _STRONG_MUSIC.search(text):
        return default
    return g


# 公演そのものではない「お知らせ記事」(写真公開・コメント到着・情報追記など)
NEWS_POST = re.compile(r"(公開しました|公開!|を公開|更新しました|追記|掲載しました|コメント到着|コメントが到着|レポート|インタビュー|舞台写真|ゲネプロ|動画公開|ビジュアル解禁|チラシ更新)")
NEWS_KEEP = re.compile(r"(決定|追加公演|発売|先行|開催|上演)")


def is_news_post(text: str) -> bool:
    return NEWS_POST.search(text) is not None and NEWS_KEEP.search(text) is None


# 専用劇場でも「特別な公演」は通常の一覧にも出す
SPECIAL = re.compile(r"(単独ライブ|単独公演|独演会|特別公演|記念公演|周年|凱旋|追善|襲名|スペシャルライブ|生誕祭|卒業公演|お披露目)")


def is_special(text: str) -> bool:
    return SPECIAL.search(text) is not None
