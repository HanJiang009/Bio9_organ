"""
工单PDF管理模块
用于管理T_PYGD.F_PDF字段，包括系统生成的PDF和用户上传的PDF
"""
from typing import Dict, Any, List, Optional
import json
from app.db.factory import get_adapter


def get_pdf_data(gdid: int) -> Dict[str, Any]:
    """
    获取工单的PDF数据
    
    Args:
        gdid: 工单ID
        
    Returns:
        {
            "generated": 123,           # 系统生成的PDF文件ID（可能为None）
            "uploaded": [456, 789]      # 用户上传的PDF文件ID列表
        }
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT F_PDF FROM T_PYGD WHERE F_ID = ?", (gdid,))
        row = cur.fetchone()
        
        if not row or not row[0]:
            return {"generated": None, "uploaded": []}
        
        try:
            pdf_data = json.loads(row[0])
            # 确保数据结构正确
            if not isinstance(pdf_data, dict):
                return {"generated": None, "uploaded": []}
            
            generated = pdf_data.get("generated")
            uploaded = pdf_data.get("uploaded", [])
            
            # 确保uploaded是列表
            if not isinstance(uploaded, list):
                uploaded = []
            
            return {
                "generated": generated,
                "uploaded": uploaded
            }
        except:
            return {"generated": None, "uploaded": []}


def set_generated_pdf(gdid: int, file_id: int) -> bool:
    """
    设置系统生成的PDF文件ID（会替换旧的）
    
    Args:
        gdid: 工单ID
        file_id: 新生成的PDF文件ID
        
    Returns:
        是否成功
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 获取当前数据
        current_data = get_pdf_data(gdid)
        
        # 更新generated字段
        current_data["generated"] = file_id
        
        # 保存回数据库
        pdf_json = json.dumps(current_data, ensure_ascii=False)
        cur.execute(
            "UPDATE T_PYGD SET F_PDF = ? WHERE F_ID = ?",
            (pdf_json, gdid)
        )
        conn.commit()
        
        return True


def add_uploaded_pdf(gdid: int, file_id: int) -> bool:
    """
    添加用户上传的PDF文件ID
    
    Args:
        gdid: 工单ID
        file_id: 上传的PDF文件ID
        
    Returns:
        是否成功
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 获取当前数据
        current_data = get_pdf_data(gdid)
        
        # 添加到uploaded列表（去重）
        if file_id not in current_data["uploaded"]:
            current_data["uploaded"].append(file_id)
        
        # 保存回数据库
        pdf_json = json.dumps(current_data, ensure_ascii=False)
        cur.execute(
            "UPDATE T_PYGD SET F_PDF = ? WHERE F_ID = ?",
            (pdf_json, gdid)
        )
        conn.commit()
        
        return True


def remove_uploaded_pdf(gdid: int, file_id: int) -> bool:
    """
    删除用户上传的PDF文件ID
    
    Args:
        gdid: 工单ID
        file_id: 要删除的PDF文件ID
        
    Returns:
        是否成功
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 获取当前数据
        current_data = get_pdf_data(gdid)
        
        # 从uploaded列表中删除
        if file_id in current_data["uploaded"]:
            current_data["uploaded"].remove(file_id)
        
        # 保存回数据库
        pdf_json = json.dumps(current_data, ensure_ascii=False)
        cur.execute(
            "UPDATE T_PYGD SET F_PDF = ? WHERE F_ID = ?",
            (pdf_json, gdid)
        )
        conn.commit()
        
        return True


def get_pdf_list_with_details(gdid: int) -> Dict[str, Any]:
    """
    获取工单的PDF列表（包含文件详细信息）
    
    Args:
        gdid: 工单ID
        
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
    
    pdf_data = get_pdf_data(gdid)
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

