// Buka URL lewat proxy, simpan HTML + screenshot, laporkan status/blokir sebagai JSON ke stdout.
// Usage: node probe.js <url> <outPrefix> [proxy]
const { chromium } = require('playwright');

const [url, prefix, proxy = 'http://127.0.0.1:8888'] = process.argv.slice(2);  // proxy "none" = langsung, tanpa proxy
if (!url || !prefix) { console.error('usage: node probe.js <url> <outPrefix> [proxy]'); process.exit(1); }

// ponytail: UA wajib dioverride - default headless Chrome mengirim "HeadlessChrome/..."
// dan Akamai me-reset koneksi sebelum halaman dimuat.
const UA = process.env.UA || 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36';

function classify(html, title) {
  // nomor referensi ada di <span id="referenceNum">, format 18.<hex>.<epoch>.<hex>
  const ref = html.match(/id="referenceNum"[^>]*>\s*([^<\s]+)/) || html.match(/\b\d{2}\.[0-9a-f]{6,8}\.\d{10}\.[0-9a-f]{6,8}\b/);
  if (/Ada yang tidak beres|Aktivitas yang tidak biasa|Access Denied/i.test(html))
    return { blocked: 'yes', reason: 'akamai-deny', reference: ref ? (ref[1] || ref[0]) : '' };
  if (/bm-verify|_sec\/verify/i.test(html))
    return { blocked: 'yes', reason: 'akamai-interstitial', reference: ref ? (ref[1] || ref[0]) : '' };
  if (/cf-browser-verification|Just a moment/i.test(html))
    return { blocked: 'yes', reason: 'cloudflare-challenge', reference: '' };
  return { blocked: 'no', reason: title.slice(0, 60), reference: '' };
}

(async () => {
  const browser = await chromium.launch({ channel: 'chrome',
    ...(proxy && proxy !== 'none' ? { proxy: { server: proxy } } : {}) });
  const out = { url, status: '', blocked: 'error', reason: '', reference: '', bytes: 0 };
  try {
    const page = await browser.newContext({ userAgent: UA, viewport: { width: 1440, height: 900 } })
      .then(c => c.newPage());
    const res = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
    await page.waitForTimeout(Number(process.env.SETTLE_MS || 8000));
    const html = await page.content();
    require('fs').writeFileSync(`${prefix}.html`, html);
    await page.screenshot({ path: `${prefix}.png`, fullPage: true });
    out.status = res ? res.status() : '';
    out.bytes = html.length;
    Object.assign(out, classify(html, await page.title()));
  } catch (e) {
    out.reason = String(e.message).split('\n')[0].slice(0, 120);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
