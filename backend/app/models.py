"""数据模型：对应 PRD 第 5 节 7 个实体"""
from sqlalchemy import Column, Integer, String, Text, Float, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class Folder(Base):
    """文件夹（一等实体，支持层级嵌套）"""
    __tablename__ = "folders"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    parent_id = Column(Integer, ForeignKey("folders.id"), nullable=True)   # NULL=顶层
    created_at = Column(DateTime, default=datetime.utcnow)


class Material(Base):
    """学习材料"""
    __tablename__ = "materials"
    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    format = Column(String(16))                 # pdf / ppt / pptx / doc / docx
    file_path = Column(String(512))
    storage_mode = Column(String(16), default="copy")   # copy 复制副本 / reference 引用原文件
    source_folder = Column(String(255), default="")      # [废弃] 来源文件夹名，已迁移到 folder_id
    folder_id = Column(Integer, ForeignKey("folders.id"), nullable=True)   # 所属文件夹，NULL=未分组
    # 来源渠道与溯源信息（外部内容接入用）。
    # ⚠️ origin 决定「这份内容能不能建索引」——能力判定只认 services/external.py 的
    # ORIGIN_RIGHTS 一张表，渠道侧不许各写一套（本项目反复踩过「漏一处就不一致」）。
    # 取值：upload 本地上传 / local 本地导入 / url 网页剪藏 / wx 公众号文章 /
    #       weread 微信读书 / feed 播客·RSS（暂缓，枚举值预留）。
    # 存量记录默认 upload，零影响。
    origin = Column(String(16), default="upload")
    # 溯源稳定键：网页=URL，微信读书=bookId，本地导入=原文件绝对路径。
    # 与 origin 组合唯一，用于「同一篇内容不重复入库」的幂等判定，
    # 也用于「最近阅读」判定某条是否已沉淀为材料（重启后仍准确，不用内存标记）。
    # ⚠️ 可能超过 255 字符，故用 Text 而非 String。
    origin_ref = Column(Text, default="")
    # 抽取到的元信息快照：{title, author, date, sitename, ...}。纯展示与溯源用，
    # 不做业务判定（判定一律走 origin_ref）。
    origin_meta = Column(JSON, default=dict)
    parsed_status = Column(String(16), default="parsing")  # parsing / success / failed
    parse_error = Column(String(255), nullable=True)
    parse_progress = Column(Integer, default=0)   # 解析进度 0-100（音视频转写有真实进度）
    page_count = Column(Integer, default=0)
    tags = Column(JSON, default=list)
    # 实体抽取的内容指纹（MD5 of 全部 chunk 内容）：重试解析时若指纹未变且已有实体，
    # 跳过 LLM 抽取、只在本地重建引用表（防无意重复消耗 token，B+C 方案的 C）
    extract_sig = Column(String(32), default="")
    # 材料【内容性质】，决定讲义走哪套工艺（P0 材料 AI 工艺分流）。
    # 取值：practical 工具实用 / conceptual 认知思维 / narrative 叙事 /
    #       fragment 碎片（图片·音视频，形态决定、不调 LLM）/ auto 未判定。
    # ⚠️ 缺省空串 = 视为 auto → 走改造前的通用工艺，**老数据/判定失败零影响**。
    # 自动判定在解析完成后触发（见 routers/materials.py），用户可手改。
    doc_kind = Column(String(16), default="")
    last_read_page = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    chunks = relationship("MaterialChunk", back_populates="material", cascade="all, delete-orphan")
    # 笔记不级联删除：删除资料后笔记保留在知识库中（material_id 成为悬空引用，UI 显示"材料已删除"）
    notes = relationship("Note", back_populates="material")


class MaterialChunk(Base):
    """原文分块（带位置信息，供知识库向量化）"""
    __tablename__ = "material_chunks"
    id = Column(Integer, primary_key=True)
    material_id = Column(Integer, ForeignKey("materials.id"), index=True)
    content = Column(Text)
    page_no = Column(Integer)
    section_path = Column(String(512), default="")  # 章节层级，如 "第2章/2.1"
    # 块性质：body 正文 / img_ocr 图片 OCR 文字。
    # img_ocr 只在【网页剪藏材料】（origin_meta.saved_from=='clip'）上打，
    # 判据见 _parse_material 的 _tag_chunk_kind。这类块**照常分块、照常入向量库与全文检索**，
    # 只是学习页默认不显示（可折叠展开）。⚠️ 判定口径是 saved_from 而不是 origin ——
    # 早期小红书笔记的 origin 记的是 'upload'，按 origin 枚举判会漏。
    # ⚠️ 只加列、不改 content：content 是 extract_sig 指纹的输入，改动会误触发重新抽实体。
    kind = Column(String(16), default="body")
    embedding_id = Column(String(64), nullable=True)

    material = relationship("Material", back_populates="chunks")


