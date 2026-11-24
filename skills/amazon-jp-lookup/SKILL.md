---
name: amazon-jp-lookup
description: amazon.co.jp の商品を調べる（検索・商品詳細・現在価格）。エージェントであることを名乗ったリクエストでページを読み、取得時刻つきの JSON で返す。「Amazonで〜を調べて」「このASINの価格は」「amazon.co.jp のこの商品の評価は」などで使う。購入・カート・注文履歴は扱わない。
---

# amazon-jp-lookup

amazon.co.jp の商品を **読むだけ** のスキル。同梱の `lookup.py`（Python 3 の標準ライブラリだけで動く）でページを取得し、項目を取り出す。

Amazon.co.jp 利用規約には「エージェント条件」があり、エージェントは自分がエージェントだと名乗り、人間のふりをしないことが求められている。このスキルはそれに合わせて作ってある。

## 守ること（例外なし）

1. **取得は必ず `lookup.py` を通す。** `lookup.py` はすべてのリクエストのユーザーエージェントに `Agent/amazon-jp-lookup` を入れて名乗る。ブラウザ操作（Claude in Chrome など）、`curl`、WebFetch など、名乗らずに Amazon を開く方法は使わない。
2. **人間のふりをしない。** ブラウザのユーザーエージェントを名乗らない、クッキーを使わない、CAPTCHA を解かない、遮断をくぐり抜けない（IP を変える・時間をおいて同じことを繰り返す・別の手段で取りに行く、をしない）。
3. **止められたら止める。** `status: "blocked"` が返ったら、その回はそれ以上 Amazon を取得しない。理由（`notes`）をそのままユーザーに伝える。robots.txt でこのエージェントが止められた場合や、Amazon から利用をやめるよう求められた場合も同じ。
4. **読むだけ。** 購入・カート・注文履歴・アカウント・ログインのページは開かない（`lookup.py` も開かない）。
5. **開くページは頼まれたものだけ。** ユーザーが指定した検索と商品に限る。関連商品をたどって巡回しない。`lookup.py` はリクエストの間を最低3秒あける（Amazon への負荷を抑えるため。人間らしく見せるためではない）。
6. **推測で埋めない。** 読めなかった項目は `null` のまま返し、理由は `notes` にある。
7. **価格・在庫は断定しない。** `fetched_at` を必ず添え、返答の文章でも「◯時◯分時点」と言う。

## 使えない環境

シェルで Python 3 を実行でき、外部のサイトに接続できるエージェント（Claude Code など）で使う。

それができない環境（ブラウザだけを持つエージェントなど）では、このスキルを使わない。ブラウザは人間と同じユーザーエージェントで Amazon を開くため、名乗る条件を満たせない。ユーザーには「この環境では Amazon を調べられません」と伝える。

## 操作

スクリプトの場所は、この SKILL.md と同じディレクトリ。

```sh
python3 lookup.py search "検索語"          # 検索結果の上位10件
python3 lookup.py product <ASIN か商品URL>  # 商品詳細
python3 lookup.py price <ASIN か商品URL>    # 価格と在庫だけ
# --fresh を付けるとキャッシュを使わない
```

- 商品URLは ASIN（`/dp/` の後の10文字）だけを取り出して `https://www.amazon.co.jp/dp/<ASIN>` を開く。アフィリエイトタグや追跡パラメータは捨てる。
- 検索のスポンサー枠は `sponsored: true` を付けて残す。
- `product` の結果は `~/.cache/amazon-jp-lookup/<ASIN>.json` に保存し、30分以内の `product` / `price` はそれを `cached: true` で返す。ユーザーが「最新で」と言ったら `--fresh` を付ける。
- 日本国外から接続すると、Amazon が価格をドルなど円以外で表示することがある。そのときは `price_jpy` を `null` にし、`notes` に理由を出す（換算はしない）。
- 取得はログインしていない状態（クッキーなし）なので、`session` は常に `signed_out`。Prime 価格・お届け先で変わる在庫や配送・一部のクーポンは、ログイン時の表示と違うことがある。

## 出力（JSON）

全操作に共通のフィールド：

```json
{
  "op": "search | get_product | get_price",
  "status": "ok | partial | blocked | not_found | error",
  "fetched_at": "2026-11-24T12:34:56+09:00",
  "source": "http",
  "user_agent": "Agent/amazon-jp-lookup (+https://github.com/mediiiiium/amazon-jp-lookup)",
  "session": "signed_out",
  "cached": false,
  "url": "実際に開いたURL",
  "notes": ["読めなかった項目と理由など"]
}
```

`search` の追加フィールド：`query`、`results`（`asin`、`title`、`price_jpy`、`rating`、`review_count`、`sponsored`、`url`）。

`get_product` の追加フィールド：`asin`、`title`、`price_jpy`、`list_price_jpy`、`availability`、`seller`、`ships_from`、`rating`、`review_count`、`rating_distribution`（星ごとの百分率）、`highlights`、`details`。

`get_price` の追加フィールド：`asin`、`price_jpy`、`availability`。

- 金額は円の整数。読めなければ `null`。
- 項目が一部でも欠けたら `status: "partial"`。

## 返答のしかた

ユーザーへの文章は短く要点だけにする（例：「12:34時点で3,999円・在庫あり、評価4.3（1,950件）。ログアウト状態での取得です」）。比較を頼まれたら表にまとめる。買う操作はしないので、必要なら「購入はご自身でどうぞ」と添える。
