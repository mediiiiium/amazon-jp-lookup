---
name: amazon-lookup
description: amazon.co.jp の商品を調べる（検索・商品詳細・現在価格）。エージェントが持っているブラウザでページを読み、取得時刻つきの JSON で返す。「Amazonで〜を調べて」「このASINの価格は」「amazon.co.jp のこの商品の評価は」などで使う。購入・カート・注文履歴は扱わない。
---

# amazon-lookup

amazon.co.jp の商品を **読むだけ** のスキル。ブラウザはエージェント側のもの（Claude Code なら Claude in Chrome、Muse ならアプリ内ブラウザ）を使う。このスキルが決めるのは「どのURLを開き、何を読み、どの形で返すか」だけ。

## 守ること（例外なし）

1. **URL を直接開いて読むだけ。ページ上のボタンやリンクはクリックしない。** 「カートに入れる」「今すぐ買う」「1-Click」「定期おトク便」「ログイン」には絶対に触れない。フォーム入力もしない（検索も検索URLを直接開く）。
2. **注文履歴・アカウント・住所・支払いのページは開かない。** `/gp/your-account`、`/gp/css/order-history`、`/ap/signin`、`/gp/cart` などに誘導されたら、そこで止めて「読めなかった」と返す。
3. **人間の閲覧ペース。** 連続でページを開くときは数秒あける。1回の依頼で開くページはユーザーが指定した検索・商品に限り、関連商品をたどって巡回しない。
4. **推測で埋めない。** 読めなかった項目は `null` にし、`notes` に理由を書く。CAPTCHA（「文字を入力してください」「ロボットではありません」）が出たら、解こうとせず `status: "blocked"` で返す。
5. **価格・在庫は断定しない。** 必ず `fetched_at` を付け、返答の文章でも「◯時◯分時点」と言う。

## ログイン状態

調べる用途にログインは要らない。ログインしたまま読むと、自動操作が買い物用アカウントに結びつく。

- 判定はスクリプトがする。商品ページに埋め込まれたデータ（`"loggedIn":true/false`）を優先して見る。なければ、ページ上部のアカウント表示が「ログイン」を含むかどうかで判定する。
- **`session: "signed_in"` だった場合**（Claude in Chrome は普段の Chrome のクッキーをそのまま使う）、ログアウトさせたりはしない。そのまま続け、その会話で最初の返答に1行だけ添える：「ログイン中のブラウザで取得しました。閲覧履歴に残るため、Amazon にログインしていないプロファイルでの利用をおすすめします（README 参照）」。同じ会話では繰り返さない。
- `signed_in` の間も「守ること」は変わらない。購入につながる操作がページ上にあっても触れない。
- ログアウト状態では、Prime 価格・お届け先に依存する在庫や配送・一部のクーポンがログイン時と変わる。`session` を必ず出力に残す。

## 読み取りの方法

**ページ上で JavaScript を実行できるブラウザ**（Claude in Chrome の `javascript_tool` など）では、このスキルに同梱したスクリプトを使う。ファイルの中身をそのまま実行すると、JSON 文字列が返る。

- 検索結果ページ → `extract_search.js`
- 商品ページ → `extract_product.js`

スクリプトは商品の項目と `session` だけを返し、氏名・お届け先・郵便番号などアカウント由来の情報は返さない。ページの全文を取得する方法（`get_page_text` など）は使わない。ログイン中のプロファイルでは、ページ上部の氏名と郵便番号まで読み込まれてしまう。全文は量も多い（検索1ページで数万字）。

JavaScript を実行できない環境（Muse など）では、ページの表示から下の項目を読み取る。そのとき、氏名・住所・郵便番号は出力にも返答にも含めない。

スクリプトの結果に足すもの：`op`、`fetched_at`、`source`、`cached`、`url`。スクリプトが `status: "blocked"` や `"not_found"` を返したら、そのまま返す。

## 操作

### search(query)

- 開くURL：`https://www.amazon.co.jp/s?k=<URLエンコードしたquery>`
- `extract_search.js` で上位10件を拾う。スポンサー枠は `sponsored: true` を付けて残す（落とさない）。
- ASIN は各商品枠の `data-asin` から取る（スクリプトが処理する）。

### get_product(asin_or_url)

- URL が来たら ASIN（`/dp/` の後の10文字の英数字）を取り出し、`https://www.amazon.co.jp/dp/<ASIN>` を開く（アフィリエイトタグや追跡パラメータは捨てる）。
- `extract_product.js` で読む項目：タイトル、現在価格（税込の表示価格。参考価格があれば併記）、在庫表示（「在庫あり。」「残り◯点」などページの文言のまま）、販売元・出荷元、評価、評価数、星ごとの割合、「この商品について」の箇条書き、登録情報の主要な項目。
- カートボックスがなく「すべての出品を見る」表示になっている場合、価格は `null` になる。その旨が `notes` に入る。

### get_price(asin)

- `get_product` と同じページを開き、価格と在庫だけを返す。
- 同じ ASIN を **30分以内** に取得済みなら（下のキャッシュ）、ページを開かずにその値を `cached: true` で返す。

## キャッシュ

ファイルを書ける環境では `~/.cache/amazon-lookup/<ASIN>.json` に最後の `get_product` の結果を保存する。30分以内ならそれを使う。書けない環境（Muse など）では、同じ会話の中で取得済みの値を使い回す。ユーザーが「最新で」と言ったらキャッシュを使わない。

## 出力（JSON）

全操作に共通のフィールド：

```json
{
  "op": "search | get_product | get_price",
  "status": "ok | partial | blocked | not_found | error",
  "fetched_at": "2026-11-24T12:34:56+09:00",
  "source": "browser",
  "session": "signed_out | signed_in | unknown",
  "cached": false,
  "url": "実際に開いたURL",
  "notes": ["読めなかった項目と理由など"]
}
```

`search` の追加フィールド：

```json
{
  "query": "検索語",
  "results": [
    {
      "asin": "B0XXXXXXXX",
      "title": "商品名",
      "price_jpy": 1980,
      "rating": 4.3,
      "review_count": 1234,
      "sponsored": false,
      "url": "https://www.amazon.co.jp/dp/B0XXXXXXXX"
    }
  ]
}
```

`get_product` の追加フィールド：

```json
{
  "asin": "B0XXXXXXXX",
  "title": "商品名",
  "price_jpy": 1980,
  "list_price_jpy": 2480,
  "availability": "在庫あり。",
  "seller": "Amazon.co.jp",
  "ships_from": "Amazon.co.jp",
  "rating": 4.3,
  "review_count": 1234,
  "rating_distribution": {"5": 68, "4": 17, "3": 7, "2": 3, "1": 5},
  "highlights": ["箇条書きの要点"],
  "details": {"メーカー": "…", "発売日": "…"}
}
```

`get_price` の追加フィールド：`asin`、`price_jpy`、`availability`。

- 金額は円の整数。読めなければ `null`。
- `rating_distribution` は百分率の整数。
- 項目が一部でも欠けたら `status: "partial"`。

## 返答のしかた

JSON を返したうえで、ユーザーへの文章は短く要点だけにする（例：「12:34時点で1,980円・在庫あり、評価4.3（1,234件）。ログアウト状態での取得です」）。比較を頼まれたら表にまとめる。最後に「購入はご自身でどうぞ」のように、買う操作はしないことを必要に応じて添える。
