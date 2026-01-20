from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, Form, File, UploadFile
from fastapi.responses import StreamingResponse
from app.schemas.common import ApiResponse
from app.schemas.drug_test import (
    ExperimentRecordOut,
    ExperimentRecordDetailQuery,
    ExperimentRecordListQuery,
    GenerateIC50Request,
    GenerateIC50Response,
    PreviewIC50Request,
)
from app.crud import drug_test_result as crud
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.core.storage import build_upload_path
from app.crud.file_center import insert_file_record

router = APIRouter(prefix="/drug-test/record", tags=["drug_test"])


def _resolve_operator(current_user: Dict[str, Any] | None) -> str | None:
    """
    从 token 中解析操作人姓名
    优先级：F_Ktype（姓名）> F_YSM（用户名）> F_NAME
    """
    if not current_user:
        return None
    return (
        current_user.get("F_Ktype")  # ✅ 优先使用姓名字段
        or current_user.get("F_YSM")
        or current_user.get("F_NAME")
    )


@router.post(
    "/save",
    response_model=ApiResponse[ExperimentRecordOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["drug_assay"]))],
)
async def save_experiment_record(
    F_SYJH_ID: int = Form(..., description="实验计划ID（自动判断新增/更新）"),
    KBDS: Optional[str] = Form(None, description="孔板读数JSON"),
    XTXBH: Optional[str] = Form(None, description="形态学变化JSON"),
    XBHL_PJ: Optional[str] = Form(None, description="平均细胞活率JSON"),
    XBHL: Optional[str] = Form(None, description="细胞活率（孔级）JSON"),
    XBHL_WC: Optional[str] = Form(None, description="细胞活率标准误差JSON"),
    GMD: Optional[str] = Form(None, description="光敏度JSON"),
    IC50: Optional[str] = Form(None, description="IC50数据JSON"),
    SYJL: Optional[str] = Form(None, description="实验结论JSON"),
    F_FJ: Optional[List[UploadFile]] = File(None, description="附件数组"),
    current_user: Dict[str, Any] = Depends(get_current_user_optional),
):
    """
    保存实验记录（新增/更新自动判断）
    
    ## 业务规则
    - **一个实验计划只能有一个实验结果记录**
    - 根据 `F_SYJH_ID` 自动判断：
      - 如果该实验计划已有记录 → 更新现有记录
      - 如果该实验计划没有记录 → 新增记录
    
    ## 接口说明
    - 使用 `multipart/form-data` 格式
    - JSON字段中使用 `F_FJ_INDEX` 标记附件位置，后端会自动替换为文件ID
    - 附件统一通过 `F_FJ` 数组上传，按索引 0, 1, 2... 映射
    
    ## 入参示例
    ```
    F_SYJH_ID: 123
    KBDS: {"ZBQ": {"A1": 0.523}, "ZBH": {"A1": 0.412}}
    XTXBH: [{"F_ZBMC": "用药前", "F_FJ_INDEX": [0], "F_XT": "致密圆形"}]
    XBHL_PJ: {"LMC": ["0.01", "0.1"], "HMS": [...], "SJ": {...}}
    IC50: [{"F_ZBID": 2, "QXSJ": "upload", "F_FJ_INDEX": 3}]
    SYJL: {"JGSJ": {...}, "JLWB": "结论文本", "F_FJ_INDEX": [4, 5]}
    F_FJ: [文件0, 文件1, 文件2, 文件3, 文件4, 文件5]
    ```
    """
    # 处理文件上传
    file_ids: List[int] = []
    
    if F_FJ:
        for upload_file in F_FJ:
            dst_path, rel_path = build_upload_path(upload_file.filename)
            content = await upload_file.read()
            dst_path.write_bytes(content)
            fid = insert_file_record(rel_path, upload_file.filename)
            if fid is not None:
                file_ids.append(int(fid))
    
    # 构建数据字典
    data = {
        "F_SYJH_ID": F_SYJH_ID,
        "KBDS": KBDS,
        "XTXBH": XTXBH,
        "XBHL_PJ": XBHL_PJ,
        "XBHL": XBHL,
        "XBHL_WC": XBHL_WC,
        "GMD": GMD,
        "IC50": IC50,
        "SYJL": SYJL,
    }
    
    operator = _resolve_operator(current_user)
    
    try:
        result = crud.save_experiment_record(data, file_ids, operator=operator)
        return ok(result, "保存成功")
    except ValueError as ve:
        return err(str(ve))
    except Exception as e:
        return err(f"保存失败: {e}")


