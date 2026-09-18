"""文件名安全化：把「用户可读的标题」转成各平台都合法的文件名主干。

## 为什么必须集中一处

标题有三个互不相干的来源 —— 网页 `<title>`（剪藏）、用户输入（新建/重命名文档）、
上传文件名 —— 每处各写一遍清洗逻辑必然漂移；而漏掉任一处就在 Windows 上以
`OSError [Errno 22] Invalid argument` 收场（实测：标题含**英文双引号**即 500）。

## 为什么只清洗文件名、不改 DB 里的 title

`title` 是**展示**用的：用户就该在列表里看到 `别再被"爱因斯坦小板凳"骗了` 这种原始
写法（含引号、含冒号）。文件名只是磁盘上的标识。两者分离 → 存量数据零迁移、
界面文案零变化、排序搜索全不受影响。

## 实测记录（Windows，修复前 → 修复后）

- `别再被"爱因斯坦小板凳"骗了` → 500 `[Errno 22]` → 落盘 `别再被_爱因斯坦小板凳_骗了.md`
- `../../../../tmp/evil` → 500（**路径穿越**：拼出的路径已跑出 files_dir，只是父目录不存在才侥幸失败）
- `a/b/c`、`a\\b` → 500（当作目录分隔符）→ `a_b_c`、`a_b`
- `a<b>c|d` → 500 → `a_b_c_d`
- `章节:一*二?三` → **200，但落盘名只剩 `章节`** —— `:` 被 NTFS 当作**数据流分隔符**，
  后半截成了 ADS（肉眼看不见却真实存在）。这是最阴的一种：不报错、还"成功"了。

## 铁律

- 只对外提供 `safe_stem`（纯函数，无 IO/无 ORM，便于离线验收）。
- **转换必须是逐字符映射**（非法字符 → `_`），不做"变聪明"的替换（如把 `"` 改成
  `“”`）：文件名的变换越可预测，出问题时越容易反查。
"""
import re

__all__ = ["safe_stem", "MAX_STEM_LEN", "ILLEGAL_CHARS", "RESERVED_NAMES"]

# Windows 保留字符（Linux/macOS 允许，但统一处理 —— 否则"换个系统就坏"）。
# `:` 尤其重要：NTFS 会把它当 ADS 分隔符 → **静默截断文件名且不报错**。
ILLEGAL_CHARS = '<>:"/\\|?*'

# 控制字符：直接**删除**而不是替换成 `_` —— 它们不携带任何可读信息，
# 留成下划线只会让文件名出现莫名的空缺。`\n` 还会截断文件名、`\x00` 直接报错。
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Windows 设备名：带不带扩展名都保留（`CON.md` 在 Windows 上打不开）。
RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)

# 结尾的「点」与「空格」：Win32 会在落盘时**静默剥离** → 磁盘名与 DB 记录不一致。
_TRAILING_RE = re.compile(r"[. ]+$")

# 单文件名主干上限。开发态 files_dir 已占用 ~66 字符（`...\ai-study-companion\backend\data\files`），
# 留足 Windows 260 字符总长余量；打包态（`~/.ai-study-companion/files`）更短。
MAX_STEM_LEN = 120

_TRANS = str.maketrans({c: "_" for c in ILLEGAL_CHARS})


def safe_stem(title: str, fallback: str = "未命名") -> str:
    """把任意标题清洗成安全的文件名主干（不含扩展名）。

    步骤顺序不可换，每一步都有平台依据（见模块 docstring 的实测记录）：

    1. 删控制字符 —— 放在最前，避免它们参与后续判断
    2. 非法字符 → `_` —— 保留位置信息，用户能把磁盘名和原标题对上
    3. 去首尾空白 + 去结尾的点/空格 —— 否则 Win32 静默剥离造成名实不符
    4. 清空则回退 `fallback` —— 纯非法字符的标题（如连打三个英文引号，或纯控制字符）
       不能产出空文件名
    5. 撞保留设备名 → 加 `_` 后缀
    6. 超长截断，**截断后必须再执行一次第 3 步** —— 截断很可能正好切出一个结尾点
    """
    s = _CTRL_RE.sub("", str(title or ""))
    s = s.translate(_TRANS).strip()
    s = _TRAILING_RE.sub("", s)
    if not s:
        s = fallback
    if s.split(".")[0].upper() in RESERVED_NAMES:
        # ⚠️ 必须插在**第一个点之前**，不能追加到尾部：
        #   `con.md` 追加成 `con.md_` → 它的首个点段仍是 `con` → Windows 照样拒绝；
        #   而且「检查-再追加」会永远不满足（死循环）。
        #   插成 `con_.md` 后，最终文件名 `con_.md.<ext>` 的首个点段是 `con_` → 合法。
        head, dot, tail = s.partition(".")
        s = f"{head}_{dot}{tail}"
    if len(s) > MAX_STEM_LEN:
        s = _TRAILING_RE.sub("", s[:MAX_STEM_LEN]) or fallback
    return s
