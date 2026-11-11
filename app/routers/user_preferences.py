from fastapi import APIRouter, Depends
from app.schemas.common import ApiResponse
from app.schemas.user_preferences import PreferenceGetIn, PreferenceSaveIn, PreferenceOut
from app.core.response import ok, err
from app.core.auth import get_current_user
from app.crud import user_preferences as crud

router = APIRouter(prefix="/user/preferences", tags=["user_preferences"])


@router.post("/get", response_model=ApiResponse[PreferenceOut], response_model_exclude_none=False)
def get_preference(body: PreferenceGetIn, current_user: dict = Depends(get_current_user)):
    uid = int(current_user["F_ID"])
    config = crud.get_preference(uid, body.page_key)
    return ok(PreferenceOut(page_key=body.page_key, config=config))


@router.post("", response_model=ApiResponse[None])
def save_preference(body: PreferenceSaveIn, current_user: dict = Depends(get_current_user)):
    uid = int(current_user["F_ID"])
    try:
        crud.save_preference(uid, body.page_key, body.config)
    except Exception as e:
        return err(f"保存失败: {e}")
    return ok(None, "保存成功")
