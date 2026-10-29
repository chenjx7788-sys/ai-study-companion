// 丹灵形象（炼丹炉）：2026-10-27 P1 起为程序化独版形象（零外部依赖，artworks.py 同款路线）。
// 存储令牌：`g:<deco>:<palette>`（如 g:flame:2），由后端 apps.spirit 入库、随导出包走（IP 资产）。
// 兼容 P0 老值 book/spark/calm → 映射到 deco + 固定调色板。
// ⚠️ PALETTES / DECOS 与后端 apps.py 等长同序，禁增删（移位会让存量令牌变色），只可微调色值。
// mood：normal 常态 / think 思考 / happy 开心 / miss 挠头（拒答）。

export const PALETTES = [
  { body: '#e9e4ff', edge: '#7c5cfc' },  // 0 紫（品牌）
  { body: '#fdeee6', edge: '#d85a30' },  // 1 珊瑚橙
  { body: '#e4f4ee', edge: '#0f6e56' },  // 2 青绿
  { body: '#e6f0fd', edge: '#3068d8' },  // 3 蓝
  { body: '#fdeef4', edge: '#c2457a' },  // 4 粉
  { body: '#fdf6e0', edge: '#a87b0f' },  // 5 琥珀
  { body: '#eef0e4', edge: '#5c7a29' },  // 6 橄榄
  { body: '#efe9f8', edge: '#6b4fa8' },  // 7 紫罗兰
]

export const DECOS = ['book', 'flame', 'halo', 'sprout', 'star', 'horn']

export const SPIRIT_OPTIONS = [
  { id: 'book', label: '书卷丹灵' },
  { id: 'flame', label: '元气丹灵' },
  { id: 'halo', label: '沉稳丹灵' },
  { id: 'sprout', label: '嫩芽丹灵' },
  { id: 'star', label: '启明丹灵' },
  { id: 'horn', label: '犄角丹灵' },
]

const LEGACY = { book: { deco: 'book', p: 0 }, spark: { deco: 'flame', p: 1 }, calm: { deco: 'halo', p: 2 } }

export function parseSpirit(sp) {
  if (sp && sp.startsWith('g:')) {
    const [, deco, p] = sp.split(':')
    const pi = PALETTES[Number(p)] ? Number(p) : 0
    return { deco: DECOS.includes(deco) ? deco : 'book', pal: PALETTES[pi], pi }
  }
  if (DECOS.includes(sp)) return { deco: sp, pal: PALETTES[0], pi: 0 }   // 裸装饰名（表单态）
  const l = LEGACY[sp] || LEGACY.book
  return { deco: l.deco, pal: PALETTES[l.p], pi: l.p }
}

// 与后端 apps.roll_spirit 同算法（sum((i+1)*charCode)）：出炉页预览与入库真身必须一致
export function rollSpiritPreview(name, seed = 0, deco = '') {
  const s = `${name || 'app'}::${seed}`
  let h = 0
  for (let i = 0; i < s.length; i++) h += (i + 1) * s.charCodeAt(i)
  const d = DECOS.includes(deco) ? deco : DECOS[h % DECOS.length]
  return `g:${d}:${h % PALETTES.length}`
}

// 选择器预览用：装饰 + 品牌紫底（选的是气质不是配色，配色由名字哈希定）
export function decoPreviewSVG(deco, size = 44) {
  return spiritSVG(`g:${deco}:0`, size, 'normal')
}

function decoPath(deco, edge) {
  switch (deco) {
    case 'flame':
      return `<path d="M32 3c3.2 4 6 6.4 6 10.4a6 6 0 1 1-12 0C26 9.4 28.8 7 32 3z" fill="${edge}"/>`
    case 'halo':
      return `<ellipse cx="32" cy="9" rx="10" ry="3.4" fill="none" stroke="${edge}" stroke-width="2"/>`
    case 'sprout':
      return `<path d="M32 15V9" stroke="${edge}" stroke-width="1.8" stroke-linecap="round"/>`
        + `<path d="M31 10C26 9 24 5 24.5 2.5 29 3 31.5 6.5 31 10z" fill="${edge}"/>`
        + `<path d="M33 10C38 9 40 5 39.5 2.5 35 3 32.5 6.5 33 10z" fill="${edge}"/>`
    case 'star':
      return `<path d="M32 2l2.3 4.8 5.2.7-3.8 3.6 1 5.1-4.7-2.6-4.7 2.6 1-5.1-3.8-3.6 5.2-.7z" fill="${edge}"/>`
    case 'horn':
      return `<path d="M32 2.5L37.5 14Q32 17.5 26.5 14z" fill="${edge}"/>`
    default: // book 书卷
      return `<g transform="rotate(-8 32 10)"><rect x="22" y="3" width="20" height="12" rx="2.5" fill="#fff" stroke="${edge}" stroke-width="1.4"/><line x1="32" y1="4.5" x2="32" y2="13.5" stroke="${edge}" stroke-width="1.2"/></g>`
  }
}

