"""サイトを開いたときの「幕が開く」演出の、架空の会場500種類を作る。

会場の種類 × 幕の開き方 × 幕の色 × 照明 の組み合わせから、見栄えの合うものを選んで500通りにする。
会場名はすべて架空。登録している実在の会場・劇場・事務所などの名前とかぶるものは除く。
"""
from __future__ import annotations

import random

from .sources import load_sources
from .textutil import normalize

TYPES = ["theater", "livehouse", "arena", "yose", "outdoor", "jazz", "blackbox", "dome", "rooftop", "manzai"]
CURTAINS = ["split", "rise", "swag", "pull", "fold", "drop"]
PALETTES = ["crimson", "navy", "emerald", "violet", "black", "gold", "joshiki", "ivory"]
LIGHTS = ["spot", "cross", "wash", "sparkle", "foot"]  # 点滅(ストロボ)は光過敏性発作の恐れがあるので使わない

# 会場の種類に合う幕・色(合わない組み合わせは使わない)
FIT = {
    "theater":   {"curtains": ["split", "rise", "swag", "fold"], "palettes": ["crimson", "navy", "emerald", "violet", "gold", "ivory"]},
    "livehouse": {"curtains": ["drop", "split", "rise"],          "palettes": ["black", "violet", "navy", "crimson"]},
    "arena":     {"curtains": ["drop", "rise", "split"],          "palettes": ["black", "navy", "violet", "gold"]},
    "yose":      {"curtains": ["pull", "split", "rise"],          "palettes": ["joshiki", "crimson", "gold", "ivory"]},
    "outdoor":   {"curtains": ["drop", "split", "rise"],          "palettes": ["navy", "emerald", "ivory", "gold"]},
    "jazz":      {"curtains": ["swag", "split", "fold"],          "palettes": ["crimson", "navy", "black", "gold"]},
    "blackbox":  {"curtains": ["split", "pull", "drop"],          "palettes": ["black", "ivory", "violet"]},
    "dome":      {"curtains": ["drop", "rise", "split"],          "palettes": ["navy", "black", "gold", "violet"]},
    "rooftop":   {"curtains": ["drop", "split", "swag"],          "palettes": ["navy", "violet", "emerald", "ivory"]},
    "manzai":    {"curtains": ["split", "rise", "pull", "swag"],  "palettes": ["gold", "crimson", "joshiki", "emerald", "navy"]},
}

# 架空の会場名の部品
HEAD = ["星降る", "月見", "港町", "裏通り", "青い", "真夜中の", "朝焼け", "夕凪", "風見", "灯台", "ひだまり", "かもめ",
        "銀河", "雨音", "花びら", "からくり", "まぼろし", "こもれび", "ほたる", "しじま", "あかつき", "すずらん",
        "たそがれ", "ゆめみ", "ぽっかり", "はなび", "わたげ", "こはく", "るり色", "あめ色", "ほうき星", "さざなみ"]
BODY = {
    "theater":   ["座", "劇場", "芸術座", "大劇場", "歌劇場"],
    "livehouse": ["ライブハウス", "クラブ", "倶楽部", "ガレージ", "地下室"],
    "arena":     ["アリーナ", "スタジアム", "大ホール", "ガーデン"],
    "yose":      ["亭", "演芸場", "寄席", "席亭"],
    "outdoor":   ["野外音楽堂", "フェス広場", "丘の上ステージ", "芝生ステージ"],
    "jazz":      ["ジャズ倶楽部", "サロン", "ラウンジ", "酒場"],
    "blackbox":  ["小劇場", "スタジオ", "アトリエ", "ポケット"],
    "dome":      ["ドーム", "円形劇場", "スタジアム"],
    "rooftop":   ["屋上ステージ", "テラス劇場", "空中ステージ"],
    "manzai":    ["漫才劇場", "笑劇場", "演芸ホール", "お笑い横丁"],
}
TAIL = ["", "", "", "・本館", "・別館", "二丁目", "・東館", "・西館"]

COUNT = 500


def _real_names() -> set[str]:
    out = set()
    for s in load_sources():
        for n in [s.name, *s.aliases]:
            k = normalize(n)
            if len(k) >= 2:
                out.add(k)
    return out


def build_variants(count: int = COUNT, seed: int = 20261117) -> list[list]:
    """[[番号, 種類, 幕, 色, 照明, 会場名], ...] を返す。毎回同じ結果になる(番号が固定)。"""
    rng = random.Random(seed)
    combos = []
    for t in TYPES:
        for c in FIT[t]["curtains"]:
            for p in FIT[t]["palettes"]:
                for l in LIGHTS:
                    combos.append((t, c, p, l))
    rng.shuffle(combos)
    real = _real_names()
    used_names: set[str] = set()
    out = []
    for t, c, p, l in combos:
        for _ in range(30):
            name = rng.choice(HEAD) + rng.choice(BODY[t]) + rng.choice(TAIL)
            k = normalize(name)
            # 実在の名前と同じ、または実在の名前を含む/含まれるものは使わない
            if k in used_names or any(k == r or (len(r) >= 4 and (r in k or k in r)) for r in real):
                continue
            used_names.add(k)
            out.append([len(out) + 1, t, c, p, l, name])
            break
        if len(out) >= count:
            break
    return out


def compact(variants: list[list]) -> list[list]:
    """ページに埋め込む形(種類などは番号にして小さくする)。"""
    return [[TYPES.index(t), CURTAINS.index(c), PALETTES.index(p), LIGHTS.index(l), n] for _, t, c, p, l, n in variants]
