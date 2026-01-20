"""
质控记录（T_ZKJL）CRUD模块
负责管理类器官质控记录的创建、更新和查询
"""
import json
from typing import Dict, Any, Optional
from app.db.factory import get_adapter
from app.crud.file_center import insert_file_record


def create_or_update_qc_record(
    gdid: int,
    qc_data: Dict[str, Any],
    operator: str = None
) -> int:
    """
    创建或更新质控记录
    
    Args:
        gdid: 工单ID
        qc_data: 质控数据字典，包含以下字段：
            - F_QKMS: 类器官生长情况描述
            - F_IMG_D1M4: 接种第一天明场4倍图（文件ID）
            - F_IMG_D1M20: 接种第一天明场20倍图（文件ID）
            - F_IMG_D3M4: 接种第三天明场4倍图（文件ID）
            - F_IMG_D3M20: 接种第三天明场20倍图（文件ID）
            - F_IMG_D7M4: 接种第七天明场4倍图（文件ID）
            - F_IMG_D7M20: 接种第七天明场20倍图（文件ID）
            - F_PYTS: 培养天数
            - F_XT: 形态
            - F_SFWR: 是否污染
            - F_XBSL: 细胞数量
            - F_XBHL: 细胞活率
            - F_ZYT: 支原体检测结果
            - F_PDF: PDF文件ID（可选）
        operator: 操作人
        
    Returns:
        质控记录ID
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 检查是否已存在记录
        cur.execute(
            "SELECT F_ID, F_PDF FROM T_ZKJL WHERE F_GDID = ? ORDER BY F_ID DESC",
            (gdid,)
        )
        cur_paginated, cur_extra = adapter.paginate(
            "SELECT F_ID, F_PDF FROM T_ZKJL WHERE F_GDID = ? ORDER BY F_ID DESC",
            0, 1
        )
        rows, cols = adapter.fetch_all(cur_paginated, [gdid] + cur_extra)
        existing = dict(zip(cols, rows[0])) if rows else None
        
        # 准备数据
        f_qkms = qc_data.get("F_QKMS")
        f_img_d1m4 = qc_data.get("F_IMG_D1M4")
        f_img_d1m20 = qc_data.get("F_IMG_D1M20")
        f_img_d3m4 = qc_data.get("F_IMG_D3M4")
        f_img_d3m20 = qc_data.get("F_IMG_D3M20")
        f_img_d7m4 = qc_data.get("F_IMG_D7M4")
        f_img_d7m20 = qc_data.get("F_IMG_D7M20")
        f_pyts = qc_data.get("F_PYTS")
        f_xt = qc_data.get("F_XT")
        f_sfwr = qc_data.get("F_SFWR")
        f_xbsl = qc_data.get("F_XBSL")
        f_xbhl = qc_data.get("F_XBHL")
        f_zyt = qc_data.get("F_ZYT")
        
        # 处理F_PDF字段
        f_pdf = None
        if qc_data.get("F_PDF"):
            # 如果传入了PDF文件ID，需要更新或初始化F_PDF JSON
            pdf_data = {"generated": None, "uploaded": []}
            if existing and existing.get("F_PDF"):
                try:
                    pdf_data = json.loads(existing["F_PDF"])
                except Exception:
                    pass
            pdf_data["generated"] = qc_data["F_PDF"]
            f_pdf = json.dumps(pdf_data, ensure_ascii=False)
        elif existing and existing.get("F_PDF"):
            # 保留原有的F_PDF值
            f_pdf = existing["F_PDF"]
        
        if existing:
            # 更新现有记录
            zkjl_id = existing["F_ID"]
            cur.execute(
                """
                UPDATE T_ZKJL SET
                    F_QKMS = ?,
                    F_IMG_D1M4 = ?,
                    F_IMG_D1M20 = ?,
                    F_IMG_D3M4 = ?,
                    F_IMG_D3M20 = ?,
                    F_IMG_D7M4 = ?,
                    F_IMG_D7M20 = ?,
                    F_PYTS = ?,
                    F_XT = ?,
                    F_SFWR = ?,
                    F_XBSL = ?,
                    F_XBHL = ?,
                    F_ZYT = ?,
                    F_PDF = ?,
                    F_GXSJ = GETDATE()
                WHERE F_ID = ?
                """,
                (
                    f_qkms, f_img_d1m4, f_img_d1m20, f_img_d3m4, f_img_d3m20,
                    f_img_d7m4, f_img_d7m20, f_pyts, f_xt, f_sfwr, f_xbsl,
                    f_xbhl, f_zyt, f_pdf, zkjl_id
                )
            )
            print(f"更新质控记录: ZKJL_ID={zkjl_id}, GDID={gdid}")
        else:
            # 插入新记录
            cur.execute(
                """
                INSERT INTO T_ZKJL (
                    F_GDID, F_QKMS, F_IMG_D1M4, F_IMG_D1M20, F_IMG_D3M4, F_IMG_D3M20,
                    F_IMG_D7M4, F_IMG_D7M20, F_PYTS, F_XT, F_SFWR, F_XBSL, F_XBHL,
                    F_ZYT, F_PDF, F_JLR
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gdid, f_qkms, f_img_d1m4, f_img_d1m20, f_img_d3m4, f_img_d3m20,
                    f_img_d7m4, f_img_d7m20, f_pyts, f_xt, f_sfwr, f_xbsl, f_xbhl,
                    f_zyt, f_pdf, operator
                )
            )
            cur.execute("SELECT MAX(F_ID) FROM T_ZKJL WHERE F_GDID = ?", (gdid,))
            zkjl_id = cur.fetchone()[0]
            print(f"创建质控记录: ZKJL_ID={zkjl_id}, GDID={gdid}")
        
        conn.commit()
        return int(zkjl_id)


