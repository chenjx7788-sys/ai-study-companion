/**
 * 知萤划词助手 · background service worker（MV3）
 *
 * 唯一网络出口：content script 在 HTTPS 页面里直连 http://127.0.0.1 会被
 * 混合内容策略拦截，所以所有对主应用的请求都从这里发出（扩展源不受此限）。
 *
 * 职责：ping 缓存、contextMenus、commands（Alt+B）、fetch/SSE、超时控制。
 * 隐私承诺：host_permissions 只有 127.0.0.1 / localhost，无任何外联。
 */

const DEFAULT_BASE = 'http://127.0.0.1:8000';
const PING_TTL_MS = 5000;
const PING_TIMEOUT_MS = 2000;
const CALL_TIMEOUT_MS = 60000;      // 默认 60s（本地/第三方模型首字节常超 10s，不用需求文档的 10s）
const SSE_FIRST_BYTE_MS = 30000;

let pingCache = { at: 0, ok: false, reason: '', base: '' };

async function getCfg() {
  const { baseUrl, token } = await chrome.storage.sync.get(['baseUrl', 'token']);
  return { baseUrl: (baseUrl || DEFAULT_BASE).replace(/\/+$/, ''), token: (token || '').trim() };
}

/** ping = 主应用活着吗 + token 配对了吗（401）。结果短缓存，避免连续点击抖动。 */
async function ping(force = false) {
  const cfg = await getCfg();
  if (!cfg.token) return { ok: false, reason: 'no-token' };
  if (!force && pingCache.base === cfg.baseUrl && Date.now() - pingCache.at < PING_TTL_MS) return pingCache;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), PING_TIMEOUT_MS);
  try {
    const r = await fetch(cfg.baseUrl + '/api/ext/ping', {
      headers: { 'X-BrainMate-Token': cfg.token }, signal: ctrl.signal,
    });
    pingCache = {
      at: Date.now(), base: cfg.baseUrl,
      ok: r.status === 200,
      reason: r.status === 200 ? '' : (r.status === 401 ? 'bad-token' : 'http-' + r.status),
    };
  } catch (e) {
    // 连接被拒 = 主应用没启动；这是用户最常见的情况，单独一个码
    pingCache = { at: Date.now(), base: cfg.baseUrl, ok: false, reason: 'no-app' };
  } finally {
    clearTimeout(timer);
  }
  return pingCache;
}

function postErr(port, reqId, code, message) {
  try { port.postMessage({ kind: 'error', reqId, code, message: message || '' }); } catch (e) { /* 页面已离开 */ }
}

async function fetchJson(cfg, path, body, timeoutMs) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs || CALL_TIMEOUT_MS);
  try {
    const r = await fetch(cfg.baseUrl + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-BrainMate-Token': cfg.token },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = data && data.detail;
      const msg = typeof d === 'string' ? d : (d && (d.hint || d.reason)) || ('HTTP ' + r.status);
      const err = new Error(msg);
      err.httpStatus = r.status;
      throw err;
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

/** 总结：SSE 流式。逐 token 转发给 content script（转发活动本身给 SW 保活）。 */
async function streamSummary(cfg, port, reqId, body) {
  const ctrl = new AbortController();
  const firstByteTimer = setTimeout(() => ctrl.abort(), SSE_FIRST_BYTE_MS);
  let r;
  try {
    r = await fetch(cfg.baseUrl + '/api/ext/summary/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-BrainMate-Token': cfg.token },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
  } catch (e) {
    clearTimeout(firstByteTimer);
    throw e;
  }
  if (!r.ok || !r.body) {
    clearTimeout(firstByteTimer);
    const data = await r.json().catch(() => ({}));
    throw new Error((data && data.detail) || ('HTTP ' + r.status));
  }
  // 首字节到了以后换成全程兜底超时（长文两段式可能跑很久）
  clearTimeout(firstByteTimer);
  const totalTimer = setTimeout(() => ctrl.abort(), 10 * 60 * 1000);
  try {
    const reader = r.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buf = '';
    let sawDone = false;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE 帧以空行分隔
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let event = 'message', dataStr = '';
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          else if (line.startsWith('data:')) dataStr += line.slice(5).trim();
        }
        let payload = {};
        try { payload = JSON.parse(dataStr); } catch (e) { /* 忽略坏帧 */ }
        if (event === 'token' && payload.t) {
          port.postMessage({ kind: 'token', reqId, t: payload.t });
        } else if (event === 'progress') {
          port.postMessage({ kind: 'progress', reqId, done: payload.done, total: payload.total });
        } else if (event === 'error') {
          throw new Error(payload.message || '总结失败');
        } else if (event === 'done') {
          sawDone = true;
        }
      }
    }
    port.postMessage({ kind: 'done', reqId, data: { truncated: !sawDone } });
  } finally {
    clearTimeout(totalTimer);
    try { ctrl.abort(); } catch (e) { /* 已结束 */ }
  }
}

