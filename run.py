import sys
import traceback
import atexit


def _prompt_exit():
    """打包后暂停窗口，便于查看信息。"""
    try:
        input("\n按回车键退出...")
    except Exception:
        pass


# 检测是否在 PyInstaller 打包环境中
IS_FROZEN = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")

# 如果是打包环境，注册退出时的暂停处理
# 这样无论程序如何退出（正常/异常/Ctrl+C/端口冲突等），都会暂停
if IS_FROZEN:
    atexit.register(_prompt_exit)


if __name__ == "__main__":
    try:
        # 打包环境：第一次运行时自动复制配置文件到外部
        if IS_FROZEN:
            from app.core.config_init import init_config_files
            init_config_files()
        
        # 导入依赖
        import uvicorn
        from app.core.settings import settings
        
        if IS_FROZEN:
            # PyInstaller 打包环境：禁用 reload，直接使用 app 对象
            from app.main import app

            uvicorn.run(
                app,
                host=settings.server.host,
                port=settings.server.port,
                reload=False,
            )
        else:
            # 开发/部署环境：使用字符串引用，支持热重载
            uvicorn.run(
                "app.main:app",
                host=settings.server.host,
                port=settings.server.port,
                reload=settings.debug,
                reload_dirs=["app"] if settings.debug else None,
                reload_excludes=["logs/*", "*.log"] if settings.debug else None,
            )
    except KeyboardInterrupt:
        print("\n收到退出指令 (Ctrl+C)")
    except Exception as e:
        print("\n" + "="*70)
        print("❌ 程序运行出错！")
        print("="*70)
        traceback.print_exc()
        print("="*70)