def get_qc_record_by_gdid(gdid: int) -> Optional[Dict[str, Any]]:
    """
    根据工单ID查询质控记录
    
    Args:
        gdid: 工单ID
        
    Returns:
        质控记录字典，如果不存在则返回None
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        sql = "SELECT * FROM T_ZKJL WHERE F_GDID = ? ORDER BY F_ID DESC"
        paginated, extra = adapter.paginate(sql, 0, 1)
        rows, cols = adapter.fetch_all(paginated, [gdid] + extra)
        
        if not rows:
            return None
        
        record = dict(zip(cols, rows[0]))
        
        # 解析F_PDF字段（质控单PDF）
        if record.get("F_PDF"):
            try:
                record["F_PDF_PARSED"] = json.loads(record["F_PDF"])
            except Exception:
                record["F_PDF_PARSED"] = {"generated": None, "uploaded": []}
        else:
            record["F_PDF_PARSED"] = {"generated": None, "uploaded": []}
        
        # 解析F_PDF_FXJC字段（放行检测PDF）
        if record.get("F_PDF_FXJC"):
            try:
                record["F_PDF_FXJC_PARSED"] = json.loads(record["F_PDF_FXJC"])
            except Exception:
                record["F_PDF_FXJC_PARSED"] = {"generated": None, "uploaded": []}
        else:
            record["F_PDF_FXJC_PARSED"] = {"generated": None, "uploaded": []}
        
        return record


def update_qc_record_pdf(gdid: int, pdf_file_id: int):
    """
    更新质控记录的PDF文件ID
    
    Args:
        gdid: 工单ID
        pdf_file_id: PDF文件ID
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 获取现有记录
        record = get_qc_record_by_gdid(gdid)
        if not record:
            # 如果没有质控记录，创建一个空记录
            create_or_update_qc_record(gdid, {"F_PDF": pdf_file_id})
            return
        
        # 更新PDF字段
        pdf_data = record.get("F_PDF_PARSED", {"generated": None, "uploaded": []})
        pdf_data["generated"] = pdf_file_id
        
        cur.execute(
            "UPDATE T_ZKJL SET F_PDF = ?, F_GXSJ = GETDATE() WHERE F_GDID = ?",
            (json.dumps(pdf_data, ensure_ascii=False), gdid)
        )
        conn.commit()
        print(f"更新质控记录PDF: GDID={gdid}, PDF_ID={pdf_file_id}")


def update_release_inspection_pdf(gdid: int, pdf_file_id: int):
    """
    更新放行检测PDF文件ID（JSON格式，支持后续人为上传）
    
    Args:
        gdid: 工单ID
        pdf_file_id: 放行检测PDF文件ID
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 检查质控记录是否存在
        paginated, extra = adapter.paginate(
            "SELECT F_PDF_FXJC FROM T_ZKJL WHERE F_GDID = ? ORDER BY F_ID DESC",
            0, 1
        )
        rows, cols = adapter.fetch_all(paginated, [gdid] + extra)
        
        # 构建JSON格式的PDF数据
        if rows and rows[0][0]:
            # 已有数据，更新generated字段
            try:
                pdf_data = json.loads(rows[0][0])
                if not isinstance(pdf_data, dict):
                    pdf_data = {"generated": None, "uploaded": []}
            except Exception:
                pdf_data = {"generated": None, "uploaded": []}
        else:
            # 初始化
            pdf_data = {"generated": None, "uploaded": []}
        
        pdf_data["generated"] = pdf_file_id
        pdf_json = json.dumps(pdf_data, ensure_ascii=False)
        
        if not rows:
            # 如果没有质控记录，创建一个基本记录
            cur.execute(
                "INSERT INTO T_ZKJL (F_GDID, F_PDF_FXJC) VALUES (?, ?)",
                (gdid, pdf_json)
            )
            print(f"创建质控记录并设置放行检测PDF（JSON格式）: GDID={gdid}, PDF_ID={pdf_file_id}")
        else:
            # 更新放行检测PDF字段
            cur.execute(
                "UPDATE T_ZKJL SET F_PDF_FXJC = ?, F_GXSJ = GETDATE() WHERE F_GDID = ?",
                (pdf_json, gdid)
            )
            print(f"更新放行检测PDF（JSON格式）: GDID={gdid}, PDF_ID={pdf_file_id}")
        
        conn.commit()