class AIAsset(Base):
    """AI 产物：summary / keywords / explain / qa（追问链用 parent_id 串联）"""
    __tablename__ = "ai_assets"
    id = Column(Integer, primary_key=True)
    material_id = Column(Integer, ForeignKey("materials.id"), index=True)
    type = Column(String(16))                    # summary / keywords / explain / qa
    content = Column(Text)
    anchor = Column(JSON, nullable=True)         # 选段位置 {page_no, text, start, end}
    parent_id = Column(Integer, ForeignKey("ai_assets.id"), nullable=True)
    version = Column(Integer, default=1)         # 再生成版本号
    created_at = Column(DateTime, default=datetime.utcnow)


class Note(Base):
    """笔记：按 material_id 隔离"""
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    material_id = Column(Integer, ForeignKey("materials.id"), index=True)
    title = Column(String(255), default="未命名笔记")
    content = Column(Text, default="")
    source_type = Column(String(16), default="manual")  # manual / ai_asset / chat / weekly_report / podcast_script
    source_asset_id = Column(Integer, ForeignKey("ai_assets.id"), nullable=True)
    anchor = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    material = relationship("Material", back_populates="notes")


class Highlight(Base):
    """划线高亮：用户在阅读器主动划线的标记（含颜色样式），按材料隔离"""
    __tablename__ = "highlights"
    id = Column(Integer, primary_key=True)
    material_id = Column(Integer, ForeignKey("materials.id"), index=True)
    chunk_id = Column(Integer, ForeignKey("material_chunks.id"), nullable=True)  # 关联文本块（PDF 原文视图可能为空）
    page_no = Column(Integer, default=0)
    selected_text = Column(Text)
    color = Column(String(16), default="yellow")   # yellow / green / blue
    start = Column(Integer, nullable=True)          # 相对 chunk 内容的字符偏移
    end = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class KbEntry(Base):
    """知识库条目：统一检索层，笔记权重更高"""
    __tablename__ = "kb_entries"
    id = Column(Integer, primary_key=True)
    material_id = Column(Integer, index=True, default=0)  # 冗余便于按材料统计/清理
    ref_type = Column(String(16))                # chunk / note
    ref_id = Column(Integer)
    embedding_id = Column(String(64))
    weight = Column(Float, default=1.0)          # 笔记 1.5 / 原文 1.0
    created_at = Column(DateTime, default=datetime.utcnow)


class ChatSession(Base):
    """全局问答会话"""
    __tablename__ = "chat_sessions"
    id = Column(Integer, primary_key=True)
    title = Column(String(255), default="新会话")
    pinned = Column(Boolean, default=False)         # 置顶
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)  # 最近活动时间（排序/相对时间）

    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    """问答消息：kb_hit 区分是否命中知识库，sources 存来源标注"""
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), index=True)
    role = Column(String(16))                    # user / assistant
    content = Column(Text)
    reasoning = Column(Text, default="")         # 思维链内容（reasoning_content，展示/多轮回传用）
    kb_hit = Column(Boolean, default=False)
    sources = Column(JSON, default=list)         # [{material_id, title, page_no, snippet}]
    suggestions = Column(JSON, default=list)     # 回答后推荐的 3 个相关问题
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("ChatSession", back_populates="messages")


class LLMUsage(Base):
    """LLM 调用记账：估算成本用（流式调用按字符数估算）"""
    __tablename__ = "llm_usage"
    id = Column(Integer, primary_key=True)
    kind = Column(String(24))                    # 业务场景：summary/section_summary/keywords/explain/ask/transform/chat/suggest/review/quiz/stats_report
    model = Column(String(64))
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    estimated = Column(Boolean, default=False)   # True = 按字符估算（流式无 usage）
    created_at = Column(DateTime, default=datetime.utcnow)


