// 统一的 Markdown 渲染器工厂。
//
// 存在的唯一理由：**外链必须新窗口打开**，而这条规则要写在每个 md 实例上，
// 项目里有 9 处各自 `new MarkdownIt(...)` —— 逐处复制粘贴必然漂移（已经漂移过：
// 只有 EphemeralView / PodcastView 开了 linkify，其余没开）。
//
// 为什么外链必须 target=_blank（两条都是实测结论，不是习惯）：
//   ① 浏览器里：同标签打开会直接把 SPA 导航走，返回键回不到原页面状态；
//   ② 桌面端 pywebview：`OPEN_EXTERNAL_LINKS_IN_BROWSER` 默认 True，
//      EdgeChromium 平台把「新窗口请求」交给 `webbrowser.open()`（见
//      webview/platforms/edgechromium.py:on_new_window_request）→ 系统浏览器打开；
//      若同标签打开，整个桌面应用会被导航到外站且**没有返回入口**。
//
//
// ---------- 图片走本站只读代理（2026-09-23） ----------
// ⚠️ 与上面「外链新窗口」不同，这条**是**为所有调用方统一加的兜底，理由如下（实测）：
//   微信 `mmbiz.qpic.cn` 对带**非微信域 Referer** 的请求返回一张 140x140 占位图
//   （HTTP 200，图上印着「此图片来自微信公众平台未经允许不可引用」）；
//   而浏览器从本地应用加载外链图，必然带 `Referer: http://127.0.0.1:<port>/`；
//   用 `referrerpolicy="no-referrer"` 去掉 Referer 后会换成 **HTTP 400**
//   （浏览器特征头 `Sec-Fetch-*` + 无 Referer）—— 三组浏览器矩阵实测结论一致。
//   ⇒ 浏览器侧无解，只能让**后端**去取（后端不带 `Sec-Fetch-*`，被当普通抓取放行）。
//   见 `backend/app/routers/imgproxy.py`。
// ⚠️ 代理取不到时会 302 回原 URL —— 所以这条改写**不会让原本能显示的图变不能显示**。
// ⚠️ 只改 http(s) 外链；站内地址（`/api/assets/…`，已本地化的配图）原样保留。
// ⚠️ 工厂**不设隐式默认值**：调用方传什么就是什么，避免迁移某个页面时行为被悄悄改掉。
import MarkdownIt from 'markdown-it'

export function createMd(opts = {}) {
  const md = new MarkdownIt(opts)
  // link_open 默认没有显式规则函数（走 renderToken），所以自己给一个兜底
  const base = md.renderer.rules.link_open || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))
  md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    tokens[idx].attrSet('target', '_blank')
    tokens[idx].attrSet('rel', 'noopener noreferrer')
    return base(tokens, idx, options, env, self)
  }
  // 图片：外链改走只读代理（见文件头「图片走本站只读代理」）。
  // ⚠️ markdown-it 的 `rules.image` 默认实现负责把 alt 从 children 渲染成文本，
  //    所以必须**先改 src 再调它**（自己重写一份等于把 alt/title 行为也接管了）。
  const baseImg = md.renderer.rules.image || ((t, i, o, e, self) => self.renderToken(t, i, o))
  md.renderer.rules.image = (tokens, idx, options, env, self) => {
    const at = tokens[idx].attrIndex('src')
    if (at >= 0) {
      const proxied = proxiedImageSrc(tokens[idx].attrs[at][1])
      if (proxied !== tokens[idx].attrs[at][1]) tokens[idx].attrs[at][1] = proxied
    }
    return baseImg(tokens, idx, options, env, self)
  }
  return md
}

// 只读图片代理：把外链图交给后端取回（原因见文件头注释）。
const IMG_PROXY = '/api/imgproxy?url='

function proxiedImageSrc(src) {
  return /^https?:\/\//i.test(src) ? IMG_PROXY + encodeURIComponent(src) : src
}