export function spiritSVG(sp, size = 44, mood = 'normal') {
  const { deco, pal } = parseSpirit(sp)
  let eyes, mouth, extra = ''
  if (mood === 'miss') {
    eyes = '<path d="M21.5 34.5l4 3.2-4 3.2" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M42.5 34.5l-4 3.2 4 3.2" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>'
    mouth = '<path d="M28 47.5q4-3 8 0" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round"/>'
    extra = '<path d="M47 28c1.6 2.2 2.4 3.4 2.4 4.8a2.6 2.6 0 1 1-5.2 0c0-1.4.8-2.6 2.8-4.8z" fill="#85b7eb"/>'
  } else if (mood === 'happy') {
    eyes = '<path d="M22 38q3-3.4 6 0" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round"/><path d="M36 38q3-3.4 6 0" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round"/>'
    mouth = '<path d="M26 45q6 6 12 0" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round"/>'
  } else if (mood === 'think') {
    eyes = '<circle cx="25" cy="37" r="2.4" fill="#3a3630"/><circle cx="39" cy="37" r="2.4" fill="#3a3630"/>'
    mouth = '<circle cx="32" cy="46.5" r="2" fill="none" stroke="#3a3630" stroke-width="1.6"/>'
  } else {
    eyes = '<circle cx="25" cy="38" r="2.4" fill="#3a3630"/><circle cx="39" cy="38" r="2.4" fill="#3a3630"/>'
    mouth = '<path d="M27 45.5q5 4 10 0" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round"/>'
  }
  return `<svg width="${size}" height="${size}" viewBox="0 0 64 64" fill="none">`
    + decoPath(deco, pal.edge)
    + `<ellipse cx="32" cy="40" rx="19" ry="17" fill="${pal.body}" stroke="${pal.edge}" stroke-width="1.6"/>`
    + '<circle cx="20.5" cy="42" r="2.8" fill="rgba(240,150,150,.4)"/><circle cx="43.5" cy="42" r="2.8" fill="rgba(240,150,150,.4)"/>'
    + eyes + mouth + extra + '</svg>'
}

// 时辰问候（陪伴型细节）：开场白前缀按小时变化
export function greetByHour() {
  const h = new Date().getHours()
  return h < 5 ? '夜深了' : h < 11 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好'
}

// 应用气质色（2026-10-28 丹房视觉）：丹灵配色升格为页面色 ——
// 卡片气质带 / 舞台渐变 / 光晕都由它派生，每个应用的页面气质因此独版。
// 用法：:style="{ '--app-body': t.body, '--app-edge': t.edge }"，CSS 里用 var() 消费。
export function spiritTheme(sp) {
  const { pal } = parseSpirit(sp)
  return { body: pal.body, edge: pal.edge }
}

