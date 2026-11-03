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

// blurb：拟人气质签名（2026-10-30 形象区拟人化）。只描述气质观感，不承诺回答行为 ——
// 真正的人格口径由应用的 persona 字段决定，两边不打架。
export const SPIRIT_OPTIONS = [
  { id: 'flame', label: '元气丹灵', blurb: '炉里窜出的小火苗，热络又有干劲' },
  { id: 'book', label: '书卷丹灵', blurb: '手不释卷的伴读，安安静静把书读透' },
  { id: 'halo', label: '沉稳丹灵', blurb: '头顶光环的老先生，不慌不忙有分寸' },
  { id: 'sprout', label: '嫩芽丹灵', blurb: '刚破土的学徒，睁大眼睛好奇打量' },
  { id: 'star', label: '启明丹灵', blurb: '提着星灯的引路人，目光清亮' },
  { id: 'horn', label: '犄角丹灵', blurb: '长角的小思辨家，有点倔有点较真' },
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

// 选择器预览用：全身 + 品牌紫底（选的是气质不是配色，配色由名字哈希定）
export function decoPreviewSVG(deco, size = 44) {
  return spiritFullSVG(`g:${deco}:0`, size, 'normal')
}

// 点击互动文案池（B2，2026-10-31）：点舞台卡丹灵冒一句人格化短语。
// 纯本地、不烧 LLM —— 互动彩蛋的打扰度红线是「零成本零等待」。
// 文案贴气质签名（见 SPIRIT_OPTIONS.blurb），不承诺回答行为。
// 双池（2026-10-31 二轮）：base = 常驻；close = 默契/灵犀（stage≥2）才解锁的熟络语气 ——
// 成长感不止在徽章上，连它说话的口气都跟着变。
export const SPIRIT_QUIPS = {
  book: {
    base: ['这段我又读了一遍', '你翻到哪儿了？', '这个问题提得好', '慢慢来，一页一页读', '嗯，我在呢'],
    close: ['这本书我们快啃透了', '你问得越来越深了', '下一本读什么？'],
  },
  flame: {
    base: ['来来来，接着聊！', '这题有意思！', '炉火正旺呢', '再来一题？', '今天干劲满满！'],
    close: ['跟你聊越来越带劲！', '咱俩配合无敌', '火候刚刚好！'],
  },
  halo: {
    base: ['不急，想清楚再问', '嗯，有道理', '一步一步来', '此事尚可商榷', '喝口茶，慢慢聊'],
    close: ['善哉，渐入佳境', '你已有自己的判断了', '比初见时稳多了'],
  },
  sprout: {
    base: ['哇，这个我也想学', '又长了一点新知识', '真的吗真的吗？', '再给我讲讲嘛', '今天也要努力发芽'],
    close: ['我好像长大了一点', '我们现在很有默契！', '下次聊点新花样？'],
  },
  star: {
    base: ['别怕，往这边走', '夜再深，灯也亮着', '你已经在路上了', '这个问题问得好', '跟着我，不会迷路'],
    close: ['这条路我们走过好几回了', '你不用灯也找得到了', '星火已成炬'],
  },
  horn: {
    base: ['我不服，再辩一轮', '你确定吗？', '这个逻辑站得住吗', '哼，有点意思', '让我较个真'],
    close: ['你的逻辑越来越硬了', '行，这轮算你赢', '再辩三百回合！'],
  },
}

// 混色助手（2026-10-30 立体化）：amt>0 往白混、amt<0 往黑混，输入 #rrggbb。
function shade(hex, amt) {
  const n = parseInt(hex.slice(1), 16)
  const t = amt > 0 ? 255 : 0
  const p = Math.abs(amt)
  const mix = c => Math.round(c + (t - c) * p)
  const r = mix((n >> 16) & 255), g = mix((n >> 8) & 255), b = mix(n & 255)
  return `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1)}`
}

// grad：装饰填色走渐变（上浅下深）；hi = shade(edge,.45)，描边仍用 edge 纯色。
// 每个装饰带一笔高光（搪胶釉面感，2026-10-30 v3）。
function decoPath(deco, edge, grad, hi) {
  switch (deco) {
    case 'flame':
      return `<path d="M32 3c3.2 4 6 6.4 6 10.4a6 6 0 1 1-12 0C26 9.4 28.8 7 32 3z" fill="${grad}"/>`
        + `<ellipse cx="29" cy="8" rx="1.6" ry="2.6" fill="#fff" opacity=".55" transform="rotate(-24 29 8)"/>`
    case 'halo':
      return `<ellipse cx="32" cy="9" rx="10" ry="3.4" fill="none" stroke="${edge}" stroke-width="2"/>`
        + `<path d="M24.5 7.2A10 3.4 0 0 1 36 5.9" fill="none" stroke="#fff" stroke-width="1.1" opacity=".8" stroke-linecap="round"/>`
    case 'sprout':
      return `<path d="M32 15V9" stroke="${edge}" stroke-width="1.8" stroke-linecap="round"/>`
        + `<path d="M31 10C26 9 24 5 24.5 2.5 29 3 31.5 6.5 31 10z" fill="${grad}"/>`
        + `<path d="M33 10C38 9 40 5 39.5 2.5 35 3 32.5 6.5 33 10z" fill="${grad}"/>`
        + `<path d="M29.4 6.6C27.6 5.2 26.6 3.9 26.3 3.2" stroke="#fff" stroke-width=".9" opacity=".6" fill="none" stroke-linecap="round"/>`
    case 'star':
      return `<path d="M32 2l2.3 4.8 5.2.7-3.8 3.6 1 5.1-4.7-2.6-4.7 2.6 1-5.1-3.8-3.6 5.2-.7z" fill="${grad}"/>`
        + `<circle cx="30" cy="5.4" r="1.1" fill="#fff" opacity=".7"/>`
    case 'horn':
      return `<path d="M32 2.5L37.5 14Q32 17.5 26.5 14z" fill="${grad}"/>`
        + `<path d="M30.2 4.2L28.4 12.6" stroke="#fff" stroke-width="1.1" opacity=".55" stroke-linecap="round"/>`
    default: // book 书卷
      return `<g transform="rotate(-8 32 10)"><rect x="22" y="3" width="20" height="12" rx="2.5" fill="#fff" stroke="${edge}" stroke-width="1.4"/><line x1="32" y1="4.5" x2="32" y2="13.5" stroke="${edge}" stroke-width="1.2"/>`
        + `<path d="M23.5 4.5h7" stroke="${edge}" stroke-width="1" opacity=".4" stroke-linecap="round"/>`
        + `<rect x="23.4" y="4.4" width="6.6" height="2.4" rx="1.2" fill="${hi}" opacity=".35"/></g>`
  }
}

// 脸型四态。⚠️ 脸路径（眼形/嘴/汗滴）是情绪判定锚点（happy=M26 45q6 6 / miss=M28 47.5 / normal=M27 45.5 / think 嘴圈 cy=46.5），禁改。
// 圆眼（normal/think）带高光点 = 搪胶神采；闭眼（happy/miss）无反光点，符合物理。
function faceParts(mood) {
  // 大眼 + 双高光点（主亮斑 + 下方次反光）= 搪胶公仔的神采；闭眼（happy/miss）无反光点，符合物理
  const eyeDot = (x, y) => `<circle cx="${x + 0.95}" cy="${y - 0.95}" r=".95" fill="#fff" opacity=".92"/><circle cx="${x - 0.8}" cy="${y + 0.7}" r=".45" fill="#fff" opacity=".65"/>`
  if (mood === 'miss') {
    return {
      eyes: '<path d="M21.5 34.5l4 3.2-4 3.2" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M42.5 34.5l-4 3.2 4 3.2" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
      mouth: '<path d="M28 47.5q4-3 8 0" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round"/>',
      extra: '<path d="M47 28c1.6 2.2 2.4 3.4 2.4 4.8a2.6 2.6 0 1 1-5.2 0c0-1.4.8-2.6 2.8-4.8z" fill="#85b7eb"/>',
    }
  }
  if (mood === 'happy') {
    return {
      eyes: '<path d="M22 38q3-3.4 6 0" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round"/><path d="M36 38q3-3.4 6 0" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round"/>',
      mouth: '<path d="M26 45q6 6 12 0" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round"/>',
      extra: '',
    }
  }
  if (mood === 'think') {
    return {
      eyes: `<circle cx="25" cy="37" r="2.7" fill="#3a3630"/>${eyeDot(25, 37)}<circle cx="39" cy="37" r="2.7" fill="#3a3630"/>${eyeDot(39, 37)}`,
      mouth: '<circle cx="32" cy="46.5" r="2" fill="none" stroke="#3a3630" stroke-width="1.6"/>',
      extra: '',
    }
  }
  return {
    eyes: `<circle cx="25" cy="38" r="2.7" fill="#3a3630"/>${eyeDot(25, 38)}<circle cx="39" cy="38" r="2.7" fill="#3a3630"/>${eyeDot(39, 38)}`,
    mouth: '<path d="M27 45.5q5 4 10 0" fill="none" stroke="#3a3630" stroke-width="1.7" stroke-linecap="round"/>',
    extra: '',
  }
}

// 光影配色派生（搪胶/黏土质感，参考盲盒公仔）
function spiritColors(pal) {
  return { bodyHi: shade(pal.body, .62), bodyLo: shade(pal.body, -.18), bodyDeep: shade(pal.body, -.32), decoHi: shade(pal.edge, .45) }
}

// 渐变/滤镜 defs。id 由令牌+mood 确定性派生：同令牌→同 def→重复 id 无害，且 v-html 稳定不抖动。
function spiritDefs(u, pal, c) {
  return `<defs>`
    + `<radialGradient id="${u}-b" cx="37%" cy="24%" r="85%">`
    + `<stop offset="0%" stop-color="${c.bodyHi}"/><stop offset="46%" stop-color="${pal.body}"/><stop offset="100%" stop-color="${c.bodyLo}"/>`
    + `</radialGradient>`
    + `<linearGradient id="${u}-d" x1="0" y1="0" x2="0" y2="1">`
    + `<stop offset="0%" stop-color="${c.decoHi}"/><stop offset="100%" stop-color="${pal.edge}"/>`
    + `</linearGradient>`
    + `<radialGradient id="${u}-s" cx="50%" cy="50%" r="50%">`
    + `<stop offset="0%" stop-color="#fff" stop-opacity=".9"/><stop offset="100%" stop-color="#fff" stop-opacity="0"/>`
    + `</radialGradient>`
    + `<radialGradient id="${u}-t" cx="34%" cy="20%" r="88%">`
    + `<stop offset="0%" stop-color="${c.bodyHi}"/><stop offset="42%" stop-color="${pal.body}"/><stop offset="100%" stop-color="${shade(pal.body, -.26)}"/>`
    + `</radialGradient>`
    + `<linearGradient id="${u}-a" x1="0" y1="0" x2="1" y2="0">`
    + `<stop offset="0%" stop-color="${c.bodyHi}"/><stop offset="55%" stop-color="${pal.body}"/><stop offset="100%" stop-color="${c.bodyLo}"/>`
    + `</linearGradient>`
    + `<radialGradient id="${u}-g" cx="35%" cy="30%" r="80%">`
    + `<stop offset="0%" stop-color="#fff"/><stop offset="100%" stop-color="${shade(pal.body, -.08)}"/>`
    + `</radialGradient>`
    + `<filter id="${u}-f" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="1.5"/></filter>`
    + `</defs>`
}

// 头部（头版与全身版共用同一份坐标，脸锚点两边逐字节一致）
// v3 结构（参考盲盒公仔）：脸 = 凹陷的浅色嵌板（face inset），不是画在球面上 ——
// 嵌板 + 顶部内阴影是「饱满立体」的主要结构来源；釉面高光压在嵌板之上（反射不受凹陷影响）。
function headMarkup(u, pal, deco, c, fp) {
  const face = shade(pal.body, .72)
  const faceLo = shade(pal.body, -.3)
  return decoPath(deco, pal.edge, `url(#${u}-d)`, c.decoHi)
    + `<ellipse cx="32" cy="40" rx="19" ry="17" fill="url(#${u}-b)" stroke="${pal.edge}" stroke-width="2.2"/>`
    + `<path d="M45.9 47A16 14 0 0 1 18.1 47" fill="none" stroke="${c.bodyDeep}" stroke-width="4.2" opacity=".5" stroke-linecap="round" filter="url(#${u}-f)"/>`
    + `<path d="M18.5 33.5A16 14 0 0 1 45.5 33.5" fill="none" stroke="#fff" stroke-width="3.2" opacity=".5" stroke-linecap="round" filter="url(#${u}-f)"/>`
    + `<ellipse cx="32" cy="41" rx="14" ry="12.5" fill="${face}" stroke="${shade(pal.body, -.12)}" stroke-width="1"/>`
    + `<path d="M20.7 35.3A13 11.5 0 0 1 43.3 35.3" fill="none" stroke="${faceLo}" stroke-width="2.2" opacity=".45" stroke-linecap="round" filter="url(#${u}-f)"/>`
    + `<ellipse cx="24.5" cy="29.5" rx="8" ry="5.4" fill="url(#${u}-s)" transform="rotate(-22 24.5 29.5)"/>`
    + `<ellipse cx="23.5" cy="28.3" rx="2.9" ry="1.9" fill="#fff" opacity=".85" transform="rotate(-22 23.5 28.3)"/>`
    + `<path d="M47.4 43.6A16 14 0 0 1 42.4 50.7" fill="none" stroke="${c.bodyHi}" stroke-width="1.6" opacity=".6" stroke-linecap="round"/>`
    + '<circle cx="20.5" cy="42" r="3" fill="rgba(240,130,130,.42)"/><circle cx="43.5" cy="42" r="3" fill="rgba(240,130,130,.42)"/>'
    + `<g class="sp-eyes">${fp.eyes}</g>` + fp.mouth + fp.extra   // sp-eyes：舞台卡眨眼动画锚点（纯包装，无视觉变化）
}

// 小身体（y54-86，叠在头下面：腿→靴→臂→躯干→躯干光影→手套，头后画盖住肩线）
// v2 防呆（2026-10-30 二轮）：躯干专属渐变 -t（对比强于头）+ 颔下模糊投影（头身一体感的关键）
// + 手臂柱面渐变 -a 且微不对称（左张右收）；手套径向渐变 -g 带高光点；靴复用装饰纵渐变 -d 带顶光。
function bodyMarkup(u, pal, deco, c) {
  return ``
    // 腿（柱面渐变）
    + `<ellipse cx="27.5" cy="79" rx="3.1" ry="5.2" fill="url(#${u}-a)" stroke="${pal.edge}" stroke-width="1.3"/>`
    + `<ellipse cx="36.5" cy="79" rx="3.1" ry="5.2" fill="url(#${u}-a)" stroke="${pal.edge}" stroke-width="1.3"/>`
    // 小靴子（纵渐变 + 顶部高光弧 + 鞋底线）
    + `<ellipse cx="27.5" cy="83.6" rx="4.2" ry="2.7" fill="url(#${u}-d)" stroke="${pal.edge}" stroke-width="1.2"/>`
    + `<ellipse cx="36.5" cy="83.6" rx="4.2" ry="2.7" fill="url(#${u}-d)" stroke="${pal.edge}" stroke-width="1.2"/>`
    + `<path d="M24.6 82.2q2.9-1.3 5.8 0" fill="none" stroke="#fff" stroke-width=".9" opacity=".7" stroke-linecap="round"/>`
    + `<path d="M33.6 82.2q2.9-1.3 5.8 0" fill="none" stroke="#fff" stroke-width=".9" opacity=".7" stroke-linecap="round"/>`
    + `<path d="M23.6 84.9q3.9 1.8 7.8 0" fill="none" stroke="${pal.edge}" stroke-width="1" opacity=".5" stroke-linecap="round"/>`
    + `<path d="M32.6 84.9q3.9 1.8 7.8 0" fill="none" stroke="${pal.edge}" stroke-width="1" opacity=".5" stroke-linecap="round"/>`
    // 手臂（微不对称：左张 21° 右收 15°、高度错 1px → 破对称僵感）
    + `<ellipse cx="19.3" cy="64.3" rx="3.2" ry="6" fill="url(#${u}-a)" stroke="${pal.edge}" stroke-width="1.3" transform="rotate(21 19.3 64.3)"/>`
    + `<ellipse cx="44.8" cy="63.4" rx="3.2" ry="6" fill="url(#${u}-a)" stroke="${pal.edge}" stroke-width="1.3" transform="rotate(-15 44.8 63.4)"/>`
    // 躯干（专属强对比渐变）
    + `<ellipse cx="32" cy="68" rx="13.5" ry="14" fill="url(#${u}-t)" stroke="${pal.edge}" stroke-width="1.6"/>`
    // 颔下投影：头投在胸口上的影子 —— 头和身体「长在一起」而不是贴纸拼接的关键一笔
    + `<ellipse cx="32" cy="58.6" rx="9" ry="3.2" fill="${c.bodyDeep}" opacity=".5" filter="url(#${u}-f)"/>`
    // 底部内阴影 + 右侧 rim 轮廓光 + 左上高光（柔 + 硬双层）
    + `<path d="M41.4 74.9A11.5 12 0 0 1 22.6 74.9" fill="none" stroke="${c.bodyDeep}" stroke-width="2.4" opacity=".45" stroke-linecap="round" filter="url(#${u}-f)"/>`
    + `<path d="M43.3 63.7A12 12.6 0 0 1 38.9 78.3" fill="none" stroke="${c.bodyHi}" stroke-width="1.4" opacity=".5" stroke-linecap="round"/>`
    + `<ellipse cx="26.5" cy="61" rx="4.4" ry="2.7" fill="#fff" opacity=".5" transform="rotate(-18 26.5 61)"/>`
    + `<ellipse cx="25.8" cy="60.2" rx="1.6" ry="1" fill="#fff" opacity=".8" transform="rotate(-18 25.8 60.2)"/>`
    // 胸前徽记（参考图的徽章特征）：白底圆章 + 迷你版头顶装饰（每个气质的徽记随装饰变）
    + `<circle cx="32" cy="64.5" r="4.6" fill="#fff" stroke="${pal.edge}" stroke-width="1.2"/>`
    + `<g transform="translate(32 63.9) scale(.3) translate(-32 -9)">${decoPath(deco, pal.edge, pal.edge, c.decoHi)}</g>`
    + `<path d="M29 62.4q3-1.6 6 0" fill="none" stroke="#fff" stroke-width=".8" opacity=".8" stroke-linecap="round"/>`
    // 白手套（径向渐变 + 高光点，高度配合手臂一高一低）
    + `<circle cx="16.6" cy="70.4" r="2.6" fill="url(#${u}-g)" stroke="${pal.edge}" stroke-width="1.1"/>`
    + `<circle cx="47.4" cy="69.3" r="2.6" fill="url(#${u}-g)" stroke="${pal.edge}" stroke-width="1.1"/>`
    + `<circle cx="15.9" cy="69.7" r=".8" fill="#fff" opacity=".9"/>`
    + `<circle cx="46.7" cy="68.6" r=".8" fill="#fff" opacity=".9"/>`
}

export function spiritSVG(sp, size = 44, mood = 'normal') {
  const { deco, pal } = parseSpirit(sp)
  const u = `sg-${String(sp).replace(/[^a-z0-9]+/gi, '-')}-${mood}`
  const c = spiritColors(pal)
  return `<svg width="${size}" height="${size}" viewBox="0 0 64 64" fill="none">`
    + spiritDefs(u, pal, c)
    + headMarkup(u, pal, deco, c, faceParts(mood))
    + '</svg>'
}

// 全身版（2026-10-30）：头与 spiritSVG 同源同坐标，身体 append 在 y54-86，viewBox 64×88。
// ⚠️ 高度 = size × 1.375，调用方容器按高度换算 size（如 128px 高容器 → size 93）。
//    小尺寸头像（30px 聊天气泡、圆形裁切）仍用头版 spiritSVG —— 全身在小尺寸下挤成一团。
export function spiritFullSVG(sp, size = 96, mood = 'normal') {
  const { deco, pal } = parseSpirit(sp)
  const u = `sg-${String(sp).replace(/[^a-z0-9]+/gi, '-')}-${mood}`
  const c = spiritColors(pal)
  const h = Math.round(size * 88 / 64)
  return `<svg width="${size}" height="${h}" viewBox="0 0 64 88" fill="none">`
    + spiritDefs(u, pal, c)
    + bodyMarkup(u, pal, deco, c)
    + headMarkup(u, pal, deco, c, faceParts(mood))
    + '</svg>'
}

// 时辰问候（陪伴型细节）：开场白前缀按小时变化
export function greetByHour() {
  const h = new Date().getHours()
  return h < 5 ? '夜深了' : h < 11 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好'
}

// ================= 位图形象（2026-10-30 P2） =================
// 搪胶公仔位图取代程序 SVG 成为展示层主形象：public/spirits/{deco}-{mood}.png
// （6 气质 × 4 表情，ImageGen 生成 + 抠图）。「换一身配色」随位图化下线 ——
// 气质色改由装饰绑定（DECO_COLORS），令牌 palette 位废弃但保留兼容读取（存量令牌不动）。
// SVG 版 spiritSVG/spiritFullSVG 保留作降级兜底与导出资产，不再接入页面。
export const DECO_COLORS = {
  book: { body: '#f2eeff', edge: '#7c5cfc' },    // 书卷 = 紫罗兰
  flame: { body: '#fdeee6', edge: '#d85a30' },   // 元气 = 珊瑚橙
  halo: { body: '#e6f0fd', edge: '#3068d8' },    // 沉稳 = 黛蓝
  sprout: { body: '#e4f4ee', edge: '#0f6e56' },  // 嫩芽 = 青绿
  star: { body: '#fdf6e0', edge: '#a87b0f' },    // 启明 = 琥珀金
  horn: { body: '#fdeef4', edge: '#c2457a' },    // 犄角 = 玫粉
}
// blink = 眨眼瞬帧（P1）：不是情绪，不参与情绪状态机，只给常态下的定时闪切用
const SPIRIT_MOODS = ['normal', 'think', 'happy', 'miss', 'blink']

// 位图路径：mood 非法值静默落 normal（webp 透明底，统一 600×825 画布、同地平线）
export function spiritImg(sp, mood = 'normal') {
  const { deco } = parseSpirit(sp)
  const m = SPIRIT_MOODS.includes(mood) ? mood : 'normal'
  return `/spirits/${deco}-${m}.webp`
}

// 应用气质色（2026-10-30 改：装饰绑定，不再读 palette 位）
export function spiritTheme(sp) {
  const { deco } = parseSpirit(sp)
  return DECO_COLORS[deco] || DECO_COLORS.book
}

// 写实金铜丹炉（2026-10-29 v2，参考游戏丹炉造型）：凤翅双耳 + 兽蹄三足 + 炉腹常亮火光窗
// + 铆钉项圈 + 莲花盖钮。空状态 / 空炉位 / 揭炉仪式三处同源。
// ⚠️ furnace / furnace-lid / furnace-fire 三个类名是揭炉动画锚点（CSS :deep 选择器），勿改；
//    uid 前缀防多实例同页时渐变 id 冲突。火光窗默认 opacity .55（炉是活的），揭炉动画升到 1。
export function furnaceSVG(size, uid = 'fx') {
  const rivets = Array.from({ length: 9 }, (_, i) =>
    `<circle cx="${68 + i * 8}" cy="63" r="1.7" fill="#f0d09a"/>`).join('')
  // 凤翅三层叠羽（外扬加大，华丽感来自翼展）
  const wingR = `<path d="M136 72C152 68 166 56 168 40 163 47 157 49 151 48 155 42 155 35 150 31 142 41 138 56 132 66Z" fill="url(#${uid}lG)" stroke="#7c5a36" stroke-width="1.4"/>`
    + `<path d="M140 74C151 72 160 65 163 56 159 60 155 61 151 60 153 56 153 51 150 49 144 56 141 66 137 72Z" fill="#c8955c" stroke="#7c5a36" stroke-width="1.1"/>`
    + `<path d="M144 76C152 75 158 70 160 64 157 66 154 67 151 66 149 70 147 74 142 76Z" fill="#a87b49" stroke="#7c5a36" stroke-width="1"/>`
    + `<path d="M164 44C166 47 167 51 166 54" fill="none" stroke="#f0d09a" stroke-width="1.4" opacity=".85"/>`
  const wingL = `<path d="M64 72C48 68 34 56 32 40 37 47 43 49 49 48 45 42 45 35 50 31 58 41 62 56 68 66Z" fill="url(#${uid}lG)" stroke="#7c5a36" stroke-width="1.4"/>`
    + `<path d="M60 74C49 72 40 65 37 56 41 60 45 61 49 60 47 56 47 51 50 49 56 56 59 66 63 72Z" fill="#c8955c" stroke="#7c5a36" stroke-width="1.1"/>`
    + `<path d="M56 76C48 75 42 70 40 64 43 66 46 67 49 66 51 70 53 74 58 76Z" fill="#a87b49" stroke="#7c5a36" stroke-width="1"/>`
    + `<path d="M36 44C34 47 33 51 34 54" fill="none" stroke="#f0d09a" stroke-width="1.4" opacity=".85"/>`
  // 兽首衔足（兽首 + 蹄足刻线）
  const legL = `<circle cx="63" cy="102" r="7" fill="#8a6844" stroke="#54391f" stroke-width="1.4"/>`
    + `<circle cx="61" cy="100" r="1.4" fill="#3a2812"/><path d="M58 97c-1-2-3-3-5-3" fill="none" stroke="#54391f" stroke-width="1.4"/>`
    + `<path d="M61 107c-4 8-8 19-10 28 3 4 12 4 15 1 1-7 2-16 5-24z" fill="#6e4f33"/>`
    + `<path d="M53 129c2 2 4 3 7 3M57 124c2 2 4 3 6 3" fill="none" stroke="#54391f" stroke-width="1.2" opacity=".6"/>`
    + `<path d="M61 107c-4 8-8 19-10 28 3 4 12 4 15 1" fill="none" stroke="#c89a5e" stroke-width="1.5" opacity=".7"/>`
  const legR = `<circle cx="137" cy="102" r="7" fill="#75543a" stroke="#472f18" stroke-width="1.4"/>`
    + `<circle cx="139" cy="100" r="1.4" fill="#2e1f0e"/><path d="M142 97c1-2 3-3 5-3" fill="none" stroke="#472f18" stroke-width="1.4"/>`
    + `<path d="M139 107c4 8 8 19 10 28-3 4-12 4-15 1-1-7-2-16-5-24z" fill="#54391f"/>`
  // 如意云纹（窗两侧对称卷草，描线）
  const scrollL = `<path d="M62 92c-3-1-5-3-4-6 1-2 4-3 6-2M62 92c-2 1-4 3-3 6 1 2 4 3 6 1" fill="none" stroke="#7c5a36" stroke-width="1.5" opacity=".55" stroke-linecap="round"/>`
  const scrollR = `<path d="M138 92c3-1 5-3 4-6-1-2-4-3-6-2M138 92c2 1 4 3 3 6-1 2-4 3-6 1" fill="none" stroke="#54391f" stroke-width="1.5" opacity=".5" stroke-linecap="round"/>`
  // 窗框八向火尖（日轮芒框，华丽度来自密度）
  const spikes = Array.from({ length: 8 }, (_, i) => {
    const a = (i * 45) * Math.PI / 180
    const x = (100 + 22 * Math.sin(a)).toFixed(1)
    const y = (98 - 22 * Math.cos(a)).toFixed(1)
    return `<path d="M0-5.5L3.4 2.4H-3.4Z" fill="#d9ab66" stroke="#7c5a36" stroke-width=".8" transform="translate(${x} ${y}) rotate(${i * 45})"/>`
  }).join('')
  return `<svg class="furnace" width="${size}" height="${Math.round(size * 0.82)}" viewBox="0 0 200 160" fill="none">`
    + `<defs>`
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
    + `<linearGradient id="${uid}pG" x1="0" y1="0" x2="0" y2="1">`
    + `<stop offset="0%" stop-color="#d9ab66"/><stop offset="100%" stop-color="#8a6844"/>`
    + `</linearGradient>`
    + `<radialGradient id="${uid}wG" cx="50%" cy="50%" r="50%">`
    + `<stop offset="0%" stop-color="#ffe9a8"/><stop offset="55%" stop-color="#ffb03a"/><stop offset="100%" stop-color="#e07b00"/>`
    + `</radialGradient>`
    + `</defs>`
    + `<ellipse cx="100" cy="153" rx="66" ry="6" fill="#3a3630" opacity=".16"/>`                                    // 影
    + `<path d="M50 138Q100 128 150 138L146 146Q100 154 54 146Z" fill="url(#${uid}pG)"/>`                            // 托盘底座
    + `<path d="M54 146Q100 154 146 146L144 149Q100 157 56 149Z" fill="#54391f"/>`                                   // 托盘侧沿（厚度）
    + `<path d="M53 139Q100 130 147 139" fill="none" stroke="#f0d09a" stroke-width="1.6" opacity=".7"/>`            // 托盘沿光
    + `<path d="M95 118h10l3 22h-16z" fill="#4c3520"/>`                                                            // 后足
    + legL + legR
    + `<path d="M62 64C46 78 40 96 44 110 50 126 74 134 100 134 126 134 150 126 156 110 160 96 154 78 138 64Z" fill="url(#${uid}bG)"/>`  // 炉腹
    + `<path d="M70 74C62 84 58 96 60 108" fill="none" stroke="#f7dda0" stroke-width="4" stroke-linecap="round" opacity=".5"/>`        // 锐利高光条
    + `<path d="M147 98C150 110 146 120 134 128" fill="none" stroke="#e8c384" stroke-width="2.4" stroke-linecap="round" opacity=".45"/>` // 右下轮廓光
    + scrollL + scrollR
    + wingL + wingR
    + `<path d="M58 58h84a5 5 0 0 1 5 5v5H53v-5a5 5 0 0 1 5-5z" fill="#7c5a36"/>`                                  // 铆钉项圈
    + `<path d="M54 60h92" stroke="#eecf92" stroke-width="1.6" opacity=".8"/>${rivets}`                             // 项圈顶反光
    + `<g class="furnace-fire" opacity=".55">`                                                                     // 火光窗（常亮）
    + `<ellipse cx="100" cy="61" rx="30" ry="9" fill="url(#${uid}wG)" opacity=".7"/>`
    + spikes
    + `<circle cx="100" cy="98" r="17" fill="#54391f" stroke="#f0d09a" stroke-width="2.5"/>`
    + `<circle cx="100" cy="98" r="11.5" fill="url(#${uid}wG)"/>`
    + `<path d="M89 90a17 17 0 0 1 8-6" fill="none" stroke="#fff" stroke-width="1.8" opacity=".7"/>`
    + `</g>`
    + `<g class="furnace-lid">`                                                                                  // 盖（揭炉动画锚点）
    + `<path d="M56 58A44 22 0 0 1 144 58Z" fill="url(#${uid}lG)"/>`                                                // 下层重檐
    + `<path d="M58 52Q100 40 142 52" fill="none" stroke="#f0d09a" stroke-width="1.6" opacity=".6"/>`
    + `<path d="M72 44A30 15 0 0 1 128 44Z" fill="url(#${uid}lG)" stroke="#7c5a36" stroke-width="1.4"/>`            // 上层小穹顶
    + `<path d="M80 38Q92 32 106 32" fill="none" stroke="#f7dda0" stroke-width="1.8" stroke-linecap="round" opacity=".7"/>`  // 盖高光
    + `<path d="M100 14c3.4 4 4.6 7.4 4.6 11.4h-9.2c0-4 1.2-7.4 4.6-11.4z" fill="#c8955c" stroke="#7c5a36" stroke-width="1.2"/>`  // 宝珠尖焰
    + `<circle cx="100" cy="28" r="4.6" fill="url(#${uid}lG)" stroke="#7c5a36" stroke-width="1.4"/>`                // 宝珠座
    + `<circle cx="98.5" cy="26.2" r="1.3" fill="#fff" opacity=".9"/>`
    + `</g></svg>`
}
