/**
 * 知萤划词助手 · content script
 *
 * 职责（只做 UI 与选区，网络全在 background）：
 *  - 监听选区 → 浮动工具栏（解释 / 总结 / 存知识库）
 *  - Shadow DOM 浮窗：拖动、关闭、复制、保存结果；SSE 逐字渲染
 *  - 接收 background 转发的右键菜单 / Alt+B 触发
 *  - 整页正文抽取（Readability，全局由 lib/Readability.js 提供）
 *
 * 样式隔离：工具栏与浮窗全部挂在同一个 ShadowRoot 里，不污染宿主页面、
 * 也不被宿主样式污染。
 */
(() => {
  if (window.__brainmateLoaded) return;   // 防重复注入
  window.__brainmateLoaded = true;

  // ---------- Shadow 宿主 ----------
  const host = document.createElement('div');
  host.id = 'brainmate-ext-host';
  host.style.cssText = 'all: initial; position: fixed; z-index: 2147483647; top: 0; left: 0;';
  (document.documentElement || document.body).appendChild(host);
  const root = host.attachShadow({ mode: 'open' });

  const style = document.createElement('style');
  style.textContent = `
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; }
    .toolbar { position: fixed; display: none; align-items: center; gap: 4px; padding: 5px;
      background: #26252a; border-radius: 9px; box-shadow: 0 4px 16px rgba(0,0,0,.28); }
    .toolbar button { border: 0; background: transparent; color: #e8e6e3; font-size: 12.5px;
      padding: 5px 10px; border-radius: 6px; cursor: pointer; white-space: nowrap; }
    .toolbar button:hover { background: rgba(255,255,255,.14); }
    .toolbar .sep { width: 1px; height: 16px; background: rgba(255,255,255,.18); }
    .panel { position: fixed; width: 380px; max-width: calc(100vw - 24px); display: none; flex-direction: column;
      background: #fff; border: 1px solid #e4e2dc; border-radius: 12px; overflow: hidden;
      box-shadow: 0 10px 40px rgba(0,0,0,.16); color: #2c2c2a; }
    .panel .head { display: flex; align-items: center; gap: 8px; padding: 10px 12px;
      background: #f7f6f3; border-bottom: 1px solid #eceae4; cursor: move; user-select: none; }
    .panel .head .title { flex: 1; font-size: 13px; font-weight: 600; color: #444441;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .panel .head .x { border: 0; background: #eceae4; width: 24px; height: 24px; border-radius: 6px;
      font-size: 13px; color: #6e6e6e; cursor: pointer; line-height: 1; }
    .panel .head .x:hover { background: #e0ded8; color: #2c2c2a; }
    .panel .quote { max-height: 64px; overflow: hidden; padding: 8px 14px 0; font-size: 12px;
      color: #888780; line-height: 1.5; }
    .panel .body { padding: 10px 14px; font-size: 13.5px; line-height: 1.75; color: #2c2c2a;
      max-height: 320px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; }
    .panel .body.loading { color: #888780; }
    .panel .progress { padding: 0 14px 6px; font-size: 12px; color: #0F6E56; display: none; }
    .panel .foot { display: flex; align-items: center; gap: 8px; padding: 9px 12px;
      border-top: 1px solid #eceae4; background: #faf9f6; }
    .panel .foot button { border: 1px solid #d3d1c7; background: #fff; color: #444441;
      font-size: 12px; padding: 5px 12px; border-radius: 7px; cursor: pointer; }
    .panel .foot button:hover { border-color: #534AB7; color: #534AB7; }
    .panel .foot button.primary { background: #534AB7; border-color: #534AB7; color: #fff; }
    .panel .foot button.primary:hover { background: #3C3489; }
    .panel .foot .hint { flex: 1; font-size: 12px; color: #888780; }
    .panel .foot .hint.ok { color: #0F6E56; }
    .panel .foot .hint.err { color: #A32D2D; }
    .toast { position: fixed; display: none; padding: 9px 16px; background: #26252a; color: #e8e6e3;
      font-size: 12.5px; border-radius: 9px; box-shadow: 0 4px 16px rgba(0,0,0,.28); }
  `;
  root.appendChild(style);

  // ---------- 工具栏 ----------
  const toolbar = document.createElement('div');
  toolbar.className = 'toolbar';
  toolbar.innerHTML = `
    <button data-act="explain">解释</button>
    <div class="sep"></div>
    <button data-act="summary">总结</button>
    <div class="sep"></div>
    <button data-act="save">存知识库</button>
  `;
  root.appendChild(toolbar);

  // ---------- 浮窗 ----------
  const panel = document.createElement('div');
  panel.className = 'panel';
  panel.innerHTML = `
    <div class="head"><span class="title">知萤</span><button class="x" title="关闭">✕</button></div>
    <div class="quote"></div>
    <div class="progress"></div>
    <div class="body"></div>
    <div class="foot">
      <span class="hint"></span>
      <button data-ft="copy">复制</button>
      <button data-ft="save" class="primary">存知识库</button>
    </div>
  `;
  root.appendChild(panel);
  const elTitle = panel.querySelector('.title');
  const elQuote = panel.querySelector('.quote');
  const elBody = panel.querySelector('.body');
  const elHint = panel.querySelector('.hint');
  const elProgress = panel.querySelector('.progress');
  const btnCopy = panel.querySelector('[data-ft="copy"]');
  const btnSave = panel.querySelector('[data-ft="save"]');

  // ---------- Toast ----------
  const toast = document.createElement('div');
  toast.className = 'toast';
  root.appendChild(toast);
  let toastTimer = 0;
  function showToast(text, nearRightBottom = true) {
    toast.textContent = text;
    toast.style.display = 'block';
    toast.style.right = '24px';
    toast.style.bottom = '24px';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.style.display = 'none'; }, 2600);
  }

  // ---------- 选区 ----------
  let lastSel = null;   // { text, context }

  function grabSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) return null;
    const text = (sel.toString() || '').trim();
    if (text.length < 2) return null;
    // 上下文：选区所在的块级元素文本（截 1500 字，给服务端 4000 上限留余量）
    let context = '';
    try {
      let node = sel.anchorNode && sel.anchorNode.parentElement;
      while (node && !/^(P|DIV|LI|SECTION|ARTICLE|TD|H[1-6]|PRE|BLOCKQUOTE)$/.test(node.tagName)) {
        node = node.parentElement;
      }
      context = ((node && node.innerText) || '').trim().slice(0, 1500);
    } catch (e) { /* 影子 DOM 等异常结构，留空 */ }
    return { text: text.slice(0, 2000), context };
  }

  function selectionRect() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    try { return sel.getRangeAt(0).getBoundingClientRect(); } catch (e) { return null; }
  }

  function showToolbar() {
    const r = selectionRect();
    if (!r) return;
    toolbar.style.display = 'flex';
    const w = toolbar.offsetWidth, h = toolbar.offsetHeight;
    let left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8);
    let top = r.top - h - 8;
    if (top < 8) top = Math.min(r.bottom + 8, window.innerHeight - h - 8);
    toolbar.style.left = left + 'px';
    toolbar.style.top = top + 'px';
  }

  function hideToolbar() { toolbar.style.display = 'none'; }

  document.addEventListener('mouseup', (e) => {
    if (host.contains(e.target)) return;   // 点的是我们自己的 UI
    setTimeout(() => {
      const s = grabSelection();
      if (s) { lastSel = s; showToolbar(); } else { hideToolbar(); }
    }, 10);
  });
  document.addEventListener('keyup', (e) => {
    if (e.key === 'Escape') { hideToolbar(); hidePanel(); }
  });
  document.addEventListener('mousedown', (e) => {
    if (!host.contains(e.target)) hideToolbar();
  });

  // ---------- 浮窗逻辑 ----------
  let panelX = 0, panelY = 0;
  function showPanel(title, quoteText) {
    elTitle.textContent = title;
    elQuote.textContent = quoteText ? ('「' + quoteText.slice(0, 80) + (quoteText.length > 80 ? '…' : '') + '」') : '';
    elBody.textContent = '';
    elBody.classList.add('loading');
    elBody.textContent = '正在联系本地知萤主应用…';
    elHint.textContent = '';
    elHint.className = 'hint';
    elProgress.style.display = 'none';
    btnSave.style.display = 'none';
    btnCopy.style.display = 'none';
    panel.style.display = 'flex';
    const w = 380;
    panelX = Math.max(12, window.innerWidth - w - 24);   // 固定右下（MVP 砍掉了方位设置）
    panelY = Math.max(12, window.innerHeight - 320 - 24);
    panel.style.left = panelX + 'px';
    panel.style.top = panelY + 'px';
  }
  function hidePanel() { panel.style.display = 'none'; }

  panel.querySelector('.x').addEventListener('click', hidePanel);

  // 拖动
  const head = panel.querySelector('.head');
  head.addEventListener('mousedown', (e) => {
    if (e.target.classList.contains('x')) return;
    const dx = e.clientX - panelX, dy = e.clientY - panelY;
    const move = (ev) => {
      panelX = Math.min(Math.max(0, ev.clientX - dx), window.innerWidth - panel.offsetWidth);
      panelY = Math.min(Math.max(0, ev.clientY - dy), window.innerHeight - 60);
      panel.style.left = panelX + 'px';
      panel.style.top = panelY + 'px';
    };
    const up = () => {
      document.removeEventListener('mousemove', move, true);
      document.removeEventListener('mouseup', up, true);
    };
    document.addEventListener('mousemove', move, true);
    document.addEventListener('mouseup', up, true);
  });

  btnCopy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(elBody.textContent || '');
      elHint.textContent = '已复制';
      elHint.className = 'hint ok';
    } catch (e) {
      elHint.textContent = '复制失败，请手动选择复制';
      elHint.className = 'hint err';
    }
  });

  // ---------- 与 background 的 Port 通信 ----------
  let port = null;
  let reqSeq = 0;
  let currentReqId = 0;
  let currentAction = '';
  let currentPayload = null;
  let resultText = '';

  function ensurePort() {
    if (port) return port;
    port = chrome.runtime.connect({ name: 'brainmate' });
    port.onMessage.addListener(onPortMessage);
    port.onDisconnect.addListener(() => { port = null; });
    return port;
  }

  function onPortMessage(msg) {
    if (!msg || msg.reqId !== currentReqId) return;
    if (msg.kind === 'token') {
      if (resultText === '') { elBody.textContent = ''; elBody.classList.remove('loading'); }
      resultText += msg.t;
      elBody.textContent = resultText;
      elBody.scrollTop = elBody.scrollHeight;
    } else if (msg.kind === 'progress') {
      elProgress.style.display = 'block';
      elProgress.textContent = '长文分段处理中 ' + msg.done + ' / ' + msg.total;
    } else if (msg.kind === 'done') {
      elBody.classList.remove('loading');
      elProgress.style.display = 'none';
      if (currentAction === 'save') {
        const dup = msg.data && msg.data.duplicated;
        showToast(dup ? '该页面已在知识库中' : '已保存到知识库');
        hidePanel();
      } else {
        if (!resultText && msg.data && msg.data.content) {
          resultText = msg.data.content;
          elBody.textContent = resultText;
        }
        if (msg.data && msg.data.truncated) {
          elHint.textContent = '连接中断，内容可能不完整';
          elHint.className = 'hint err';
        }
        btnCopy.style.display = '';
        btnSave.style.display = '';
      }
    } else if (msg.kind === 'error') {
      elBody.classList.remove('loading');
      elProgress.style.display = 'none';
      showError(msg.code, msg.message);
    }
  }

  function showError(code, message) {
    const map = {
      'no-app': ['请先打开知萤主应用', '主应用运行后，扩展才能调用本机 AI 能力。'],
      'no-token': ['还没有配对', '请在扩展设置页粘贴配对令牌（主应用 → 设置 → 浏览器扩展）。'],
      'bad-token': ['令牌已失效', '主应用的令牌已更新，请到设置页重新复制粘贴。'],
      'timeout': ['处理超时，请重试', '模型响应太慢。长文可换短一点的选区再试。'],
    };
    const [title, tip] = map[code] || ['出错了', message || '请稍后重试'];
    elBody.textContent = title + (tip ? ('\n\n' + tip) : '');
    elHint.textContent = (code === 'no-token' || code === 'bad-token') ? '' : '';
    if (code === 'no-token' || code === 'bad-token') {
      elHint.textContent = '';
      btnCopy.style.display = 'none';
      btnSave.style.display = 'none';
      // 引导按钮复用「复制」位，直接开一个去配对
      const go = document.createElement('button');
      go.textContent = '去配对';
      go.className = 'primary';
      go.style.marginLeft = 'auto';
      go.addEventListener('click', () => {
        chrome.runtime.sendMessage({ kind: 'open-options' });
        go.remove();
      }, { once: true });
      panel.querySelector('.foot').appendChild(go);
      setTimeout(() => go.remove(), 15000);
    }
  }

  function startAction(action, payload) {
    hideToolbar();
    const titles = { explain: '解释', summary: '总结', save: '存知识库' };
    showPanel('知萤 · ' + (titles[action] || ''), payload.text || payload.title || '');
    currentReqId = ++reqSeq;
    currentAction = action;
    currentPayload = payload;
    resultText = '';
    ensurePort().postMessage({
      kind: 'request', reqId: currentReqId, action,
      payload: {
        text: payload.text || '', context: payload.context || '',
        url: location.href, title: document.title || '',
      },
    });
  }

  // 浮窗里的「存知识库」：把 AI 结果 + 原文摘录一起存（S03 的 MVP 形态）
  btnSave.addEventListener('click', () => {
    if (!currentPayload) return;
    const quote = (currentPayload.text || '').slice(0, 500);
    const md = '## AI ' + elTitle.textContent.replace('知萤 · ', '') + '结果\n\n' + resultText +
      '\n\n---\n\n## 原文摘录\n\n' + quote;
    startAction('save', { text: md, context: '' });
  });

  // ---------- 工具栏点击 ----------
  toolbar.addEventListener('mousedown', (e) => e.preventDefault());   // 防止点按钮丢选区
  toolbar.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn || !lastSel) return;
    startAction(btn.dataset.act, lastSel);
  });

  // ---------- 整页正文抽取 ----------
  function extractArticle() {
    try {
      if (typeof Readability === 'function') {
        const doc = document.cloneNode(true);
        const art = new Readability(doc).parse();
        if (art && art.textContent && art.textContent.trim().length >= 200) {
          return { title: art.title || document.title || '', text: art.textContent.trim() };
        }
      }
    } catch (e) { /* 落到兜底 */ }
    // 兜底：article/main 优先，否则 body（可能含导航噪声，MVP 接受）
    const node = document.querySelector('article') || document.querySelector('main') || document.body;
    return { title: document.title || '', text: ((node && node.innerText) || '').trim() };
  }

  // ---------- background 转发来的触发（右键菜单 / Alt+B） ----------
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || msg.kind !== 'trigger') return false;
    if (msg.action === 'save-page') {
      const art = extractArticle();
      if (!art.text || art.text.length < 50) {
        showToast('没抽到正文，建议划词后点「存知识库」');
        return false;
      }
      startAction('save', { text: art.text.slice(0, 190000), context: '', title: art.title });
      return false;
    }
    const s = grabSelection() || (msg.selectionText ? { text: msg.selectionText.trim().slice(0, 2000), context: '' } : lastSel);
    if (!s || !s.text) { showToast('请先选中一段文字'); return false; }
    lastSel = s;
    startAction(msg.action, s);
    return false;
  });
})();