@router.post(
    "/detail",
    response_model=ApiResponse[ExperimentRecordOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["drug_assay"]))],
)
def get_experiment_record_detail(payload: ExperimentRecordDetailQuery):
    """
    获取实验记录详情（根据实验计划ID查询）
    
    ## 入参
    - F_SYJH_ID: 实验计划ID
    
    ## 返回内容
    1. **实验记录数据**：孔板读数、形态学变化、细胞活率等
    2. **实验计划信息**：
       - F_SYMC: 实验名称
       - F_SYKSRQ: 实验开始日期
       - F_SYR: 实验人
       - F_SYJHYMD: 实验计划与目的
    3. **组别信息（ZBXX）**：包含组别名称和浓度梯度
    4. **IC50参数（IC50_PARAMS）**：用于前端生成IC50曲线
       - concentrations: 浓度数组
       - responses: 活率数组
       - custom_files: 用户上传的自定义IC50图片ID（如有）
    
    ## 前端使用示例
    ```javascript
    // 1. 获取详情（使用实验计划ID）
    const detail = await fetch('/drug-test/record/detail', {
      method: 'POST',
      body: JSON.stringify({ F_SYJH_ID: 123 })
    }).then(r => r.json());
    
    // 2. 展示实验计划信息
    console.log(detail.data.F_SYMC);  // 实验名称
    
    // 3. 生成IC50曲线
    detail.data.IC50_PARAMS.forEach(param => {
      if (param.custom_files) {
        // 优先展示用户上传的图片
        showImage(param.custom_files[0]);
      } else {
        // 调用IC50Analyzer生成曲线
        generateIC50Curve(param.concentrations, param.responses);
      }
    });
    ```
    """
    data = crud.get_experiment_record_by_plan(payload.F_SYJH_ID)
    if not data:
        return err("实验计划不存在")
    return ok(data, "查询成功")


@router.post(
    "/list",
    response_model=ApiResponse[Dict[str, Any]],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["drug_assay"]))],
)
def list_experiment_records(payload: ExperimentRecordListQuery):
    """实验记录列表（分页）"""
    keyword = (payload.keyword or "").strip() or None
    syjh_id = payload.F_SYJH_ID
    jlr = payload.F_JLR
    skip = payload.skip or 0
    limit = payload.limit or 20

    records = crud.list_experiment_records(
        skip=skip,
        limit=limit,
        syjh_id=syjh_id,
        jlr=jlr,
        keyword=keyword,
    )
    total = crud.count_experiment_records(
        syjh_id=syjh_id,
        jlr=jlr,
        keyword=keyword,
    )
    
    return ok(
        {"items": records, "total": total, "skip": skip, "limit": limit},
        f"查询成功，共 {total} 条"
    )


@router.post(
    "/ic50/generate",
    response_model=ApiResponse[GenerateIC50Response],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["drug_assay"]))],
)
def generate_ic50_image(payload: GenerateIC50Request):
    """
    生成IC50曲线图
    
    ## 功能说明
    根据实验计划ID和组别ID，自动提取该组别的：
    - 平均细胞活率数据（从实验记录）
    - 浓度梯度（从实验计划）
    
    然后使用四参数Sigmoid曲线拟合，计算IC50值并生成曲线图。
    
    ## 入参
    ```json
    {
      "F_SYJH_ID": 8,
      "F_ZBID": 49
    }
    ```
    
    ## 返回示例
    ```json
    {
      "code": 1,
      "msg": "IC50图像生成成功",
      "data": {
        "success": true,
        "message": "IC50图像生成成功",
        "ic50": 2.35,
        "r2": 0.985,
        "slope": -1.2,
        "image_url": "/uploads/ic50/ic50_8_49_20251218_160530.png",
        "image_id": 301,
        "group_name": "5-FU组"
      }
    }
    ```
    
    ## 注意事项
    1. 需要至少3个数据点才能拟合曲线
    2. 如果所有活率都>90%，IC50可能不准确
    3. R²值越接近1，拟合质量越好
    4. 图像保存在 `uploads/ic50/` 目录
    """
    result = crud.generate_ic50_image(payload.F_SYJH_ID, payload.F_ZBID)
    
    if result["success"]:
        return ok(
            {
                "success": True,
                "message": result.get("message", "IC50图像生成成功"),
                "ic50": result.get("ic50"),
                "r2": result.get("r2"),
                "slope": result.get("slope"),
                "image_url": result.get("image_url"),
                "image_id": result.get("image_id"),
                "group_name": result.get("group_name")
            },
            result.get("message", "IC50图像生成成功")
        )
    else:
        return err(result.get("message", "IC50图像生成失败"))


