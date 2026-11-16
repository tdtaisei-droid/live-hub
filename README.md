# LIVEHUB — 全国ライブ情報集約サイト

全国の **音楽ライブ・お笑いライブ・ミュージカル・舞台** の新規発表とチケット発売を、
会場・イベンター・劇場・芸能事務所・主催者の **公式情報から横断的に** 集めて一つのサイトにまとめます。
「知らなかった、知ってたら行ったのに」をなくすのが目的です。(映画の舞台挨拶は対象外)

## しくみ

```
config/sources/*.csv(情報源リスト 6種類)
        │  毎日 06:17 に GitHub Actions が実行
        ▼
 取得(robots.txt 遵守・間隔をあける・差分取得)
        ▼
 解析(RSS / Atom / iCal / schema.org構造化データ / スケジュールページ)
        ▼
 判定(ジャンル・新規発表/チケット発売/公演・会場→都道府県→地方・日程と発売日の切り分け)
        ▼
 重複排除(同じ公演を複数の情報源から見つけたら1件にまとめ、出典を全部残す)
        ▼
 data/livehub.sqlite3 に蓄積 → site/ に静的サイトを書き出し → GitHub Pages で公開
```

外部ライブラリは使っていません(Python 3.11以上の標準ライブラリのみ)。

## コマンド

```bash
python -m livehub check      # 情報源リストの不備を表示
python -m livehub discover   # 各サイトのRSS・構造化データ・robots.txtを調査 → data/source_status.json
python -m livehub collect    # 巡回して蓄積
python -m livehub build      # site/ を生成
python -m livehub daily      # collect + build
python -m livehub sample     # 架空データで画面だけ確認
python -m pytest             # テスト
```

## 公開までの手順

1. このフォルダを GitHub リポジトリにする
2. Settings → Pages → Source を「GitHub Actions」にする
3. Settings → Variables に `LIVEHUB_UA`(連絡先URLを含むボット名。ASCIIのみ)を設定
4. `site/about.html` の連絡先(テンプレート `livehub/templates/about.html`)を書き換える
5. Actions → daily →「Run workflow」で初回実行。`source-status` に各サイトの調査結果が出る
6. 調査結果を見て `config/sources/*.csv` の `url` / `schedule_url` / `feed_url` / `method` を埋め、`verified=1` にしていく

## 画面

- **新着**: 前回の訪問以降に見つかった情報(端末ごとに記録)
- **まもなく発売**: 14日以内に始まるチケット発売(今日 / 明日 / 今週 / 来週以降)
- **開催予定**: 月ごと
- **★ フォロー中**: 登録したアーティスト・会場・地域だけ
- ジャンル・地方・都道府県・キーワードで絞り込み

## 守っていること

- 保存・表示するのは事実情報(公演名・日時・会場・発売日)と出典リンクだけ。本文・画像は転載しない
- robots.txt を確認できないサイトは巡回しない。同一サイトへは3秒以上あける
- チケット販売サイト(ぴあ・イープラス・ローチケ等)はクローリングしない
- 掲載停止の連絡先を about ページに明記する
