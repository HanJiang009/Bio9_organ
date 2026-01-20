from fastapi import APIRouter, Depends, UploadFile, File, Form
from app.schemas.common import ApiResponse
from app.schemas.drug_test import (
    TestPlanCreateIn, TestPlanCreateOut,
    TestPlanUpdateIn, TestPlanDetailOut,
    TestPlanListQuery, TestPlanListOut,
    TestPlanDeleteIn, TestPlanStatusUpdateIn,
    ExperimentRecordSave,
    MarkUnqualifiedIn
)
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.core.storage import build_upload_path
from app.crud.file_center import insert_file_record
from app.crud import drug_test as crud
import json

router = APIRouter(prefix="/drug-test", tags=["drug_test"])


@router.post("/create", response_model=ApiResponse[TestPlanCreateOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["drug_assay"]))])
async def create_test_plan(
    JCXX: str = Form(..., description="基础信息JSON"),
    ZBXX: str = Form(..., description="组别信息JSON"),
    KWXX: str = Form(..., description="孔位信息JSON"),
    F_FJ: UploadFile | None = File(None, description="附件文件"),
    F_FJ_ID: str | None = Form(default=None, description="附件ID"),
    current_user: dict = Depends(get_current_user_optional)
):
    """
    创建实验计划
    
    ## 功能说明
    - 创建药物试验实验计划，分段传入：基础信息、组别信息、孔位信息
    - 系统自动生成唯一的实验编号（F_SYBH）
    - 支持上传附件文件或传入附件ID
    - 根据用药信息自动生成出库单
    - 自动验证药物库存是否充足
    
    ## 请求方式
    **Content-Type**: `multipart/form-data`
    
    ## 请求参数
    
    ### **JCXX（基础信息）** - 必填
    ```json
    {
        "F_GDID": 93,
        "F_SYMC": "PDO药敏试验-001",
        "F_SYKSRQ": "2025-11-20",
        "F_SYR": "张三",
        "F_SYJHYMD": "评估5-FU、奥沙利铂对类器官的抑制效果",
        "F_LQGMD": "5x104 cells/mL",
        "F_JZJND": "5%",
        "F_JZJTJ": "50uL",
        "F_KBLX": "96孔板",
        "F_KBBH": "KB20251120001",
        "F_BZ": "备注信息"
    }
    ```
    
    ### **ZBXX（组别信息）** - 必填
    ```json
    [
        {
            "F_ZBMC": "对照组",
            "F_ZBLX": "阴性对照",
            "F_FKSSZ": 3,
            "F_ND": ["0"],
            "F_YY": []
        },
        {
            "F_ZBMC": "5-FU组",
            "F_ZBLX": "单药",
            "F_SORT": 2,
            "F_FKSSZ": 3,
            "F_ND": ["10uM", "50uM", "100uM"],
            "F_YY": [
                {
                    "F_SJID": 3,
                    "F_PCH": "20251115001",
                    "F_YL": "10",
                    "F_XSFA": "DMSO稀释至工作浓度"
                }
            ]
        }
    ]
    ```
    
    **说明**：
    - `F_SORT` 是排序字段（可选），不传则按数组顺序自动生成 1, 2, 3...
    - `F_YY` 是用药信息列表，对照组可以为空数组
    - `F_FKSSZ`、`F_ND` 现在为组别级配置，每个组可有不同的复孔数与浓度梯度
    
    ### **KWXX（孔位信息）** - 必填
    ```json
    [
        {
            "F_WZ": "A1",
            "F_ZBMC": "对照组",
            "F_ND": "0",
            "F_JYTJ": "50uL",
            "F_YS": "#FFFFFF"
        },
        {
            "F_WZ": "A2",
            "F_ZBMC": "5-FU组",
            "F_ND": "10uM",
            "F_JYTJ": "50uL",
            "F_XSFA": "DMSO稀释",
            "F_YS": "#FFE5E5"
        }
    ]
    ```
    
    ### **F_FJ（附件文件）** - 可选
    直接上传文件
    
    ### **F_FJ_ID（附件ID）** - 可选
    如果不上传文件，传已存在的文件ID
    
    ## 返回示例
    ```json
    {
        "code": 1,
        "msg": "创建成功",
        "data": {
            "F_ID": 1,
            "F_GDID": 93,
            "F_SYMC": "PDO药敏试验-001",
            "auto_outbound_count": 1,
            "auto_outbound_codes": ["YWCKD202511200001"]
        }
    }
    ```
    
    ## 注意事项
    - 使用 multipart/form-data 格式提交
    - JCXX、ZBXX、KWXX 都是 JSON 字符串
    - 可以上传附件文件（F_FJ）或传附件ID（F_FJ_ID）
    - **孔位数量校验**：按组别累加校验：`期望孔位数 = Σ(组别复孔数 × 该组浓度数量)`，实际传入孔位数必须一致，且孔位所属组别与浓度必须匹配该组配置
    - 用药信息会自动验证库存并创建出库单
    - 如果任何一个药物库存不足，整个创建操作会回滚
    """
    # 解析基础信息
    try:
        jcxx = json.loads(JCXX) if JCXX else {}
    except Exception as e:
        return err(f"基础信息(JCXX)解析失败: {e}")
    
    # 解析组别信息
    try:
        zbxx = json.loads(ZBXX) if ZBXX else []
    except Exception as e:
        return err(f"组别信息(ZBXX)解析失败: {e}")
    
    # 解析孔位信息
    try:
        kwxx = json.loads(KWXX) if KWXX else []
    except Exception as e:
        return err(f"孔位信息(KWXX)解析失败: {e}")
    
    # 处理附件文件/ID
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
    
    # 组装完整数据
    payload = {**jcxx, "ZBXX": zbxx, "KWXX": kwxx}
    if file_id is not None:
        payload["F_FJ"] = file_id
    
    # 提取操作人信息
    operator = current_user.get("username") if current_user else None
    
    try:
        result = crud.create_test_plan(payload, operator=operator)
        return ok(result, "创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {e}")