@router.post(
    "/ic50/preview",
    dependencies=[Depends(require_roles(["drug_assay"]))],
)
def preview_ic50_image(payload: PreviewIC50Request):
    """
    预览IC50曲线图（基于原始数据）
    
    ## 功能说明
    在录入实验记录之前，基于用户提供的原始数据（浓度和细胞活率）
    实时生成IC50曲线图预览。
    
    **区别于 `/ic50/generate`**：
    - 不需要实验计划ID和组别ID
    - 不保存图像到服务器
    - 不记录到数据库
    - 直接返回图像文件流（PNG格式）
    
    ## 入参示例
    ```json
    {
      "F_ND": [0.01, 0.1, 1, 10, 100],
      "F_XBHL": [98.5, 96.2, 85.3, 45.2, 12.8],
      "F_ZBMC": "5-FU组"
    }
    ```
    
    ## 响应
    - **Content-Type**: `image/png`
    - **直接返回PNG图像文件流**
    
    ## 使用场景
    1. 实验人员在录入数据前，想先看看IC50曲线是否合理
    2. 快速验证数据质量，无需保存
    3. 前端可以直接显示为 `<img>` 标签
    
    ## 注意事项
    1. 需要至少3个数据点
    2. 浓度必须 > 0（用于对数尺度）
    3. 如果拟合失败，会返回错误信息（JSON格式）
    """
    import io
    import re
    from datetime import datetime
    from urllib.parse import quote
    
    group_name = payload.F_ZBMC or "预览组"
    
    result = crud.preview_ic50_image(
        concentrations=payload.F_ND,
        responses=payload.F_XBHL,
        group_name=group_name
    )
    
    if result["success"]:
        # 生成文件名：清理组别名称，只保留安全字符
        # 移除特殊字符，保留中文、字母、数字、下划线、连字符
        safe_name = re.sub(r'[^\w\u4e00-\u9fff-]', '_', group_name)
        # 添加时间戳避免重复
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ic50_{safe_name}_{timestamp}.png"
        
        # URL 编码文件名（支持中文）
        # 使用 RFC 2231 标准：filename*=UTF-8''encoded_filename
        filename_encoded = quote(filename, safe='')
        
        # 同时提供两个版本以兼容不同浏览器
        # filename - ASCII 版本（旧浏览器）
        # filename* - UTF-8 版本（现代浏览器）
        content_disposition = f'inline; filename="ic50_preview.png"; filename*=UTF-8\'\'{filename_encoded}'
        
        # 返回图像文件流
        return StreamingResponse(
            io.BytesIO(result["image_bytes"]),
            media_type="image/png",
            headers={
                "Content-Disposition": content_disposition,
                "X-IC50-Value": str(result.get("ic50", "")),
                "X-R2-Value": str(result.get("r2", "")),
                "X-Slope": str(result.get("slope", ""))
            }
        )
    else:
        # 拟合失败，返回JSON错误信息
        return err(result.get("message", "IC50预览图像生成失败"))