class ReviewCard(Base):
    """复习卡片：由笔记 AI 出题生成，按简化 SM-2 间隔复习"""
    __tablename__ = "review_cards"
    id = Column(Integer, primary_key=True)
    note_id = Column(Integer, ForeignKey("notes.id"), index=True)
    material_id = Column(Integer, index=True, default=0)   # 冗余便于展示来源（材料删除后仍可复习）
    material_title = Column(String(255), default="")       # 来源标题快照
    question = Column(Text)
    answer = Column(Text)                       # qa: 答案正文 / choice: 解析
    type = Column(String(8), default="qa")      # qa 简答 / choice 选择
    source = Column(String(8), default="note")   # note 笔记出题 / quiz 测一测出题
    options = Column(JSON, nullable=True)       # choice: 4 个选项
    correct_index = Column(Integer, nullable=True)  # choice: 正确项下标 0-3
    level = Column(Integer, default=0)         # 0-5，对应 INTERVALS 下标
    ease = Column(Float, default=2.5)          # SM-2 ease factor：表现好↑(间隔拉长)、表现差↓(间隔缩短)
    next_review_at = Column(DateTime)          # 下次复习时间
    last_reviewed_at = Column(DateTime, nullable=True)
    review_count = Column(Integer, default=0)
    history = Column(JSON, default=list)        # 复习记录 [{r: result, t: iso, l: level}]
    created_at = Column(DateTime, default=datetime.utcnow)


