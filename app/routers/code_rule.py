"""
编码规则管理模块路由
"""
from fastapi import APIRouter, Depends
from app.schemas.common import ApiResponse
from app.schemas.code_rule import (
    CodeRuleCreate,
    CodeRuleUpdate,
    CodeRuleOut,
    CodeRuleListQuery,
    CodeRuleListOut,
    CodeRuleRefFieldsQuery,
    CodeRuleRefFieldsOut,
    CodeRuleDeleteRequest,
    CodeGenerateRequest,
    CodeGenerateOut,
    CodePreviewRequest,
    CodePreviewOut,
    CodeSequenceListQuery,
    CodeSequenceListOut,
)
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user
from app.crud import code_rule as crud


router = APIRouter(prefix="/code-rule", tags=["code_rule"])


# ============================================
# 编码规则相关接口
# ============================================

@router.post(
    "/rule/list",
    response_model=ApiResponse[CodeRuleListOut],
    response_model_exclude_none=False,
)
async def list_rules(query: CodeRuleListQuery):
    """查询编码规则列表（所有用户可访问）"""
    try:
        result = crud.list_code_rules(query.model_dump())
        return ok(result, "查询成功")
    except Exception as e:
        return err(str(e))


@router.post(
    "/rule/ref-fields",
    response_model=ApiResponse[CodeRuleRefFieldsOut],
    response_model_exclude_none=False,
)
async def list_ref_fields(query: CodeRuleRefFieldsQuery):
    """按规则代码返回引用类型可选字段。F_MCTP 额外包含培养天数、细胞倍数、培养工单号、代次ID。"""
    try:
        result = crud.list_reference_fields(query.F_CODE)
        return ok(result, "查询成功")
    except Exception as e:
        return err(str(e))


@router.post(
    "/rule/create",
    response_model=ApiResponse[CodeRuleOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["dict_mgmt"]))],
)
async def create_rule(
    data: CodeRuleCreate,
    current_user: dict = Depends(get_current_user)
):
    """创建编码规则（需鉴权）"""
    try:
        rule_id = crud.create_code_rule(data.model_dump())
        rule = crud.get_code_rule_by_id(rule_id)
        return ok(rule, "创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {str(e)}")


@router.post(
    "/rule/update",
    response_model=ApiResponse[CodeRuleOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["dict_mgmt"]))],
)
async def update_rule(
    data: CodeRuleUpdate,
    current_user: dict = Depends(get_current_user)
):
    """更新编码规则（需鉴权）"""
    try:
        rule_id = data.F_ID
        # 过滤掉 None 值和 F_ID
        update_data = {k: v for k, v in data.model_dump().items() if v is not None and k != 'F_ID'}
        crud.update_code_rule(rule_id, update_data)
        rule = crud.get_code_rule_by_id(rule_id)
        return ok(rule, "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {str(e)}")


@router.post(
    "/rule/delete",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["dict_mgmt"]))],
)
async def delete_rule(
    request: CodeRuleDeleteRequest,
    current_user: dict = Depends(get_current_user)
):
    """删除编码规则（需鉴权，会删除相关序列号记录）"""
    try:
        crud.delete_code_rule(request.F_ID)
        return ok({"F_ID": request.F_ID}, "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"删除失败: {str(e)}")


# ============================================
# 编码生成相关接口
# ============================================

@router.post(
    "/generate",
    response_model=ApiResponse[CodeGenerateOut],
    response_model_exclude_none=True,
)
async def generate_code(request: CodeGenerateRequest):
    """
    生成编码（所有用户可访问）
    
    **功能说明：**
    - 根据指定的规则代码和上下文数据生成编码
    - 自动管理序列号
    
    **请求示例：**
    ```json
    {
      "rule_code": "ORGAN_CODE",
      "context": {
        "F_ZZID": "ZZ001",
        "F_DC": "P0"
      }
    }
    ```
    """
    try:
        code = crud.generate_code(request.rule_code, request.context)
        rule = crud.get_code_rule_by_code(request.rule_code)
        
        return ok({
            "code": code,
            "rule_code": request.rule_code,
            "rule_name": rule["F_NAME"] if rule else ""
        }, "编码生成成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"编码生成失败: {str(e)}")


@router.post(
    "/preview",
    response_model=ApiResponse[CodePreviewOut],
    response_model_exclude_none=True,
)
async def preview_code(request: CodePreviewRequest):
    """
    预览编码（所有用户可访问）
    
    **功能说明：**
    - 根据规则模板和上下文数据预览生成的编码
    - 不会保存序列号，仅用于测试规则
    
    **请求示例：**
    ```json
    {
      "rule_template": "ORG-{YYYYMMDD}-{SEQ:4}",
      "context": {}
    }
    ```
    """
    try:
        preview = crud.preview_code(request.rule_template, request.context)
        
        return ok({
            "preview_code": preview,
            "explanation": "这是预览编码，不会保存序列号"
        }, "预览成功")
    except Exception as e:
        return err(f"预览失败: {str(e)}")


# ============================================
# 序列号管理相关接口
# ============================================

@router.post(
    "/sequence/list",
    response_model=ApiResponse[CodeSequenceListOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["admin"]))],
)
async def list_sequences(
    query: CodeSequenceListQuery,
    current_user: dict = Depends(get_current_user)
):
    """查询编码序列列表（需鉴权）"""
    try:
        result = crud.list_code_sequences(query.model_dump())
        return ok(result)
    except Exception as e:
        return err(str(e))
