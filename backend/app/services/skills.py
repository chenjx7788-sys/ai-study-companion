"""技能注册表：扫描 / 解析 / 校验 / 缓存 / 匹配。

一个技能 = 一份单文件 `.skill.md`：`skill` 围栏包 JSON（manifest）+ 围栏外 Markdown
（instructions，即"怎么做这件事"的方法说明）。

⚠️ 零依赖解析：**不引入 PyYAML**。`requirements.txt` 实测无此依赖，且依赖全部 `>=`
未锁定，引新依赖会波及打包体积与 CI 一致性。JSON 只承载短字段、长指令放在围栏外，
因此不需要 `\\n` 转义 —— 这是选这个格式而非 YAML front-matter 的真实理由。

⚠️ 校验原则「宁可拒绝，不可半错」：任一字段不合法 → 整个文件被拒，并记录**字段级原因**，
供接口与 UI 展示。绝不做"清洗后接受"（清洗会把 `../evil` 变成 `.._evil` 这种看似合法的东西）。
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import settings
from ..core.filename import safe_stem

# 内置技能随包发布（spec 的 datas 用 glob 收 app/skills/*.skill.md）
BUILTIN_DIR = Path(__file__).resolve().parent.parent / "skills"
USER_SUBDIR = "skills"          # 用户技能：<data_dir>/skills，绝不写进 _internal

# 状态层（P2）：启停与「导入来源」都不属于技能文件本身（文件是唯一真相，但这两项是
# 本机偏好），单独存一份 JSON。放进同一目录是为了「整个 skills 目录拷走 = 状态一起走」。
# ⚠️ 文件名不能用 *.skill.md 后缀（会被 _scan_dir 当技能扫），也不能是 . 开头（Windows 上
#    某些压缩/同步工具会吞隐藏文件），取一个明确不匹配 glob 的名字。
STATE_FILE = "_skills_state.json"

# 状态文件的**完整**键集合。⚠️ 任何一处只写其中一部分（例如漏掉 semantic），
# 另一次无关操作（停用某个技能）就会把它读出来再写回去时**静默清空**。
STATE_KEYS = ("disabled", "imported")

# 语义预判（P3）：规则未命中时才调一次 LLM 分类。
# ⚠️ 默认**关**：规则命中已覆盖 3 个内置技能的主要触发词，而开着的代价是
#    「每次规则没命中的发问都多 1–2s 延迟 + 一次 token」。开关在技能页可见可改。
DEFAULT_SEMANTIC = False
SEMANTIC_KIND = "skill_match"          # 记账用的业务场景名（log_usage）
MAX_SEMANTIC_CANDIDATES = 13           # 单次语义路由的候选上限：13 个内置技能全部入池，候选越多 prompt 越贵
MAX_ALTERNATIVES = 2                   # 建议卡上最多再列 2 个备选

# ⚠️ 这段提示词正文里有花括号（JSON 示例）→ **绝不能**对它用 str.format（方案 §十二 风险 1）
SEMANTIC_PROMPT = (
    "你是技能路由器。判断用户这一轮问题是否明显适合某个技能。\n"
    "规则：\n"
    "1. 只输出一行 JSON，不要解释、不要加代码块：{\"id\": \"技能id\"} 或 {\"id\": null}\n"
    "2. 不确定就输出 null。宁可漏掉，也不要挑一个「稍微沾边」的技能。\n"
    "3. 只能从给定列表里选，不得编造 id。\n"
)

# 导出时回写的 manifest 键顺序（同时也是未知键的过滤白名单）
MANIFEST_KEYS = ("schema_version", "id", "name", "icon", "kind", "description",
                 "triggers", "priority", "citation", "capabilities", "tool_first")

# 工具能力白名单（P4）。技能只能从这张表里挑，运行时也只认这里出现过的名字。
# ⚠️ 未知能力名**忽略 + 记警告**，不拒绝整份技能：将来加了新能力，旧版本打开这份
#    技能文件不该变成「完全不可用」，那等于用一次 schema 演进废掉用户的技能。
# ⚠️ `builtin_only=True` 的能力只允许**内置**技能声明（见方案 §十：导入技能的可信度
#    不由文件自述决定）。导入一份带 save_note 的技能 → 该能力被剔除并记警告，技能本身照常可用。
CAPABILITIES = {
    "list_materials": {"label": "列出资料清单", "builtin_only": False},
    "search_kb":      {"label": "检索知识库",   "builtin_only": False},
    "read_material":  {"label": "读材料原文",   "builtin_only": False},
    "save_note":      {"label": "写入笔记",     "builtin_only": True},
}

ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,39}$")

# 与 routers/skills.py 里的固定路径同名的 id 一律拒绝：否则 `/api/skills/semantic`
# 到底指"语义开关"还是"叫 semantic 的技能"，取决于路由声明顺序 —— 这是最难查的一类故障。
RESERVED_IDS = frozenset({"semantic", "match", "import", "validate", "state", "usage"})
FENCE_RE = re.compile(r"^```skill\s*\r?\n(.*?)\r?\n```\s*\r?\n", re.S)

MAX_NAME = 20
MAX_DESC = 120
MAX_TRIGGERS = 8
MAX_TRIGGER_LEN = 20
# 指令长度的预算本质是 **token**（挂载后每轮都进 system prompt）。
# 按字符卡会误杀英文技能包：英文 ≈4 字符/token、中文 ≈1.6 字符/token ——
# 2026-10-26 起按估算 token 卡（用户拍板：2400 中文字 ≈ 1500 tokens 的口径放宽，
# 换取「整包英文技能可导入」；同日再放宽到 6000——web-design 这类重型技能（22K 字符 ≈5.4K tokens）
# 用户明确要原样导入。代价已知：挂载期间每轮多背这些 token，只能挂载 1 个的约束因此更重要）。
MAX_INSTRUCTION_TOKENS = 6000
MAX_RESIDENT = MAX_SEMANTIC_CANDIDATES
# ⚠️ 历史字段名：当前没有「每轮注入技能清单」。resident 只表示**语义建议候选**，
#    保留 MAX_RESIDENT / resident 字段是为了兼容既有 API 与前端，不再按字面叫常驻。
MAX_PRIORITY = 99

ALLOWED_KINDS = ("chat", "craft")
ALLOWED_CITATION = ("required", "optional")
SOURCE_BUILTIN, SOURCE_LOCAL, SOURCE_IMPORTED = "builtin", "local", "imported"

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")


def est_tokens(text: str) -> int:
    """粗估 token 数：CJK/全角按 1.6 字符 = 1 token，ASCII 按 4 字符 = 1，其余按 2。
    只用于预算校验（MAX_INSTRUCTION_TOKENS），不是精确 tokenizer。"""
    cjk = len(_CJK_RE.findall(text))
    ascii_ = sum(1 for ch in text if ord(ch) < 128)
    other = len(text) - cjk - ascii_
    return int(cjk / 1.6 + ascii_ / 4.0 + other / 2.0 + 0.999)


class SkillInvalid(Exception):
    """技能文件不合法。`reason` 必须能直接给人看（字段级）。"""

    status = 400                      # 路由按此给 HTTP 状态，子类可覆盖

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SkillConflict(SkillInvalid):
    """id 冲突 / 已存在（409）。"""
    status = 409


class SkillForbidden(SkillInvalid):
    """内置技能只读（403）。"""
    status = 403


class SkillMissing(SkillInvalid):
    """技能不存在（404）。"""
    status = 404


@dataclass
class Skill:
    id: str
    name: str
    icon: str
    kind: str
    description: str
    triggers: list[str]
    priority: int
    citation: str
    instructions: str
    builtin: bool
    source: str                  # 运行时定，不信文件自述
    path: str
    enabled: bool = True
    # 运行时能力（P4）：已过白名单与 builtin_only 校验后的**有效**列表。
    # 文件里写了但被剔除的，记在 warnings 里——不能既不给能力又不告诉用户为什么。
    capabilities: list[str] = field(default_factory=list)
    # 首轮必调工具（P3-①）：开启后挂载期间**每个问题的第一轮**都以 tool_choice=required
    # 强制发起一次工具调用。代价明确：连「你好」这种寒暄也会先查一次。
    # 只适合「找全/对比/校验」这类必须以材料为准的技能。
    tool_first: bool = False
    warnings: list[str] = field(default_factory=list, repr=False)
    # 文件里原始的 manifest（未被默认值填充）。导出时原样回写 → 往返保真，
    # 而不是「重建一份我认为正确的 JSON」。手工构造的 Skill 允许为空。
    manifest: dict = field(default_factory=dict, repr=False)
    # 加载期编译好，避免每次 match 重编（非法正则已在加载期拒绝）
    _compiled: list = field(default_factory=list, repr=False)

    def to_public(self, with_instructions: bool = False) -> dict:
        d = {
            "id": self.id, "name": self.name, "icon": self.icon, "kind": self.kind,
            "description": self.description, "triggers": list(self.triggers),
            "priority": self.priority, "citation": self.citation,
            "builtin": self.builtin, "source": self.source, "enabled": self.enabled,
            "capabilities": list(self.capabilities),
            "tool_first": bool(self.tool_first),
            "warnings": list(self.warnings),
        }
        if with_instructions:
            d["instructions"] = self.instructions
        return d


# ---------------------------------------------------------------- 解析与校验

def parse_skill_text(text: str, *, file_stem: str, builtin: bool, source: str,
                     path: str = "") -> Skill:
    """把一份技能文件文本解析成 Skill；不合法抛 SkillInvalid（字段级原因）。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")   # 导入文件可能是 CRLF

    m = FENCE_RE.match(text)
    if not m:
        raise SkillInvalid("不是有效的技能文件：开头缺少 ```skill 围栏（或围栏后未换行）")
    try:
        man = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise SkillInvalid(f"围栏内 JSON 解析失败：{e.msg}（第 {e.lineno} 行）")
    if not isinstance(man, dict):
        raise SkillInvalid("围栏内必须是 JSON 对象")

    instructions = text[m.end():].strip()

    ver = man.get("schema_version")
    if ver != 1:
        raise SkillInvalid(f"不支持的 schema_version: {ver!r}（当前只支持 1）")

    sid = man.get("id")
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise SkillInvalid(
            f"id 不合法: {sid!r}（需匹配 ^[a-z][a-z0-9_]{{2,39}}$，小写字母开头、仅小写/数字/下划线）")
    # 双保险：safe_stem 挡路径穿越（`../`）与 NTFS ADS（`:`）。ID_RE 已排除它们，
    # 这里仍要断言，因为将来 ID_RE 一旦放宽，这层是唯一防线。
    if safe_stem(sid) != sid:
        raise SkillInvalid(f"id 含不安全字符（safe_stem 清洗后为 {safe_stem(sid)!r}）")
    if file_stem and file_stem != sid:
        raise SkillInvalid(f"文件名与 id 不一致：文件名 {file_stem!r}，id {sid!r}")
    # 保留字：这些 id 与路由里的固定路径同名，`GET /skills/semantic` 会被
    # `GET /skills/{skill_id}` 或反之吃掉一条 —— 症状是「技能明明存在但打不开」。
    if sid in RESERVED_IDS:
        raise SkillInvalid(f"id {sid!r} 是保留字（与接口路径冲突），请换一个")

    name = man.get("name")
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= MAX_NAME):
        raise SkillInvalid(f"name 必填且 1–{MAX_NAME} 字，当前: {name!r}")

    icon = man.get("icon") or "skill"
    if not isinstance(icon, str):
        raise SkillInvalid("icon 必须是字符串")

    kind = man.get("kind") or "chat"
    if kind not in ALLOWED_KINDS:
        raise SkillInvalid(f"kind 非法: {kind!r}（允许 {list(ALLOWED_KINDS)}）")

    desc = man.get("description")
    if not isinstance(desc, str) or not (1 <= len(desc.strip()) <= MAX_DESC):
        raise SkillInvalid(f"description 必填且 1–{MAX_DESC} 字（它决定能不能被匹配上），当前长度: "
                           f"{len(desc) if isinstance(desc, str) else 'N/A'}")

    raw_triggers = man.get("triggers") or []
    if kind == "chat" and not raw_triggers:
        raise SkillInvalid("chat 技能必须声明 triggers（否则永远不会被匹配到）")
    if not isinstance(raw_triggers, list) or len(raw_triggers) > MAX_TRIGGERS:
        raise SkillInvalid(f"triggers 必须是数组且不超过 {MAX_TRIGGERS} 条")
    triggers: list[str] = []
    for t in raw_triggers:
        if not isinstance(t, str) or not t.strip():
            raise SkillInvalid(f"triggers 含空项或非字符串: {t!r}")
        if len(t) > MAX_TRIGGER_LEN:
            raise SkillInvalid(f"triggers 单条不超过 {MAX_TRIGGER_LEN} 字: {t!r}")
        # ⚠️ 必须在加载期编译：留到 match 时才编译，等于把一个坏技能变成一次 500
        try:
            re.compile(t)
        except re.error as e:
            raise SkillInvalid(f"triggers 正则无法编译: {t!r}（{e.msg}）")
        triggers.append(t)

    priority = man.get("priority", 10)
    if not isinstance(priority, int) or isinstance(priority, bool) or not (0 <= priority <= MAX_PRIORITY):
        raise SkillInvalid(f"priority 必须是 0–{MAX_PRIORITY} 的整数，当前: {priority!r}")

    citation = man.get("citation") or "optional"
    if citation not in ALLOWED_CITATION:
        raise SkillInvalid(f"citation 非法: {citation!r}（允许 {list(ALLOWED_CITATION)}）")

    # 工具能力（P4）：坏值不拒收，逐条剔除 + 记警告。
    # ⚠️ 只有「整份文件无法解析」才该拒收；能力声明是**可选增强**，
    #    因为它把一份还能用的技能变成不可用，是最差的一种严格。
    raw_caps = man.get("capabilities") or []
    caps: list[str] = []
    warnings: list[str] = []
    if not isinstance(raw_caps, list):
        warnings.append(f"capabilities 必须是数组，已忽略（当前 {type(raw_caps).__name__}）")
        raw_caps = []
    for c in raw_caps:
        if not isinstance(c, str):
            warnings.append(f"capabilities 含非字符串项，已忽略：{c!r}")
            continue
        spec = CAPABILITIES.get(c)
        if spec is None:
            warnings.append(f"未知能力「{c}」（允许：{'、'.join(CAPABILITIES)}），已忽略")
            continue
        if spec["builtin_only"] and not builtin:
            warnings.append(f"能力「{c}」仅内置技能可用，已从本技能移除"
                            f"（{spec['label']}会改动用户数据，导入技能不授予）")
            continue
        if c not in caps:
            caps.append(c)

    # 首轮必调工具（P3-①）：坏值不拒收（与 capabilities 同一原则——可选增强
    # 不该把一份还能用的技能变成不可用）。
    raw_tf = man.get("tool_first")
    tool_first = False
    if raw_tf is not None:
        if isinstance(raw_tf, bool):
            tool_first = raw_tf
        else:
            warnings.append(f"tool_first 必须是布尔值，已忽略（当前 {type(raw_tf).__name__}）")
    if tool_first and not any(c in ("list_materials", "search_kb", "read_material") for c in caps):
        # 没有读类能力却要「首轮必调」：强制模型调一个它没有的牌，第一轮必然失败。
        warnings.append("tool_first 需要至少一项读类能力（list_materials/search_kb/read_material），已忽略")
        tool_first = False

    if not instructions:
        raise SkillInvalid("instructions 为空：围栏外必须写「怎么做这件事」的方法说明")
    if est_tokens(instructions) > MAX_INSTRUCTION_TOKENS:
        raise SkillInvalid(
            f"方法说明超过预算：约 {est_tokens(instructions)} tokens（上限 {MAX_INSTRUCTION_TOKENS}，"
            f"约合中文 9600 字 / 英文 24000 字符；当前 {len(instructions)} 字符），请先精简")

    return Skill(
        id=sid, name=name.strip(), icon=icon, kind=kind, description=desc.strip(),
        triggers=triggers, priority=priority, citation=citation,
        instructions=instructions, builtin=builtin, source=source, path=path,
        capabilities=caps, warnings=warnings, tool_first=tool_first,
        manifest=copy.deepcopy(man),
        _compiled=[re.compile(t, re.I) for t in triggers],
    )


