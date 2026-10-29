// 回车发送的统一判定（2026-10-29）
//
// ⚠️ 为什么必须单独做：中文输入法「选词回车」同样会派发 keydown Enter，而
//    · Vue 的 @keydown.enter 不看 event.isComposing；
//    · `.exact` 只排除 Shift/Ctrl/Alt/Meta，也不排除组词；
//    · `.prevent` 更糟 —— 组词确认时被 preventDefault，候选词上不了屏，
//      同时还把半句话发送出去。实测症状：打「政治学」按回车选词 = 发出「政治」。
//
// keyCode === 229 是旧 WebKit / 部分安卓输入法的兜底：组词中的 keydown 在那些实现里
// isComposing 可能为 false，但 keyCode 恒为 229。
export function isEnterSend(e) {
  if (!e || e.key !== 'Enter') return false
  if (e.isComposing || e.keyCode === 229) return false
  if (e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return false
  return true
}

// 模板里用 @keydown.enter="onEnterSend"，setup 里 const onEnterSend = enterToSend(send)
// ⚠️ preventDefault 只在「确定要发送」时调用：组词回车不能拦，textarea 换行也不能拦。
export function enterToSend(fn) {
  return (e) => {
    if (!isEnterSend(e)) return
    e.preventDefault()
    fn()
  }
}
