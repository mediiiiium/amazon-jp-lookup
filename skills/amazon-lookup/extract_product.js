// amazon.co.jp 商品ページ（/dp/<ASIN>）から商品情報を取り出す。
// ブラウザのページ上で実行し、最後の式（JSON文字列）が結果になる。
// 氏名・住所・お届け先などアカウント由来の情報は返さない。HTMLやURLも返さない。
(() => {
  const q = (s) => document.querySelector(s);
  const txt = (e) => (e ? e.textContent.replace(/\s+/g, ' ').trim() : null);
  const t = (s) => txt(q(s));
  const num = (s) => {
    if (!s) return null;
    const m = s.replace(/[,，]/g, '').match(/\d+(\.\d+)?/);
    return m ? Number(m[0]) : null;
  };
  const clean = (s) => (s ? s.replace(/[‎‏]/g, '').replace(/\s+/g, ' ').trim() : s);

  const notes = [];
  if (q('form[action*="validateCaptcha"]')) {
    return JSON.stringify({ status: 'blocked', notes: ['CAPTCHA が表示された'] });
  }
  const html = document.documentElement.innerHTML;
  const li = html.match(/"loggedIn"\s*:\s*(true|false)/);
  const line1 = t('#nav-link-accountList-nav-line-1') || '';
  const session = li ? (li[1] === 'true' ? 'signed_in' : 'signed_out')
    : (!line1 ? 'unknown' : /ログイン/.test(line1) ? 'signed_out' : 'signed_in');

  const asin = (location.pathname.match(/\/dp\/([A-Z0-9]{10})/) || [])[1]
    || (q('#ASIN') && q('#ASIN').value) || null;
  const title = t('#productTitle');
  if (!title) {
    return JSON.stringify({ status: 'not_found', asin, session, notes: ['商品タイトルが見つからない（商品ページではない可能性）'] });
  }

  const price = num(t('#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen')
    || t('#corePrice_feature_div .a-offscreen')
    || t('.priceToPay .a-offscreen'));
  const listPrice = num(t('#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen'));

  const availEl = q('#availability span') || q('#availability');
  const availability = availEl ? clean(availEl.childNodes.length && availEl.id !== 'availability'
    ? availEl.textContent : (availEl.innerText || '').split('\n')[0]) : null;

  const offerText = (sel) => {
    const e = q(sel + ' .offer-display-feature-text-message') || q(sel + ' .offer-display-feature-text');
    return e ? clean(e.textContent).split(' ')[0] : null;
  };
  const seller = offerText('#merchantInfoFeature_feature_div') || t('#sellerProfileTriggerId');
  const shipsFrom = offerText('#fulfillerInfoFeature_feature_div');

  const rating = num((t('#acrPopover .a-icon-alt') || '').replace('5つ星のうち', ''));
  const reviewCount = num(t('#acrCustomerReviewText'));

  const dist = {};
  const h = q('#histogramTable');
  if (h) {
    const s = h.innerText.replace(/\s+/g, '');
    for (const m of s.matchAll(/星([1-5])つ(\d+)%/g)) dist[m[1]] = Number(m[2]);
    if (!Object.keys(dist).length) for (const m of s.matchAll(/([1-5])つ星[^\d]*?(\d+)%/g)) dist[m[1]] = Number(m[2]);
  }

  const highlights = [...document.querySelectorAll('#feature-bullets li span.a-list-item')]
    .map((e) => clean(e.textContent)).filter(Boolean).slice(0, 6);

  const details = {};
  const add = (k, v) => {
    k = clean(k || '').replace(/[:：]$/, '').trim();
    v = clean(v || '');
    if (k && v && !(k in details) && Object.keys(details).length < 15
      && !/カスタマーレビュー|売れ筋ランキング/.test(k)) details[k] = v.slice(0, 100);
  };
  document.querySelectorAll('#prodDetails tr, #productDetails tr, table.a-normal tr').forEach((r) => {
    const c = r.querySelectorAll('th, td');
    if (c.length >= 2) add(c[0].textContent, c[c.length - 1].textContent);
  });
  document.querySelectorAll('#detailBullets_feature_div li').forEach((r) => {
    const k = r.querySelector('.a-text-bold');
    if (k) add(k.textContent, r.textContent.replace(k.textContent, ''));
  });

  const fields = { price_jpy: price, availability, rating, review_count: reviewCount };
  for (const [k, v] of Object.entries(fields)) if (v === null) notes.push(`${k} を読めなかった`);
  if (q('#buybox-see-all-buying-choices') && price === null) notes.push('カートボックスがなく「すべての出品を見る」表示（出品者ごとに価格が違う）');

  return JSON.stringify({
    status: notes.length ? 'partial' : 'ok',
    session, asin, title,
    price_jpy: price,
    list_price_jpy: listPrice !== price ? listPrice : null,
    availability, seller, ships_from: shipsFrom,
    rating, review_count: reviewCount,
    rating_distribution: Object.keys(dist).length ? dist : null,
    highlights, details, notes,
  });
})()
