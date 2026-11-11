"""
类器官质控模块 - API路由
"""
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, Body, Request
from pydantic import BaseModel

from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional, get_current_user
from app.crud import organoid_qc as crud
from app.crud import qc_upload
from app.schemas.organoid_culture_tissue import CultureOrderListQuery
from app.schemas.common import ApiResponse


router = APIRouter(prefix="/organoid-qc", tags=["organoid_qc"])


# Schema for delete upload record
class DeleteUploadRecordRequest(BaseModel):
    F_ID: int


@router.post("/orders/list", response_model=ApiResponse[Dict[str, Any]], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["organoid_qc"]))])
def list_qc_culture_orders(payload: CultureOrderListQuery, current_user: dict = Depends(get_current_user_optional)):
    """
    获取类器官质控培养工单列表
    
    相比普通培养工单列表，额外返回质控相关字段：
    - F_ZQTYS: 知情同意书 (来自T_YBZJ.F_ZQTYS；若为空但已上传文件则显示「是」)
    - F_LTSJ: 离体时间 (来自T_YCL.F_LTSJ)
    - F_KSCLSJ: 开始处理时间 (来自T_YCL.F_KSCLSJ，预处理信息)
    - F_JSZK: 接收质控PDF (来自T_YBZJ.F_PDF)
    - F_YBZLPFB: 样本质量评分表PDF (来自T_YCL.F_PDF)
    - F_LQGZK: 类器官质控PDF (来自T_ZKJL.F_PDF)
    - F_LQGFX: 类器官放行报告PDF (来自T_ZKJL.F_PDF_FXJC)
    - F_YWSYBG: 药物实验报告PDF (来自T_SYJH.F_BG_PDF，关联最新实验计划)
    - F_DWSYBG: 动物实验报告PDF (来自T_DWSY_JH.F_BG_PDF，关联最新实验计划)
    - F_SYCLSX: 剩余处理时效（小时）= 48 - (F_KSCLSJ或当前时间 - F_LTSJ)
    
    新增筛选条件：
    - F_SQR: 培养人筛选
    - F_ZZBW: 组织部位筛选
    
    入参：
    {
        "F_XM": "患者姓名",  // 可选，支持模糊查询
        "F_BRBH": "病人编号",  // 可选，支持模糊查询
        "F_ZZID": "标本号",  // 可选，支持模糊查询
        "F_GDBH": "工单编号",  // 可选，支持模糊查询
        "F_GDLX": "工单类型",  // 可选，支持模糊查询
        "F_DC": "代次",  // 可选，支持模糊查询
        "F_DCID": "代次ID",  // 可选，支持模糊查询
        "F_ZZBW": "组织部位",  // 可选，支持模糊查询
        "F_XYZL": "新辅助治疗",  // 可选，支持模糊查询
        "F_SQR": "培养人",  // 可选，支持模糊查询
        "F_GDZT": "工单状态",  // 可选，支持模糊查询
        "F_PYSJ_START": "培养开始日期",  // 可选，格式：YYYY-MM-DD
        "F_PYSJ_END": "培养结束日期",  // 可选，格式：YYYY-MM-DD
        "F_HSSL_MIN": 最小细胞数量,  // 可选
        "F_HSSL_MAX": 最大细胞数量,  // 可选
        "skip": 0,  // 可选，默认0
        "limit": 20  // 可选，默认20
    }
    
    返回：
    {
        "code": 1,
        "msg": "查询成功，共 X 条",
        "data": {
            "items": [
                {
                    "F_ID": 1,
                    "F_GDBH": "工单编号",
                    "F_ZZID": "标本号",
                    "F_XM": "患者姓名",
                    "F_BRBH": "病人编号",
                    "F_BBLX": "标本类型",
                    "F_DC": "代次",
                    "F_DCID": "代次ID",
                    "F_ZZBW": "组织部位",
                    "F_XYZL": "新辅助治疗",
                    "F_QYFS": "取样方式",
                    "F_YBLY": "样本来源",
                    "F_YBGG": "样本规格",
                    "F_CDQXBZS": "传代前细胞总数",
                    "F_FSQXBZS": "复苏前细胞总数",
                    "F_SQR": "申请人",
                    "F_PYSJ": "培养时间",
                    "F_FJ": [],  // 附件列表
                    "F_CJSJ": "创建时间",
                    "F_GDLX": "工单类型",
                    "F_GDZT": "工单状态",
                    "F_SHZT": "审核状态",
                    "F_DCJL": "冻存记录ID",
                    "F_YCL": true/false,  // 是否有预处理记录
                    "F_PARENT_ID": 父工单ID,
                    "F_HSSJ": "回收时间",
                    "F_HSSL": 回收数量,
                    "F_PYTS": 培养天数,
                    // === 以下是质控专属字段 ===
                    "F_ZQTYS": "患者本人签署",  // 知情同意书（来自T_YBZJ.F_ZQTYS）
                    "F_LTSJ": "2025-11-13 10:00:00",  // 离体时间（来自T_YCL.F_LTSJ）
                    "F_KSCLSJ": "2025-11-13 22:00:00",  // 开始处理时间（来自T_YCL.F_KSCLSJ）
                    "F_JSZK": {  // 接收质控PDF
                        "generated": 文件ID或null,
                        "uploaded": []
                    },
                    "F_YBZLPFB": {  // 样本质量评分表PDF
                        "generated": 文件ID或null,
                        "uploaded": []
                    },
                    "F_LQGZK": {  // 类器官质控PDF
                        "generated": 文件ID或null,
                        "uploaded": []
                    },
                    "F_LQGFX": {  // 类器官放行报告PDF
                        "generated": 文件ID或null,
                        "uploaded": []
                    },
                    "F_YWSYBG": {  // 药物试验报告PDF（来自实验计划）
                        "generated": {
                            "file_id": 2456,
                            "filename": "药物试验报告_20.pdf",
                            "url": "drug_test_report/xxx.pdf"
                        },
                        "uploaded": []
                    },
                    "F_SYCLSX": 35.5  // 剩余处理时效（小时），48小时内剩余时间
                }
            ],
            "total": 100,
            "skip": 0,
            "limit": 20
        }
    }
    """
    from app.core.data_scope import resolve_data_scope
    data = crud.list_qc_culture_orders(
        payload.model_dump(),
        user=current_user,
        scope=resolve_data_scope(current_user, "organoid_qc"),
    )
    return ok(data, f"查询成功，共 {data.get('total', 0)} 条")


