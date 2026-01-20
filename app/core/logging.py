"""日志初始化模块：控制台 + 文件 JSON 日志，可选请求日志中间件。"""
import logging, sys, json
from pathlib import Path
from .settings import settings

def setup_logging(level: str = "INFO") -> None:
    """初始化全局日志配置。

    Args:
        level: 日志级别名称（如 "DEBUG"/"INFO"/"WARNING"）。
    """
    level_val = getattr(logging, level.upper(), logging.INFO)
    handlers = [logging.StreamHandler(sys.stdout)]
    # 文件日志
    logs_dir = Path(__file__).resolve().parents[2] / settings.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(logs_dir / "app.log", encoding="utf-8")
    file_handler.setLevel(level_val)

    class JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            data = {
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            return json.dumps(data, ensure_ascii=False)

    file_handler.setFormatter(JsonFormatter())
    handlers.append(file_handler)

    logging.basicConfig(
        level=level_val,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
    )
