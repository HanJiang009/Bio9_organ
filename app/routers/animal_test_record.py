"""
动物实验记录路由
  - /animal-test/record/detail  统一详情（观察记录列表 + 终点记录）
  - /animal-test/record/save    统一保存（观察记录变更 + 终点记录）
  - /animal-test/observation/*  观察记录单条操作（备用）
  - /animal-test/endpoint/*     终点记录单独操作（备用）
"""
import json
import logging
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.schemas.common import ApiResponse
from app.schemas.animal_test_record import (
    ObservationDetail,
    ObservationListOut,
    ObservationDeleteIn,
    ObservationListQuery,
    EndpointRecordDetail,
    EndpointDetailQuery,
    ExperimentRecordDetailQuery,
)
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.core.storage import build_upload_path
from app.crud.file_center import insert_file_record
from app.crud import animal_test_record as crud

router = APIRouter(prefix="/animal-test", tags=["animal_test"])
logger = logging.getLogger(__name__)


def _resolve_operator(current_user: Optional[Dict[str, Any]]) -> Optional[str]:
    if not current_user:
        return None
    return (
        current_user.get("F_Ktype")
        or current_user.get("F_YSM")
        or current_user.get("F_NAME")
    )


async def _upload_files(files: Optional[List[UploadFile]]) -> List[int]:
    """批量上传文件，返回文件ID列表（顺序与入参一致）"""
    ids: List[int] = []
    if not files:
        return ids
    for f in files:
        if f is None or not f.filename:
            continue
        dst_path, rel = build_upload_path(f.filename)
        content = await f.read()
        dst_path.write_bytes(content)
        fid = insert_file_record(rel, f.filename)
        if fid is not None:
            ids.append(int(fid))
    return ids


# ==================== 观察记录 ====================

