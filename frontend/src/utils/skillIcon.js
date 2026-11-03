// 技能图标唯一出口：后端 icon 可能是英文 alias，也可能直接是中文单字。
// 符号图标在不同字体下会渲染成方块（原型阶段实测）→ 一律映射成中文单字。
const ICON_TEXT = { term: '译', argue: '证', law: '法', skill: '技' }

export function skillIconText(key) {
  const s = String(key || '').trim()
  if (!s) return '技'
  return ICON_TEXT[s] || ([...s].length <= 2 ? s : '技')
}