@router.post("/detail", response_model=ApiResponse[TestPlanDetailOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["drug_assay"]))])
def get_test_plan_detail(data: dict):
    """
    获取实验计划详情
    
    ## 请求示例
    ```json
    {
        "F_ID": 1
    }
    ```
    
    ## 返回示例
    ```json
    {
        "code": 1,
        "msg": "查询成功",
        "data": {
            "F_ID": 1,
            "F_GDID": 93,
            "F_SYBH": "SYSY20251120153045123",
            "F_SYMC": "PDO药敏试验-001",
            "F_SYKSRQ": "2025-11-20 00:00:00",
            "F_SYR": "张三",
            "F_SYJHYMD": "评估5-FU、奥沙利铂对类器官的抑制效果",
            "F_LQGMD": "5x104 cells/mL",
        "F_JZJND": "5%",
        "F_JZJTJ": "50uL",
        "F_FJ": null,
        "F_KBLX": "96孔板",
            "F_KBBH": "KB20251120001",
            "F_ZT": "草稿",
            "F_CJSJ": "2025-11-20 10:30:00",
            "F_BZ": null,
            "GDXX": {
                "F_ZZID": "BB20251027011",
                "F_DCID": "ZGMF-X42Sα-T-P1-CD",
                "F_DC": "P1",
                "F_GDBH": "BB20251027011P120251028100000",
                "F_YBLY": "组织",
                "F_SQR": "张三",
                "F_PYSJ": "2025-10-28 10:00:00"
            },
            "ZBXX": [
                {
                    "F_ID": 1,
                    "F_ZBMC": "对照组",
                    "F_ZBLX": "阴性对照",
                "F_SORT": 1,
                "F_FKSSZ": 3,
                "F_ND": ["0"],
                    "F_YY": []
                },
                {
                    "F_ID": 2,
                    "F_ZBMC": "5-FU组",
                    "F_ZBLX": "单药",
                "F_SORT": 2,
                "F_FKSSZ": 3,
                "F_ND": ["10uM", "50uM", "100uM"],
                    "F_YY": [
                        {
                            "F_ID": 1,
                            "F_SJID": 3,
                            "F_PCH": "20251115001",
                            "F_YL": "10",
                            "F_XSFA": "DMSO稀释至工作浓度",
                            "F_CKDH": "YWCKD202511200001"
                        }
                    ]
                }
            ],
            "KWXX": [
                {
                    "F_ID": 1,
                    "F_WZ": "A1",
                    "F_ZBMC": "对照组",
                    "F_ND": "0",
                    "F_JYTJ": "50uL",
                    "F_XSFA": null,
                    "F_YS": "#FFFFFF"
                }
            ]
        }
    }
    ```
    
    **说明**:
    - `GDXX`: 工单样本信息（标本号、代次、培养工单编号等）
    - `ZBXX`: 组别信息列表
    - `KWXX`: 孔位信息列表
    """
    try:
        plan_id = int(data.get("F_ID"))
    except Exception:
        return err("缺少或非法的F_ID")
    
    result = crud.get_test_plan_detail(plan_id)
    if not result:
        return err("实验计划不存在")
    
    return ok(result, "查询成功")


