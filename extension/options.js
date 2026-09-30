/** 知萤划词助手 · 设置页（仅两项：主应用地址 + 配对令牌） */
const DEFAULT_BASE = 'http://127.0.0.1:8000';

const elBase = document.getElementById('baseUrl');
const elToken = document.getElementById('token');
const elStatus = document.getElementById('status');

function setStatus(text, ok) {
  elStatus.textContent = text;
  elStatus.className = ok ? 'ok' : 'err';
}

async function load() {
  const { baseUrl, token } = await chrome.storage.sync.get(['baseUrl', 'token']);
  elBase.value = baseUrl || DEFAULT_BASE;
  elToken.value = token || '';
}

async function save() {
  const baseUrl = (elBase.value || '').trim().replace(/\/+$/, '') || DEFAULT_BASE;
  const token = (elToken.value || '').trim();
  if (!/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(baseUrl)) {
    setStatus('地址只能是本机回环地址（127.0.0.1 / localhost）', false);
    return null;
  }
  await chrome.storage.sync.set({ baseUrl, token });
  return { baseUrl, token };
}

document.getElementById('save').addEventListener('click', async () => {
  const cfg = await save();
  if (cfg) setStatus('已保存', true);
});

document.getElementById('test').addEventListener('click', async () => {
  const cfg = await save();
  if (!cfg) return;
  if (!cfg.token) { setStatus('请先粘贴配对令牌', false); return; }
  setStatus('连接中…', true);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 3000);
  try {
    const r = await fetch(cfg.baseUrl + '/api/ext/ping', {
      headers: { 'X-BrainMate-Token': cfg.token }, signal: ctrl.signal,
    });
    if (r.status === 200) {
      const j = await r.json().catch(() => ({}));
      setStatus('连接成功（主应用 v' + (j.version || '?') + '）', true);
    } else if (r.status === 401) {
      setStatus('令牌不对，请回主应用设置页重新复制', false);
    } else {
      setStatus('主应用返回异常（HTTP ' + r.status + '）', false);
    }
  } catch (e) {
    setStatus('连不上主应用 —— 请先打开知萤', false);
  } finally {
    clearTimeout(timer);
  }
});

load();
