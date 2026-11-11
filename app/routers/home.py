from fastapi import APIRouter, Depends

from app.core.auth import require_roles, get_current_user_optional
from app.core.data_scope import resolve_data_scope
from app.core.response import ok
from app.crud import home as crud
from app.schemas.common import ApiResponse
from app.schemas.home import HomeCardQuery, HomeListQuery

router = APIRouter(prefix="/home", tags=["home"])


@router.post(
    "/cards",
    response_model=ApiResponse[dict],
    response_model_exclude_none=False,
    dependencies=[Depends(require_roles(["home"]))],
)
def home_cards(
    payload: HomeCardQuery,
    current_user: dict = Depends(get_current_user_optional),
):
    data = crud.list_home_cards(
        payload.model_dump(),
        user=current_user,
        scope=resolve_data_scope(current_user, "home"),
    )
    return ok(data, f"查询成功，共 {data.get('total', 0)} 条")


@router.post(
    "/list",
    response_model=ApiResponse[dict],
    response_model_exclude_none=False,
    dependencies=[Depends(require_roles(["home"]))],
)
def home_list(
    payload: HomeListQuery,
    current_user: dict = Depends(get_current_user_optional),
):
    data = crud.list_home_orders(
        payload.model_dump(),
        user=current_user,
        scope=resolve_data_scope(current_user, "home"),
    )
    return ok(data, f"查询成功，共 {data.get('total', 0)} 条")
