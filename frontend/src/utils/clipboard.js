// 复制到剪贴板 —— **全项目唯一实现**。
//
// ⚠️ 为什么单独抽一个 utils：
//    之前 ChatView / PodcastView / StudyView 各写了一份内联实现，形态已经漂移
//    （有的只有 clipboard API、有的带 execCommand 兜底、有的连失败都不管）。
//    这里的三个坑一个都不能省：
//      ① `navigator.clipboard` 在**非安全上下文**下不存在 —— 应用内浏览器里
//         侧栏是 `http://127.0.0.1:端口`（localhost 算安全上下文，通常可用），
//         但一旦将来换到局域网 IP 访问（http://192.168.x.x）就**直接 undefined**，
//         不是 reject 而是 TypeError → 必须 try 包住并兜底。
//      ② QtWebEngine 下剪贴板可能被权限策略拒绝（reject）。
//      ③ `execCommand('copy')` 需要元素**真的在文档里**并且被选中 ——
//         临时 textarea 忘记 append/remove 会在某些内核上静默失败。
//
// ⚠️ 返回值语义：`true` = 已复制；`false` = **两条路都失败**（调用方要如实提示，
//    不要一律弹「已复制」—— 那会让用户以为复制成功了，粘贴时才发现是空的）。
export async function copyText(text) {
  const s = String(text == null ? '' : text)
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(s)
      return true
    }
  } catch {
    // 落到下面的 execCommand 兜底
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = s
    // 避开 fixed 定位与聚焦滚动：放在视口外但不 display:none（后者无法选中）
    ta.setAttribute('readonly', 'readonly')
    ta.style.position = 'fixed'
    ta.style.top = '-1000px'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return !!ok
  } catch {
    return false
  }
}
