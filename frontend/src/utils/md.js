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
  return md
}