@router.post("/list", response_model=ApiResponse[TestPlanListOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["drug_assay"]))])
def list_test_plans(query: TestPlanListQuery):
    """
    查询实验计划列表
    
    ## 请求示例
    ```json
    {
        "F_GDID": 93,
        "F_SYMC": "药敏",
        "F_SYR": "张三",
        "F_ZT": "草稿",
        "page": 1,
        "page_size": 20
    }
    ```
    
    ## 返回示例
    ```json
    {
        "code": 1,
        "msg": "查询成功",
        "data": {
            "items": [
                {
                    "F_ID": 1,
                    "F_GDID": 93,
                    "F_SYMC": "PDO药敏试验-001",
                    "F_SYKSRQ": "2025-11-20 00:00:00",
                    "F_SYR": "张三",
                    "F_FKSSZ": 3,
                    "F_ND_COUNT": 3,
                    "F_GROUP_COUNT": 3,
                    "F_WELL_COUNT": 27,
                    "F_KBLX": "96孔板",
                    "F_KBBH": "KB20251120001",
                    "F_ZT": "草稿",
                    "F_CJSJ": "2025-11-20 10:30:00"
                }
            ],
            "total": 1
        }
    }
    ```
    """
    try:
        items, total = crud.list_test_plans(query.model_dump())
        return ok({"items": items, "total": total}, "查询成功")
    except Exception as e:
        return err(f"查询失败: {e}")


@router.post("/update", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["drug_assay"]))])
async def update_test_plan(
    JCXX: str = Form(..., description="基础信息JSON，必须包含F_ID"),
    ZBXX: str | None = Form(default=None, description="组别信息JSON（更新则覆盖）"),
    KWXX: str | None = Form(default=None, description="孔位信息JSON（更新则覆盖）"),
    F_FJ: UploadFile | None = File(None, description="附件文件"),
    current_user: dict = Depends(get_current_user_optional)
):
    """
    更新实验计划
    
    ## 功能说明
    - 更新实验计划的基础信息、组别、用药、孔位信息
    - 根据实验状态自动限制可修改的字段
    - 如果更新用药信息，会删除旧的出库单并创建新的出库单（仅已创建状态）
    
    ## 请求方式
    **Content-Type**: `multipart/form-data`
    
    ## 编辑权限
    - **已创建状态** - 可以修改所有内容
    - **进行中状态** - 只能修改：实验名称、实验人、实验目的、附件、备注、状态
    - **已完成状态** - 完全只读，不允许任何修改
    
    ## 请求参数
    
    ### **JCXX（基础信息）** - 必填
    必须包含 `F_ID` 字段标识要更新的实验计划
    ```json
    {
        "F_ID": 1,
        "F_SYMC": "PDO药敏试验-001（修订版）",
        "F_SYR": "李四",
        "F_ZT": "进行中"
    }
    ```
    
    ### **ZBXX（组别信息）** - 可选
    如果提供，将完全替换原有的组别和用药信息
    
    ### **KWXX（孔位信息）** - 可选
    如果提供，将完全替换原有的孔位信息
    
    ### **F_FJ（附件文件）** - 可选
    直接上传新的附件文件
    
    ## 返回示例
    ```json
    {
        "code": 1,
        "msg": "更新成功",
        "data": {
            "F_ID": 1,
            "updated": true,
            "auto_outbound_count": 0,
            "auto_outbound_codes": []
        }
    }
    ```
    
    ## 注意事项
    - 使用 multipart/form-data 格式提交
    - JCXX、ZBXX、KWXX 都是 JSON 字符串
    - 已创建状态：可以修改所有内容，修改用药会重新验证库存并生成出库单
    - 进行中状态：不允许修改组别、用药、孔位、浓度梯度等实验设计
    - 已完成状态：完全不允许修改
    """
    # 解析基础信息
    try:
        jcxx = json.loads(JCXX) if JCXX else {}
    except Exception as e:
        return err(f"基础信息(JCXX)解析失败: {e}")
    
    if not jcxx.get("F_ID"):
        return err("基础信息(JCXX)必须包含F_ID字段")
    
    # 解析组别信息
    zbxx = None
    if ZBXX is not None:
        try:
            zbxx = json.loads(ZBXX)
        except Exception as e:
            return err(f"组别信息(ZBXX)解析失败: {e}")
    
    # 解析孔位信息
    kwxx = None
    if KWXX is not None:
        try:
            kwxx = json.loads(KWXX)
        except Exception as e:
            return err(f"孔位信息(KWXX)解析失败: {e}")
    
    # 处理附件文件上传
    file_id = None
    if F_FJ is not None:
        dst_path, rel = build_upload_path(F_FJ.filename)
        content = await F_FJ.read()
        dst_path.write_bytes(content)
        fid = insert_file_record(rel, F_FJ.filename)
        if fid is not None:
            file_id = int(fid)
    
    # 组装完整数据
    payload = {**jcxx}
    if zbxx is not None:
        payload["ZBXX"] = zbxx
    if kwxx is not None:
        payload["KWXX"] = kwxx
    if file_id is not None:
        payload["F_FJ"] = file_id
    
    # 提取操作人信息
    operator = current_user.get("username") if current_user else None
    
    try:
        result = crud.update_test_plan(payload, operator=operator)
        return ok(result, "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {e}")


