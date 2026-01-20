"""应用配置模块：提供默认值、读取 app/config/settings.json，并返回强类型 Settings。

支持 PyInstaller 打包：优先从可执行文件所在目录读取 settings.json。"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict
import json
import sys

@dataclass
class ServerSettings:
    """服务器运行参数。

    - host: 监听地址（开发默认 127.0.0.1）
    - port: 监听端口（开发默认 8000）
    """
    host: str = "127.0.0.1"
    port: int = 8000

@dataclass
class Settings:
    """应用配置总表。

    - app_name: 应用标题
    - debug: 调试开关（影响日志级别与热重载）
    - server: 服务器相关配置
    """
    app_name: str = "类器官样本库管理系统"
    debug: bool = True
    server: ServerSettings = field(default_factory=ServerSettings)
    # JWT 配置
    jwt_secret: str = "change-me-to-a-strong-secret"
    jwt_exp_minutes: int = 120
    # 目录配置
    upload_dir: str = "uploads"
    templates_dir: str = "templates"
    logs_dir: str = "logs"
    log_level: str = "INFO"
    request_log: bool = True
    # 数据库 SQL 日志
    sql_log: bool = False  # 是否输出 SQL 执行日志（用于调试）

def load_settings() -> Settings:
    """从 settings.json 读取配置并与默认值合并，返回 Settings 实例。

    优先级：
    1. 打包后可执行文件所在目录下的 settings.json
    2. 打包后可执行文件所在目录下 config/settings.json
    3. 源码目录 app/config/settings.json
    """
    data: Dict[str, Any] = {}

    # 统一从程序根目录的 config/ 读取
    if getattr(sys, "frozen", False):
        # 打包后：从可执行文件同级的 config/ 目录读取
        base_dir = Path(sys.executable).parent
    else:
        # 开发环境：app/core/settings.py -> parents[1] 是 app 目录
        base_dir = Path(__file__).resolve().parents[1]

    cfg_path = base_dir / "config" / "settings.json"
    
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

    server_raw = data.get("server", {})
    server = ServerSettings(
        host=server_raw.get("host", ServerSettings.host),
        port=int(server_raw.get("port", ServerSettings.port)),
    )
    
    # 计算项目根目录(Bio9_organ)
    if getattr(sys, "frozen", False):
        # 打包后：可执行文件所在目录
        project_root = Path(sys.executable).parent
    else:
        # 开发环境：Bio9_organ 仓库根目录
        project_root = Path(__file__).resolve().parents[2]
    
    # 处理目录路径,转换为绝对路径
    upload_dir_raw = data.get("upload_dir", Settings.upload_dir)
    upload_dir = str(project_root / upload_dir_raw) if not Path(upload_dir_raw).is_absolute() else upload_dir_raw
    
    templates_dir_raw = data.get("templates_dir", Settings.templates_dir)
    templates_dir = str(project_root / templates_dir_raw) if not Path(templates_dir_raw).is_absolute() else templates_dir_raw
    
    logs_dir_raw = data.get("logs_dir", Settings.logs_dir)
    logs_dir = str(project_root / logs_dir_raw) if not Path(logs_dir_raw).is_absolute() else logs_dir_raw
    
    return Settings(
        app_name=data.get("app_name", Settings.app_name),
        debug=bool(data.get("debug", Settings.debug)),
        server=server,
        jwt_secret=data.get("jwt_secret", Settings.jwt_secret),
        jwt_exp_minutes=int(data.get("jwt_exp_minutes", Settings.jwt_exp_minutes)),
        upload_dir=upload_dir,
        templates_dir=templates_dir,
        logs_dir=logs_dir,
        log_level=data.get("log_level", Settings.log_level),
        request_log=bool(data.get("request_log", Settings.request_log)),
        sql_log=bool(data.get("sql_log", Settings.sql_log)),
    )

# 加载全局配置实例，供其他模块直接导入使用

settings = load_settings()