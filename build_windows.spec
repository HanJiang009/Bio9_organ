# -*- mode: python ; coding: utf-8 -*-

"""
Bio9_organ Windows 打包配置文件

使用方法：
    pyinstaller build_windows.spec

打包后的文件位于：
    dist/Bio9_organ/Bio9_organ.exe
"""

import sys
from pathlib import Path

# 项目根目录
project_root = Path('.').resolve()
parent_root = project_root.parent  # pywork 目录（用于找到 bio9_license）

# 收集所有数据文件（不包括 .py 源代码，PyInstaller 会自动编译）
app_datas = [
    # ❌ 不要添加 ('app', 'app')，会导致源码泄露！
    # ✅ 只添加真正的数据文件
    ('app/templates', 'app/templates'),  # Word 模板文件
]

# 检查并添加 static 静态文件（如果存在）
static_path = project_root / 'static'
if static_path.exists():
    app_datas.append(('static', 'static'))
    print(f"✅ 找到 static 目录: {static_path}")

# 检查并添加 app/config 中的业务配置（示例数据等）
app_config_path = project_root / 'app' / 'config'
if app_config_path.exists():
    app_datas.append(('app/config', 'app/config'))
    print(f"✅ 找到 app/config 目录: {app_config_path}")

# ⚠️ bio9_license 通过 hiddenimports 和 hooks 自动打包，不要作为 datas 添加！
# 作为 datas 会导致源码泄露，PyInstaller 会自动分析并编译为字节码
bio9_license_path = parent_root / 'bio9_license'
if bio9_license_path.exists():
    print(f"✅ 找到 bio9_license 模块: {bio9_license_path}")
    print(f"   将通过 PyInstaller 自动分析和编译（不泄露源码）")
else:
    print(f"⚠️  未找到 bio9_license 模块: {bio9_license_path}")

# 收集额外的数据文件（如果存在）
extra_files = [
    '.env.example',
    'PACKAGE_README.md',
    'DEPLOYMENT.md',
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
    # ✅ Bio9 授权模块（重要！）
    'bio9_license',
    'bio9_license.fingerprint',
    'bio9_license.license_manager',
    'bio9_license.guard',
    
    # 加密相关（授权系统依赖）
    'cryptography',
    'cryptography.fernet',
    'cryptography.hazmat',
    'cryptography.hazmat.primitives',
    'cryptography.hazmat.primitives.ciphers',
    'cryptography.hazmat.backends',
    'cryptography.hazmat.backends.openssl',
    
    # FastAPI 和 Web 服务
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
    
    # 数据库
    'pyodbc',
    'dmPython',
    
    # 文档处理
    'docx',
    'docxtpl',
    'jinja2',
    'lxml',
    'lxml.etree',
    'lxml._elementpath',
    'openpyxl',
    
    # 认证和加密
    'python-jose',
    'passlib',
    'passlib.handlers',
    'passlib.handlers.bcrypt',
    'bcrypt',
    
    # 文件上传
    'multipart',
    'python_multipart',
    
    # 其他
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
    pathex=[
        str(project_root),    # Bio9_organ 目录
        str(parent_root),     # ✅ pywork 目录（用于找到 bio9_license）
    ],
    binaries=[],
    datas=app_datas,
    hiddenimports=hidden_imports,
    hookspath=['hooks'],     # ✅ 使用自定义 hook 目录
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
    console=True,  # 显示控制台窗口，方便查看日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # 如果有图标文件，可以指定：icon='app/static/icon.ico'
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
print("✅ 打包完成！")
print("="*70)
print(f"可执行文件位于: dist/Bio9_organ/Bio9_organ.exe")
print("\n📦 发布包准备步骤：")
print("1. 复制 dist/Bio9_organ/ 目录到发布位置")
print("2. 在 Bio9_organ/ 目录下创建 config/ 文件夹")
print("3. ⚠️  复制授权文件到 config/ 目录：")
print("   - config/secret.key    ← 授权密钥（必须）")
print("   - config/license.key   ← 授权码（客户添加）")
print("4. 复制 .env.example 为 .env 并配置数据库")
print("5. 创建 uploads/ 和 logs/ 目录")
print("6. 安装 LibreOffice（用于 PDF 转换）")
print("7. 运行 Bio9_organ.exe")
print("\n⚠️  注意：")
print("   - secret.key 和 license.key 必须放在 exe 同级的 config/ 目录")
print("   - 不要放在 _internal/ 目录里")
print("="*70 + "\n")

