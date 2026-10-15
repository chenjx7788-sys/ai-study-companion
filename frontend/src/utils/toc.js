/**
 * 学习页「目录（TOC）」定位的纯函数层。
 *
 * 为什么单独成模块：同一份目录要被**三条路径**驱动 —— 文本视图按 DOM 章节锚点滚动、
 * PDF 原文视图按「当前页」、EPUB 原文视图按「当前章」。三者最终都要落成
 * 「页(章)号 → 目录条目」这一个换算，实现只留一份，避免各自漂移。
 * ⚠️ 归一化口径也必须共用：section_path 常含 \xa0（不间断空格）与换行，
 *    与 DOM 文本直接精确比较会匹配不上（原文视图里表现为「高亮永远不出现」）。
 */

/** 归一化：连续空白（含 \xa0 之外的一切 \s）压成单空格并去首尾 */
export const normPath = (t) => (t || '').replace(/\s+/g, ' ').trim()

/**
 * 按「当前页(章)号」反查所属目录条目的 key。
 *
 * @param {Array} secs 目录条目 [{ path, page, key }]，page = 该章节首现页(章)号，
 *                     **必须按 page 升序**（sections 由按页升序的正文块产出）
 * @param {number} page 当前页(章)号
 * @returns {string} 命中条目的 key；当前页早于第一条目录时返回 ''（首屏前不硬套第一章）
 *
 * 同一页有多个条目（如「第1章」与其下的「1.1」首现同页）取**最后一条**：嵌套更深的更贴近读者位置。
 */
export function sectionKeyForPage(secs, page) {
  const p = Number(page) || 0
  if (!p || !Array.isArray(secs) || !secs.length) return ''
  let lo = 0
  let hi = secs.length - 1
  let hit = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if ((Number(secs[mid].page) || 0) <= p) { hit = mid; lo = mid + 1 } else hi = mid - 1
  }
  return hit < 0 ? '' : (secs[hit].key || '')
}
