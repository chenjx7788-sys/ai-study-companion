/**
 * 轻量文本指纹（djb2 变体）——用于判断「派生产物是否还对得上源内容」。
 *
 * 场景：播客脚本转成笔记后，用户又改了脚本 —— 界面需要回答
 * 「这条笔记还对得上当前脚本吗」。存整篇正文做比对太占 anchor，
 * 存一个 8 位十六进制指纹即可。
 *
 * ⚠️ 只用于「等值比较」，不用于安全场景（非加密哈希，可能碰撞）。
 * ⚠️ 输入必须稳定：调用方要先把「会影响内容的字段」归一化
 *    （如脚本只取 speaker + text，不含时间戳 / 音色 —— 那些不影响笔记内容）。
 */
export function textHash(text) {
  let h = 5381
  const s = String(text ?? '')
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h + s.charCodeAt(i)) | 0
  }
  return (h >>> 0).toString(16).padStart(8, '0')
}