@router.post("/upload", response_model=ApiResponse[Dict[str, Any]], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_qc"]))])
async def upload_qc_file_api(
    F_ZKJD: str = Form(..., description="质控阶段（知情同意书、接收质控、样本质量评分表、类器官质控、类器官放行报告、药物实验报告、动物实验报告）"),
    F_FJ: List[UploadFile] = File(..., description="上传的文件列表"),
    F_GDID: int | None = Form(None, description="工单ID（与F_GDBH/F_ZZID三选一）"),
    F_GDBH: str | None = Form(None, description="工单编号（与F_GDID/F_ZZID三选一）"),
    F_ZZID: str | None = Form(None, description="标本号（与F_GDID/F_GDBH三选一，仅支持知情同意书和接收质控）"),
    current_user: dict = Depends(get_current_user)
):
    """
    质控文件统一上传接口
    
    **参数说明：**
    - F_GDID、F_GDBH、F_ZZID 三选一
    - 使用 F_ZZID 时，仅允许上传"知情同意书"和"接收质控"（因为这两个不强关联培养工单）
    - 其他质控阶段必须使用 F_GDID 或 F_GDBH
    
    **质控阶段取值：**
    - 知情同意书（可用F_ZZID）
    - 接收质控（可用F_ZZID）
    - 样本质量评分表（必须用F_GDID或F_GDBH）
    - 类器官质控（必须用F_GDID或F_GDBH）
    - 类器官放行报告（必须用F_GDID或F_GDBH）
    - 药物实验报告（必须用F_GDID或F_GDBH）
    - 动物实验报告（必须用F_GDID或F_GDBH）
    
    **请求示例1（使用工单ID）：**
    ```
    POST /organoid-qc/upload
    Content-Type: multipart/form-data
    
    F_ZKJD: "类器官质控"
    F_GDID: 93
    F_FJ: [文件1, 文件2]
    ```
    
    **请求示例2（使用工单编号）：**
    ```
    POST /organoid-qc/upload
    Content-Type: multipart/form-data
    
    F_ZKJD: "类器官质控"
    F_GDBH: "S234332320251103P020251111155334"
    F_FJ: [文件1]
    ```
    
    **请求示例3（使用标本号）：**
    ```
    POST /organoid-qc/upload
    Content-Type: multipart/form-data
    
    F_ZKJD: "知情同意书"
    F_ZZID: "S234332320251103"
    F_FJ: [文件1, 文件2]
    ```
    
    **响应示例：**
    ```json
    {
        "code": 0,
        "message": "类器官质控上传成功",
        "data": {
            "success": true,
            "message": "类器官质控上传成功",
            "file_ids": [2301, 2302],
            "gdid": 93,
            "zzid": "S234332320251103",
            "gdbh": "S234332320251103P020251111155334"
        }
    }
    ```
    """
    # 1. 上传文件到文件中心
    from app.core.storage import build_upload_path
    from app.crud.file_center import insert_file_record
    
    uploaded_file_ids = []
    
    try:
        for file in F_FJ:
            # 保存文件
            dst_path, rel_path = build_upload_path(file.filename)
            content = await file.read()
            dst_path.write_bytes(content)
            
            # 登记文件
            file_id = insert_file_record(rel_path, file.filename)
            if file_id:
                uploaded_file_ids.append(int(file_id))
        
        if not uploaded_file_ids:
            return err("文件上传失败")
        
        # 2. 调用CRUD逻辑，将文件ID写入对应表字段
        uploader = current_user.get("F_Ktype") or current_user.get("F_YSM") or "未知"
        result = qc_upload.upload_qc_file(
            zkjd=F_ZKJD,
            file_ids=uploaded_file_ids,
            uploader=uploader,
            gdid=F_GDID,
            gdbh=F_GDBH,
            zzid=F_ZZID
        )
        
        if result.get("success"):
            return ok(result, result.get("message", "上传成功"))
        else:
            return err(result.get("message", "上传失败"))
            
    except Exception as e:
        return err(f"上传异常: {str(e)}")


@router.post("/upload-logs", response_model=ApiResponse[Dict[str, Any]], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["organoid_qc"]))])
async def get_upload_logs_api(request: Request):
    """
    查询质控上传日志

    **支持两种请求格式：**
    1. JSON格式（推荐）：`Content-Type: application/json`
    2. form-data格式：`Content-Type: application/x-www-form-urlencoded`

    > 注意：早期版本仅支持 form-data 格式，若请求以 JSON 方式发送，参数会被静默忽略，
    > 导致查询条件全部失效并返回全表数据。现已修复为自动识别请求的 Content-Type。

    **参数说明：**
    - 使用 F_ZZID（标本号）查询时，自动过滤只返回"知情同意书"和"接收质控"两个阶段的记录
    - 使用 F_GDID 或 F_GDBH 查询时，返回所有阶段的记录
    - 可以同时使用 F_ZKJD 指定质控阶段进行进一步过滤

    **请求示例1（JSON格式，工单号查询）：**
    ```json
    POST /organoid-qc/upload-logs
    Content-Type: application/json

    {
        "F_GDBH": "S234332320251103P020251111155334",
        "skip": 0,
        "limit": 50
    }
    ```

    **请求示例2（form-data格式，标本号查询，自动过滤阶段）：**
    ```
    POST /organoid-qc/upload-logs
    Content-Type: application/x-www-form-urlencoded

    F_ZZID: S234332320251103
    skip: 0
    limit: 50
    ```

    **响应示例：**
    ```json
    {
        "code": 0,
        "message": "查询成功",
        "data": {
            "items": [
                {
                    "F_ID": 1,
                    "F_ZZID": "S234332320251103",
                    "F_GDBH": "S234332320251103P020251111155334",
                    "F_GDID": 93,
                    "F_ZKJD": "类器官质控",
                    "F_FJ": [
                        {
                            "id": 2301,
                            "name": "质控报告.pdf",
                            "path": "/uploads/2025/11/14/xxx.pdf"
                        },
                        {
                            "id": 2302,
                            "name": "检测数据.pdf",
                            "path": "/uploads/2025/11/14/yyy.pdf"
                        }
                    ],
                    "F_SCR": "张三",
                    "F_SCSJ": "2025-11-14 10:30:00"
                }
            ],
            "total": 1
        }
    }
    ```

    **字段说明：**
    - `F_FJ`: 附件列表，包含文件ID、原始文件名和存储路径
      - `id`: 文件ID
      - `name`: 原始文件名
      - `path`: 文件存储路径（相对路径，可直接用于构建下载URL）
    """
    # 根据 Content-Type 解析请求数据，兼容 JSON 与 form-data 两种格式
    content_type = request.headers.get("content-type", "").lower()
    try:
        if "application/json" in content_type:
            body = await request.json()
        else:
            form_data = await request.form()
            body = dict(form_data)
    except Exception as e:
        return err(f"请求参数解析失败: {str(e)}")

    def _to_int(v, default=None):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except Exception:
            return default

    def _to_str(v):
        if v is None:
            return None
        v = str(v).strip()
        return v or None

    F_ZZID = _to_str(body.get("F_ZZID"))
    F_GDBH = _to_str(body.get("F_GDBH"))
    F_GDID = _to_int(body.get("F_GDID"))
    F_ZKJD = _to_str(body.get("F_ZKJD"))
    skip = _to_int(body.get("skip"), 0)
    limit = _to_int(body.get("limit"), 50)

    try:
        data = qc_upload.get_upload_logs(
            zzid=F_ZZID,
            gdbh=F_GDBH,
            gdid=F_GDID,
            zkjd=F_ZKJD,
            skip=skip,
            limit=limit
        )
        return ok(data, "查询成功")
    except Exception as e:
        return err(f"查询失败: {str(e)}")


