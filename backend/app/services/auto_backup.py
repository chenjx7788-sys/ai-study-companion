"""自动定时备份：启动时 + 每日一次，把 data 目录（排除可重下的大模型）打包到 backups/，保留最近 N 份"""
import threading
import time
import zipfile
from pathlib import Path

from ..core.config import settings

BACKUP_DIR = settings.data_dir.parent / "backups"
KEEP = 7            # 保留最近 7 份
INTERVAL = 24 * 3600   # 每天一次
EXCLUDE_DIRS = {"models"}   # whisper/BGE 模型可重新下载，不打进备份（省 ~600MB）

_lock = threading.Lock()


def make_backup_zip(dest_zip: Path) -> None:
    """把 data 目录打包成 zip，排除 EXCLUDE_DIRS。"""
    root = settings.data_dir
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if rel.parts and rel.parts[0] in EXCLUDE_DIRS:
                continue
            if p.is_dir():
                continue
            zf.write(p, rel)


def do_backup() -> Path | None:
    """打包 data 目录到 backups/asc-backup-<时间戳>.zip，清理旧备份。失败返回 None。"""
    with _lock:
        try:
            BACKUP_DIR.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            zip_path = BACKUP_DIR / f"asc-backup-{stamp}.zip"
            make_backup_zip(zip_path)
            # 清理旧备份（保留最近 KEEP 份）
            backups = sorted(BACKUP_DIR.glob("asc-backup-*.zip"))
            for old in backups[:-KEEP]:
                old.unlink(missing_ok=True)
            return zip_path
        except Exception as e:
            print(f"[backup] 自动备份失败: {e}")
            return None


def _loop():
    # 启动后延迟 30s 做首次备份（等首次启动稳定）
    time.sleep(30)
    while True:
        p = do_backup()
        if p:
            print(f"[backup] 已自动备份: {p.name}")
        time.sleep(INTERVAL)


def start_auto_backup():
    """启动后台备份线程（守护线程，随进程退出）"""
    t = threading.Thread(target=_loop, daemon=True, name="auto-backup")
    t.start()
