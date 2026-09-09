import sys
from pathlib import Path

from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _default_data_dir() -> Path:
    """数据目录：开发态=backend/data；PyInstaller 打包态=用户目录（跨平台）"""
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "ai-study-companion"
        return Path.home() / ".ai-study-companion"
    return BASE_DIR / "data"


DATA_DIR = _default_data_dir()


class Settings(BaseSettings):
    # 数据目录：原文文件 / SQLite / 向量库
    data_dir: Path = DATA_DIR
    files_dir: Path = DATA_DIR / "files"
    db_url: str = f"sqlite:///{DATA_DIR / 'app.db'}"
    chroma_dir: Path = DATA_DIR / "chroma"

    # LLM 配置（也可在设置页覆盖，存库后优先读库）
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    summary_model: str = "deepseek-chat"
    chat_model: str = "deepseek-chat"
    embedding_model: str = ""
    embedding_base_url: str = ""   # 留空则复用 llm_base_url
    embedding_api_key: str = ""    # 留空则复用 llm_api_key

    # 业务参数
    max_file_mb: int = 100
    chunk_size: int = 700        # 知识库分块字数
    chunk_overlap: int = 100
    # 入库去噪开关（设置页可关，防特殊语料误伤）
    clean_header_footer: bool = True   # 页眉页脚 + 页码行
    clean_watermark: bool = True       # 高频短句水印
    clean_garbled: bool = True         # 乱码行
    clean_dedup: bool = True           # 重复块去重
    kb_top_k: int = 8            # 问答检索条数
    # 命中阈值：BGE-small-zh 实测 相关 0.18~0.51 / 无关 <0，取 0.15 为界
    # （若改用其他 embedding 模型需重新校准）
    kb_hit_threshold: float = 0.15
    review_daily_limit: int = 30   # 每日复习上限，防积压

    class Config:
        env_prefix = "ASC_"

settings = Settings()
settings.files_dir.mkdir(parents=True, exist_ok=True)
settings.chroma_dir.mkdir(parents=True, exist_ok=True)