@router.post("/delete", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["drug_assay"]))])
def delete_test_plan(data: TestPlanDeleteIn, current_user: dict = Depends(get_current_user_optional)):
    """
    删除实验计划
    
    ## 功能说明
    - 删除实验计划及其所有关联数据
    - 同时删除关联的出库单
    - 只有草稿状态的实验计划可以删除
    
    ## 请求示例
    ```json
    {
        "F_ID": 1
    }
    ```
    
    ## 返回示例
    ```json
    {
        "code": 1,
        "msg": "删除成功",
        "data": {
            "deleted": true
        }
    }
    ```
    """
    # 提取操作人信息
    operator = current_user.get("username") if current_user else None
    
    try:
        crud.delete_test_plan(data.F_ID, operator=operator)
        return ok({"deleted": True}, "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"删除失败: {e}")


@router.post("/update-status", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["drug_assay"]))])
def update_test_plan_status(data: TestPlanStatusUpdateIn):
    """
    更新实验计划状态
    
    ## 功能说明
    - 更新实验计划状态：已创建 → 进行中 → 已完成
    
    ## 请求示例
    ```json
    {
        "F_ID": 1,
        "F_ZT": "进行中"
    }
    ```
    
    ## 返回示例
    ```json
    {
        "code": 1,
        "msg": "状态更新成功",
        "data": {
            "F_ID": 1,
            "F_ZT": "进行中"
        }
    }
    ```
    
    ## 有效状态值
    - 已创建
    - 进行中
    - 已完成
    """
    try:
        crud.update_test_plan_status(data.F_ID, data.F_ZT)
        return ok({"F_ID": data.F_ID, "F_ZT": data.F_ZT}, "状态更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"状态更新失败: {e}")


# ⚠️ 此路由已废弃，请使用 drug_test_result.py 中的新版本（支持文件上传）
# @router.post("/record/save", response_model=ApiResponse[dict], response_model_exclude_none=True,
#              dependencies=[Depends(require_roles(["drug_assay"]))])
# def save_experiment_record(
#     F_SYJH_ID: int = Form(..., description="实验计划ID"),
#     KBDS: str | None = Form(default=None, description="孔板读数JSON"),
#     XTXBH: str | None = Form(default=None, description="形态学变化JSON"),
#     XBHL_PJ: str | None = Form(default=None, description="平均细胞活率JSON"),
#     XBHL: str | None = Form(default=None, description="细胞活率（孔级）JSON"),
#     XBHL_WC: str | None = Form(default=None, description="细胞活率标准误差JSON"),
#     GMD: str | None = Form(default=None, description="光敏度JSON"),
#     IC50: str | None = Form(default=None, description="IC50 JSON"),
#     SYJL: str | None = Form(default=None, description="实验结论JSON"),
#     current_user: dict = Depends(get_current_user_optional)
# ):
#     """
#     保存实验记录
#     
#     ## 功能说明
#     - 保存实验结果数据（孔板读数、形态学变化、细胞活率、光敏度、IC50、实验结论等）
#     - 一个实验计划只能有一个实验结果记录
#     - 如果该实验计划已有记录 → 自动更新
#     - 如果该实验计划没有记录 → 自动新增
#     - **保存成功后自动将实验计划状态更新为"进行中"**（如果当前状态是"已创建"）
#     
#     ## 请求方式
#     **Content-Type**: `multipart/form-data` 或 `application/x-www-form-urlencoded`
#     
#     ## 请求参数
#     
#     - **F_SYJH_ID** (必填): 实验计划ID
#     - **KBDS** (可选): 孔板读数JSON字符串
#       - 格式: `{"ZBQ": {"A1": 0.523}, "ZBH": {"A1": 0.412}}`
#     - **XTXBH** (可选): 形态学变化JSON字符串
#       - 格式: `[{"F_ZBID": 1, "F_FJ_INDEX": [0], "F_XT": "描述"}]`
#     - **XBHL_PJ** (可选): 平均细胞活率JSON字符串
#       - 格式: `{"1": {"0.01": 98, "0.1": 96}}`
#     - **XBHL** (可选): 细胞活率（孔级）JSON字符串
#       - 格式: `{"2": {"0.1": {"A1": 78}}}`
#     - **XBHL_WC** (可选): 细胞活率标准误差JSON字符串
#       - 格式: `{"2": {"0.1": 1.5}}`
#     - **GMD** (可选): 光敏度JSON字符串
#       - 格式: `{"2": {"0.1": {"A1": 10}}}`
#     - **IC50** (可选): IC50 JSON字符串
#       - 格式: `[{"F_ZBID": 2, "F_FJ_INDEX": 5}]`
#     - **SYJL** (可选): 实验结论JSON字符串
#       - 格式: `{"JGSJ": {"相对IC50": {"1": null, "2": 0.45}}, "JLWB": "文本", "F_FJ_INDEX": [4]}`
#     
#     ## 返回示例
#     ```json
#     {
#         "code": 1,
#         "msg": "保存成功",
#         "data": {
#             "F_ID": 1,
#             "F_SYJH_ID": 1,
#             "status_updated": true,
#             "current_status": "进行中"
#         }
#     }
#     ```
#     
#     ## 注意事项
#     - 使用 multipart/form-data 格式提交
#     - 所有JSON字段（KBDS、XTXBH、XBHL_PJ等）都是字符串格式
#     - 如果实验计划状态是"已创建"，保存后会自动更新为"进行中"
#     - 如果实验计划状态已经是"进行中"或"已完成"，则不会修改状态
#     - status_updated 字段表示是否自动更新了状态
#     """
#     # 组装数据
#     data = {
#         "F_SYJH_ID": F_SYJH_ID,
#         "KBDS": KBDS,
#         "XTXBH": XTXBH,
#         "XBHL_PJ": XBHL_PJ,
#         "XBHL": XBHL,
#         "XBHL_WC": XBHL_WC,
#         "GMD": GMD,
#         "IC50": IC50,
#         "SYJL": SYJL
#     }
#     
#     try:
#         result = crud.save_experiment_record(data)
#         return ok(result, "保存成功")
#     except ValueError as e:
#         return err(str(e))
#     except Exception as e:
#         return err(f"保存失败: {e}")


@router.post(
    "/mark-unqualified",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["drug_assay"]))]
)
def mark_unqualified(
    payload: MarkUnqualifiedIn,
    current_user: dict = Depends(get_current_user_optional)
):
    """
    标记实验计划为不合格
    
    ## 功能说明
    - 将指定的实验计划标记为不合格
    - 记录不合格原因
    - 自动创建质控审核记录
    
    ## 请求参数
    - **F_SYJH_ID** (必填): 实验计划ID
    - **F_BHGYY** (可选): 不合格原因
    
    ## 返回示例
    ```json
    {
        "code": 1,
        "msg": "标记成功",
        "data": {
            "F_SYJH_ID": 123,
            "F_BHG": "true",
            "F_BHGYY": "数据异常",
            "qc_audit_id": 456
        }
    }
    ```
    
    ## 注意事项
    - 标记为不合格后，该实验计划将无法继续操作
    - 此操作会记录到质控审核系统中
    - 建议填写详细的不合格原因
    """
    plan_id = payload.F_SYJH_ID
    reason = payload.F_BHGYY
    
    if not plan_id:
        return err("缺少实验计划ID")
    
    # 获取操作人信息
    creator = (
        current_user.get("F_Ktype")
        or current_user.get("F_YSM")
        or ""
    ) if current_user else ""
    
    try:
        # 标记为不合格并创建质控审核记录
        qc_audit_id = crud.mark_unqualified(plan_id, reason, creator)
        
        result = {
            "F_SYJH_ID": plan_id,
            "F_BHG": "true",
            "F_BHGYY": reason or ""
        }
        
        # 添加质控审核记录ID到返回结果
        if qc_audit_id:
            result["qc_audit_id"] = qc_audit_id
        
        return ok(result, "标记成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"标记失败: {e}")

