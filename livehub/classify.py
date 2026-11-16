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
    ("comedy", re.compile(r"(お笑い|漫才|コント|落語|独演会|単独ライブ|ルミネtheよしもと|なんばグランド花月|NGK|芸人|M-1|キングオブコント|R-1)", re.I)),
    ("stage", re.compile(r"(舞台|演劇|公演.*(劇場|座)|劇団|朗読劇|ストレートプレイ|歌舞伎|バレエ|オペラ|戯曲|上演)")),
    ("music", re.compile(r"(ライブ|LIVE|ツアー|TOUR|コンサート|フェス|ワンマン|対バン|リサイタル|アリーナ|ドーム公演|ホール公演|歌唱)", re.I)),
]

RELEVANT = re.compile(
    r"(ライブ|LIVE|ツアー|TOUR|公演|コンサート|フェス|ワンマン|開催|上演|独演会|単独|"
    r"チケット|先行|一般発売|追加公演|再演|初演|開幕|千秋楽|全国ツアー|主催)",
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