# ---------------------------------------------------------------- 状态层（P2）

def state_path() -> Path:
    return user_dir() / STATE_FILE


def _default_state() -> dict:
    return {"disabled": [], "imported": [], "semantic": DEFAULT_SEMANTIC}


def read_state() -> dict:
    """读启停 / 导入来源 / 语义开关。坏文件**不抛**，但把原因带回（页面要能看见）。

    ⚠️ 这里绝不能因为一个坏 JSON 让整个技能系统不可用——那会让用户连"哪里出错了"
       都看不到（技能列表 500，只剩一条报错）。
    """
    p = state_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _default_state()
    except (OSError, ValueError) as e:
        return {**_default_state(),
                "error": f"状态文件 {STATE_FILE} 无法解析（{e.__class__.__name__}），"
                         f"已按「全部启用」处理"}
    if not isinstance(d, dict):
        return {**_default_state(),
                "error": f"状态文件 {STATE_FILE} 不是 JSON 对象，已按「全部启用」处理"}
    out = {}
    for k in STATE_KEYS:
        v = d.get(k)
        # 只吃字符串数组，其他一律当空（防御手改）
        out[k] = [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
    # 语义开关只吃真正的 bool：手改写成 "false" 字符串时 bool("false") 是 True —— 那会让
    # 用户「明明关了还在多花 token」，比直接回落默认值更糟。
    v = d.get("semantic")
    out["semantic"] = v if isinstance(v, bool) else DEFAULT_SEMANTIC
    if d.get("error"):
        out["error"] = d["error"]
    return out


def write_state(state: dict) -> None:
    """原子写：先写 .tmp 再 os.replace，避免断电/占用留下半个 JSON。

    ⚠️ 必须把 `semantic` 一起写回。只写 disabled/imported 的话，下一次「停用某技能」
       会把用户打开的语义开关顺手关掉——而且没有任何提示。
    """
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: sorted({x for x in (state.get(k) or []) if isinstance(x, str)})
             for k in STATE_KEYS}
    clean["semantic"] = bool(state.get("semantic", DEFAULT_SEMANTIC))
    # 键顺序固定，便于人眼核对与探针逐字段比对
    clean = {"disabled": clean["disabled"], "imported": clean["imported"],
             "semantic": clean["semantic"]}
    data = (json.dumps(clean, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)


def semantic_enabled() -> bool:
    return bool(read_state().get("semantic", DEFAULT_SEMANTIC))


def set_semantic(enabled: bool) -> bool:
    """开/关语义预判。返回落盘后的真值（不信任入参，以状态文件为准）。"""
    st = read_state()
    st["semantic"] = bool(enabled)
    write_state(st)
    return semantic_enabled()


def set_enabled(skill_id: str, enabled: bool) -> None:
    # ⚠️ 必须校验存在：否则 `disabled` 里会攒下一堆不存在的 id。将来有人新建一个
    #    同名技能，它会**直接以停用状态出现**——症状是"保存成功但列表里没有"。
    if get_skill(skill_id) is None:
        raise SkillMissing(f"技能不存在：{skill_id}")
    st = read_state()
    cur = set(st["disabled"])
    if enabled:
        cur.discard(skill_id)
    else:
        cur.add(skill_id)
    st["disabled"] = sorted(cur)
    write_state(st)
    invalidate()


def set_imported(skill_id: str, imported: bool) -> None:
    st = read_state()
    cur = set(st["imported"])
    if imported:
        cur.add(skill_id)
    else:
        cur.discard(skill_id)
    st["imported"] = sorted(cur)
    write_state(st)
    invalidate()


# ---------------------------------------------------------------- 扫描与缓存

def user_dir() -> Path:
    """用户技能目录。用 settings.data_dir（支持 ASC_DATA_DIR 重定向到隔离实例）。"""
    return Path(settings.data_dir) / USER_SUBDIR


_lock = threading.Lock()
_cache: dict | None = None
_cache_fp: tuple | None = None


def _snapshot() -> tuple:
    """技能目录与状态文件的变更指纹。用户按页面指引手动放文件后，下一次列表必须看见。"""

    def dir_sig(directory) -> tuple | None:
        d = Path(directory)
        if not d.is_dir():
            return None
        sig = []
        for p in sorted(d.glob("*.skill.md")):
            try:
                st = p.stat()
                sig.append((p.name, st.st_size, st.st_mtime_ns))
            except OSError:
                # 读不到元信息也不能假装目录没变；让本次按「不可缓存」签名继续扫描。
                sig.append((p.name, -1, -1))
        return tuple(sig)

    sp = state_path()
    try:
        st = sp.stat()
        state_sig = (st.st_size, st.st_mtime_ns)
    except FileNotFoundError:
        state_sig = None
    except OSError:
        state_sig = (-1, -1)
    return (dir_sig(BUILTIN_DIR), dir_sig(user_dir()), state_sig)


def invalidate() -> None:
    global _cache, _cache_fp
    with _lock:
        _cache = None
        _cache_fp = None


def _scan_dir(directory: Path, builtin: bool, rejected: list, st: dict | None = None) -> list[Skill]:
    # ⚠️ 收 str 也要能跑：BUILTIN_DIR 是可被测试/隔离实例替换的模块级常量，
    #    探针传进来的是临时目录字符串。这里不做 Path() 转换就会在扫描时才崩。
    directory = Path(directory)
    if not directory.is_dir():
        return []
    st = st if st is not None else {"disabled": [], "imported": []}
    disabled = set(st.get("disabled") or [])
    imported = set(st.get("imported") or [])
    out: list[Skill] = []
    # 只认 *.skill.md；sort 保证同一目录内加载顺序稳定（判据可比）
    for p in sorted(directory.glob("*.skill.md")):
        # 文件名主干去掉 .skill.md 后的部分，用于与 id 比对
        stem = p.name[:-len(".skill.md")]
        # source 由**所在目录 + 状态文件**决定，绝不信文件里自述的 builtin/source
        src = SOURCE_BUILTIN if builtin else (SOURCE_IMPORTED if stem in imported else SOURCE_LOCAL)
        try:
            text = p.read_text(encoding="utf-8")
            s = parse_skill_text(text, file_stem=stem, builtin=builtin, source=src, path=str(p))
            s.enabled = stem not in disabled
            out.append(s)
        except SkillInvalid as e:
            rejected.append({"file": p.name, "dir": str(directory), "reason": e.reason})
        except OSError as e:
            rejected.append({"file": p.name, "dir": str(directory), "reason": f"读取失败：{e}"})
        except UnicodeDecodeError:
            rejected.append({"file": p.name, "dir": str(directory),
                             "reason": "不是 UTF-8 编码，无法解析"})
    return out


def load_all(force: bool = False) -> dict:
    """返回 {"skills": [Skill], "rejected": [...]}。内置优先于同名用户技能。

    ⚠️ 缓存必须感知目录外部变化：官方使用路径之一就是「手动把 .skill.md 放进目录后刷新」。
       只靠 invalidate() 会把这条路径变成「必须重启」，而且没有任何报错。
    """
    global _cache, _cache_fp
    with _lock:
        fp = _snapshot()
        if _cache is not None and _cache_fp == fp and not force:
            return _cache

        rejected: list = []
        st = read_state()
        if st.get("error"):
            # 状态坏了必须露出来：否则用户会以为「开关明明关了怎么还在」
            rejected.append({"file": STATE_FILE, "dir": str(user_dir()), "reason": st["error"]})
        builtin_skills = _scan_dir(BUILTIN_DIR, True, rejected, st)
        user_skills = _scan_dir(user_dir(), False, rejected, st)

        merged: dict[str, Skill] = {}
        for s in builtin_skills:
            merged[s.id] = s
        for s in user_skills:
            if s.id in merged:
                # 内置优先，但**不静默**：记一条拒绝，让用户知道自己的同名文件被忽略了
                rejected.append({
                    "file": Path(s.path).name, "dir": str(user_dir()),
                    "reason": f"与内置技能同 id（{s.id}），已忽略用户版本（内置优先）",
                })
                continue
            merged[s.id] = s

        # 排序：priority 降序 → id 升序（稳定、可断言）
        skills = sorted(merged.values(), key=lambda x: (-x.priority, x.id))
        _cache = {"skills": skills, "rejected": rejected}
        _cache_fp = fp
        return _cache


# ---------------------------------------------------------------- 查询

def get_skill(skill_id: str) -> Skill | None:
    for s in load_all()["skills"]:
        if s.id == skill_id:
            return s
    return None


def usage_map() -> tuple[dict, str]:
    """技能使用统计：`({skill_id: {"count": n, "last_at": "…Z"}}, error)`（P0-1 资产掌控）。

    ⚠️ `last_at` 带 `Z` 后缀：库里存的是 `datetime.utcnow()`（naive UTC），
       不带时区标记时前端 `new Date("…")` 会按**本地时间**解析，整体偏 8 小时。
    ⚠️ 延迟导入 ORM：本模块是纯文件层，顶层导入 database/models 会把两条依赖链绑死。
    ⚠️ 查不出来时返回**第二个值**而不是假装成功：表缺失（改了代码没重启）时若静默
       返回 `{}`，界面上所有技能都显示「还没用过」—— 那是一个看起来完全正常的假数据。
       宁可让接口带上 error，也不能让它安静地错。
    """
    from sqlalchemy import func

    from ..database import SessionLocal
    from ..models import SkillUse

    db = None
    try:
        db = SessionLocal()
        rows = (db.query(SkillUse.skill_id, func.count(SkillUse.id),
                         func.max(SkillUse.created_at))
                .group_by(SkillUse.skill_id).all())
    except BaseException as e:            # noqa: BLE001 — 统计失败绝不外抛
        return {}, f"使用记录暂不可读（{e.__class__.__name__}）：重启后端建表后可用"
    finally:
        if db is not None:
            try:
                db.close()
            except BaseException:
                pass
    out: dict = {}
    for sid, cnt, last in rows:
        out[str(sid)] = {"count": int(cnt or 0),
                         "last_at": (last.isoformat() + "Z") if last else ""}
    return out, ""


def list_public(limit: int = MAX_RESIDENT) -> tuple[list[dict], int]:
    """列表（不含 instructions）。返回 (items, truncated)。

    ⚠️ items 里**包含已停用的技能**（带 `enabled=false`）：只返回启用的话，用户一关掉
       就再也看不到它，等于给自己制造了一个无法回滚的开关。
    ⚠️ `limit` 是语义路由的候选上限（候选越多，单次 match 的 prompt 越贵），
       不是列表长度 → 每条附历史字段 `resident` 标出是否进入语义建议候选。
    """
    all_skills = load_all()["skills"]
    enabled = [s for s in all_skills if s.enabled]
    truncated = max(0, len(enabled) - limit)
    resident_ids = {s.id for s in enabled[:limit]}
    items = []
    for s in all_skills:
        d = s.to_public()
        d["resident"] = s.id in resident_ids
        items.append(d)
    return items, truncated


def _why(hits: list[str]) -> str:
    return "命中「" + "、".join(hits[:3]) + "」"


def _no_suggest(info: dict, skipped: str) -> dict:
    """统一的「不推荐」返回。`skipped` 让探针能分辨「关了 / 没候选 / 模型说不用 / 出错」。

    ⚠️ 这几个分支**必须是同一个返回值形状**：只要有一处偷偷塞个 suggest 进去，
       「无匹配不得硬塞 top-1」那条判据就形同虚设。
    """
    info["skipped"] = skipped
    return {"suggest": None, "matched": 0, "alternatives": [],
            "ambiguous": False, "semantic": info}


def _match_candidates() -> list[Skill]:
    """能参与自动建议的技能。

    ⚠️ `source=imported` **不参与自动建议**（方案 §十 可信度设计）：导入技能的方法说明
       来自他人，自动把它挂到用户这一问上等于让陌生指令劫持回答。只能手动挂载。
    """
    return [s for s in load_all()["skills"]
            if s.kind == "chat" and s.enabled and s.source != SOURCE_IMPORTED]


_PICK_ID_RE = re.compile(r'"id"\s*:\s*"([A-Za-z0-9_]+)"')
_PICK_NULL_RE = re.compile(r'"id"\s*:\s*(null|None|"")')


def _parse_pick(raw: str, valid: set[str]) -> str | None:
    """严格解析模型输出：只认候选里的 id，其余一律 None（不猜、不取第一个）。

    这一条是「不得硬塞 top-1」的落点：模型答非所问时，把它的答案当成「没有答案」，
    而不是从候选里随便挑一个最像的。
    """
    if not raw:
        return None
    # 先看显式 null（模型按指令说「都不适用」）
    if _PICK_NULL_RE.search(raw):
        return None
    m = _PICK_ID_RE.search(raw)
    if m:
        sid = m.group(1)
        return sid if sid in valid else None
    # 兜底：整段就是一个裸 id（不少小模型会这么回）。必须整段相等且在候选里才算。
    bare = raw.strip().strip("`").strip().strip('"').strip()
    return bare if bare in valid else None


def _semantic_pick(question: str, cands: list[Skill]) -> tuple[str | None, str | None]:
    """让对话模型从候选里挑一个。返回 (id 或 None, 失败原因 或 None)。

    ⚠️ 任何异常都降级为「没有建议」：语义预判是**可选增强**，不能因为「模型没配置」
       让用户连发问都发不出去；更不能用随机/高分兜底（那就是硬塞）。
    """
    from . import llm as llm_svc          # 局部导入：llm 体量大，且与技能层无反向依赖

    pool = cands[:MAX_SEMANTIC_CANDIDATES]
    listing = "\n".join(f"- {s.id}：{s.name} —— {s.description}" for s in pool)
    messages = [
        {"role": "system", "content": SEMANTIC_PROMPT},
        # 问题截断到 500 字：路由只需要「用户想干什么」，不需要全文
        {"role": "user", "content": f"可用技能：\n{listing}\n\n用户问题：{question[:500]}"},
    ]
    try:
        raw = llm_svc.chat(messages, kind=SEMANTIC_KIND)
    except Exception as e:                # HTTPException(NEED_SETUP) 也在内
        return None, e.__class__.__name__
    return _parse_pick(raw or "", {s.id for s in pool}), None


def match(question: str, scope: str = "all", limit: int = 1,
          semantic: bool | None = None) -> dict:
    """给出建议技能（规则优先，语义兜底）。

    三段：
    1. 规则命中 → 立刻返回（<5ms），`via=rule`，并带出被挤下去的候选
    2. 规则未命中 **且** 语义开关开 → 调一次 LLM 分类，`via=semantic`
    3. 其余（关了 / 没候选 / 模型说都不合适 / 调用失败）→ `suggest=None`

    ⚠️ **绝不硬塞 top-1**：语义分支的 id 必须真在候选里；模型说不确定、返回陌生 id、
       格式不对、调用报错，一律当作「没有建议」（方案 §十一 P3 判据 ①）。
    ⚠️ `semantic` 传 bool 可强制开/关（探针用）；传 None 则读状态文件的开关。
    ⚠️ `scope` / `limit` 是历史参数，当前不参与计算，保留以免破坏既有调用签名。
    """
    q = (question or "").strip()
    on = semantic_enabled() if semantic is None else bool(semantic)
    info = {"enabled": on, "used": False, "skipped": None}

    if not q:
        return _no_suggest(info, "empty")

    cands = _match_candidates()
    scored: list[tuple[int, Skill, list[str]]] = []
    for s in cands:
        hits = [t for pat, t in zip(s._compiled, s.triggers) if pat.search(q)]
        if hits:
            # 命中的触发词越多越可信；同分用 priority 拉开
            scored.append((len(hits) * 100 + s.priority, s, hits))

    if scored:
        scored.sort(key=lambda x: (-x[0], x[1].id))
        _, best, best_hits = scored[0]
        info["skipped"] = "rule_hit"
        # 并列：命中次数相同 → 规则其实分不出高下（priority 只是排序兜底，不代表更合适），
        # 此时必须如实告诉用户「还有同样沾边的」，而不是假装很确定。
        ambiguous = len(scored) > 1 and (scored[0][0] - scored[1][0]) < 100
        return {
            "suggest": {"id": best.id, "name": best.name,
                        "why": _why(best_hits), "via": "rule"},
            "matched": len(scored),
            "alternatives": [{"id": s.id, "name": s.name, "why": _why(h)}
                             for _, s, h in scored[1:1 + MAX_ALTERNATIVES]],
            "ambiguous": ambiguous,
            "semantic": info,
        }

    if not on:
        return _no_suggest(info, "off")
    if not cands:
        return _no_suggest(info, "no_candidates")

    info["used"] = True
    sid, err = _semantic_pick(q, cands)
    if err:
        info["error"] = err             # 给页面/探针留诊断线索，但不影响回答
        return _no_suggest(info, "error")
    if not sid:
        return _no_suggest(info, "no_pick")
    picked = next((x for x in cands if x.id == sid), None)
    if picked is None:                  # 双保险：_parse_pick 已校验过候选范围
        return _no_suggest(info, "unknown_id")
    return {
        "suggest": {"id": picked.id, "name": picked.name,
                    "why": "AI 判断这条问题适合它", "via": "semantic"},
        "matched": 0,
        "alternatives": [],
        "ambiguous": False,
        "semantic": info,
    }


# ---------------------------------------------------------------- 注入（P1）

def build_injection(skill: Skill) -> str:
    """把技能指令包成可追加到 system 的段落。

    ⚠️ 绝不用 `str.format` / `string.Template` 渲染：指令正文里可能含 `{count}`
       （`law_compare.skill.md` 就是这样），format 会抛 KeyError 或静默吞掉正文。
       本函数**只做字符串拼接**，用户文件里的花括号原样保留。
    """
    parts = [
        "【本轮启用的技能：" + skill.name + "】",
        "以下是完成本次回答应遵循的方法。与通用风格要求冲突时，以本段为准。",
        "",
        skill.instructions.strip(),
    ]
    if skill.citation == "required":
        parts += [
            "",
            "【引用要求（本技能强制）】",
            "每条结论都要标依据：来自材料的写明段落或页码，来自前文的写「前文」，"
            "属于自身推断的必须显式写「推测」。找不到依据就不要写成结论。",
        ]
    return "\n".join(parts)


# ---------------------------------------------------------------- 写操作（P2）
#
# 三个不变量，违反任一都会静默出错：
#   ① 写盘一律 `write_bytes`（文本模式会把 \n 翻成 \r\n，每存一次叠一层 CR）
#   ② 文件名 = safe_stem(id)，且**写盘前必须再用 file_stem=id 解析一遍**（权威校验），
#      否则一个 id 合法但正文不合法的文件会先落盘再报错 → 列表里多一个坏技能
#   ③ 每次写/删后 `invalidate()`，否则「改了 triggers 但 match 不变」

def render_skill(manifest: dict, instructions: str) -> str:
    """渲染成 .skill.md 文本（LF 结尾）。围栏内 JSON，围栏外方法说明。"""
    body = ("```skill\n"
            + json.dumps(manifest, ensure_ascii=False, indent=2)
            + "\n```\n\n"
            + (instructions or "").strip()
            + "\n")
    return body


def manifest_of(skill: Skill) -> dict:
    """取该技能的完整 manifest：原始键优先（保真），缺的用规范化字段补齐。

    ⚠️ `id` 强制回写为 skill.id：编辑器里 id 不可改，文件名才是真相。
    """
    man = dict(skill.manifest or {})
    man.setdefault("schema_version", 1)
    defaults = {
        "name": skill.name, "icon": skill.icon, "kind": skill.kind,
        "description": skill.description, "triggers": list(skill.triggers),
        "priority": skill.priority, "citation": skill.citation,
    }
    for k, v in defaults.items():
        man.setdefault(k, v)
    # capabilities 只在真有的时候回写：给所有技能平白补一个 "capabilities": [] 会改变
    # 导出文本（P2 的「导出→导入→导出逐字节相等」就靠不擅自加字段）
    if skill.capabilities:
        man.setdefault("capabilities", list(skill.capabilities))
    # tool_first 同理：False 是默认，不该给所有技能平白补 "tool_first": false
    if skill.tool_first:
        man.setdefault("tool_first", True)
    man["id"] = skill.id
    return man


def build_text(fields: dict) -> str:
    """由编辑器字段拼出技能文本（只带白名单键，顺序稳定）。"""
    man = {"schema_version": 1}
    for k in MANIFEST_KEYS[1:]:
        if k in fields and fields[k] is not None:
            # tool_first 是「只有开启才有意义」的开关：关掉时不写 "tool_first": false 这种噪音键
            if k == "tool_first" and not fields[k]:
                continue
            man[k] = fields[k]
    return render_skill(man, fields.get("instructions") or "")


def validate_text(text: str) -> Skill:
    """dry-run 校验：不落盘。`file_stem` 留空（此时还没有文件名，id 自证即可）。"""
    return parse_skill_text(text, file_stem="", builtin=False,
                            source=SOURCE_LOCAL, path="")


def _skill_file(skill_id: str) -> Path:
    # safe_stem 挡 `../` 与 NTFS ADS；ID_RE 已排除，这里是第二道防线
    if safe_stem(skill_id) != skill_id:
        raise SkillInvalid(f"id 含不安全字符：{skill_id!r}")
    return user_dir() / (skill_id + ".skill.md")


def _write_file(skill_id: str, text: str) -> Path:
    p = _skill_file(skill_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 统一 LF：导出的文本可能在别处被改成 CRLF，落盘时收口，避免"同一份技能两种换行"
    p.write_bytes(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))
    return p


def _reject_builtin_id(skill_id: str) -> None:
    """内置同 id 必须**前置拒绝**：内置优先，写进去也永远不会生效。"""
    for s in load_all()["skills"]:
        if s.id == skill_id and s.builtin:
            raise SkillConflict(
                f"id「{skill_id}」已被内置技能占用。内置优先，同名用户技能不会生效 —— 请换一个 id"
                "（例如加 _my 后缀），或在技能页用「复制为自定义」自动改名。")


def create_skill(fields: dict) -> Skill:
    """新建用户技能。fields 为编辑器字段（id 必填）。"""
    sid = (fields.get("id") or "").strip()
    if not sid:
        raise SkillInvalid("id 必填")
    if not ID_RE.match(sid):
        raise SkillInvalid(f"id 不合法: {sid!r}（小写字母开头，仅小写/数字/下划线，3–40 位）")
    _reject_builtin_id(sid)
    p = _skill_file(sid)          # 顺带做 safe_stem 校验
    if p.exists():
        raise SkillConflict(f"技能「{sid}」已存在，请直接编辑它，或换一个 id")

    text = build_text({**fields, "id": sid})
    # 权威校验：以真实文件名再解析一次（id 合法 ≠ 正文合法）
    skill = parse_skill_text(text, file_stem=sid, builtin=False,
                             source=SOURCE_LOCAL, path=str(p))
    _write_file(sid, text)
    # 新建即启用：state 可能残留同名 id 的停用 / 导入记录（用户手工删过文件），不清掉就会出现
    # 「保存成功但列表里看不到这个技能」，或者新建的技能莫名带着"导入"徽章
    st = read_state()
    st["disabled"] = [x for x in st["disabled"] if x != sid]
    st["imported"] = [x for x in st["imported"] if x != sid]
    write_state(st)
    invalidate()
    return get_skill(sid) or skill


def update_skill(sid: str, fields: dict) -> Skill:
    old = get_skill(sid)
    if old is None:
        raise SkillMissing(f"技能不存在：{sid}")
    if old.builtin:
        raise SkillForbidden("内置技能只读。想改它请用「复制为自定义」复制一份再改")
    new_id = (fields.get("id") or sid).strip()
    if new_id != sid:
        raise SkillInvalid("id 不可修改（改名等于删旧建新）。如需换 id，请新建一个技能")

    # 以旧 manifest 为基底：保留用户手写在文件里的未知键，只覆盖编辑器管得到的字段
    man = manifest_of(old)
    for k in MANIFEST_KEYS[1:]:
        if k in fields and fields[k] is not None:
            if k == "tool_first" and not fields[k]:
                # 编辑器里把开关关掉 = 从文件里**拿掉**这个键，而不是留一个 false
                man.pop("tool_first", None)
                continue
            man[k] = fields[k]
    man["id"] = sid
    text = render_skill(man, fields.get("instructions", old.instructions))

    p = _skill_file(sid)
    parse_skill_text(text, file_stem=sid, builtin=False,
                     source=old.source, path=str(p))    # 权威校验，先验后写
    _write_file(sid, text)
    invalidate()
    return get_skill(sid)


def delete_skill(sid: str) -> dict:
    s = get_skill(sid)
    if s is None:
        raise SkillMissing(f"技能不存在：{sid}")
    if s.builtin:
        raise SkillForbidden("内置技能不能删除（随包发布）。可以把它「停用」")
    p = Path(s.path)
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        # 删一个技能文件不会触发删除护栏，但被外部编辑器占用会抛 WindowsError
        raise SkillInvalid(f"删除失败（文件可能被占用）：{e}")
    st = read_state()
    for k in ("disabled", "imported"):
        st[k] = [x for x in st[k] if x != sid]
    write_state(st)
    invalidate()
    return {"deleted": sid}


def import_skill(text: str, overwrite: bool = False) -> Skill:
    """导入一份技能文本。返回落盘后的 Skill。

    ⚠️ 导入的技能 `source=imported`，**不参与自动建议**，只能手动挂载。
    """
    if not (text or "").strip():
        raise SkillInvalid("导入内容为空")
    skill = validate_text(text)          # 先校验，任何字段不合法都不落盘
    sid = skill.id
    builtin = [s for s in load_all()["skills"] if s.id == sid and s.builtin]
    if builtin:
        raise SkillConflict(
            f"这份文件与内置技能「{builtin[0].name}」同 id（{sid}）。内置优先，导入后也不会生效 —— "
            "请先改文件里的 id 再导入")
    p = _skill_file(sid)
    if p.exists() and not overwrite:
        raise SkillConflict(f"已存在同名技能「{sid}」。勾选「覆盖」后再导入，或先改 id")

    # ⚠️ 原样落盘（只做 BOM / 换行归一），**不重建格式**：
    #    重建（重新 json.dumps）会让 round-trip 后的文件与原文不同，而「导出→导入→导出
    #    逐字节相等」是 P2 的验收判据之一。归一换行是为了 Windows 上不出现两种换行混排。
    text_out = (text or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    parse_skill_text(text_out, file_stem=sid, builtin=False,
                     source=SOURCE_IMPORTED, path=str(p))
    _write_file(sid, text_out)
    # 一次写完两项：标 imported + **强制启用**（导入完却因为历史停用状态不出现 =
    # 「导入成功但没反应」，是最难查的那类故障）
    st = read_state()
    st["imported"] = sorted(set(st["imported"]) | {sid})
    st["disabled"] = [x for x in st["disabled"] if x != sid]
    write_state(st)
    invalidate()
    return get_skill(sid)


def export_text(sid: str) -> tuple[str, str]:
    """导出：返回 (文件名, 文件原文)。

    ⚠️ 读**磁盘原文**而不是 render 重建：用户想看/改的是他自己那份文件，
       我们重建一遍等于悄悄改他的格式（缩进、未知键顺序都会变）。
    """
    s = get_skill(sid)
    if s is None:
        raise SkillMissing(f"技能不存在：{sid}")
    try:
        raw = Path(s.path).read_bytes()
    except OSError as e:
        raise SkillInvalid(f"读取技能文件失败：{e}")
    text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return s.id + ".skill.md", text


# ---------------------------------------------------------------- 外部技能包（P6）

_FM_RE = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*\n?", re.S)


def _slug_to_id(slug: str) -> str:
    """外部技能名 → 本系统 id：连字符转下划线、只留小写/数字/下划线、字母开头、3–40 位。"""
    s = re.sub(r"[^a-z0-9_]", "_", (slug or "").lower().replace("-", "_"))
    s = re.sub(r"_+", "_", s).strip("_")
    if not s or not s[0].isalpha():
        s = "skill_" + s
    s = s[:40].rstrip("_")
    return s if len(s) >= 3 else (s + "_x")[:40]


def external_to_skill_text(text: str, *, fallback_name: str = "") -> str:
    """把外部技能文件转成本系统 .skill.md 文本。

    两类输入：
    - 已是本系统格式（```skill 围栏）→ 原样返回（走原有校验/落盘链）；
    - Claude 系 SKILL.md（YAML frontmatter + Markdown 正文）→ 转 manifest + 正文照搬。

    ⚠️ 触发词**不用 LLM 生成**（v1 拍板：机械生成中文触发词是噪音）。
    但 chat 技能的硬约束是「必须有 triggers」，所以放一条**技能名原文**（真实、不造假：
    用户打出技能名就能匹配到；且导入的技能本就不参与自动建议，这条只是满足格式）。
    ⚠️ 正文**照搬不改写**：翻译/压缩是创作行为，不能在导入时静默发生。
    """
    text = (text or "").lstrip("﻿")
    if FENCE_RE.match(text):
        return text
    m = _FM_RE.match(text)
    if not m:
        raise SkillInvalid("既不是本系统技能格式（```skill 围栏），也不是带 YAML frontmatter 的 SKILL.md")
    fm: dict[str, str] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # 只取顶层 key: value（缩进行是多行/嵌套内容）
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if v.startswith(("|", ">")):
                # YAML 块标量（`description: |` 后接缩进多行）：后续缩进行都是值
                block = []
                i += 1
                while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                    block.append(lines[i].strip())
                    i += 1
                fm[k] = " ".join(x for x in block if x).strip()
                continue
            fm[k] = v.strip('"').strip("'")
        i += 1
    body = text[m.end():].strip()
    if not body:
        raise SkillInvalid("frontmatter 之后没有正文（方法说明为空）")
    name = (fm.get("name") or fallback_name or "imported_skill").strip()
    desc = (fm.get("description") or "").strip() or f"从外部技能包导入：{name}"
    # 显示名：连字符转空格；超长按词边界截（「prioritization matri」这种断词太丑）
    name_disp = name.replace("-", " ").strip()
    if len(name_disp) > MAX_NAME:
        name_disp = (name_disp[:MAX_NAME].rsplit(" ", 1)[0] or name_disp[:MAX_NAME]).strip()
    man = {
        "schema_version": 1,
        "id": _slug_to_id(name),
        "name": name_disp,
        "icon": "skill",
        "kind": "chat",
        "description": desc[:MAX_DESC],
        # 触发词 = 技能名原文（只保留单词字符/中文/空格，天然无正则元字符，不需要 escape）
        "triggers": [re.sub(r"[^\w一-鿿 ]", "", name_disp).strip()[:MAX_TRIGGER_LEN] or "skill"],
        "priority": 10,
        "citation": "optional",
    }
    return render_skill(man, body)
