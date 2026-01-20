"""
样本接收质控PDF管理模块
职责：管理 T_YBZJ.F_PDF 字段（JSON格式存储PDF文件ID）

与 order_pdf_manager 的区别：
- 基于标本号（F_ZZID）而非工单ID（F_GDID）
- 不提供用户上传接口（日后统一添加）
"""
import json
from typing import Dict, Any, List, Optional
from app.db.factory import get_adapter


def get_pdf_data(zzid: str) -> Dict[str, Any]:
    """
    获取样本接收质控PDF数据
    
    Args:
        zzid: 标本号
        
    Returns:
        {
            "generated": 系统生成的PDF文件ID,
            "uploaded": [用户上传的PDF文件ID列表]
        }
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT F_PDF FROM T_YBZJ WHERE F_ZZID = ? ORDER BY F_ID DESC",
            (zzid,)
        )
        row = cur.fetchone()
        
        if not row or not row[0]:
            return {"generated": None, "uploaded": []}
        
        try:
            pdf_data = json.loads(row[0])
            return {
                "generated": pdf_data.get("generated"),
                "uploaded": pdf_data.get("uploaded", [])
            }
        except Exception as e:
            print(f"警告: 解析F_PDF字段失败 (ZZID: {zzid}): {e}")
            return {"generated": None, "uploaded": []}


def set_generated_pdf(zzid: str, file_id: int) -> bool:
    """
    设置系统生成的PDF文件ID
    
    Args:
        zzid: 标本号
        file_id: 文件ID
        
    Returns:
        是否成功
    """
    adapter = get_adapter()
    
    # 获取现有数据
    pdf_data = get_pdf_data(zzid)
    pdf_data["generated"] = file_id
    
    # 保存
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE T_YBZJ SET F_PDF = ? WHERE F_ZZID = ?",
            (json.dumps(pdf_data, ensure_ascii=False), zzid)
        )
        conn.commit()
        
        print(f"已保存系统生成的PDF文件ID {file_id} 到样本 {zzid}")
        return True


def add_uploaded_pdf(zzid: str, file_id: int) -> bool:
    """
    添加用户上传的PDF文件ID
    
    Args:
        zzid: 标本号
        file_id: 文件ID
        
    Returns:
        是否成功
    """
    adapter = get_adapter()
    
    # 获取现有数据
    pdf_data = get_pdf_data(zzid)
    
    # 添加到上传列表（避免重复）
    if file_id not in pdf_data["uploaded"]:
        pdf_data["uploaded"].append(file_id)
    
    # 保存
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE T_YBZJ SET F_PDF = ? WHERE F_ZZID = ?",
            (json.dumps(pdf_data, ensure_ascii=False), zzid)
        )
        conn.commit()
        
        print(f"已添加用户上传的PDF文件ID {file_id} 到样本 {zzid}")
        return True


def remove_uploaded_pdf(zzid: str, file_id: int) -> bool:
    """
    删除用户上传的PDF文件ID
    
    Args:
        zzid: 标本号
        file_id: 文件ID
        
    Returns:
        是否成功
    """
    adapter = get_adapter()
    
    # 获取现有数据
    pdf_data = get_pdf_data(zzid)
    
    # 从上传列表中删除
    if file_id in pdf_data["uploaded"]:
        pdf_data["uploaded"].remove(file_id)
    
    # 保存
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE T_YBZJ SET F_PDF = ? WHERE F_ZZID = ?",
            (json.dumps(pdf_data, ensure_ascii=False), zzid)
        )
        conn.commit()
        
        print(f"已删除样本 {zzid} 的PDF文件ID {file_id}")
        return True


def get_pdf_list_with_details(zzid: str) -> Dict[str, Any]:
    """
    获取样本接收质控PDF列表（包含文件详细信息）
    
    Args:
        zzid: 标本号
        
    Returns:
        {
            "generated": {
                "file_id": 123,
                "filename": "xxx.pdf",
                "url": "/uploads/pdf/xxx.pdf",
                "created_at": "2025-11-11 10:00:00"
            },
            "uploaded": [
                {
                    "file_id": 456,
                    "filename": "yyy.pdf",
                    "url": "/uploads/xxx/yyy.pdf",
                    "created_at": "2025-11-11 11:00:00"
                }
            ]
        }
    """
    from app.crud.file_center import get_file_by_id
    
    pdf_data = get_pdf_data(zzid)
    result = {
        "generated": None,
        "uploaded": []
    }
    
    # 获取系统生成的PDF详情
    if pdf_data["generated"]:
        file_info = get_file_by_id(pdf_data["generated"])
        if file_info:
            result["generated"] = {
                "file_id": file_info.get("F_ID"),
                "filename": file_info.get("F_ORIGINAL_NAME") or "",
                "url": f"/{file_info.get('F_PATH', '').lstrip('/')}",
                "created_at": str(file_info.get("F_UPLOAD_TIME", ""))
            }
    
    # 获取用户上传的PDF详情
    for file_id in pdf_data["uploaded"]:
        file_info = get_file_by_id(file_id)
        if file_info:
            result["uploaded"].append({
                "file_id": file_info.get("F_ID"),
                "filename": file_info.get("F_ORIGINAL_NAME") or "",
                "url": f"/{file_info.get('F_PATH', '').lstrip('/')}",
                "created_at": str(file_info.get("F_UPLOAD_TIME", ""))
            })
    
    return result

