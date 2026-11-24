#!/usr/bin/env python3
"""amazon.co.jp の商品を読むだけのスクリプト（標準ライブラリのみ）。

Amazon.co.jp 利用規約の「エージェント条件」に合わせて、次のように動く。
- すべてのリクエストのユーザーエージェントで "Agent/amazon-jp-lookup" と名乗る
- 人間のふりをしない（ブラウザの UA を名乗らない・クッキーを使わない・CAPTCHA を解かない）
- robots.txt で止められているときや、Amazon が遮断・停止を求める表示を返したときは取得をやめる
- リクエストの間は最低 MIN_INTERVAL 秒あける（Amazon への負荷を抑えるため）

使い方:
  python3 lookup.py search "検索語"
  python3 lookup.py product <ASIN または商品URL>
  python3 lookup.py price <ASIN または商品URL>
  オプション: --fresh（キャッシュを使わない）
"""
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

AGENT_NAME = "amazon-jp-lookup"
USER_AGENT = f"Agent/{AGENT_NAME} (+https://github.com/mediiiiium/amazon-jp-lookup)"
BASE = "https://www.amazon.co.jp"
CACHE_DIR = os.path.expanduser("~/.cache/amazon-jp-lookup")
CACHE_TTL = 30 * 60
MIN_INTERVAL = 3.0
JST = timezone(timedelta(hours=9))

# 開かないページ（購入・アカウントまわり）
FORBIDDEN = re.compile(r"/(gp/cart|gp/buy|ap/|gp/your-account|gp/css|gp/sign-in|checkout|hz/)")


# ---------- 小さな DOM と CSS セレクタ ----------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    def text(self):
        out = []
        stack = [self]
        while stack:
            n = stack.pop()
            if isinstance(n, str):
                out.append(n)
            elif n.tag not in ("script", "style"):
                stack.extend(reversed(n.children))
        return "".join(out)

    def classes(self):
        return self.attrs.get("class", "").split()

    def iter(self):
        stack = list(reversed([c for c in self.children if isinstance(c, Node)]))
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed([c for c in n.children if isinstance(c, Node)]))


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


SIMPLE = re.compile(r"([a-zA-Z0-9]+)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:([*^$]?=)\"?([^\"\]]*)\"?)?\]|:not\(([^)]*)\)")


def parse_simple(sel):
    parts = []
    for m in SIMPLE.finditer(sel):
        tag, id_, cls, attr, op, val, neg = m.groups()
        if tag:
            parts.append(("tag", tag.lower()))
        elif id_:
            parts.append(("id", id_))
        elif cls:
            parts.append(("class", cls))
        elif attr:
            parts.append(("attr", attr, op, val))
        elif neg:
            parts.append(("not", parse_simple(neg)))
    return parts


def match_simple(n, parts):
    for p in parts:
        k = p[0]
        if k == "tag" and n.tag != p[1]:
            return False
        if k == "id" and n.attrs.get("id") != p[1]:
            return False
        if k == "class" and p[1] not in n.classes():
            return False
        if k == "attr":
            _, a, op, val = p
            if a not in n.attrs:
                return False
            v = n.attrs[a]
            if op == "=" and v != val:
                return False
            if op == "*=" and val not in v:
                return False
            if op == "^=" and not v.startswith(val):
                return False
            if op == "$=" and not v.endswith(val):
                return False
        if k == "not" and match_simple(n, p[1]):
            return False
    return True


def select(root, selector):
    """子孫結合子とカンマ区切りだけに対応した querySelectorAll。文書順で返す。"""
    found = []
    for group in selector.split(","):
        chain = [parse_simple(s) for s in group.split()]
        for n in root.iter():
            if not match_simple(n, chain[-1]):
                continue
            i, a = len(chain) - 2, n.parent
            while i >= 0 and a is not None and a is not root.parent:
                if a.tag != "#root" and match_simple(a, chain[i]):
                    i -= 1
                a = a.parent
            if i < 0 and n not in found:
                found.append(n)
    if "," in selector:
        order = {id(n): k for k, n in enumerate(root.iter())}
        found.sort(key=lambda n: order.get(id(n), 0))
    return found


