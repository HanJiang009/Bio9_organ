"""
PyInstaller Hook for bio9_license module
仅声明隐藏导入，不收集数据文件（防止源码泄露）
"""

# ⚠️ 不使用 collect_all，因为它会收集源码！
# ✅ 只通过 hiddenimports 让 PyInstaller 自动分析和编译

# 确保所有子模块都被包含
hiddenimports = [
    'bio9_license',
    'bio9_license.fingerprint',
    'bio9_license.license_manager',
    'bio9_license.guard',
    # Cryptography 依赖
    'cryptography',
    'cryptography.fernet',
    'cryptography.hazmat',
    'cryptography.hazmat.primitives',
    'cryptography.hazmat.primitives.ciphers',
    'cryptography.hazmat.primitives.ciphers.aead',
    'cryptography.hazmat.backends',
    'cryptography.hazmat.backends.openssl',
    'cryptography.hazmat.backends.openssl.backend',
]

# ⚠️ 明确设置：不收集任何数据文件或二进制文件
datas = []
binaries = []
