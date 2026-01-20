# -*- mode: python ; coding: utf-8 -*-

"""
Bio9_organ Linux/麒麟 打包配置文件

使用方法：
    pyinstaller build_linux.spec

打包后的文件位于：
    dist/Bio9_organ/Bio9_organ
"""

import sys
from pathlib import Path

# 项目根目录
project_root = Path('.').resolve()

# 收集所有应用文件
app_datas = [
    ('app', 'app'),                      # 应用代码
    ('app/templates', 'app/templates'),  # 模板文件
    ('app/config', 'config'),            # 配置文件 - 直接放在程序根目录的 config/ 下
]

# 收集额外的数据文件（如果存在）
extra_files = [
    '.env.example',
    'PACKAGE_README.md',
    'DEPLOYMENT.md',
    'LINUX_SETUP.md',
    'PYINSTALLER_PACKAGE_GUIDE.md',
]
for f in extra_files:
    if Path(f).exists():
        app_datas.append((f, '.'))

# 如果 PACKAGE_README.md 存在，也复制一份为 README.md
if Path('PACKAGE_README.md').exists():
    app_datas.append(('PACKAGE_README.md', 'README.md'))

# 隐藏导入（PyInstaller 可能无法自动检测的模块）
hidden_imports = [
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'fastapi',
    'starlette',
    'pydantic',
    'pydantic_core',
    'pyodbc',
    'docx',
    'docxtpl',
    'jinja2',
    'lxml',
    'lxml.etree',
    'lxml._elementpath',
    'openpyxl',
    'python-jose',
    'passlib',
    'passlib.handlers',
    'passlib.handlers.bcrypt',
    'bcrypt',
    'multipart',
    'python_multipart',
    'email_validator',
    'dnspython',
    'motor',
    'pymongo',
    'mongoengine',
    'APScheduler',
    'pytz',
    'tzlocal',
    'requests',
    'httpx',
    'anyio',
    'sniffio',
    'h11',
    'httpcore',
    'certifi',
    'uvloop',  # Linux 专用
]

# 排除不需要的模块（减小打包体积）
excludes = [
    # 注意：numpy、pandas、matplotlib、scipy 项目中有使用，不能排除
    # PIL/Pillow 是 matplotlib 的依赖，也不能排除
    'tkinter',
    'PyQt5',
    'wx',
]

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=app_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Bio9_organ',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # 显示控制台输出
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Bio9_organ',
)

# 打包完成后的提示
print("\n" + "="*70)
print("✓ 打包完成！")
print("="*70)
print(f"可执行文件位于: dist/Bio9_organ/Bio9_organ")
print("\n部署步骤：")
print("1. 压缩打包文件:")
print("   tar -czf Bio9_organ_linux.tar.gz -C dist Bio9_organ/")
print("2. 上传到目标服务器并解压")
print("3. 复制 .env.example 为 .env 并配置")
print("4. 创建 upload/ 和 logs/ 目录")
print("5. 安装 LibreOffice: sudo apt-get install libreoffice")
print("6. 设置执行权限: chmod +x Bio9_organ")
print("7. 运行: ./Bio9_organ")
print("="*70 + "\n")

