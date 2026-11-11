"""
配置文件初始化模块

打包后第一次运行时，自动从 _internal 复制配置文件到程序目录
"""
import sys
import shutil
from pathlib import Path


def init_config_files():
    """
    打包环境下，自动从 _internal 复制配置文件到程序目录
    
    这样用户可以直接修改程序目录下的配置文件，无需进入 _internal
    """
    # 只在打包环境执行
    if not (getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')):
        return
    
    # 源目录：_internal/app/config/
    source_config = Path(sys._MEIPASS) / "app" / "config"
    
    # 目标目录：Bio9_organ.exe 同级的 config/
    target_config = Path(sys.executable).parent / "config"
    
    # 如果目标目录已存在，不覆盖（保留用户的修改）
    if target_config.exists():
        return
    
    # 复制整个 config 目录，使用 UTF-8 编码处理文本文件
    try:
        if not source_config.exists():
            print(f"⚠ 源配置目录不存在: {source_config}")
            return
        
        # 创建目标目录
        target_config.mkdir(parents=True, exist_ok=True)
        
        # 递归复制所有文件
        for source_file in source_config.rglob("*"):
            if source_file.is_file():
                # 计算相对路径
                rel_path = source_file.relative_to(source_config)
                target_file = target_config / rel_path
                
                # 创建父目录
                target_file.parent.mkdir(parents=True, exist_ok=True)
                
                # 复制文件（对 JSON 文件特殊处理以确保编码正确）
                if source_file.suffix == '.json':
                    # JSON 文件：读取后重新写入，确保 UTF-8 编码
                    import json
                    data = json.loads(source_file.read_text(encoding='utf-8-sig'))
                    target_file.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding='utf-8'
                    )
                else:
                    # 其他文件：直接复制
                    shutil.copy2(source_file, target_file)
        
        print(f"✓ 已初始化配置文件目录: {target_config}")
    except Exception as e:
        print(f"⚠ 复制配置文件失败: {e}")
        print(f"  源目录: {source_config}")
        print(f"  目标目录: {target_config}")
        import traceback
        traceback.print_exc()

