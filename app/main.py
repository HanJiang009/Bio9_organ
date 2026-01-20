from fastapi import FastAPI, Request
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .routers.health import router as health_router
from .core.settings import settings
from .core.logging import setup_logging
from .core import exceptions
from .core.middleware import RequestLogMiddleware
import logging
import sys
from pathlib import Path

# 添加授权包到路径
# 处理打包环境和开发环境的路径差异
if getattr(sys, 'frozen', False):
    # 打包环境：使用 sys._MEIPASS（PyInstaller 的临时解压目录）
    base_path = Path(sys._MEIPASS)
    sys.path.insert(0, str(base_path))
else:
    # 开发环境：bio9_license 在 pywork 目录下
    pywork_dir = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(pywork_dir))

try:
    from bio9_license import LicenseGuard, create_license_middleware
    LICENSE_ENABLED = True
except ImportError:
    LICENSE_ENABLED = False
    logging.warning("⚠️  授权模块未找到，授权功能已禁用")

logger = logging.getLogger(__name__)
from .routers.user import router as user_router
from .routers.auth import router as auth_router
from .routers.role import router as role_router
from .routers.shortcut import router as shortcut_router
from .routers.file import router as file_router
from .routers.pdf import router as pdf_router
from .routers.sample_receive import router as sample_receive_router
from .routers.organoid_culture_tissue import router as organoid_culture_tissue_router
from .routers.organoid_culture_detail import router as organoid_culture_detail_router
from .routers.organoid_observation import router as organoid_observation_router
from .routers.organoid_recycle import router as organoid_recycle_router
from .routers.qc import router as qc_router
from .routers.organoid_qc import router as organoid_qc_router
from .routers.organoid_freeze import router as organoid_freeze_router
from .routers.organoid_thaw import router as organoid_thaw_router
from .routers.sample_linkage import router as sample_linkage_router
from .routers.transport_mgmt import router as transport_mgmt_router
from .routers.unified_search import router as unified_search_router
from .routers.preprocess import router as preprocess_router
from .routers.op_log import router as op_log_router
from .routers.drug_mgmt import router as drug_mgmt_router
from .routers.drug_test import router as drug_test_router
from .routers.drug_test_result import router as drug_test_result_router
from .routers.license import router as license_router
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

setup_logging("DEBUG" if settings.debug else "INFO")

app = FastAPI(title=settings.app_name)

# 🔐 初始化授权守卫（启动时执行）
@app.on_event("startup")
async def startup_event():
    """应用启动时初始化授权系统"""
    if LICENSE_ENABLED:
        try:
            # 获取系统根目录
            if getattr(sys, 'frozen', False):
                # 打包环境：使用 exe 所在目录
                app_root = Path(sys.executable).parent
            else:
                # 开发环境：Bio9_organ/ 目录
                app_root = Path(__file__).parent.parent
            
            logger.info(f"🔍 授权系统根目录: {app_root}")
            
            guard = LicenseGuard()
            guard.initialize(
                system_name="Bio9_organ",
                license_file=str(app_root / "config" / "license.key"),
                secret_key_file=str(app_root / "config" / "secret.key")
            )
        except Exception as e:
            logger.error(f"❌ 授权系统初始化失败: {e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://demo.51syf.com", "http://192.168.31.205", "*"],  # 开发期可先用 *
    allow_credentials=True,
    allow_methods=["*"],   # 或 ["GET","POST","PUT","DELETE","OPTIONS"]
    allow_headers=["*"],   # 至少要允许 Authorization, Content-Type
)

# 🔐 添加授权中间件（拦截未授权请求）
if LICENSE_ENABLED:
    try:
        app.add_middleware(
            create_license_middleware(
                system_name="Bio9_organ",
                excluded_paths=['/health', '/docs', '/openapi.json', '/redoc', '/license/info', '/license/fingerprint']
            )
        )
    except Exception as e:
        logger.error(f"❌ 授权中间件添加失败: {e}")

