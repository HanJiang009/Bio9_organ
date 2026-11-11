"""
快捷工位模块 Schemas
"""
from typing import Optional, Any, Dict
from pydantic import BaseModel, Field


class WorkstationDetailIn(BaseModel):
    """快捷工位详情查询入参"""
    F_VALUE: str = Field(
        description=(
            "统一查询字段，支持以下四种输入：\n"
            "1. 送检条码号（T_YBZJ.F_SJTMH）\n"
            "2. 培养工单号（T_PYGD.F_GDBH）\n"
            "3. 自定义编号（T_PYGD.F_ZDYXH）\n"
            "4. 样本编号（T_YBFZ.F_SGBH）"
        )
    )


class WorkstationDetailOut(BaseModel):
    """快捷工位详情出参（data 为自由结构，通过 ApiResponse[dict] 返回）"""
    pass
