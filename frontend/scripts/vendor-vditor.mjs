/**
 * 把 vditor 的运行时资源从 node_modules 复制到 public/vditor/，
 * 使编辑器**不依赖公网 CDN**。
 *
 * ## 为什么需要这个脚本
 *
 * `EditorView.vue` 里 `new Vditor('vditor', {...})` **没有传 `cdn`**，
 * 于是 vditor 用默认值 `Constants.CDN = "https://unpkg.com/vditor@4.0.0"`，
 * 并在**运行时**动态 `<script>` 加载这些资源：
 *
 *   {cdn}/dist/js/lute/lute.min.js        ← 3.57 MB，编辑器内核，**必需**
 *   {cdn}/dist/js/i18n/{lang}.js          ← 语言包，**必需**
 *   {cdn}/dist/js/icons/{icon}.js         ← 工具栏图标，**必需**
 *   {cdn}/dist/js/highlight.js/…          ← 代码高亮（按需，含 254 个语言文件）
 *   {cdn}/dist/js/katex|mathjax|mermaid…  ← 公式/图表（按需）
 *   {cdn}/dist/css/content-theme/…        ← 预览主题（按需）
 *   {cdn}/dist/images/emoji/…             ← emoji（按需）
 *
 * 对一个**本地桌面应用**来说这是错的：离线 / 弱网 / unpkg 不可达时，
 * 新增文档页的编辑器会**失效**（工具栏不出来、正文读不到）。
 *
 * ## 为什么必须**全量**复制，而不是只挑 lute/i18n/icons
 *
 * `vditor/dist/index.js` 的 `addScript()` 在 `script.onerror` 时 `reject(event)`，
 * 而所有调用点都是裸 `.then(...)` —— **没有任何 `.catch()`**。
 * 少放一个文件 = 那次加载产生**未捕获的 Promise rejection**。
 * 虽然只在「用户真的用到该语法」时才触发，但那时故障现场离原因很远，极难定位。
 * 21.9 MB 的磁盘代价换「零遗漏风险」，对一个本地应用是划算的。
 *
 * ## 为什么落在 public/ 而不是 dist/
 *
 * `public/` 是 Vite 的静态资源目录：
 *   - `npm run dev`  → dev server 直接 serve `/vditor/...`
 *   - `npm run build` → 原样复制到 `dist/vditor/...`
 * 两条路径都与后端 `main.py` 的 SPA catch-all 兼容（它会把存在的文件直接返回）。
 *
 * ⚠️ 产物目录 `frontend/public/vditor/` 已加入 `.gitignore`（21.9 MB 不该进仓库）。
 *    本脚本在 `predev` / `prebuild` 自动执行，所以**永远不会缺**。
 */
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const FRONTEND = join(HERE, '..')
const SRC = join(FRONTEND, 'node_modules', 'vditor', 'dist')
const DEST = join(FRONTEND, 'public', 'vditor', 'dist')
const STAMP = join(FRONTEND, 'public', 'vditor', '.version')

/** 读 vditor 实际安装版本（用来判断要不要重拷） */
function installedVersion() {
  const pkg = join(FRONTEND, 'node_modules', 'vditor', 'package.json')
  if (!existsSync(pkg)) return null
  try {
    return JSON.parse(readFileSync(pkg, 'utf8')).version || null
  } catch {
    return null
  }
}

/** 统计目录里的文件数（用来校验复制完整性，别只看「目录存在」） */
function countFiles(dir) {
  let n = 0
  const walk = (d) => {
    for (const e of readdirSync(d, { withFileTypes: true })) {
      if (e.isDirectory()) walk(join(d, e.name))
      else n++
    }
  }
  try {
    walk(dir)
  } catch {
    return -1
  }
  return n
}

/**
 * 递归复制目录。
 *
 * ⚠️ 不用 `fs.cpSync`（Node 18+ 的 native 实现）：在 Windows 上复制 vditor/dist
 *    这 530 个文件时会抛 `EIO, Access is denied`（实测），而逐个 `copyFileSync` 全部成功。
 *    手写递归还有个好处：**能报出是哪个文件失败**，而不是只给一个目录级错误。
 */
function copyTree(src, dest) {
  mkdirSync(dest, { recursive: true })
  let ok = 0
  const failed = []
  for (const e of readdirSync(src, { withFileTypes: true })) {
    const sp = join(src, e.name)
    const dp = join(dest, e.name)
    if (e.isDirectory()) {
      const r = copyTree(sp, dp)
      ok += r.ok
      failed.push(...r.failed)
    } else {
      try {
        copyFileSync(sp, dp)
        ok++
      } catch (err) {
        failed.push(`${sp} :: ${err.code || err.message}`)
      }
    }
  }
  return { ok, failed }
}

function main() {
  if (!existsSync(SRC)) {
    // ⚠️ 大声失败：静默跳过 = 产出一个「编辑器依赖公网」的包，而构建日志里毫无痕迹
    console.error('[vendor-vditor] ✗ 找不到 %s —— 先 npm install', SRC)
    process.exit(1)
  }

  const ver = installedVersion()
  if (ver && existsSync(STAMP) && existsSync(DEST)) {
    let stamped = ''
    try {
      stamped = readFileSync(STAMP, 'utf8').trim()
    } catch {
      /* 读不到就当作需要重拷 */
    }
    if (stamped === ver) {
      console.log('[vendor-vditor] 已是 vditor@%s，跳过复制', ver)
      return
    }
  }

  // 先删后拷：避免旧版本残留文件被一起带进产物
  if (existsSync(DEST)) rmSync(DEST, { recursive: true, force: true })
  const { ok, failed } = copyTree(SRC, DEST)
  if (failed.length) {
    console.error('[vendor-vditor] ✗ %d 个文件复制失败：', failed.length)
    for (const f of failed.slice(0, 8)) console.error('    %s', f)
    process.exit(1)
  }

  const n = countFiles(DEST)
  if (n <= 0 || n !== ok) {
    console.error('[vendor-vditor] ✗ 复制后文件数为 %d（拷贝成功 %d）—— 复制不完整', n, ok)
    process.exit(1)
  }

  // 关键资源存在性：缺任何一个都会在运行时产生未捕获 rejection
  const must = [
    'js/lute/lute.min.js',
    'js/i18n/zh_CN.js',
    'js/icons/ant.js',
    'css/content-theme/light.css',
  ]
  const missing = must.filter((f) => !existsSync(join(DEST, f)))
  if (missing.length) {
    console.error('[vendor-vditor] ✗ 关键资源缺失：%s', missing.join(', '))
    process.exit(1)
  }

  mkdirSync(dirname(STAMP), { recursive: true })
  writeFileSync(STAMP, (ver || 'unknown') + '\n', 'utf8')
  console.log('[vendor-vditor] ✓ vditor@%s → public/vditor/dist（%d 个文件）', ver || '?', n)
}

main()
