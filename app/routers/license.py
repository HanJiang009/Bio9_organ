"""
授权信息接口
提供机器指纹查询和授权状态查询
"""
from fastapi import APIRouter
import sys
from pathlib import Path

# 添加授权包到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

try:
    from bio9_license import LicenseGuard
    LICENSE_ENABLED = True
except ImportError:
    LICENSE_ENABLED = False

from ..core.response import ok, err

router = APIRouter(prefix="/license", tags=["授权管理"])


@router.get("/fingerprint")
def get_fingerprint():
    """
    获取当前机器指纹
    客户需要将此指纹提供给供应商以获取授权码
    """
    if not LICENSE_ENABLED:
        return err("授权模块未启用")
    
    try:
        guard = LicenseGuard()
        fingerprint = guard.get_machine_fingerprint()
        
        if not fingerprint:
            return err("无法获取机器指纹")
        
        return ok({
            "fingerprint": fingerprint,
            "short_fingerprint": fingerprint[:16].upper(),
            "system_name": "Bio9_organ"
        }, "获取成功")
    except Exception as e:
        return err(f"获取机器指纹失败: {str(e)}")


@router.get("/info")
def get_license_info():
    """
    查看当前授权信息
    包括客户名称、授权系统、过期时间等
    """
    if not LICENSE_ENABLED:
        return err("授权模块未启用")
    
    try:
        guard = LicenseGuard()
        
        if not guard.is_licensed():
            return ok({
                "licensed": False,
                "system_name": "Bio9_organ",
                "message": "未授权或授权已过期"
            }, "查询成功")
        
        license_data = guard.license_data
        
        # 处理过期日期（None表示永久有效）
        expire_date_raw = license_data.get('expire_date')
        if expire_date_raw is None:
            expire_date_display = "永久"
        else:
            expire_date_display = expire_date_raw.split('T')[0]
        
        return ok({
            "licensed": True,
            "system_name": "Bio9_organ",
            "customer": license_data.get('customer'),
            "authorized_systems": license_data.get('systems', ['*']),
            "issue_date": license_data.get('issue_date', '').split('T')[0],
            "expire_date": expire_date_display,
            "features": license_data.get('features', {})
        }, "查询成功")
    except Exception as e:
        return err(f"查询授权信息失败: {str(e)}")