class Podcast(Base):
    """AI 播客：材料/笔记 → 知识简报 → 双人对话脚本 → 本地音频

    两阶段产物分开存：
    - brief  = 提炼层产物（结构化知识简报，可单独复用为笔记）
    - script = 演绎层产物（对话脚本，逐句含说话人与时间戳）
    改脚本不必重新提炼，换音色不必重新生成脚本。
    """
    __tablename__ = "podcasts"
    id = Column(Integer, primary_key=True)
    title = Column(String(255), default="未命名播客")
    source_type = Column(String(16), default="article")   # article / notes / highlights / review / mixed
    source_refs = Column(JSON, default=list)              # [{type, id, title}] 来源快照
    style = Column(String(24), default="dialogue")        # dialogue 知识对谈 / solo 单人精讲
    # 生成方式：two_step 两步式 / fast 快速 / verbatim 原文朗读。
    # 旧记录为空串（升级前没有这列）→ 一律视为 AI 改写类，行为不变。
    gen_mode = Column(String(16), default="")
    voice_map = Column(JSON, default=dict)                # {"host": 音色id, "expert": 音色id}
    target_minutes = Column(Integer, default=3)           # 目标时长（分钟）
    # 生成时用户填写的「附加要求」。必须持久化：选中作品时生成台要能完整还原，
    # 否则基于原作品重新生成会丢掉这条自定义要求，产出风格与原来不一致。
    instruction = Column(Text, default="")
    brief = Column(Text, default="")                      # 知识简报（提炼层）
    script = Column(JSON, default=list)                   # [{speaker, name, text, start_ms, end_ms}]
    # ⚠️ 只存相对文件名，不存绝对路径 —— 开发态(backend/data)与打包版(~/.ai-study-companion)
    # 数据目录不同，存绝对路径会让切换环境后历史记录全部失效。
    audio_name = Column(String(255), default="")
    audio_bytes = Column(Integer, default=0)
    duration_sec = Column(Integer, default=0)             # 合成后回读的实际时长
    # 已合成音频对应的脚本文本指纹。与当前 script 算出的指纹不一致 → 音频是上一版，
    # 界面需显式标注「音频对应的是修改前的文案」，避免用户以为听到的就是当前文案。
    audio_sig = Column(String(32), default="")
    # 背景音乐配置。bgm_id="" 表示不配背景音乐（默认）；
    # bgm_volume 是 BGM 相对人声的基准音量（dB），说话时会再按 ducking 压低。
    # ⚠️ 二者都要进音频指纹（见 podcast.script_signature）：换 BGM 或调音量后，
    # 已合成的音频就不再对应当前配置，界面必须能标出「音频是上一版」。
    bgm_id = Column(String(64), default="")
    bgm_volume = Column(Integer, default=-20)
    # 记住这首曲子「叫什么」。素材被删除后 bgm_id 仍指向它（不静默改作品），
    # 但 bgm.track_name() 查不到名字 → 界面只能显示一句「已失效」，用户根本
    # 不知道原本配的是哪首。存下最后一次成功选择时的曲名，删曲后仍能显示原曲名。
    # ⚠️ 纯展示用元数据，**不进音频指纹**（见 podcast.script_signature）。
    bgm_label = Column(String(64), default="")
    status = Column(String(16), default="draft")          # draft / script_ready / done / failed
    error = Column(String(255), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ActivityLog(Base):
    """行为事件埋点：学习时长 / 笔记回看（数据统计面板用）"""
    __tablename__ = "activity_logs"
    id = Column(Integer, primary_key=True)
    type = Column(String(16))                  # duration 学习时长 / note_view 笔记回看 / video_* 视频埋点
    ref_id = Column(Integer, default=0)        # material_id（duration）或 note_id（note_view）或 video_id（video_*）
    duration_seconds = Column(Integer, default=0)  # duration 类型：本次时长（秒）
    # 行为明细小对象（JSON 字符串）：video_render 的 stage / video_play 的 completed 等。
    # ⚠️ 埋点边界（PRD 4.2）：只装行为量与长度，绝不装讲稿正文 / 来源内容。
    detail = Column(String(255), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------- 知识图谱（轻量实体索引 · P1） ----------

class Entity(Base):
    """知识图谱实体：从材料原文中抽取的人物/产品/概念/知识点/方法。

    归并口径：同一材料内「标准化名称 + 类型」唯一（见 services/entity.py 的归一化），
    跨材料同名实体各自成行（分属不同材料，语义不必强合）。
    """
    __tablename__ = "entities"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), index=True)          # 标准化后的实体名
    type = Column(String(16), index=True)           # person/product/concept/knowledge/method
    material_id = Column(Integer, ForeignKey("materials.id"), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Relation(Base):
    """知识图谱关系：src 实体 --rel--> dst 实体（暂不交叉材料，均为同材料内关系）。

    rel 为简短动词短语（如「提出」「包含」「用于判断」）。src/dst 存的是**实体名**而非
    entity.id —— 抽取层按名对齐（跨 chunk 的实体靠同名归并），存名能跨 chunk 关联，
    也避免抽取时先查 id 的一次往返。
    """
    __tablename__ = "relations"
    id = Column(Integer, primary_key=True)
    material_id = Column(Integer, ForeignKey("materials.id"), index=True)
    src = Column(String(128), index=True)
    rel = Column(String(64))
    dst = Column(String(128), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EntityRef(Base):
    """实体 → 内容引用：实体出现在哪些 chunk / note 里（检索扩展的桥梁）。

    ref_type ∈ chunk / note；ref_id 对应 material_chunks.id 或 notes.id。
    一个实体可引用多处，一处内容也可含多个实体。
    """
    __tablename__ = "entity_refs"
    id = Column(Integer, primary_key=True)
    entity = Column(String(128), index=True)
    ref_type = Column(String(16), index=True)       # chunk / note
    ref_id = Column(Integer, index=True)
    material_id = Column(Integer, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------- AI 陪练（PRD 模块 J · V1.0） ----------

class DrillSession(Base):
    """陪练会话：一次完整陪练（范围 + 配置 + 若干轮问答 + 总评分）。

    ⚠️ 会话生命周期的**唯一权威是本表的 status 列**；内存中的后台任务只是执行器。
    见 PRD 2.2.6：服务启动时回收 updated_at 超时的 outlining/running 孤儿会话，
    并发闸也**按本表统计**（不用进程内计数）。
    """
    __tablename__ = "drill_sessions"
    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)                # 用户可留空；空则后端生成「《材料名》陪练」
    scope = Column(String(16), nullable=False, default="all")   # all / material / note / folder
    scope_refs = Column(JSON, default=list)                     # 范围快照 [{type,id,title}]
    config = Column(JSON, default=dict)                         # {question_count, follow_up, show_answer}
    status = Column(String(16), default="draft")                # draft/outlining/running/finished/failed
    outline = Column(JSON, default=list)                        # [{point,difficulty,hint,source}]
    current_seq = Column(Integer, default=0)                    # 已推进到题纲下标+1（按 outline_index 计）
    total_score = Column(Float, nullable=True)
    written_back_at = Column(DateTime, nullable=True)           # 错题回写时间（幂等标记）
    error = Column(String(255), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    turns = relationship("DrillTurn", back_populates="session", cascade="all, delete-orphan",
                         order_by="DrillTurn.seq")


class DrillTurn(Base):
    """陪练轮次：一次「AI 提问 → 用户作答 → AI 评分」。

    追问轮 is_followup=True 且 outline_index 沿用同一考点（不消耗新考点、不推进度）。
    """
    __tablename__ = "drill_turns"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("drill_sessions.id"), index=True)
    seq = Column(Integer, default=1)                  # 轮次序号（从 1 起）
    outline_index = Column(Integer, nullable=True)    # 对应题纲下标；追问沿用同一值
    is_followup = Column(Boolean, default=False)      # 是否追问轮次
    # 本题**实际使用**的难度（难度自适应调整后的结果；easy / medium / hard）。
    # 空 = 未记录（本功能上线前的旧数据）→ 前端不显示「已调整」标注。
    # ⚠️ 不要改成读 outline 里那个静态难度：那样就看不出自适应到底有没有生效。
    difficulty = Column(String(8), nullable=True)
    question = Column(Text)                           # AI 提问
    expected_points = Column(JSON, default=list)      # 期望要点（评分对照标准）
    source_material_id = Column(Integer, nullable=True)   # 该题来源材料 id（无法确定时回写用 -1）
    source_title = Column(String(255), default="")    # 来源标题快照
    user_answer = Column(Text, nullable=True)         # 用户作答；NULL=未作答，""=跳过本题
    score = Column(Integer, nullable=True)            # 0-100
    verdict = Column(String(8), nullable=True)        # correct / partial / wrong
    dimensions = Column(JSON, nullable=True)          # {正确性, 完整度, 表达}
    comment = Column(Text, default="")                # 点评
    missing_points = Column(JSON, default=list)       # 漏掉的要点
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("DrillSession", back_populates="turns")


# ---------- AI 视频（PRD 模块 K · V1.0） ----------

class Video(Base):
    """AI 视频作品：音轨（复用播客 / 本片单独生成）+ 一层跟着音轨动的画面。

    ⚠️ 生命周期与陪练同构：**唯一权威是本表的 status 列**，渲染进程只是执行器。
    服务启动时回收 `status ∈ {scripting, queued, rendering}` 且 updated_at 超窗的
    孤儿作品（见 PRD 2.2.6），否则会出现「永远渲染中且点不开」的僵尸卡片。

    ⚠️ 视频对播客是**只读消费**：只读 podcasts.audio_name / script / duration_sec。
    绝不复用 routers/podcasts.py::_finalize_audio —— 它会把结果直接写进 Podcast 的
    audio_name / duration_sec / audio_sig / status，视频调用它等于**把视频的音频指纹
    写进播客记录**，播客界面立刻误报「音频对应的是修改前的文案」（已有教训，见 PRD 4.5）。

    ⚠️ 不新增 review_cards.source 枚举：视频是消费型产物，需要沉淀时走「转笔记」。
    """
    __tablename__ = "videos"
    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)          # 用户可留空；空则由后端按「《来源名》讲解视频」生成后写入
    # 内容来源（沿用播客口径）：article / notes / highlights / review / mixed / text
    source_type = Column(String(16), default="article")
    source_refs = Column(JSON, default=list)             # [{type,id,title}] 来源快照
    # 视觉方案 / 皮肤。talking_head 是**叠加层不是替代层** → 必须同时有 inner_skin。
    skin = Column(String(16), default="source")          # source / graph / talking_head
    inner_skin = Column(String(16), default="")           # talking_head 时的内层：source / graph
    avatar_id = Column(String(64), default="")            # 形象标识（预置 id 或上传文件标识）
    avatar_pos = Column(String(16), default="right")      # left / right / pip
    # 自定义背景图（解压型皮肤 radio / night / ambient 生效）。存**背景库里的 id**，
    # 不存路径 —— 库与作品目录同级，删作品不删素材（与 avatar_id 同一形态）。
    # ⚠️ 为空 = 用皮肤自带画面；非空但文件丢了 = 渲染时回落自带画面（不报错、也不静默改作品）。
    bg_id = Column(String(64), default="")
    # 皮肤预设变体（radio / night 各 3 套，2026-10-12）。空 = 默认套（存量零回归，
    # 指纹与升级前逐字节一致）；白名单归一在 services/video.py::norm_variant。
    skin_variant = Column(String(16), default="")
    # 画面署名（解压型皮肤 radio / night 画面底部那行字）。空 = 回落作品标题。
    # ⚠️ 与 title **解耦**，不是冗余：标题管列表与导出文件名，可以长（50 字）；
    #    画面那行字空间有限（58px 楷体居中），必须允许单独改短。
    #    长度口径（上限 / 归一）见 services/video.py::norm_brand_text，不得两处各写一套。
    brand_text = Column(String(32), default="")
    # 音轨来源：podcast 复用既有播客（**绝不重新合成**）/ self 本片单独生成
    audio_source = Column(String(16), default="podcast")
    podcast_id = Column(Integer, nullable=True)           # 复用模式：来源播客 id
    audio_name = Column(String(255), default="")          # 只存相对文件名，不存绝对路径
    audio_bytes = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)              # 音轨真实时长（从音频回读，不取估算）
    audio_sig = Column(String(32), default="")            # 音轨指纹（沿用 podcast.script_signature 口径）
    # 本片音色（作品级）：{"host": 音色id}，与播客 voice_map 同构。空 dict = 跟随设置页。
    # ⚠️ 换音色后旧音轨的音色就不对了 → `video.audio_stale` 立刻标注「音轨对应上一版音色」，
    #    不会静默沿用。音色比对在 audio_stale 里**独立**做，绝不动 script_signature 的
    #    指纹口径 —— 改那个会让全部历史记录的指纹失配、集体误报过期。
    voice_map = Column(JSON, default=dict)
    # 背景音乐（作品级，与 podcasts.bgm_* 同构）。bgm_id="" 表示不配背景音乐（默认）。
    # ⚠️ 只对「本片单独生成」的音轨生效：复用播客时音轨里已烘了播客自己的 BGM，
    #    再叠一层会双音乐打架（2026-10-12 产品决策：复用模式不支持视频级 BGM）。
    # ⚠️ 空串 → script_signature 不追加任何字符，与升级前指纹逐字节一致（不集体误报）。
    bgm_id = Column(String(64), default="")
    bgm_volume = Column(Integer, default=-20)            # BGM 相对人声的基准音量（dB），说话时 ducking 再压低
    # 记住曲名：素材被删后 bgm_id 仍指向它（不静默改作品），界面据此如实显示「已失效」。
    bgm_label = Column(String(64), default="")
    style = Column(String(24), default="solo")            # 讲稿风格（沿用播客）：dialogue / solo
    gen_mode = Column(String(16), default="fast")         # two_step / fast / verbatim
    target_minutes = Column(Integer, default=3)
    # 附加要求必须持久化：否则基于原作品重新生成会丢掉这条自定义要求，产出与原来不一致。
    instruction = Column(Text, default="")
    script = Column(JSON, default=list)                   # [{speaker,name,text,start_ms,end_ms}]
    shots = Column(JSON, default=list)                    # [{n,l,a,b}] 分镜（展示单位，非渲染单位）
    points = Column(JSON, default=list)                   # [{t,k,at}] 结构图皮肤右侧要点栏
    steps = Column(JSON, default=list)                    # [{at,page,anchor,hit,sc,x,y,w,h}] 视觉步点
    gate_report = Column(JSON, nullable=True)             # 质量闸门自检结果
    # 连续未通过次数（通过即清零）：≥3 才允许「跳过闸门」（PRD 3.9.7 / 验收 #33）。
    # ⚠️ 必须是「连续」：历史累计会把「改好了又改坏」误算成反复不通过。
    gate_fail_count = Column(Integer, default=0)
    # 当前成片是否为「跳过闸门」产出（记录在案 + 成片标注「未通过质量自检」）；
    # 闸门下一次通过时清零 —— 它描述的是「当前这份成片」，不是这部作品的历史。
    gate_skipped = Column(Boolean, default=False)
    status = Column(String(16), default="draft")          # draft/scripting/gate_check/queued/rendering/finished/failed
    progress = Column(Float, default=0.0)                 # 渲染进度 0-1（只反映真实进度，不伪造百分比）
    video_name = Column(String(255), default="")
    video_bytes = Column(Integer, default=0)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    fps = Column(Integer, default=0)
    frame_count = Column(Integer, default=0)
    # 本次渲染实际耗时（秒）。**必须落库**：它是「同一台机器上片子变长了多久」的唯一依据，
    # 也是排查「渲染变慢」的基线（此前 finalize_render 收了这个参数却没有列，被静默丢弃）。
    render_seconds = Column(Float, default=0.0)
    poster_name = Column(String(255), default="")
    # 派生指纹 hash(script + audio_sig + skin + inner_skin + avatar_id + avatar_pos + w/h/fps)。
    # ⚠️ 判定同步状态一律用**指纹**，不用「最后修改时间」；不一致时**不删除旧成片**，
    #    而是标注「对应上一个版本的配置」+ 提供一键重建（见 PRD 3.11.4）。
    signature = Column(String(64), default="")
    error = Column(String(255), default="")
    rendered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