@router.post("/delete-upload-record", response_model=ApiResponse[Dict[str, Any]], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_qc"]))])
async def delete_upload_record_api(request: Request):
    """
    删除质控上传记录
    
    **支持两种请求格式：**
    1. JSON格式（推荐）
    2. form-data格式
    
    **功能说明：**
    - 删除 T_ZKSC 表中的上传记录
    - 同时从对应的目标表（T_YBZJ、T_YCL、T_ZKJL）中移除关联的文件ID
    - 删除操作是事务性的，失败会自动回滚
    
    **请求示例1（JSON格式）：**
    ```
    POST /organoid-qc/delete-upload-record
    Content-Type: application/json
    
    {
        "F_ID": 1
    }
    ```
    
    **请求示例2（form-data格式）：**
    ```
    POST /organoid-qc/delete-upload-record
    Content-Type: application/x-www-form-urlencoded
    
    F_ID=1
    ```
    
    **响应示例（成功）：**
    ```json
    {
        "code": 0,
        "message": "上传记录已删除，已从类器官质控中移除 2 个文件",
        "data": {
            "success": true,
            "message": "上传记录已删除，已从类器官质控中移除 2 个文件"
        }
    }
    ```
    
    **响应示例（失败）：**
    ```json
    {
        "code": 1,
        "message": "上传记录 999 不存在",
        "data": null
    }
    ```
    
    **注意事项：**
    - 删除记录后，文件不会从文件系统中删除，仅解除关联
    - 如果文件仍被其他记录引用，不会影响其他记录
    - 建议在删除前确认该记录确实需要删除
    """
    # 根据 Content-Type 解析请求数据
    record_id = None
    content_type = request.headers.get("content-type", "").lower()
    
    try:
        if "application/json" in content_type:
            # JSON 格式
            body = await request.json()
            record_id = body.get("F_ID")
        else:
            # form-data 格式
            form_data = await request.form()
            f_id_value = form_data.get("F_ID")
            if f_id_value is not None:
                record_id = int(f_id_value)
    except Exception as e:
        return err(f"请求参数解析失败: {str(e)}")
    
    if record_id is None:
        return err("缺少参数 F_ID")
    
    try:
        result = qc_upload.delete_upload_record(record_id)
        
        if result.get("success"):
            return ok(result, result.get("message", "删除成功"))
        else:
            return err(result.get("message", "删除失败"))
            
    except Exception as e:
        return err(f"删除异常: {str(e)}")