@router.post(
    "/observation/save",
    response_model=ApiResponse[ObservationDetail],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
async def save_observation(
    F_SYJH_ID:  int                        = Form(...,    description="实验计划ID"),
    F_GCJL_ID:  Optional[str]             = Form(None,  description="观察记录ID（有则更新，无则新增）"),
    GCXX:      str                         = Form(...,    description="观察头信息JSON"),
    ZBXX:      str                         = Form("[]",  description="各组别观察数据JSON数组"),
    F_FJ:      Optional[List[UploadFile]]  = File(None,  description="附件文件列表（按索引0,1,2...映射到ZBXX中的F_FJ_INDEX）"),
    current_user: Dict[str, Any]           = Depends(get_current_user_optional),
):
    """
    保存观察记录（新增/更新自动判断）

    ## 请求方式
    **Content-Type**: `multipart/form-data`

    ## 入参说明

    ### GCXX（观察头信息）- 必填
    ```json
    {
        "F_GCSJ":  "2025-12-01 09:00:00",
        "F_PYDS":  7,
        "F_JLRQ":  "2025-12-01",
        "F_JLR":   "张三"
    }
    ```

    ### ZBXX（各组别观察数据）- 可选，默认 []
    ```json
    [
        {
            "F_ZB":        "实验组",
            "F_XSYS":      "正常",
            "F_HDD":       "活跃",
            "F_YCBX":      "",
            "F_XSTZ":      "25.3g",
            "F_ZLDX":      "长轴15mm 短轴8mm",
            "F_TJJS":      "480mm³",
            "F_YZWZSFYC":  "否",
            "F_FJ": [
                {"F_FJ_INDEX": 0, "F_BS": "40x"},
                {"F_FJ_INDEX": 1, "F_BS": "100x"}
            ],
            "F_BZ":        ""
        },
        {
            "F_ZB":  "对照组",
            "F_FJ":  [{"F_FJ": 101, "F_BS": "40x"}]
        }
    ]
    ```

    > **附件说明**：
    > - `F_FJ_INDEX`：新上传文件的索引，对应 `F_FJ` 参数数组的位置（0-based）
    > - `F_FJ`（整数）：已存在的文件ID，直接保留，无需重新上传
    > - 两者可混用

    ## 返回
    保存后的完整观察记录详情（含 ZBXX，附件已转为文件信息对象）
    """
    try:
        gcxx = json.loads(GCXX)
    except Exception as e:
        return err(f"GCXX 解析失败: {e}")
    try:
        zbxx = json.loads(ZBXX) if ZBXX else []
    except Exception as e:
        return err(f"ZBXX 解析失败: {e}")

    gcjl_id: Optional[int] = None
    if F_GCJL_ID is not None and F_GCJL_ID.strip():
        try:
            gcjl_id = int(F_GCJL_ID)
        except Exception:
            return err("F_GCJL_ID 非法，须为整数")

    file_ids = await _upload_files(F_FJ)
    operator = _resolve_operator(current_user)

    try:
        result = crud.save_observation(F_SYJH_ID, gcjl_id, gcxx, zbxx, file_ids, operator)
        return ok(result, "保存成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("保存观察记录异常")
        return err(f"保存失败: {e}")


@router.post(
    "/observation/delete",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
def delete_observation(
    data: ObservationDeleteIn,
    current_user: Dict[str, Any] = Depends(get_current_user_optional),
):
    """
    删除观察记录

    ## 业务规则
    - 级联删除该时间点下所有组别观察数据
    - 操作记录到质控审核系统

    ## 请求示例
    ```json
    { "F_GCJL_ID": 5 }
    ```
    """
    body_plan_id = data.F_SYJH_ID
    if not body_plan_id:
        # 从数据库反查 plan_id
        detail = crud.get_observation_detail(data.F_GCJL_ID)
        if not detail:
            return err("观察记录不存在")
        body_plan_id = detail["F_SYJH_ID"]

    operator = _resolve_operator(current_user)
    try:
        crud.delete_observation(data.F_GCJL_ID, body_plan_id, operator)
        return ok({"deleted": True}, "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"删除失败: {e}")


@router.post(
    "/observation/list",
    response_model=ApiResponse[ObservationListOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
def list_observations(query: ObservationListQuery, current_user: dict = Depends(get_current_user_optional)):
    """
    获取实验计划的所有观察记录列表

    按培养天数升序排列，每条记录包含完整的各组别观察数据（ZBXX）。

    ## 请求示例
    ```json
    { "F_SYJH_ID": 1 }
    ```
    """
    from app.core.data_scope import deny_owner_access, fetch_dwsy_syr
    denied = deny_owner_access(current_user, fetch_dwsy_syr(query.F_SYJH_ID), module="animal_experiment")
    if denied:
        return denied
    try:
        items, total = crud.list_observations(query.F_SYJH_ID)
        return ok({"items": items, "total": total}, "查询成功")
    except Exception as e:
        return err(f"查询失败: {e}")


# ==================== 终点记录 ====================

@router.post(
    "/endpoint/save",
    response_model=ApiResponse[EndpointRecordDetail],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
async def save_endpoint_record(
    F_SYJH_ID: int                        = Form(...,   description="实验计划ID"),
    JLXX:      str                        = Form(...,   description="终点记录数据JSON"),
    F_FJ:      Optional[List[UploadFile]] = File(None, description="新上传附件文件列表"),
    current_user: Dict[str, Any]          = Depends(get_current_user_optional),
):
    """
    保存实验终点记录（新增/更新自动判断）

    ## 请求方式
    **Content-Type**: `multipart/form-data`

    ## JLXX 字段说明
    ```json
    {
        "F_SYZQ":    "28",
        "F_YZZZQK":  "实验组小鼠皮下均可见明显肿块，对照组未见成瘤",
        "F_FJ":      [101, 102],
        "F_SYJL":    "类器官移植模型构建成功，成瘤率85%",
        "F_JLRQ":    "2025-12-28"
    }
    ```

    > **附件说明**：
    > - `F_FJ`：已有文件ID列表（保留，不会被删除）
    > - `F_FJ`（multipart 参数）：本次新上传的文件（追加到已有列表后）
    > - `F_JLR`（实验记录人）：首次创建时自动取当前登录用户，**不支持修改**

    ## 状态联动
    - 首次提交终点记录时，实验计划状态自动从 **进行中** 变为 **待审核**
    """
    try:
        jlxx = json.loads(JLXX)
    except Exception as e:
        return err(f"JLXX 解析失败: {e}")

    new_file_ids = await _upload_files(F_FJ)
    operator = _resolve_operator(current_user)

    try:
        result = crud.save_endpoint_record(F_SYJH_ID, jlxx, new_file_ids, operator)
        return ok(result, "保存成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("保存终点记录异常")
        return err(f"保存失败: {e}")


@router.post(
    "/endpoint/detail",
    response_model=ApiResponse[EndpointRecordDetail],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
def get_endpoint_record(query: EndpointDetailQuery):
    """
    获取实验终点记录详情

    若该计划尚未提交终点记录，同时返回默认实验周期（取最后一条观察记录的培养天数）。

    ## 请求示例
    ```json
    { "F_SYJH_ID": 1 }
    ```
    """
    result = crud.get_endpoint_record(query.F_SYJH_ID)
    if not result:
        # 返回默认值（还没有记录时前端可以用来填充表单）
        default_syzq = crud._get_default_syzq(query.F_SYJH_ID)
        return ok({
            "F_SYJH_ID":  query.F_SYJH_ID,
            "F_SYZQ":     default_syzq,
            "F_FJ":       [],
        }, "暂无终点记录")
    return ok(result, "查询成功")


# ==================== 统一接口 ====================

@router.post(
    "/record/detail",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
def get_experiment_record(query: ExperimentRecordDetailQuery, current_user: dict = Depends(get_current_user_optional)):
    """
    统一详情接口——实验记录页面初始化加载

    一次返回该计划的**所有观察记录列表**与**实验终点记录**，供页面整体渲染。

    ## 请求示例
    ```json
    { "F_SYJH_ID": 1 }
    ```

    ## 返回示例
    ```json
    {
        "code": 200,
        "msg": "查询成功",
        "data": {
            "gcjl_list": [
                {
                    "F_ID": 1,
                    "F_SYJH_ID": 1,
                    "F_GCSJ": "2025-12-01 09:00:00",
                    "F_PYDS": 7,
                    "F_JLRQ": "2025-12-01",
                    "F_JLR": "张三",
                    "ZBXX": [ ... ]
                }
            ],
            "gcjl_total": 1,
            "endpoint": {
                "F_SYJH_ID": 1,
                "F_SYZQ": "28",
                "F_YZZZQK": "...",
                "F_FJ": [...],
                "F_SYJL": "...",
                "F_JLR": "张三",
                "F_JLRQ": "2025-12-28"
            }
        }
    }
    ```

    > 若该计划尚未提交终点记录，`endpoint` 返回默认结构（含默认实验周期）；
    > `gcjl_list` 为空数组时表示尚无观察记录。
    """
    from app.core.data_scope import deny_owner_access, fetch_dwsy_syr
    denied = deny_owner_access(current_user, fetch_dwsy_syr(query.F_SYJH_ID), module="animal_experiment")
    if denied:
        return denied
    try:
        result = crud.get_experiment_record(query.F_SYJH_ID)
        return ok(result, "查询成功")
    except Exception as e:
        return err(f"查询失败: {e}")


@router.post(
    "/record/save",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
async def save_experiment_record(
    F_SYJH_ID:       int                        = Form(...,   description="实验计划ID"),
    GCJL_LIST:       str                        = Form("[]", description="观察记录列表JSON（新增无F_GCJL_ID，更新有F_GCJL_ID）"),
    GCJL_DEL_IDS:    str                        = Form("[]", description="需删除的观察记录ID列表JSON，如 [1,2]"),
    JLXX:            str                        = Form("{}",  description="终点记录数据JSON"),
    F_FJ_GCJL:       Optional[List[UploadFile]] = File(None, description="观察记录附件（全局索引，映射到各ZBXX中的F_FJ_INDEX）"),
    F_FJ_JLXX:       Optional[List[UploadFile]] = File(None, description="终点记录附件（新上传，追加到已有列表）"),
    current_user:    Dict[str, Any]             = Depends(get_current_user_optional),
):
    """
    统一保存接口——实验记录页面"确认"按钮

    ## 请求方式
    **Content-Type**: `multipart/form-data`

    ## 说明
    **暂存接口**：只保存业务数据，**不创建** `T_ZKSH` 质控审核记录。

    一次提交：
    - **观察记录变更**：新增 / 更新 / 删除
    - **终点记录**：新增或更新

    ## GCJL_LIST 结构
    ```json
    [
        {
            "F_GCJL_ID": null,
            "F_GCSJ":    "2025-12-01 09:00:00",
            "F_PYDS":    7,
            "F_JLRQ":    "2025-12-01",
            "F_JLR":     "张三",
            "ZBXX": [
                {
                    "F_ZB":        "实验组",
                    "F_XSYS":      "正常",
                    "F_HDD":       "活跃",
                    "F_YCBX":      "",
                    "F_XSTZ":      "25.3g",
                    "F_ZLDX":      "长轴15mm 短轴8mm",
                    "F_TJJS":      "480mm3",
                    "F_YZWZSFYC":  "否",
                    "F_FJ": [{"F_FJ_INDEX": 0, "F_BS": "40x"}],
                    "F_BZ": ""
                }
            ]
        },
        {
            "F_GCJL_ID": 3,
            "F_PYDS": 14,
            "ZBXX": [ ... ]
        }
    ]
    ```

    > **F_FJ_INDEX 全局索引说明**：
    > 跨多条观察记录时，`F_FJ_INDEX` 仍是全局唯一的，对应 `F_FJ_GCJL` 文件数组的下标。
    > 例如第1条记录用index 0，第2条记录用index 1、2，以此类推。

    ## GCJL_DEL_IDS 结构
    ```json
    [1, 3, 5]
    ```

    ## JLXX 结构（同终点记录保存接口）
    ```json
    {
        "F_SYZQ":    "28",
        "F_YZZZQK":  "...",
        "F_FJ":      [101, 102],
        "F_SYJL":    "...",
        "F_JLRQ":    "2025-12-28"
    }
    ```
    """
    try:
        gcjl_list = json.loads(GCJL_LIST) if GCJL_LIST else []
    except Exception as e:
        return err(f"GCJL_LIST 解析失败: {e}")
    try:
        gcjl_del_ids = json.loads(GCJL_DEL_IDS) if GCJL_DEL_IDS else []
    except Exception as e:
        return err(f"GCJL_DEL_IDS 解析失败: {e}")
    try:
        endpoint_data = json.loads(JLXX) if JLXX else {}
    except Exception as e:
        return err(f"JLXX 解析失败: {e}")

    gcjl_file_ids    = await _upload_files(F_FJ_GCJL)
    endpoint_file_ids = await _upload_files(F_FJ_JLXX)
    operator = _resolve_operator(current_user)

    try:
        result = crud.save_experiment_record(
            plan_id=F_SYJH_ID,
            gcjl_list=gcjl_list,
            gcjl_del_ids=gcjl_del_ids,
            endpoint_data=endpoint_data,
            gcjl_file_ids=gcjl_file_ids,
            endpoint_file_ids=endpoint_file_ids,
            operator=operator,
            skip_qc=True,
        )
        return ok(result, "保存成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("统一保存实验记录异常")
        return err(f"保存失败: {e}")


@router.post(
    "/record/submit-for-review",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
async def submit_for_review(
    F_SYJH_ID:       int                        = Form(...,   description="实验计划ID"),
    GCJL_LIST:       str                        = Form("[]", description="观察记录列表JSON（同 record/save）"),
    GCJL_DEL_IDS:    str                        = Form("[]", description="需删除的观察记录ID列表JSON"),
    JLXX:            str                        = Form("{}",  description="终点记录数据JSON"),
    F_FJ_GCJL:       Optional[List[UploadFile]] = File(None, description="观察记录附件"),
    F_FJ_JLXX:       Optional[List[UploadFile]] = File(None, description="终点记录附件"),
    current_user:    Dict[str, Any]             = Depends(get_current_user_optional),
):
    """
    提交复核——实验记录填写完成后提交报告审核

    ## 与 record/save 的区别
    - **record/save**：仅保存数据，不写质控
    - **record/submit-for-review**：保存数据的同时创建质控阶段为 **"动物实验报告"** 的质控记录，
      审核通过后系统自动生成实验报告 PDF，并将计划状态更新为 **待审核**

    ## 请求方式
    **Content-Type**: `multipart/form-data`（参数与 record/save 完全相同）

    ## 状态联动
    - 调用本接口后，实验计划状态变更为 **待审核**
    - 质控人员在质控模块审核通过后，后台异步生成动物实验报告 PDF
    """
    try:
        gcjl_list     = json.loads(GCJL_LIST)    if GCJL_LIST    else []
        gcjl_del_ids  = json.loads(GCJL_DEL_IDS) if GCJL_DEL_IDS else []
        endpoint_data = json.loads(JLXX)          if JLXX          else {}
    except Exception as e:
        return err(f"参数解析失败: {e}")

    gcjl_file_ids     = await _upload_files(F_FJ_GCJL)
    endpoint_file_ids = await _upload_files(F_FJ_JLXX)
    operator          = _resolve_operator(current_user)

    from app.crud.animal_test import get_animal_test_plan_detail
    from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
    from datetime import datetime as _dt

    # 预先获取实验计划（后续两步都需要）
    plan = get_animal_test_plan_detail(F_SYJH_ID)
    if not plan:
        return err("未找到实验计划")

    gdid = plan.get("F_GDID")
    gdxx = plan.get("GDXX") or {}
    zzid = gdxx.get("F_ZZID")
    if not zzid or not gdid:
        return err("实验计划缺少关联工单或标本信息，无法提交复核")

    # 1. 静默保存（skip_qc=True，不产生中间质控记录）
    try:
        save_result = crud.save_experiment_record(
            plan_id=F_SYJH_ID,
            gcjl_list=gcjl_list,
            gcjl_del_ids=gcjl_del_ids,
            endpoint_data=endpoint_data,
            gcjl_file_ids=gcjl_file_ids,
            endpoint_file_ids=endpoint_file_ids,
            operator=operator,
            skip_qc=True,
        )
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        logger.exception("提交复核-保存记录异常")
        return err(f"保存记录失败: {e}")

    # 2. 创建唯一一条"动物实验报告"质控记录，携带保存后的完整整页快照
    report_qc_id = None
    try:
        sample_info = get_sample_basic_info(zzid)
        # 保存完成后读取最新整页数据作为快照，供质控审核界面完整渲染
        full_snapshot = crud.get_experiment_record(F_SYJH_ID)

        submit_time = str(_dt.now())
        changes = {
            "变更字段": [
                {"字段": "操作",   "新值": "提交复核"},
                {"字段": "实验计划", "新值": plan.get("F_SYMC")},
                {"字段": "提交人",  "新值": operator or "系统"},
                {"字段": "提交时间", "新值": submit_time},
                {"字段": "观察记录数", "新值": full_snapshot.get("gcjl_total", 0)},
            ]
        }
        report_qc_id = create_qc_audit_record(
            F_ZKJD="动物实验报告",
            F_BGLX="新增",
            F_ZZID=zzid,
            F_CHANGES=changes,
            F_JLR=operator or "系统",
            F_GDID=gdid,
            F_XM=sample_info.get("F_XM"),
            F_BRBH=sample_info.get("F_BRBH"),
            F_YBLY=gdxx.get("F_YBLY"),
            F_GDBH=gdxx.get("F_GDBH"),
            F_DCID=gdxx.get("F_DCID"),
            F_DC=gdxx.get("F_DC"),
            F_PYSJ=gdxx.get("F_PYSJ"),
            F_SQR=gdxx.get("F_SQR"),
            F_OLD_DATA=full_snapshot,   # 完整整页快照，包含所有观察记录 + 终点记录
        )
    except Exception as e:
        logger.error(f"[提交复核] 创建质控记录失败: {e}", exc_info=True)
        return err(f"创建质控记录失败: {e}")

    # 3. 更新计划状态 → 待审核
    try:
        from app.crud.animal_test import update_animal_test_plan_status
        update_animal_test_plan_status(F_SYJH_ID, "待审核")
    except Exception as e:
        logger.warning(f"[提交复核] 更新计划状态失败（不影响主流程）: {e}")

    return ok({
        "save_result":   save_result,
        "report_qc_id":  report_qc_id,
        "message":       "实验记录已保存，报告已提交复核，等待审核",
    }, "提交成功")


@router.post(
    "/record/generate-pdf",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment", "qc_review"]))],
)
def generate_pdf(payload: Dict[str, Any]):
    """
    主动生成动物实验报告 PDF（测试 / 失败重新生成）

    ## 入参
    ```json
    { "F_SYJH_ID": 1 }
    ```
    """
    try:
        syjh_id = int(payload.get("F_SYJH_ID"))
    except (TypeError, ValueError):
        return err("缺少或非法的实验计划ID (F_SYJH_ID)")

    try:
        from app.crud.animal_test_report_pdf import generate_animal_test_report_pdf
        result = generate_animal_test_report_pdf(syjh_id=syjh_id)
        if result["success"]:
            return ok(result, "PDF生成成功")
        else:
            return err(result["message"])
    except Exception as e:
        logger.exception("生成动物实验报告PDF异常")
        return err(f"生成PDF失败: {e}")