async function handleRequest(port, msg) {
  const { reqId, action, payload } = msg;
  const p = await ping();
  if (!p.ok) { postErr(port, reqId, p.reason); return; }
  const cfg = await getCfg();
  if (action === 'explain') {
    const data = await fetchJson(cfg, '/api/ext/explain', {
      selected_text: payload.text,
      context_text: payload.context || '',
      page_title: payload.title || '',
      page_url: payload.url || '',
    });
    port.postMessage({ kind: 'done', reqId, data });
  } else if (action === 'summary') {
    await streamSummary(cfg, port, reqId, { text: payload.text, title: payload.title || '' });
  } else if (action === 'save') {
    const data = await fetchJson(cfg, '/api/ext/save', {
      url: payload.url, text: payload.text, title: payload.title || '',
    });
    port.postMessage({ kind: 'done', reqId, data });
  } else {
    postErr(port, reqId, 'bad-action', '未知操作：' + action);
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'brainmate') return;
  port.onMessage.addListener((msg) => {
    if (!msg || msg.kind !== 'request') return;
    handleRequest(port, msg).catch((e) => {
      const code = e && e.name === 'AbortError' ? 'timeout' : (e && e.httpStatus ? 'http' : 'http');
      postErr(port, msg.reqId, code, e && e.message ? String(e.message).slice(0, 300) : '');
    });
  });
});

// 设置页里「打开配对设置」按钮 / 浮窗里的「去配对」按钮
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.kind === 'open-options') {
    chrome.runtime.openOptionsPage();
    sendResponse({ ok: true });
    return false;
  }
  if (msg && msg.kind === 'ping-now') {
    ping(true).then((p) => sendResponse(p));
    return true;   // 异步 sendResponse
  }
  return false;
});

// ---------- 右键菜单 ----------
const MENU_ACTIONS = [
  ['bm-explain', '解释选中内容'],
  ['bm-summary', '总结选中内容'],
  ['bm-save', '保存选中内容到知识库'],
];

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: 'bm-root', title: '知萤', contexts: ['selection'] });
    for (const [id, title] of MENU_ACTIONS) {
      chrome.contextMenus.create({ id, parentId: 'bm-root', title, contexts: ['selection'] });
    }
    chrome.contextMenus.create({ id: 'bm-save-page', title: '保存本页到知萤', contexts: ['page'] });
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (!tab || !tab.id) return;
  if (info.menuItemId === 'bm-save-page') {
    chrome.tabs.sendMessage(tab.id, { kind: 'trigger', action: 'save-page' }).catch(() => {});
  } else if (info.menuItemId.startsWith('bm-')) {
    chrome.tabs.sendMessage(tab.id, {
      kind: 'trigger', action: info.menuItemId.slice(3), selectionText: info.selectionText || '',
    }).catch(() => {});
  }
});

// ---------- 快捷键 Alt+B（默认操作：解释） ----------
chrome.commands.onCommand.addListener(async (cmd) => {
  if (cmd !== 'brainmate-explain') return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab && tab.id) {
    chrome.tabs.sendMessage(tab.id, { kind: 'trigger', action: 'explain' }).catch(() => {});
  }
});
