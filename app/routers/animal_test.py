from fastapi import APIRouter, Depends, UploadFile, File, Form
from app.schemas.common import ApiResponse
from app.schemas.animal_test import (
    AnimalTestPlanCreateOut,
    AnimalTestPlanDetailOut,
    AnimalTestPlanListQuery,
    AnimalTestPlanListOut,
    AnimalTestPlanDeleteIn,
    AnimalTestPlanStatusUpdateIn,
    MarkAnimalTestUnqualifiedIn,
)
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.core.storage import build_upload_path
from app.crud.file_center import insert_file_record
from app.crud import animal_test as crud
import json

router = APIRouter(prefix="/animal-test", tags=["animal_test"])


@router.post(
    "/create",
    response_model=ApiResponse[AnimalTestPlanCreateOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
async def create_animal_test_plan(
    JCXX: str = Form(..., description="基础信息JSON"),
    JZXX: str = Form(default="[]", description="动物接种信息JSON数组"),
    F_FJ: UploadFile | None = File(None, description="附件文件"),
    F_FJ_ID: str | None = Form(default=None, description="附件ID（已存在文件）"),
    current_user: dict = Depends(get_current_user_optional),
):
    """
    创建动物实验计划

    ## 请求方式
    **Content-Type**: `multipart/form-data`

    ## 请求参数

    ### **JCXX（基础信息）** - 必填
    ```json
    {
        "F_GDID": 93,
        "F_SYMC": "PDO动物移植实验-001",
        "F_SYKSRQ": "2025-11-20",
        "F_SYR": "张三",
        "F_SYJHYMD": "评估类器官在裸鼠皮下移植的成瘤率",
        "F_XBSL": "5x10⁴ cells/mL",
        "F_JZJND": "5%",
        "F_JZJTJ": "50uL",
        "F_BZ": "备注"
    }
    ```

    ### **JZXX（动物接种信息）** - 可选，默认空数组
    ```json
    [
        {
            "F_DWBH": "M001",
            "F_XB": "雄",
            "F_NL": "6周龄",
            "F_LH": "A01",
            "F_ZB": "实验组",
            "F_JHYZSJ": "2025-11-25",
            "F_JHJZXBS": "5×10⁶",
            "F_JHYZBW": "右侧背部皮下",
            "F_JHCZR": "李四"
        }
    ]
    ```

    ## 返回示例
    ```json
    {
        "code": 1,
        "msg": "创建成功",
        "data": {
            "F_ID": 1,
            "F_GDID": 93,
            "F_SYBH": "DWSY20251120153045123",
            "F_SYMC": "PDO动物移植实验-001"
        }
    }
    ```
    """
    try:
        jcxx = json.loads(JCXX) if JCXX else {}
    except Exception as e:
        return err(f"基础信息(JCXX)解析失败: {e}")

    try:
        jzxx = json.loads(JZXX) if JZXX else []
    except Exception as e:
        return err(f"接种信息(JZXX)解析失败: {e}")

    # 处理附件
    file_id = None
    if F_FJ is not None:
        dst_path, rel = build_upload_path(F_FJ.filename)
        content = await F_FJ.read()
        dst_path.write_bytes(content)
        fid = insert_file_record(rel, F_FJ.filename)
        if fid is not None:
            file_id = int(fid)
    if F_FJ_ID is not None:
        try:
            file_id = int(F_FJ_ID)
        except Exception:
            return err("F_FJ_ID 非法")

    payload = {**jcxx, "JZXX": jzxx}
    if file_id is not None:
        payload["F_FJ"] = file_id

    operator = current_user.get("username") if current_user else None

    try:
        result = crud.create_animal_test_plan(payload, operator=operator)
        return ok(result, "创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {e}")


@router.post(
    "/detail",
    response_model=ApiResponse[AnimalTestPlanDetailOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
def get_animal_test_plan_detail(data: dict, current_user: dict = Depends(get_current_user_optional)):
    """
    获取动物实验计划详情

    ## 请求示例
    ```json
    { "F_ID": 1 }
    ```

    ## 返回说明
    - `GDXX`: 关联工单的样本信息（标本号、代次、工单编号等）
    - `JZXX`: 动物接种信息列表（按 F_SORT 升序）
    """
    try:
        plan_id = int(data.get("F_ID"))
    except Exception:
        return err("缺少或非法的F_ID")

    result = crud.get_animal_test_plan_detail(plan_id)
    if not result:
        return err("实验计划不存在")
    from app.core.data_scope import deny_owner_access
    denied = deny_owner_access(current_user, result.get("F_SYR"), module="animal_experiment")
    if denied:
        return denied
    return ok(result, "查询成功")


@router.post(
    "/list",
    response_model=ApiResponse[AnimalTestPlanListOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
def list_animal_test_plans(query: AnimalTestPlanListQuery, current_user: dict = Depends(get_current_user_optional)):
    """
    查询动物实验计划列表（分页）

    ## 支持过滤字段
    - 实验编号、实验名称、实验人、状态（计划自身字段）
    - 标本号、培养工单编号、代次ID、组织部位、培养人（工单关联字段）
    """
    try:
        from app.core.data_scope import resolve_data_scope
        items, total = crud.list_animal_test_plans(
            query.model_dump(),
            user=current_user,
            scope=resolve_data_scope(current_user, "animal_experiment"),
        )
        return ok({"items": items, "total": total}, "查询成功")
    except Exception as e:
        return err(f"查询失败: {e}")


@router.post(
    "/update",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
async def update_animal_test_plan(
    JCXX: str = Form(..., description="基础信息JSON，必须包含F_ID"),
    JZXX: str | None = Form(default=None, description="接种信息增量更新JSON（有F_ID=更新，无F_ID=新增）"),
    JZXX_DEL_IDS: str | None = Form(default=None, description="要删除的接种记录ID数组JSON，如 [2, 3]"),
    F_FJ: UploadFile | None = File(None, description="附件文件"),
    current_user: dict = Depends(get_current_user_optional),
):
    """
    更新动物实验计划

    ## 编辑权限
    - **已创建**：可修改所有字段 + 接种信息
    - **进行中**：只能修改实验名称、实验人、实验目的、附件、备注
    - **已完成/不合格**：禁止修改

    ## 接种信息增量更新规则
    - `JZXX` 中有 `F_ID` → 更新对应记录
    - `JZXX` 中无 `F_ID` → 新增记录
    - `JZXX_DEL_IDS` → 删除指定ID的记录

    ## 返回示例
    ```json
    {
        "code": 1,
        "msg": "更新成功",
        "data": { "F_ID": 1, "updated": true }
    }
    ```
    """
    try:
        jcxx = json.loads(JCXX) if JCXX else {}
    except Exception as e:
        return err(f"基础信息(JCXX)解析失败: {e}")

    if not jcxx.get("F_ID"):
        return err("基础信息(JCXX)必须包含F_ID字段")

    jzxx = None
    if JZXX is not None:
        try:
            jzxx = json.loads(JZXX)
        except Exception as e:
            return err(f"接种信息(JZXX)解析失败: {e}")

    jzxx_del_ids = None
    if JZXX_DEL_IDS is not None:
        try:
            jzxx_del_ids = json.loads(JZXX_DEL_IDS)
            if not isinstance(jzxx_del_ids, list):
                return err("JZXX_DEL_IDS 必须是数组，如 [2, 3]")
        except Exception as e:
            return err(f"删除接种记录ID列表(JZXX_DEL_IDS)解析失败: {e}")

    file_id = None
    if F_FJ is not None:
        dst_path, rel = build_upload_path(F_FJ.filename)
        content = await F_FJ.read()
        dst_path.write_bytes(content)
        fid = insert_file_record(rel, F_FJ.filename)
        if fid is not None:
            file_id = int(fid)

    payload = {**jcxx}
    if jzxx is not None:
        payload["JZXX"] = jzxx
    if jzxx_del_ids is not None:
        payload["JZXX_DEL_IDS"] = jzxx_del_ids
    if file_id is not None:
        payload["F_FJ"] = file_id

    operator = current_user.get("username") if current_user else None

    try:
        result = crud.update_animal_test_plan(payload, operator=operator)
        return ok(result, "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {e}")


@router.post(
    "/delete",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
def delete_animal_test_plan(
    data: AnimalTestPlanDeleteIn,
    current_user: dict = Depends(get_current_user_optional),
):
    """
    删除动物实验计划

    ## 业务规则
    - 只能删除**已创建**状态的实验计划
    - 级联删除所有关联的动物接种信息
    - 操作会记录到质控审核系统

    ## 请求示例
    ```json
    { "F_ID": 1 }
    ```
    """
    operator = current_user.get("username") if current_user else None
    try:
        crud.delete_animal_test_plan(data.F_ID, operator=operator)
        return ok({"deleted": True}, "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"删除失败: {e}")


@router.post(
    "/update-status",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
def update_animal_test_plan_status(data: AnimalTestPlanStatusUpdateIn):
    """
    更新动物实验计划状态

    ## 有效状态值
    - 已创建
    - 进行中
    - 待审核
    - 已完成
    - 不合格

    ## 请求示例
    ```json
    { "F_ID": 1, "F_ZT": "进行中" }
    ```
    """
    try:
        crud.update_animal_test_plan_status(data.F_ID, data.F_ZT)
        return ok({"F_ID": data.F_ID, "F_ZT": data.F_ZT}, "状态更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"状态更新失败: {e}")


@router.post(
    "/mark-unqualified",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["animal_experiment"]))],
)
def mark_animal_test_unqualified(
    payload: MarkAnimalTestUnqualifiedIn,
    current_user: dict = Depends(get_current_user_optional),
):
    """
    标记动物实验计划为不合格

    ## 功能说明
    - 将实验计划状态改为"不合格"并记录原因
    - 自动创建质控审核记录
    - 标记后不可再修改

    ## 请求示例
    ```json
    {
        "F_SYJH_ID": 1,
        "F_BHGYY": "接种成瘤率过低，数据不可信"
    }
    ```
    """
    plan_id = payload.F_SYJH_ID
    reason  = payload.F_BHGYY

    creator = (
        current_user.get("F_Ktype")
        or current_user.get("F_YSM")
        or ""
    ) if current_user else ""

    try:
        qc_audit_id = crud.mark_animal_test_unqualified(plan_id, reason, creator)
        result: dict = {
            "F_SYJH_ID": plan_id,
            "F_ZT": "不合格审批中",
            "F_BHGYY": reason or "",
        }
        if qc_audit_id:
            result["qc_audit_id"] = qc_audit_id
        return ok(result, "标记成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"标记失败: {e}")
