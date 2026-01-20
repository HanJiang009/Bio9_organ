from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.schemas.common import ApiResponse
from app.schemas.op_log import OpLog, OpLogQuery, OpLogListOut
from app.crud.op_log import list_op_logs
from app.core.auth import require_roles
from app.core.response import ok, err

router = APIRouter(
    prefix="/op_log",
    tags=["日志管理"]
)

@router.post("/list", response_model=ApiResponse[OpLogListOut], response_model_exclude_none=False,
            dependencies=[Depends(require_roles(["op_log"]))])
async def get_op_log_list(payload: OpLogQuery):
    data = list_op_logs(payload)
    return ok(data, f"查询成功，共 {data.get('total', 0)} 条")