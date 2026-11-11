"""
盒子管理模块路由
"""
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, Header, HTTPException
from app.schemas.common import ApiResponse
from app.schemas.box_mgmt import (
    BoxTpltCreate, BoxTpltUpdate, BoxTpltOut, BoxTpltListQuery, BoxTpltListOut,
    BoxCreate, BoxUpdate, BoxOut, BoxListQuery, BoxListOut,
    BoxDeleteRequest, BoxCapacityOut, BoxLayoutOut, BoxHzhRequest,
    BoxDetailRequest, BoxUpdatePositionsRequest,
    PreviewHzhRequest, PreviewHzhOut,
)
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user
from app.core.token import verify_token
from app.core.settings import settings
from app.crud import box_mgmt as crud


router = APIRouter(prefix="/box-mgmt", tags=["box_mgmt"])


def _require_any_roles(allowed: List[str]):
    """任一角色即可（require_roles 为全部满足，此处按 OR）。"""

    def _dep(
        authorization: str = Header(None),
        x_dev_roles: str | None = Header(default=None, alias="X-Dev-Roles"),
        x_token: str | None = Header(default=None, alias="X-Token"),
    ) -> Dict[str, Any]:
        roles_list: List[str] = []
        payload: Dict[str, Any] = {}
        if settings.debug and x_dev_roles:
            roles_list = [r.strip() for r in x_dev_roles.split(",") if r and r.strip()]
            payload = {"roles": roles_list}
        else:
            token_str = None
            if authorization:
                token_str = authorization[7:].strip() if authorization.startswith("Bearer ") else authorization.strip()
            if not token_str and x_token:
                token_str = x_token.strip()
            if not token_str:
                raise HTTPException(status_code=401, detail="缺少或非法的认证头")
            try:
                payload = verify_token(token_str)
            except Exception:
                raise HTTPException(status_code=401, detail="Token 无效或已过期")
            roles_list = list(payload.get("roles") or [])
        try:
            from app.utils.license_ui_modules import filter_module_ids
            roles_list = filter_module_ids(roles_list)
        except Exception:
            pass
        if not set(allowed).intersection(roles_list):
            raise HTTPException(status_code=403, detail="权限不足")
        return payload

    return _dep


# ============================================
# 盒子模板接口
# ============================================

@router.post(
    "/template/list",
    response_model=ApiResponse[BoxTpltListOut],
    response_model_exclude_none=True,
)
async def list_templates(query: BoxTpltListQuery):
    """查询盒子模板列表（所有用户可访问）"""
    try:
        result = crud.list_box_templates(query.model_dump())
        return ok(result,"查询成功")
    except Exception as e:
        return err(str(e))


@router.post(
    "/template/create",
    response_model=ApiResponse[BoxTpltOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["box_mgmt"]))],
)
async def create_template(
    data: BoxTpltCreate,
    current_user: dict = Depends(get_current_user),
):
    """创建盒子模板（需鉴权）"""
    try:
        tpl_id = crud.create_box_template(data.model_dump())
        tpl = crud.get_box_template_by_id(tpl_id)
        return ok(tpl, "创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {str(e)}")


@router.post(
    "/template/update",
    response_model=ApiResponse[BoxTpltOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["box_mgmt"]))],
)
async def update_template(
    data: BoxTpltUpdate,
    current_user: dict = Depends(get_current_user),
):
    """更新盒子模板（需鉴权）"""
    try:
        tpl_id = data.F_ID
        update_data = {k: v for k, v in data.model_dump().items() if v is not None and k != "F_ID"}
        crud.update_box_template(tpl_id, update_data)
        tpl = crud.get_box_template_by_id(tpl_id)
        return ok(tpl, "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {str(e)}")


@router.post(
    "/template/delete",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["box_mgmt"]))],
)
async def delete_template(
    request: BoxDeleteRequest,
    current_user: dict = Depends(get_current_user),
):
    """删除盒子模板（需鉴权，不可删除已有使用记录的模板）"""
    try:
        crud.delete_box_template(request.F_ID)
        return ok({"F_ID": request.F_ID}, "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"删除失败: {str(e)}")


# ============================================
# 盒号编码接口（样本关联新增盒子时使用）
# ============================================

@router.post(
    "/preview-hzh",
    response_model=ApiResponse[PreviewHzhOut],
    response_model_exclude_none=False,
    dependencies=[Depends(_require_any_roles(["box_mgmt", "sample_linkage"]))],
)
async def preview_hzh(payload: PreviewHzhRequest):
    """
    预览盒号（预计值，**不消耗**序列号）——对齐 `/sample-receive/preview-zzid`。

    - 已配置字典编码规则 `F_HZH`：返回 peek 预计盒号，`rule_available=true`
    - 未配置规则：`F_HZH=null`，`rule_available=false`，前端应允许用户手填
    - 预览会跳过 T_BOX / T_YBFZ 中已占用的盒号（手填、改库留下的空洞）

    用户关闭页面不保存时不会跳号。
    正式占号不在本接口完成，而是在样本关联 **save/draft 创建盒子时**
   （`create_box` 内嵌与标本号相同的 peek/generate/手填判断）。
    """
    try:
        data = crud.peek_hzh(payload.context)
        return ok(data, "预览成功" if data.get("rule_available") else "未配置盒号编码规则，请手动输入")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"预览失败: {e}")


