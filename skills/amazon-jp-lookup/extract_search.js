// amazon.co.jp 検索結果ページ（/s?k=...）から上位の商品を取り出す。
// ブラウザのページ上で実行し、最後の式（JSON文字列）が結果になる。
// 氏名・住所などアカウント由来の情報は返さない。HTMLやURLのクエリも返さない。
(() => {
  const LIMIT = 10;
  const q = (s) => document.querySelector(s);
  const txt = (e) => (e ? e.textContent.replace(/\s+/g, ' ').trim() : null);
  const num = (s) => {
    if (!s) return null;
    const m = s.replace(/[,，]/g, '').match(/\d+(\.\d+)?/);
    return m ? Number(m[0]) : null;
  };
  // 「(1.7k)」「(2.3万)」「(5829)」を数に直す
  const count = (s) => {
    if (!s) return null;
    const m = s.replace(/[,，()（）]/g, '').match(/(\d+(?:\.\d+)?)\s*(k|万)?/i);
    if (!m) return null;
    const n = Number(m[1]);
    return Math.round(m[2] ? n * (m[2] === '万' ? 10000 : 1000) : n);
  };

  if (q('form[action*="validateCaptcha"]')) {
    return JSON.stringify({ status: 'blocked', notes: ['CAPTCHA が表示された'] });
  }
  const html = document.documentElement.innerHTML;
  const li = html.match(/"loggedIn"\s*:\s*(true|false)/);
  const line1 = txt(q('#nav-link-accountList-nav-line-1')) || '';
  const session = li ? (li[1] === 'true' ? 'signed_in' : 'signed_out')
    : (!line1 ? 'unknown' : /ログイン/.test(line1) ? 'signed_out' : 'signed_in');

  const seen = new Set();
  const results = [];
  for (const e of document.querySelectorAll('div[data-component-type="s-search-result"][data-asin]')) {
    const asin = e.dataset.asin;
    if (!asin || seen.has(asin)) continue;
    seen.add(asin);
    const rc = e.querySelector('[data-csa-c-content-id*="customer-ratings"], a[href*="customerReviews"]');
    results.push({
      asin,
      title: txt(e.querySelector('h2')),
      price_jpy: num(txt(e.querySelector('.a-price:not(.a-text-price) .a-offscreen'))),
      rating: num((txt(e.querySelector('.a-icon-alt')) || '').replace('5つ星のうち', '')),
      review_count: count(txt(rc)),
      sponsored: !!e.querySelector('.puis-sponsored-label-text, .s-sponsored-label-text'),
      url: `https://www.amazon.co.jp/dp/${asin}`,
    });
    if (results.length >= LIMIT) break;
  }
  return JSON.stringify({
    status: results.length ? 'ok' : 'not_found',
    session, results,
    notes: results.length ? [] : ['検索結果の商品枠が見つからない'],
  });
})()
