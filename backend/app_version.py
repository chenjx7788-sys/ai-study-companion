"""版本号单一来源（启动器与后端共用，避免两处不一致）

- 后端 `app/main.py` 的 `/api/version` 读这里
- 启动器 `launcher.py` 读这里，用于「升级后清理 WebView 旧缓存」

放在包外是为了让 launcher 无需导入 app.main（后者会连带加载 ~6s 的重依赖）。
"""

__version__ = "0.1.3"
