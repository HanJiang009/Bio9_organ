"""
药物管理模块路由
"""
from fastapi import APIRouter, Depends
from app.schemas.common import ApiResponse
from typing import List
from app.schemas.drug_mgmt import (
    DrugCheckIn,
    DrugCheckOut,
    DrugCreateIn,
    DrugCreateOut,
    DrugUpdateIn,
    DrugUpdateOut,
    DrugDetailIn,
    DrugDetailOut,
    DrugListQuery,
    DrugListOut,
    DrugSimpleListOut,
    DrugDeleteIn,
    DrugDeleteOut,
    InboundCreateIn,
    InboundCreateOut,
    InboundListQuery,
    InboundListOut,
    InboundOptionsQuery,
    InboundOptionsOut,
    InboundListForOutboundQuery,
    InboundListForOutboundOut,
    OutboundCreateIn,
    OutboundCreateOut,
    OutboundListQuery,
    OutboundListOut,
    BatchOutboundItem,
    BatchOutboundCreateOut,
    ReagentListQuery,
    ReagentListOut,
)
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user
from app.crud import drug_mgmt as crud


router = APIRouter(prefix="/drug-mgmt", tags=["drug_mgmt"])


@router.post(
    "/drug/check",
    response_model=ApiResponse[DrugCheckOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def check_drug(payload: DrugCheckIn):
    """
    验证药物是否已存在
    
    **功能说明：**
    - 根据药物名称查询数据库中是否已存在同名药物
    - 如果不存在，返回"药物可登记"
    - 如果已存在，返回"已存在该药物，请直接入库"，并附带药物ID和编号
    
    **请求示例：**
    ```json
    {
        "F_YWMC": "来曲唑"
    }
    ```
    
    **响应示例（不存在）：**
    ```json
    {
        "code": 1,
        "msg": "药物可登记",
        "data": {
            "exists": false,
            "message": "药物可登记",
            "drug_id": null,
            "drug_code": null
        }
    }
    ```
    
    **响应示例（已存在）：**
    ```json
    {
        "code": 1,
        "msg": "已存在该药物，请直接入库",
        "data": {
            "exists": true,
            "message": "已存在该药物，请直接入库",
            "drug_id": 123,
            "drug_code": "YW001"
        }
    }
    ```
    """
    try:
        result = crud.check_drug_exists(ywmc=payload.F_YWMC)
        return ok(DrugCheckOut(**result), result["message"])
    except Exception as e:
        return err(f"验证失败: {str(e)}")


@router.post(
    "/drug/create",
    response_model=ApiResponse[DrugCreateOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def create_drug(
    payload: DrugCreateIn,
    current_user: dict = Depends(get_current_user),
):
    """
    创建药物（支持试剂登记）
    
    **功能说明：**
    - 创建原药或试剂
    - 自动生成药物编号（YW001、YW002...）
    - 自动填充登记人
    
    **原药登记示例：**
    ```json
    {
        "F_YWLX": "抑制剂",
        "F_YWMC": "来曲唑",
        "F_GSMC": "某某公司",
        "F_CAS": "112809-51-5",
        "F_YWFZL": "285.3",
        "F_CD": "99%"
    }
    ```
    
    **试剂登记示例：**
    ```json
    {
        "F_YWLX": "试剂",
        "F_YWMC": "培养基A",
        "F_GSMC": "自制",
        "F_SJPF": [1, 5, 8],
        "F_BZ": "由来曲唑等3种原药配制"
    }
    ```
    
    **注意事项：**
    - 当 `F_YWLX` 为"试剂"时，应传入 `F_SJPF` 字段（原药ID数组）
    - `F_SJPF` 存储试剂配方，数组中的每个元素是原药的ID
    - 原药登记不需要 `F_SJPF` 字段
    """
    try:
        # 自动填充登记人
        data = payload.dict(exclude_none=True)
        if "F_DJR" not in data and current_user:
            data["F_DJR"] = current_user.get("F_Ktype") or current_user.get("F_YSM")
        
        result = crud.create_drug(data=data)
        return ok(DrugCreateOut(**result), "药物创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {str(e)}")


@router.post(
    "/drug/update",
    response_model=ApiResponse[DrugUpdateOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def update_drug(payload: DrugUpdateIn):
    """更新药物"""
    try:
        data = payload.dict(exclude={"F_ID"}, exclude_none=True)
        result = crud.update_drug(
            drug_id=payload.F_ID,
            data=data
        )
        return ok(DrugUpdateOut(**result), "药物更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {str(e)}")


@router.post(
    "/drug/detail",
    response_model=ApiResponse[DrugDetailOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def get_drug_detail(payload: DrugDetailIn):
    """获取药物详情"""
    try:
        result = crud.get_drug_detail(drug_id=payload.F_ID)
        if not result:
            return err("药物不存在")
        return ok(DrugDetailOut(**result), "查询成功")
    except Exception as e:
        return err(f"查询失败: {str(e)}")


@router.post(
    "/drug/list",
    response_model=ApiResponse[DrugListOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def list_drugs(payload: DrugListQuery):
    """获取药物列表"""
    try:
        result = crud.list_drugs(
            ywbh=payload.F_YWBH,
            ywlx=payload.F_YWLX,
            ywmc=payload.F_YWMC,
            gsmc=payload.F_GSMC,
            skip=payload.skip,
            limit=payload.limit
        )
        return ok(DrugListOut(**result), "查询成功")
    except Exception as e:
        return err(f"查询失败: {str(e)}")


@router.post(
    "/drug/simple-list",
    response_model=ApiResponse[DrugSimpleListOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def list_drugs_simple(payload: DrugListQuery):
    """
    获取原药简化列表（仅返回ID和名称，排除试剂）
    
    **功能说明：**
    - 用于试剂登记时选择配方成分（原药）
    - 仅返回原药ID和名称，自动排除药物类型为"试剂"的记录
    - 支持按编号、类型、名称、公司筛选
    
    **请求示例1（查询所有原药）：**
    ```json
    {
        "skip": 0,
        "limit": 100
    }
    ```
    
    **请求示例2（按类型筛选原药）：**
    ```json
    {
        "F_YWLX": "抑制剂",
        "skip": 0,
        "limit": 100
    }
    ```
    
    **请求示例3（按名称模糊搜索原药）：**
    ```json
    {
        "F_YWMC": "曲",
        "skip": 0,
        "limit": 20
    }
    ```
    
    **响应示例：**
    ```json
    {
        "code": 1,
        "msg": "查询成功",
        "data": {
            "total": 2,
            "data": [
                {
                    "F_ID": 1,
                    "F_YWMC": "来曲唑"
                },
                {
                    "F_ID": 5,
                    "F_YWMC": "阿曲库铵"
                }
            ]
        }
    }
    ```
    
    **注意事项：**
    - 本接口自动排除药物类型为"试剂"的记录
    - 只返回原药的ID和名称，不返回其他字段
    - 适用于试剂登记时选择配方成分
    """
    try:
        result = crud.list_drugs_simple(
            ywbh=payload.F_YWBH,
            ywlx=payload.F_YWLX,
            ywmc=payload.F_YWMC,
            gsmc=payload.F_GSMC,
            skip=payload.skip,
            limit=payload.limit
        )
        return ok(DrugSimpleListOut(**result), "查询成功")
    except Exception as e:
        return err(f"查询失败: {str(e)}")


@router.post(
    "/reagent/list",
    response_model=ApiResponse[ReagentListOut],
    dependencies=[Depends(require_roles(["drug_assay"]))],
)
def list_reagents(payload: ReagentListQuery):
    """
    获取试剂列表（用于药物实验，含批次信息）
    
    **功能说明：**
    - 用于药物实验模块选择试剂及其批次
    - 仅返回药物类型为"试剂"且有库存的记录
    - 每个试剂聚合其所有有库存的入库批次（入库单号、批次号、有效期、在库数量）
    - 支持按药物编号、药物名称模糊搜索
    - 批次按有效期升序排列（先到期先出，FEFO原则）
    
    **应用场景：**
    - 前端可展示试剂列表，用户选择试剂后，再选择具体批次进行出库
    - 每个批次包含入库单号，可用于追溯
    
    **请求示例1（查询所有有库存的试剂）：**
    ```json
    {
        "skip": 0,
        "limit": 20
    }
    ```
    
    **请求示例2（按名称模糊搜索试剂）：**
    ```json
    {
        "F_YWMC": "药丸",
        "skip": 0,
        "limit": 20
    }
    ```
    
    **请求示例3（按编号模糊搜索试剂）：**
    ```json
    {
        "F_YWBH": "YW2025",
        "skip": 0,
        "limit": 20
    }
    ```
    
    **响应示例：**
    ```json
    {
        "code": 1,
        "msg": "查询成功",
        "data": {
            "total": 1,
            "data": [
                {
                    "F_ID": 3,
                    "F_YWBH": "YW202511170003",
                    "F_YWMC": "混合大药丸",
                    "F_YWSPM": null,
                    "F_CAS": null,
                    "F_GSMC": "FROM SOFTWARE",
                    "F_ZKSL": 87.0,
                    "batches": [
                        {
                            "F_RKDH": "YWRKD20251118141923428",
                            "F_PCH": "SJ20251117001",
                            "F_YXQ": "2026-06-17 00:00:00",
                            "F_ZKSL": 87.0,
                            "F_RKSL": 100.0,
                            "F_CKSL": 13.0
                        }
                    ]
                }
            ]
        }
    }
    ```
    
    **字段说明：**
    - `F_ZKSL`（试剂级别）: 该试剂的总在库数量
    - `batches`: 该试剂的所有有库存的入库批次
    - `F_ZKSL`（批次级别）: 该批次的在库数量
    - `F_RKDH`: 入库单号，用于出库时关联
    - `F_PCH`: 批次号
    - `F_YXQ`: 有效期（批次按此字段升序排列）
    """
    try:
        result = crud.list_reagents(
            ywbh=payload.F_YWBH,
            ywmc=payload.F_YWMC,
            skip=payload.skip,
            limit=payload.limit
        )
        return ok(ReagentListOut(**result), "查询成功")
    except Exception as e:
        return err(f"查询失败: {str(e)}")


@router.post(
    "/drug/delete",
    response_model=ApiResponse[DrugDeleteOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def delete_drug(payload: DrugDeleteIn):
    """删除药物"""
    try:
        result = crud.delete_drug(drug_id=payload.F_ID)
        return ok(DrugDeleteOut(**result), "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"删除失败: {str(e)}")


# ==================== 入库单接口 ====================

@router.post(
    "/inbound/create",
    response_model=ApiResponse[InboundCreateOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def create_inbound(
    payload: InboundCreateIn,
    current_user: dict = Depends(get_current_user),
):
    """
    创建入库单（支持试剂入库并自动出库原药）
    
    **功能说明：**
    - 创建药物或试剂的入库单
    - 当入库试剂时，需要提供原药使用信息（原药ID、入库单号、使用数量）
    - 系统会自动为使用的原药创建出库单，并验证库存是否充足
    
    **原药入库示例：**
    ```json
    {
        "F_YWID": 1,
        "F_RKSL": 100.0,
        "F_PCH": "20251117001",
        "F_YXQ": "2026-11-17T00:00:00",
        "F_BZ": "来曲唑入库"
    }
    ```
    
    **试剂入库示例：**
    ```json
    {
        "F_YWID": 3,
        "F_RKSL": 50.0,
        "F_PCH": "SJ20251117001",
        "F_YXQ": "2026-06-17T00:00:00",
        "F_YYSY": [
            {
                "F_YWID": 1,
                "F_RKDH": "YWRKD20251117153045123",
                "F_SYSL": 10.5
            },
            {
                "F_YWID": 2,
                "F_RKDH": "YWRKD20251117153045456",
                "F_SYSL": 5.2
            }
        ],
        "F_BZ": "配制试剂A"
    }
    ```
    
    **试剂入库响应示例：**
    ```json
    {
        "code": 1,
        "msg": "入库单创建成功",
        "data": {
            "F_ID": 10,
            "F_RKDH": "YWRKD20251117160000001",
            "F_YWID": 3,
            "F_RKSL": 50.0,
            "auto_outbound_count": 2,
            "auto_outbound_codes": [
                "YWCKD20251117160000001",
                "YWCKD20251117160000002"
            ],
            ...
        }
    }
    ```
    
    **字段说明：**
    - `F_YYSY`: 原药使用信息数组（仅试剂入库时需要）
      - `F_YWID`: 原药ID
      - `F_RKDH`: 原药的入库单号
      - `F_SYSL`: 使用数量（会从该入库单中自动出库）
    - `auto_outbound_count`: 自动创建的原药出库单数量
    - `auto_outbound_codes`: 自动创建的原药出库单号列表
    
    **业务逻辑：**
    1. 创建试剂入库单
    2. 验证配方中每个原药的入库单是否存在
    3. 验证每个原药的剩余库存是否充足
    4. 为每个原药自动创建出库单（备注为"试剂'{试剂名}'入库自动出库"）
    5. 所有操作在同一事务中，任何失败都会回滚
    
    **注意事项：**
    - 试剂入库必须提供 `F_YYSY` 字段
    - 原药库存不足时会报错并回滚
    - 自动创建的出库单会记录为"试剂入库自动出库"
    """
    try:
        # 自动填充入库操作人
        data = payload.dict(exclude_none=True)
        if "F_RKCZR" not in data and current_user:
            data["F_RKCZR"] = current_user.get("F_Ktype") or current_user.get("F_YSM")
        
        result = crud.create_inbound(data=data)
        return ok(InboundCreateOut(**result), "入库单创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {str(e)}")


@router.post(
    "/inbound/list",
    response_model=ApiResponse[InboundListOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def list_inbounds(payload: InboundListQuery):
    """
    获取入库单列表
    
    **功能说明：**
    - 查询入库单列表，包含药物信息和剩余库存
    - 支持按药物ID、药物编号（多选）、批次号过滤
    - 每条入库单返回其剩余在库数量（入库数量 - 基于该入库单的出库数量）
    
    **请求示例1（基本查询）：**
    ```json
    {
        "skip": 0,
        "limit": 20
    }
    ```
    
    **请求示例2（按药物编号多选过滤）：**
    ```json
    {
        "F_YWBH": ["YW001", "YW003", "YW005"],
        "skip": 0,
        "limit": 20
    }
    ```
    
    **请求示例3（组合过滤）：**
    ```json
    {
        "F_YWBH": ["YW001", "YW002"],
        "F_PCH": "20251117",
        "skip": 0,
        "limit": 20
    }
    ```
    
    **响应示例：**
    ```json
    {
        "code": 1,
        "msg": "查询成功",
        "data": {
            "total": 10,
            "data": [
                {
                    "F_ID": 1,
                    "F_RKDH": "YWRKD20231027153045123",
                    "F_YWID": 1,
                    "F_RKSL": 100.0,
                    "F_ZKSL": 65.0,
                    "F_YWBH": "YW001",
                    "F_YWMC": "来曲唑",
                    "F_YWLX": "抑制剂",
                    "F_GSMC": "某某公司",
                    ...
                }
            ]
        }
    }
    ```
    
    **字段说明：**
    - `F_ZKSL`: 该入库单的剩余在库数量（入库数量 - 出库数量）
    - `F_YWBH`: 支持数组，可同时选择多个药物编号进行过滤
    """
    try:
        result = crud.list_inbounds(
            ywid=payload.F_YWID,
            ywbh_list=payload.F_YWBH,
            pch=payload.F_PCH,
            skip=payload.skip,
            limit=payload.limit
        )
        return ok(InboundListOut(**result), "查询成功")
    except Exception as e:
        return err(f"查询失败: {str(e)}")


@router.post(
    "/inbound/options",
    response_model=ApiResponse[InboundOptionsOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def get_inbound_options(payload: InboundOptionsQuery):
    """
    获取指定药物的入库单选项（用于试剂配制时选择原药批次）
    
    **功能说明：**
    - 根据药物ID获取该药物的所有入库单（含批次号、有效期、剩余库存）
    - 只返回剩余库存 > 0 的入库单
    - 按有效期升序排列（先到期的优先使用）
    - 用于试剂入库时选择使用哪个批次的原药
    
    **使用场景：**
    1. 用户在试剂登记页面选择配方成分（原药）
    2. 调用此接口获取该原药的所有可用批次
    3. 用户选择批次并输入使用数量
    4. 提交试剂入库时，传入选中的 F_RKDH（入库单号）
    
    **请求示例：**
    ```json
    {
        "F_YWID": 1
    }
    ```
    
    **响应示例：**
    ```json
    {
        "code": 1,
        "msg": "查询成功",
        "data": {
            "data": [
                {
                    "F_RKDH": "YWRKD20231101153045123",
                    "F_PCH": "20231101",
                    "F_YXQ": "2025-11-01",
                    "F_ZKSL": 50.0
                },
                {
                    "F_RKDH": "YWRKD20231215160000456",
                    "F_PCH": "20231215",
                    "F_YXQ": "2026-12-15",
                    "F_ZKSL": 80.0
                }
            ]
        }
    }
    ```
    
    **字段说明：**
    - `F_RKDH`: 入库单号（用于试剂入库时指定使用哪个批次）
    - `F_PCH`: 批次号
    - `F_YXQ`: 有效期（YYYY-MM-DD）
    - `F_ZKSL`: 剩余在库数量（可用库存）
    
    **前端使用示例：**
    ```javascript
    // 1. 用户选择原药
    const drug = { F_ID: 1, F_YWMC: "来曲唑" };
    
    // 2. 获取该原药的所有批次
    const response = await axios.post('/drug-mgmt/inbound/options', {
        F_YWID: drug.F_ID
    });
    
    // 3. 展示批次下拉框
    const options = response.data.data.data.map(item => ({
        label: `批次: ${item.F_PCH} | 有效期: ${item.F_YXQ} | 剩余: ${item.F_ZKSL}g`,
        value: item.F_RKDH
    }));
    
    // 4. 用户选择批次并输入使用数量
    const selectedRKDH = "YWRKD20231101153045123";
    const usageAmount = 10.5;
    
    // 5. 提交试剂入库
    await axios.post('/drug-mgmt/inbound/create', {
        F_YWID: 3,  // 试剂ID
        F_RKSL: 50.0,
        F_PCH: "SJ20251224001",
        F_YXQ: "2026-12-24",
        F_YYSY: [
            {
                F_YWID: drug.F_ID,
                F_RKDH: selectedRKDH,  // 用户选择的入库单号
                F_SYSL: usageAmount
            }
        ]
    });
    ```
    
    **注意事项：**
    - 只返回剩余库存 > 0 的入库单
    - 自动按有效期升序排列，建议优先使用先到期的批次
    - 试剂入库接口无需修改，直接传入选中的 F_RKDH 即可
    """
    try:
        result = crud.get_inbound_options_by_drug(ywid=payload.F_YWID)
        return ok(InboundOptionsOut(**result), "查询成功")
    except Exception as e:
        return err(f"查询失败: {str(e)}")


@router.post(
    "/inbound/list-for-outbound",
    response_model=ApiResponse[InboundListForOutboundOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def list_inbounds_for_outbound(payload: InboundListForOutboundQuery):
    """
    获取可用于出库的入库单列表
    
    **功能说明：**
    - 查询可用于出库的入库单列表（仅显示剩余库存 > 0 的记录）
    - 支持按药物编号、药物名称、入库单号模糊查询
    - 按有效期升序排列（先到期的优先）
    - 用于出库时选择对应的入库单
    
    **使用场景：**
    1. 用户在出库页面输入搜索条件（药物编号/名称/入库单号）
    2. 调用此接口获取可用的入库单列表
    3. 选择入库单并填写出库数量
    4. 提交出库申请
    
    **请求示例1（无过滤条件）：**
    ```json
    {
        "skip": 0,
        "limit": 20
    }
    ```
    
    **请求示例2（按药物编号查询）：**
    ```json
    {
        "F_YWBH": "YW001",
        "skip": 0,
        "limit": 20
    }
    ```
    
    **请求示例3（按药物名称查询）：**
    ```json
    {
        "F_YWMC": "来曲唑",
        "skip": 0,
        "limit": 20
    }
    ```
    
    **请求示例4（按入库单号查询）：**
    ```json
    {
        "F_RKDH": "YWRKD20231027",
        "skip": 0,
        "limit": 20
    }
    ```
    
    **请求示例5（组合查询）：**
    ```json
    {
        "F_YWBH": "YW",
        "F_YWMC": "曲",
        "skip": 0,
        "limit": 20
    }
    ```
    
    **响应示例：**
    ```json
    {
        "code": 1,
        "msg": "查询成功",
        "data": {
            "total": 5,
            "data": [
                {
                    "F_ID": 1,
                    "F_RKDH": "YWRKD20231027153045123",
                    "F_YWID": 1,
                    "F_YWBH": "YW001",
                    "F_YWMC": "来曲唑",
                    "F_YWLX": "抑制剂",
                    "F_GSMC": "某某公司",
                    "F_RKSL": 100.0,
                    "F_ZKSL": 65.0,
                    "F_PCH": "20231027",
                    "F_YXQ": "2025-10-27",
                    "F_RKRQ": "2023-10-27 15:30:45",
                    "F_RKCZR": "张三"
                }
            ]
        }
    }
    ```
    
    **字段说明：**
    - `F_ZKSL`: 剩余在库数量（入库数量 - 出库数量），必定 > 0
    - `F_YXQ`: 有效期（YYYY-MM-DD）
    - 结果按有效期升序排列，先到期的排在前面
    
    **与 `/inbound/list` 的区别：**
    1. **过滤逻辑**：本接口强制过滤 `F_ZKSL > 0`，`/inbound/list` 显示所有记录
    2. **查询字段**：本接口支持药物名称模糊查询，`/inbound/list` 不支持
    3. **排序规则**：本接口按有效期升序（先到期优先），`/inbound/list` 按创建时间降序
    4. **使用场景**：本接口用于出库选择，`/inbound/list` 用于入库记录管理
    """
    try:
        result = crud.list_inbounds_for_outbound(
            ywbh=payload.F_YWBH,
            ywmc=payload.F_YWMC,
            ywlx=payload.F_YWLX,
            rkdh=payload.F_RKDH,
            skip=payload.skip,
            limit=payload.limit
        )
        return ok(InboundListForOutboundOut(**result), "查询成功")
    except Exception as e:
        return err(f"查询失败: {str(e)}")


# ==================== 出库单接口 ====================

@router.post(
    "/outbound/create",
    response_model=ApiResponse[OutboundCreateOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def create_outbound(
    payload: OutboundCreateIn,
    current_user: dict = Depends(get_current_user),
):
    """创建出库单"""
    try:
        # 自动填充出库人
        data = payload.dict(exclude_none=True)
        if "F_CKR" not in data and current_user:
            data["F_CKR"] = current_user.get("F_Ktype") or current_user.get("F_YSM")
        
        result = crud.create_outbound(data=data)
        return ok(OutboundCreateOut(**result), "出库单创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {str(e)}")


@router.post(
    "/outbound/list",
    response_model=ApiResponse[OutboundListOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def list_outbounds(payload: OutboundListQuery):
    """获取出库单列表"""
    try:
        result = crud.list_outbounds(
            ywid=payload.F_YWID,
            ywbh=payload.F_YWBH,
            ywmc=payload.F_YWMC,
            ywlx=payload.F_YWLX,
            ckdh=payload.F_CKDH,
            rkdh=payload.F_RKDH,
            pch=payload.F_PCH,
            skip=payload.skip,
            limit=payload.limit
        )
        return ok(OutboundListOut(**result), "查询成功")
    except Exception as e:
        return err(f"查询失败: {str(e)}")


@router.post(
    "/outbound/batch-create",
    response_model=ApiResponse[BatchOutboundCreateOut],
    dependencies=[Depends(require_roles(["drug_mgmt"]))],
)
def batch_create_outbounds(
    items: List[BatchOutboundItem],
    current_user: dict = Depends(get_current_user),
):
    """批量创建出库单
    
    接口说明：
    - 批量创建出库单，从入库单同步药物信息
    - 一次批量操作生成一个统一的出库单号，所有出库记录共享该出库单号
    - 每条出库单独立验证，部分失败不影响其他出库单
    - 自动验证库存充足性
    - 自动填充出库人
    
    入参说明：
    - 直接传出库单数组（不需要嵌套在items字段中）
    - 数组项：
      - F_RKDH: 入库单号（必填）
      - F_CKSL: 出库数量（必填，大于0）
      - F_BZ: 备注（可选）
    
    返回说明：
    - success_count: 成功创建的出库记录数量
    - failed_count: 失败的出库记录数量
    - success_items: 成功的出库记录列表（所有记录共享同一个出库单号）
    - failed_items: 失败的出库记录列表（包含失败原因）
    """
    try:
        # 转换为字典格式
        items_dict = [item.dict(exclude_none=True) for item in items]
        
        # 自动获取出库人
        current_user_code = None
        if current_user:
            current_user_code = current_user.get("F_Ktype") or current_user.get("F_YSM")
        
        # 批量创建出库单
        result = crud.batch_create_outbounds(
            items=items_dict,
            current_user_code=current_user_code
        )
        
        # 构建响应消息
        # 获取出库单号（从第一个成功项中获取）
        ckdh = result["success_items"][0]["F_CKDH"] if result["success_items"] else ""
        
        if result["failed_count"] == 0:
            msg = f"批量出库成功，共创建 {result['success_count']} 条出库记录，出库单号: {ckdh}"
        elif result["success_count"] == 0:
            msg = f"批量出库失败，{result['failed_count']} 条全部失败"
        else:
            msg = f"批量出库部分成功，成功 {result['success_count']} 条，失败 {result['failed_count']} 条，出库单号: {ckdh}"
        
        return ok(BatchOutboundCreateOut(**result), msg)
    except Exception as e:
        return err(f"批量出库失败: {str(e)}")