// 写实金铜丹炉（2026-10-29 v2，参考游戏丹炉造型）：凤翅双耳 + 兽蹄三足 + 炉腹常亮火光窗
// + 铆钉项圈 + 莲花盖钮。空状态 / 空炉位 / 揭炉仪式三处同源。
// ⚠️ furnace / furnace-lid / furnace-fire 三个类名是揭炉动画锚点（CSS :deep 选择器），勿改；
//    uid 前缀防多实例同页时渐变 id 冲突。火光窗默认 opacity .55（炉是活的），揭炉动画升到 1。
export function furnaceSVG(size, uid = 'fx') {
  const rivets = Array.from({ length: 9 }, (_, i) =>
    `<circle cx="${68 + i * 8}" cy="61" r="1.7" fill="#f0d09a"/>`).join('')
  return `<svg class="furnace" width="${size}" height="${Math.round(size * 0.8)}" viewBox="0 0 200 156" fill="none">`
    + `<defs>`
    // 柱状金属反射：暗边→亮带→锐利高光带→回落→暗边（强反差是金铜感的关键）
    + `<linearGradient id="${uid}bG" x1="0" y1="0" x2="1" y2="0">`
    + `<stop offset="0%" stop-color="#5e4128"/><stop offset="18%" stop-color="#a87f52"/>`
    + `<stop offset="32%" stop-color="#e8c384"/><stop offset="44%" stop-color="#f7dda0"/>`
    + `<stop offset="56%" stop-color="#c89a5e"/><stop offset="74%" stop-color="#8a6844"/>`
    + `<stop offset="100%" stop-color="#54391f"/>`
    + `</linearGradient>`
    + `<linearGradient id="${uid}lG" x1="0" y1="0" x2="0" y2="1">`
    + `<stop offset="0%" stop-color="#f0d09a"/><stop offset="35%" stop-color="#d9ab66"/>`
    + `<stop offset="70%" stop-color="#a87b49"/><stop offset="100%" stop-color="#6e4c2e"/>`
    + `</linearGradient>`
    + `<radialGradient id="${uid}wG" cx="50%" cy="50%" r="50%">`
    + `<stop offset="0%" stop-color="#ffe9a8"/><stop offset="55%" stop-color="#ffb03a"/><stop offset="100%" stop-color="#e07b00"/>`
    + `</radialGradient>`
    + `</defs>`
    + `<ellipse cx="100" cy="148" rx="64" ry="7" fill="#3a3630" opacity=".16"/>`                                    // 影
    + `<path d="M95 118h10l3 24h-16z" fill="#4c3520"/>`                                                            // 后足
    + `<path d="M64 104c-4 10-8 20-10 30 3 4 12 4 15 1 1-7 3-18 7-27z" fill="#6e4f33"/>`                            // 左前兽蹄足
    + `<path d="M64 104c-4 10-8 20-10 30 3 4 12 4 15 1" fill="none" stroke="#c89a5e" stroke-width="1.6" opacity=".7"/>`  // 足沿反光
    + `<path d="M136 104c4 10 8 20 10 30-3 4-12 4-15 1-1-7-3-18-7-27z" fill="#54391f"/>`                           // 右前兽蹄足（背光更深）
    + `<path d="M62 62C46 76 40 94 44 108 50 124 74 132 100 132 126 132 150 124 156 108 160 94 154 76 138 62Z" fill="url(#${uid}bG)"/>`  // 炉腹
    + `<path d="M70 72C62 82 58 94 60 106" fill="none" stroke="#f7dda0" stroke-width="4" stroke-linecap="round" opacity=".5"/>`        // 锐利高光条
    + `<path d="M147 96C150 108 146 118 134 126" fill="none" stroke="#e8c384" stroke-width="2.4" stroke-linecap="round" opacity=".45"/>` // 右下轮廓光
    + `<path d="M136 70C148 66 158 56 159 44 155 49 150 51 146 50 149 45 149 39 145 36 139 44 136 56 132 64Z" fill="url(#${uid}lG)" stroke="#7c5a36" stroke-width="1.4"/>`   // 右凤翅耳
    + `<path d="M64 70C52 66 42 56 41 44 45 49 50 51 54 50 51 45 51 39 55 36 61 44 64 56 68 64Z" fill="url(#${uid}lG)" stroke="#7c5a36" stroke-width="1.4"/>`                // 左凤翅耳
    + `<path d="M43 46C45 50 49 53 53 54" fill="none" stroke="#f0d09a" stroke-width="1.6" opacity=".8"/>`          // 耳尖高光
    + `<path d="M58 56h84a5 5 0 0 1 5 5v5H53v-5a5 5 0 0 1 5-5z" fill="#7c5a36"/>`                                  // 铆钉项圈
    + `<path d="M54 58h92" stroke="#eecf92" stroke-width="1.6" opacity=".8"/>${rivets}`                             // 项圈顶反光
    + `<g class="furnace-fire" opacity=".55">`                                                                     // 火光窗（常亮）
    + `<ellipse cx="100" cy="59" rx="30" ry="9" fill="url(#${uid}wG)" opacity=".7"/>`
    + `<circle cx="100" cy="96" r="17" fill="#54391f" stroke="#f0d09a" stroke-width="2.5"/>`
    + `<circle cx="100" cy="96" r="11.5" fill="url(#${uid}wG)"/>`
    + `<path d="M89 88a17 17 0 0 1 8-6" fill="none" stroke="#fff" stroke-width="1.8" opacity=".7"/>`               // 窗框高光
    + `</g>`
    + `<g class="furnace-lid">`                                                                                  // 盖（揭炉动画锚点）
    + `<path d="M56 56A44 25 0 0 1 144 56Z" fill="url(#${uid}lG)"/>`
    + `<path d="M64 45Q100 34 136 45" fill="none" stroke="#7c5a36" stroke-width="1.6" opacity=".6"/>`               // 盖面弦纹
    + `<path d="M72 42Q88 34 104 33" fill="none" stroke="#f7dda0" stroke-width="2.2" stroke-linecap="round" opacity=".7"/>`  // 盖高光
    + `<path d="M100 18c2.6 3 3.6 5.6 3.6 8.6h-7.2c0-3 1-5.6 3.6-8.6z" fill="#7c5a36"/>`                           // 莲花钮尖
    + `<circle cx="100" cy="31" r="5.6" fill="url(#${uid}lG)" stroke="#7c5a36" stroke-width="1.5"/>`                // 莲花钮座
    + `<circle cx="98.4" cy="29.2" r="1.6" fill="#f7dda0" opacity=".9"/>`                                           // 钮高光点
    + `</g></svg>`
}