# ============================================
# 盒子使用接口
# ============================================

@router.post(
    "/boxes/list",
    response_model=ApiResponse[BoxListOut],
    response_model_exclude_none=True,
    dependencies=[Depends(_require_any_roles(["box_mgmt", "sample_linkage"]))],
)
async def list_boxes(query: BoxListQuery):
    """查询盒子使用列表（支持按日期、状态、模板过滤）"""
    try:
        result = crud.list_boxes(query.model_dump())
        return ok(result)
    except Exception as e:
        return err(str(e))


@router.post(
    "/box/detail",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(_require_any_roles(["box_mgmt", "sample_linkage"]))],
)
async def box_detail(request: BoxDetailRequest):
    """
    查询盒子详情（含完整样本管列表和容量统计）

    tubes 列表包含每根样本管的完整字段（含 F_ID、F_SFYS），可直接用于：
    - 页面展示
    - 将含 F_ID 的条目传给 `POST /sample-linkage/assign/save` 发起运输
    - 将含 F_ID 的条目传给 `POST /box/update-positions` 调整格子坐标

    通过 F_YSZT 过滤仅影响 tubes 列表内容；
    capacity / used / remaining 始终反映盒内真实总量。
    """
    try:
        data = crud.get_box_detail(request.F_HZH, request.F_YSZT)
        if not data:
            return ok(None, f"盒号 {request.F_HZH} 未创建，可通过发起运输或暂存进行创建")
        return ok(data)
    except Exception as e:
        return err(str(e))


@router.post(
    "/box/update",
    response_model=ApiResponse[BoxOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["box_mgmt"]))],
)
async def update_box(
    data: BoxUpdate,
    current_user: dict = Depends(get_current_user),
):
    """更新盒子状态/位置/备注（需鉴权）"""
    try:
        box_id = data.F_ID
        update_data = {k: v for k, v in data.model_dump().items() if v is not None and k != "F_ID"}
        crud.update_box(box_id, update_data)
        box = crud.get_box_by_id(box_id)
        return ok(box, "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {str(e)}")


@router.post(
    "/box/delete",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["box_mgmt"]))],
)
async def delete_box(
    request: BoxDeleteRequest,
    current_user: dict = Depends(get_current_user),
):
    """删除盒子记录（需鉴权，不可删除含样本管的盒子）"""
    try:
        crud.delete_box(request.F_ID)
        return ok({"F_ID": request.F_ID}, "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"删除失败: {str(e)}")


@router.post(
    "/box/capacity",
    response_model=ApiResponse[BoxCapacityOut],
    response_model_exclude_none=True,
)
async def box_capacity(request: BoxHzhRequest):
    """
    查询盒子剩余容量（分装时调用，所有用户可访问）

    返回：总容量、已用管数、剩余空间
    """
    try:
        data = crud.get_box_capacity(request.F_HZH)
        return ok(data)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e))


@router.post(
    "/box/layout",
    response_model=ApiResponse[BoxLayoutOut],
    response_model_exclude_none=True,
)
async def box_layout(request: BoxHzhRequest):
    """
    查询盒子平面图（所有用户可访问）

    返回盒子尺寸（行数×列数）及每个格子的占用状态与样本信息，
    前端可直接按 row/col 渲染二维格子图，悬浮/点击时展示 sample 字段内容。

    - cells 数组长度固定为 rows × cols
    - occupied=true 时 sample 包含样本管简要信息
    - occupied=false 时 sample 为 null（即空格子）
    - F_WZ 为 null 的样本管（未指定位置）不计入格子，可通过 /box/detail 查看完整管列表
    """
    try:
        data = crud.get_box_layout(request.F_HZH)
        return ok(data, "查询成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e))



@router.post(
    "/box/update-positions",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["box_mgmt"]))],
)
async def update_box_positions(
    request: BoxUpdatePositionsRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    批量更新暂存分装记录的盒号和格子坐标（需鉴权）

    仅允许操作 F_YSZT='未运送' 的分装记录；已在运途或已接收的记录无法调整。

    ## 请求示例
    ```json
    {
      "ITEMS": [
        { "F_ID": 123, "F_WZ": "B3" },
        { "F_ID": 124, "F_HZH": "BOX002", "F_WZ": "A1" }
      ],
      "BOXES": [
        { "F_HZH": "BOX002", "F_TEMPLATE_ID": 1 }
      ]
    }
    ```
    - 只提供 F_WZ：在原盒内调整位置
    - 同时提供 F_HZH：将该记录移至另一个盒子的指定位置
    - 目标盒子不存在时需在 BOXES 中提供 F_TEMPLATE_ID 以自动创建
    """
    try:
        czr = (
            current_user.get("F_KTYPE")
            or current_user.get("F_Ktype")
            or str(current_user.get("F_ID", ""))
        )
        data = crud.update_box_positions(request.model_dump(), czr)
        return ok(data, f"成功更新 {data['count']} 条记录的位置信息")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {str(e)}")
