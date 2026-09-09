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
    parsed_status = Column(String(16), default="parsing")  # parsing / success / failed
    parse_error = Column(String(255), nullable=True)
    parse_progress = Column(Integer, default=0)   # 解析进度 0-100（音视频转写有真实进度）
    page_count = Column(Integer, default=0)
    tags = Column(JSON, default=list)
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
    source_type = Column(String(16), default="manual")  # manual / ai_asset
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


class ActivityLog(Base):
    """行为事件埋点：学习时长 / 笔记回看（数据统计面板用）"""
    __tablename__ = "activity_logs"
    id = Column(Integer, primary_key=True)
    type = Column(String(16))                  # duration 学习时长 / note_view 笔记回看
    ref_id = Column(Integer, default=0)        # material_id（duration）或 note_id（note_view）
    duration_seconds = Column(Integer, default=0)  # duration 类型：本次时长（秒）
    created_at = Column(DateTime, default=datetime.utcnow)
