# app/db/factory.py
import json
import sys
from pathlib import Path
from .adapters.sqlserver import SqlServerAdapter
from .adapters.dameng import DamengAdapter
from .base import DatabaseAdapter

def get_adapter() -> DatabaseAdapter:
    # 确定配置文件路径
    if getattr(sys, 'frozen', False):
        # 打包后：从exe所在目录的config/文件夹读取
        base_dir = Path(sys.executable).parent
    else:
        # 开发环境：从app/config/读取
        # app/db/factory.py -> parents[1] 是 app 目录
        base_dir = Path(__file__).resolve().parents[1]
    
    cfg_path = base_dir / "config" / "database.json"
    
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"找不到数据库配置文件: {cfg_path}\n"
            f"请确保 config/database.json 文件与程序在同一目录下"
        )
    
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    db_type = data.get("database_type","sqlserver").lower()
    if db_type == "sqlserver":
        return SqlServerAdapter(data["sqlserver"])
    if db_type == "dameng":
        return DamengAdapter(data["dameng"])
    raise ValueError(f"未知数据库类型: {db_type}")