def select_one(root, selector):
    for group in selector.split(","):
        r = select(root, group)
        if r:
            return r[0]
    return None


# ---------- 取得 ----------

def norm(s):
    return re.sub(r"\s+", " ", s or "").strip() or None


def clean(s):
    return norm(re.sub(r"[‎‏]", "", s or ""))


def num(s):
    if not s:
        return None
    m = re.search(r"\d+(\.\d+)?", s.replace(",", "").replace("，", ""))
    return float(m.group()) if m and "." in m.group() else (int(m.group()) if m else None)


FOREIGN_NOTE = "価格が円以外の通貨で表示された（日本国外からの接続の可能性）。price_jpy は null にした"


def yen(s, notes=None):
    """円表示の価格だけを整数で返す。$ などほかの通貨なら null にして notes に理由を残す"""
    if not s:
        return None
    if "￥" in s or "¥" in s or "円" in s:
        v = num(s)
        return int(v) if v is not None else None
    if notes is not None and FOREIGN_NOTE not in notes:
        notes.append(FOREIGN_NOTE)
    return None


def count(s):
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(k|万)?", re.sub(r"[,，()（）]", "", s), re.I)
    if not m:
        return None
    n = float(m.group(1))
    return round(n * (10000 if m.group(2) == "万" else 1000) if m.group(2) else n)


_last = [0.0]
_robots = [None]


