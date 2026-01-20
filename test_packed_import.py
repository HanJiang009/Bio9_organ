"""
测试打包后的 bio9_license 导入
复制此文件到 dist/Bio9_organ/ 目录运行
"""
import sys
from pathlib import Path

print("=" * 70)
print("测试 bio9_license 模块导入")
print("=" * 70)

# 检查是否在打包环境
if getattr(sys, 'frozen', False):
    print(f"✅ 运行环境: 打包后的可执行文件")
    print(f"   sys.executable: {sys.executable}")
    print(f"   sys._MEIPASS: {sys._MEIPASS}")
else:
    print(f"✅ 运行环境: 开发模式")

print(f"\n当前 Python 路径:")
for i, path in enumerate(sys.path[:5], 1):
    print(f"  {i}. {path}")

# 尝试导入 bio9_license
print("\n尝试导入 bio9_license...")
try:
    import bio9_license
    print(f"✅ bio9_license 导入成功")
    print(f"   版本: {bio9_license.__version__}")
    print(f"   位置: {bio9_license.__file__}")
    
    from bio9_license import LicenseGuard, create_license_middleware
    print(f"✅ 成功导入: LicenseGuard, create_license_middleware")
except ImportError as e:
    print(f"❌ 导入失败: {e}")