app.add_middleware(RequestLogMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    自定义422错误处理器 - 记录详细的验证错误信息
    帮助诊断文件上传相关的422错误
    """
    logger.error("=" * 80)
    logger.error("❌ 422 Validation Error 发生")
    logger.error(f"请求路径: {request.url.path}")
    logger.error(f"请求方法: {request.method}")
    logger.error(f"Content-Type: {request.headers.get('content-type', 'N/A')}")
    
    # 记录详细的验证错误
    logger.error("\n验证错误详情:")
    for idx, error in enumerate(exc.errors(), 1):
        logger.error(f"\n  错误 #{idx}:")
        logger.error(f"    字段位置: {' -> '.join(str(x) for x in error.get('loc', []))}")
        logger.error(f"    错误类型: {error.get('type', 'unknown')}")
        logger.error(f"    错误消息: {error.get('msg', 'N/A')}")
        
        # 安全地处理input字段（避免UploadFile序列化问题）
        input_val = error.get('input')
        if input_val is not None:
            try:
                if hasattr(input_val, 'filename'):
                    logger.error(f"    输入值: <UploadFile: {input_val.filename}>")
                else:
                    input_str = str(input_val)
                    if len(input_str) > 200:
                        input_str = input_str[:200] + "..."
                    logger.error(f"    输入值: {input_str}")
            except Exception:
                logger.error(f"    输入值: <无法序列化>")
    
    logger.error("=" * 80)
    
    # 清理错误信息中的不可序列化对象（如 bytes）
    def clean_error(error):
        """递归清理错误信息中的不可序列化对象"""
        if isinstance(error, dict):
            return {k: clean_error(v) for k, v in error.items()}
        elif isinstance(error, list):
            return [clean_error(item) for item in error]
        elif isinstance(error, bytes):
            return f"<bytes object, length={len(error)}>"
        elif hasattr(error, '__class__') and error.__class__.__name__ not in ('str', 'int', 'float', 'bool', 'NoneType'):
            try:
                return str(error)
            except:
                return f"<{error.__class__.__name__} object>"
        return error
    
    errors = exc.errors()
    cleaned_errors = [clean_error(err) for err in errors]
    
    # 返回标准的422响应
    return JSONResponse(
        status_code=422,
        content={"detail": cleaned_errors}
    )


app.add_exception_handler(HTTPException, exceptions.http_exception_handler)
app.add_exception_handler(Exception, exceptions.global_exception_handler)

# 静态文件：暴露 uploads 目录，便于直接通过 URL 访问上传文件
# settings.upload_dir 是绝对路径，需要提取最后一部分作为路由
uploads_dir = Path(settings.upload_dir)
if uploads_dir.exists():
    # 使用目录名作为路由（例如 "uploads"）
    route_name = uploads_dir.name
    app.mount(f"/{route_name}", StaticFiles(directory=str(uploads_dir)), name="uploads")
    
app.include_router(health_router)
app.include_router(user_router)
app.include_router(auth_router)
app.include_router(role_router)
app.include_router(shortcut_router)
app.include_router(file_router)
app.include_router(pdf_router)
app.include_router(sample_receive_router)
app.include_router(op_log_router)
app.include_router(organoid_culture_tissue_router)
app.include_router(preprocess_router)
app.include_router(organoid_culture_detail_router)
app.include_router(organoid_observation_router)
app.include_router(organoid_recycle_router)
app.include_router(qc_router)
app.include_router(organoid_qc_router)
app.include_router(organoid_freeze_router)
app.include_router(organoid_thaw_router)
app.include_router(sample_linkage_router)
app.include_router(transport_mgmt_router)
app.include_router(unified_search_router)
app.include_router(drug_mgmt_router)
app.include_router(drug_test_router)
app.include_router(drug_test_result_router)
app.include_router(license_router)