def robots_ok(url):
    if _robots[0] is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(BASE + "/robots.txt", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as r:
                rp.parse(r.read().decode("utf-8", "ignore").splitlines())
        except Exception:
            rp = False  # 読めなければ判定できない。取得自体は止めない
        _robots[0] = rp
    return True if _robots[0] is False else _robots[0].can_fetch(USER_AGENT, url)


def fetch(url):
    """(status, html, notes) を返す。status は ok / blocked / error。"""
    if FORBIDDEN.search(urllib.parse.urlparse(url).path):
        return "error", None, ["購入・アカウントまわりのページなので開かない"]
    if not robots_ok(url):
        return "blocked", None, ["robots.txt でこのエージェントには止められている"]
    wait = MIN_INTERVAL - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html",
        "Accept-Language": "ja-JP,ja;q=0.9",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            final = r.geturl()
    except urllib.error.HTTPError as e:
        _last[0] = time.time()
        if e.code in (403, 429, 503):
            return "blocked", None, [f"HTTP {e.code}。Amazon が取得を断った可能性があるため、再試行しない"]
        if e.code == 404:
            return "not_found", None, ["HTTP 404"]
        return "error", None, [f"HTTP {e.code}"]
    except Exception as e:
        _last[0] = time.time()
        return "error", None, [f"取得に失敗: {e.__class__.__name__}"]
    _last[0] = time.time()
    if FORBIDDEN.search(urllib.parse.urlparse(final).path):
        return "blocked", None, ["購入・ログインのページに転送されたので読まない"]
    html = body.decode("utf-8", "ignore")
    if "validateCaptcha" in html or "api-services-support@amazon.com" in html:
        return "blocked", None, ["CAPTCHA または自動アクセスの案内が表示された。解かずに止める"]
    return "ok", html, []


def dom(html):
    b = TreeBuilder()
    b.feed(html)
    return b.root


def base_result(op, url):
    return {
        "op": op, "status": "ok",
        "fetched_at": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "http", "user_agent": USER_AGENT,
        "session": "signed_out", "cached": False, "url": url, "notes": [],
    }


# ---------- 操作 ----------

def search(query, limit=10):
    url = f"{BASE}/s?k={urllib.parse.quote(query)}"
    out = base_result("search", url)
    out["query"] = query
    st, html, notes = fetch(url)
    if st != "ok":
        out.update(status=st, results=[], notes=notes)
        return out
    root = dom(html)
    seen, results = set(), []
    for e in select(root, 'div[data-component-type="s-search-result"]'):
        asin = e.attrs.get("data-asin")
        if not asin or asin in seen:
            continue
        seen.add(asin)
        price = select_one(e, ".a-price:not(.a-text-price) .a-offscreen")
        rating = select_one(e, ".a-icon-alt")
        rc = select_one(e, '[data-csa-c-content-id*="customer-ratings"], a[href*="customerReviews"]')
        h2 = select_one(e, "h2")
        results.append({
            "asin": asin,
            "title": norm(h2.text()) if h2 else None,
            "price_jpy": yen(price.text(), out["notes"]) if price else None,
            "rating": num((rating.text() if rating else "").replace("5つ星のうち", "")),
            "review_count": count(rc.text()) if rc else None,
            "sponsored": bool(select_one(e, ".puis-sponsored-label-text, .s-sponsored-label-text")),
            "url": f"{BASE}/dp/{asin}",
        })
        if len(results) >= limit:
            break
    out["results"] = results
    if not results:
        out.update(status="not_found", notes=["検索結果の商品枠が見つからない"])
    return out


def to_asin(s):
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", s) or re.fullmatch(r"([A-Z0-9]{10})", s.strip())
    return m.group(1) if m else None


def cache_path(asin):
    return os.path.join(CACHE_DIR, f"{asin}.json")


def read_cache(asin):
    try:
        p = cache_path(asin)
        if time.time() - os.path.getmtime(p) < CACHE_TTL:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            d["cached"] = True
            return d
    except Exception:
        pass
    return None


def write_cache(asin, d):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_path(asin), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


PRICE_SELECTORS = (
    "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen",
    "#corePrice_feature_div .a-offscreen",
    ".priceToPay .a-offscreen",
    "#apex_desktop .a-price .a-offscreen",
    "#desktop_unifiedPrice .a-price .a-offscreen",
    "#tp_price_block_total_price_ww .a-offscreen",
    "#price_inside_buybox", "#priceblock_ourprice", "#priceblock_dealprice",
)


def price_text(root, html, t):
    """商品ページの価格の文字列を返す。表示の枠で見つからなければ、
    ページに埋め込まれた機械向けの価格データ（通貨コードつき）を読む。
    日本国外からの接続では表示の枠が変わることがあるため"""
    for sel in PRICE_SELECTORS:
        v = t(sel)
        if v and re.search(r"\d", v):
            return v
    key = "items[0.base][customerVisiblePrice]"
    inputs = {}
    for n in root.iter():
        name = n.attrs.get("name", "")
        if n.tag == "input" and name.startswith(key) and name not in inputs:
            inputs[name] = n.attrs.get("value", "")
    code = inputs.get(key + "[currencyCode]")
    shown = inputs.get(key + "[displayString]")
    amount = inputs.get(key + "[amount]")
    if code:
        if code == "JPY":
            return shown if shown and re.search(r"\d", shown) else (f"￥{amount}" if amount else None)
        return shown or f"{code} {amount}"  # 円以外。yen() が null にして理由を残す
    m = re.search(r'"priceAmount":([\d.]+),"currencySymbol":"([^"]+)"', html)
    if m:
        return f"{m.group(2)}{m.group(1)}"
    return None


def product(arg, fresh=False):
    asin = to_asin(arg)
    url = f"{BASE}/dp/{asin}" if asin else None
    out = base_result("get_product", url)
    if not asin:
        out.update(status="error", notes=["ASIN を読み取れない"])
        return out
    if not fresh:
        c = read_cache(asin)
        if c:
            return c
    st, html, notes = fetch(url)
    out["asin"] = asin
    if st != "ok":
        out.update(status=st, notes=notes)
        return out
    dump = os.environ.get("AMAZON_JP_LOOKUP_DUMP_DIR")
    if dump:  # 調査用。指定したときだけ取得した HTML を保存する
        try:
            os.makedirs(dump, exist_ok=True)
            with open(os.path.join(dump, f"{asin}.html"), "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            pass
    root = dom(html)
    t = lambda sel: (lambda n: norm(n.text()) if n else None)(select_one(root, sel))
    title = t("#productTitle")
    if not title:
        out.update(status="not_found", notes=["商品タイトルが見つからない（商品ページではない可能性）"])
        return out
    price = yen(price_text(root, html, t), out["notes"])
    list_price = yen(t("#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen"), out["notes"])
    av = select_one(root, "#availability span") or select_one(root, "#availability")
    availability = clean(av.text()) if av else None

    def offer(sel):
        e = select_one(root, sel + " .offer-display-feature-text-message") or \
            select_one(root, sel + " .offer-display-feature-text")
        v = clean(e.text()) if e else None
        return v.split(" ")[0] if v else None

    seller = offer("#merchantInfoFeature_feature_div") or t("#sellerProfileTriggerId")
    ships_from = offer("#fulfillerInfoFeature_feature_div")
    rating = num((t("#acrPopover .a-icon-alt") or "").replace("5つ星のうち", ""))
    review_count = num(t("#acrCustomerReviewText"))

    dist = {}
    h = select_one(root, "#histogramTable")
    if h:
        # 各行の aria-label「レビューの58%に星5つがついています」を優先して読む
        for e in [h] + list(h.iter()):
            m = re.search(r"(\d+)%に星([1-5])つ", e.attrs.get("aria-label", ""))
            if m and m.group(2) not in dist:
                dist[m.group(2)] = int(m.group(1))
        s = "" if dist else re.sub(r"\s+", "", h.text())
        for m in re.finditer(r"星([1-5])つ(\d+)%", s):
            dist[m.group(1)] = int(m.group(2))
        if not dist:
            for m in re.finditer(r"([1-5])つ星[^\d]*?(\d+)%", s):
                dist[m.group(1)] = int(m.group(2))

    highlights = [clean(e.text()) for e in select(root, "#feature-bullets li span.a-list-item")]
    highlights = [x for x in highlights if x][:6]

    details = {}

    def add(k, v):
        k = re.sub(r"[:：]$", "", clean(k) or "").strip()
        v = clean(v)
        if k and v and k not in details and len(details) < 15 and \
                not re.search(r"カスタマーレビュー|売れ筋ランキング", k):
            details[k] = v[:100]

    for r in select(root, "#prodDetails tr, #productDetails tr, table.a-normal tr"):
        cells = [c for c in r.iter() if c.tag in ("th", "td")]
        if len(cells) >= 2:
            add(cells[0].text(), cells[-1].text())
    for r in select(root, "#detailBullets_feature_div li"):
        k = select_one(r, ".a-text-bold")
        if k:
            add(k.text(), r.text().replace(k.text(), ""))

    notes = list(out["notes"])  # yen() が残した通貨の理由を消さない
    for k, v in {"price_jpy": price, "availability": availability,
                 "rating": rating, "review_count": review_count}.items():
        if v is None and not (k == "price_jpy" and FOREIGN_NOTE in notes):
            notes.append(f"{k} を読めなかった")
    if price is None and select_one(root, "#buybox-see-all-buying-choices"):
        notes.append("カートボックスがなく「すべての出品を見る」表示（出品者ごとに価格が違う）")

    out.update(
        status="partial" if notes else "ok", notes=notes, title=title,
        price_jpy=price, list_price_jpy=list_price if list_price != price else None,
        availability=availability, seller=seller, ships_from=ships_from,
        rating=rating, review_count=review_count,
        rating_distribution=dist or None, highlights=highlights, details=details,
    )
    write_cache(asin, out)
    return out


def price(arg, fresh=False):
    p = product(arg, fresh)
    keep = ("op", "status", "fetched_at", "source", "user_agent", "session", "cached",
            "url", "notes", "asin", "price_jpy", "availability")
    out = {k: p.get(k) for k in keep}
    out["op"] = "get_price"
    return out


def main(argv):
    fresh = "--fresh" in argv
    args = [a for a in argv if a != "--fresh"]
    if len(args) < 2 or args[0] not in ("search", "product", "price"):
        print(__doc__, file=sys.stderr)
        return 2
    op, arg = args[0], " ".join(args[1:])
    r = search(arg) if op == "search" else product(arg, fresh) if op == "product" else price(arg, fresh)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