@router.post(
    "/submit-for-review",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["drug_assay"]))],
)
async def submit_for_review(
    F_SYJH_ID: int = Form(..., description="实验计划ID"),
    KBDS: Optional[str] = Form(None, description="孔板读数JSON"),
    XTXBH: Optional[str] = Form(None, description="形态学变化JSON"),
    XBHL_PJ: Optional[str] = Form(None, description="平均细胞活率JSON"),
    XBHL: Optional[str] = Form(None, description="细胞活率（孔级）JSON"),
    XBHL_WC: Optional[str] = Form(None, description="细胞活率标准误差JSON"),
    GMD: Optional[str] = Form(None, description="光敏度JSON"),
    IC50: Optional[str] = Form(None, description="IC50数据JSON"),
    SYJL: Optional[str] = Form(None, description="实验结论JSON"),
    F_FJ: Optional[List[UploadFile]] = File(None, description="附件数组"),
    current_user: Dict[str, Any] = Depends(get_current_user_optional),
):
    """
    保存实验记录并提交复核
    
    ## 功能说明
    1. 保存实验记录数据（调用 save_experiment_record）
    2. 创建质控阶段为"药物试验报告"的质控记录
    3. 审核通过后会自动生成药物试验报告PDF（功能待实现）
    
    ## 与普通保存的区别
    - **普通保存**（`/save`）：只记录"药物实验记录"质控
    - **提交复核**（`/submit-for-review`）：同时记录"药物实验记录"和"药物试验报告"质控
    
    ## 使用场景
    - 实验记录填写完成后，需要提交给质控人员审核
    - 质控人员审核通过后，系统会自动生成药物试验报告PDF
    
    ## 入参说明
    - 所有参数与 `/save` 接口相同
    - 使用 `multipart/form-data` 格式
    """
    # 1. 处理文件上传
    file_ids: List[int] = []
    
    if F_FJ:
        for upload_file in F_FJ:
            dst_path, rel_path = build_upload_path(upload_file.filename)
            content = await upload_file.read()
            dst_path.write_bytes(content)
            fid = insert_file_record(rel_path, upload_file.filename)
            if fid is not None:
                file_ids.append(int(fid))
    
    # 2. 构建数据字典
    data = {
        "F_SYJH_ID": F_SYJH_ID,
        "KBDS": KBDS,
        "XTXBH": XTXBH,
        "XBHL_PJ": XBHL_PJ,
        "XBHL": XBHL,
        "XBHL_WC": XBHL_WC,
        "GMD": GMD,
        "IC50": IC50,
        "SYJL": SYJL,
    }
    
    operator = _resolve_operator(current_user)
    
    # 3. 先保存实验记录（会自动记录"药物实验记录"质控）
    try:
        save_result = crud.save_experiment_record(data, file_ids, operator=operator)
    except Exception as e:
        return err(f"保存实验记录失败: {e}")
    
    # 4. 创建"药物试验报告"质控记录
    report_qc_id = None
    try:
        from app.crud.drug_test import get_test_plan_detail
        from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
        from datetime import datetime
        
        syjh_id = F_SYJH_ID
        plan_detail = get_test_plan_detail(syjh_id)
        
        if not plan_detail:
            return err("未找到实验计划")
        
        gdid = plan_detail.get("F_GDID")
        gdxx = plan_detail.get("GDXX") or {}
        zzid = gdxx.get("F_ZZID")
        
        if not zzid or not gdid:
            return err("实验计划缺少工单或样本信息")
        
        sample_info = get_sample_basic_info(zzid)
        record_detail = crud.get_experiment_record_by_plan(syjh_id)
        
        # 变更内容：提交复核
        changes = {
            "变更字段": [
                {"字段": "操作", "新值": "提交复核"},
                {"字段": "实验计划", "新值": plan_detail.get("F_SYMC")},
                {"字段": "提交人", "新值": operator or "系统"},
                {"字段": "提交时间", "新值": str(datetime.now())}
            ]
        }
        
        report_qc_id = create_qc_audit_record(
            F_ZKJD="药物试验报告",
            F_BGLX="新增",
            F_ZZID=zzid,
            F_CHANGES=changes,
            F_JLR=operator or "系统",
            F_GDID=gdid,
            F_XM=sample_info.get("F_XM"),
            F_BRBH=sample_info.get("F_BRBH"),
            F_GDBH=gdxx.get("F_GDBH"),
            F_DCID=gdxx.get("F_DCID"),
            F_DC=gdxx.get("F_DC"),
            F_PYSJ=gdxx.get("F_PYSJ"),
            F_SQR=gdxx.get("F_SQR"),
            F_OLD_DATA=record_detail
        )
        
        # 5. 返回结果
        return ok({
            "record_save_result": save_result,
            "record_qc_id": save_result.get("qc_audit_id"),
            "report_qc_id": report_qc_id,
            "message": "实验记录已保存，报告已提交复核"
        }, "提交成功，等待审核")
        
    except Exception as e:
        # 即使质控记录创建失败，实验记录已经保存成功
        # 返回警告信息但不阻塞主流程
        return ok({
            "record_save_result": save_result,
            "record_qc_id": save_result.get("qc_audit_id"),
            "report_qc_id": None,
            "message": f"实验记录已保存，但提交复核失败: {e}",
            "warning": True
        }, "实验记录已保存，但提交复核失败")


@router.post(
    "/report/preview-data",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["drug_assay"]))],
)
def preview_drug_test_report_data(payload: Dict[str, Any]):
    """
    预览药物试验报告数据（调试用）
    
    ## 功能说明
    用于查看报告生成时会使用哪些数据，包括：
    - 实验计划详细信息（核心）
    - 实验记录基本信息（如果已填写）
    - 培养工单信息
    - 质控记录（质检数据：细胞活率、污染情况、支原体等）
    - 观察记录（培养过程数据）
    - 所有JSON字段原样返回（字符串形式）
    
    ## 请求参数
    - **F_SYJH_ID** (必填): 实验计划ID
    
    ## 返回示例
    ```json
    {
        "code": 1,
        "msg": "报告数据组装成功",
        "data": {
            "报告类型": "药物试验报告",
            "生成时间": "2026-01-19 10:30:00",
            "实验计划": {
                "F_ID": 456,
                "F_SYMC": "5-FU药敏实验",
                "F_ZZID": "ZZID123456",
                "F_GDID": 789,
                "F_GDBH": "GD20260119001",
                ...
            },
            "实验记录": {
                "F_ID": 123,
                "F_SYJH_ID": 456,
                "F_JLR": "张三",
                "F_JLRQ": "2026-01-19 10:00:00",
                "F_KBDS_ZBQ": "{...JSON字符串...}",
                "F_IC50": "{...JSON字符串...}",
                ...
            },
            "培养工单": {...},
            "观察记录": [...],
            "快捷信息": {
                "实验名称": "5-FU药敏实验",
                "工单编号": "GD20260119001",
                "标本号": "ZZID123456",
                "是否已填写记录": true,
                ...
            }
        }
    }
    ```
    
    ## 使用场景
    1. **调试数据获取**: 查看从数据库获取的数据是否完整
    2. **检查JSON字段**: 查看JSON字段的原始内容
    3. **设计PDF模板**: 根据返回的数据结构设计PDF模板
    
    ## 注意事项
    - 以实验计划ID为主键，符合"一个计划一个报告"的业务逻辑
    - JSON字段在此接口中原样返回（字符串形式）
    - JSON的解析和表格化由后续的PDF生成模块负责
    - 如果实验记录尚未填写，"实验记录"字段会返回null
    - 如果关联的工单或观察记录不存在，对应字段会返回null或空数组
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        syjh_id = int(payload.get("F_SYJH_ID"))
    except (TypeError, ValueError):
        return err("缺少或非法的实验计划ID (F_SYJH_ID)")
    
    try:
        from app.crud.drug_test_report import assemble_drug_test_report
        
        logger.info(f"[药物试验报告预览] 开始组装报告数据，实验计划ID: {syjh_id}")
        report_data = assemble_drug_test_report(syjh_id)
        logger.info(f"[药物试验报告预览] 报告数据组装成功，实验计划ID: {syjh_id}")
        
        return ok(report_data, "报告数据组装成功")
    except ValueError as e:
        logger.warning(f"[药物试验报告预览] 数据验证失败: {e}")
        return err(str(e))
    except Exception as e:
        logger.error(f"[药物试验报告预览] 组装报告数据失败: {e}", exc_info=True)
        return err(f"组装失败: {e}")
