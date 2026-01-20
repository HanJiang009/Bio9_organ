from fastapi import APIRouter, Depends
from app.schemas.common import ApiResponse
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.crud import organoid_freeze as crud
from typing import Any, Dict, List


router = APIRouter(prefix="/organoid-freeze", tags=["organoid_freeze"])


@router.post("/save", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def create(payload: Dict[str, Any], current_user: dict = Depends(get_current_user_optional)):
    try:
        gdid = int(payload.get("F_GDID"))
    except Exception:
        return err("缺少或非法的 F_GDID")

    xhmsy_list = payload.get("XHMSYJL") or []
    if xhmsy_list and not isinstance(xhmsy_list, list):
        return err("XHMSYJL 应为数组")
    dcjl = payload.get("DCJL") or {}
    xh_del = payload.get("F_XHMSYJL_DEL") or []
    if xh_del and not isinstance(xh_del, list):
        return err("F_XHMSYJL_DEL 应为ID数组")
    try:
        operator = None
        if current_user:
            operator = current_user.get("F_Ktype") or current_user.get("F_YSM")
        res = crud.create_freeze(gdid, xhmsy_list, dcjl, operator=operator, delete_xh_ids=xh_del)
        return ok(res, "创建成功")
    except Exception as e:
        return err(f"创建失败: {e}")


@router.post("/detail", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def detail(payload: Dict[str, Any]):
    try:
        gdid = int(payload.get("F_GDID"))
    except Exception:
        return err("缺少或非法的 F_GDID")

    from app.db.factory import get_adapter
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 工单信息（T_PYGD）
        cur.execute(
            "SELECT F_ID, F_GDBH, F_ZZID, F_XM, F_BRBH, F_BBLX, F_DC, F_DCID, F_ZZBW, F_XYZL, F_QYFS, F_YBLY, F_YBGG, F_SQR, F_PYSJ, F_AZ FROM T_PYGD WHERE F_ID = ?",
            (gdid,),
        )
        row = cur.fetchone()
        if not row:
            return err("未找到工单")
        cols = [c[0] for c in cur.description]
        gdxx = dict(zip(cols, row))

        # 冻存信息（一对一，按 GDID 取最新）
        paginated_sql, extra = adapter.paginate("SELECT * FROM T_DCJL WHERE F_GDID = ? ORDER BY F_ID DESC", 0, 1)
        cur.execute(paginated_sql, (gdid,) + tuple(extra))
        dc_row = cur.fetchone()
        dc_cols = [c[0] for c in cur.description]
        dc_detail = dict(zip(dc_cols, dc_row)) if dc_row else None

        # 关联消化酶记录：通过 T_XHMSY.F_DCJL = T_DCJL.F_ID 限定到本冻存域
        xh_list: List[Dict[str, Any]] = []
        if dc_detail and dc_detail.get("F_ID") is not None:
            dc_id = int(dc_detail["F_ID"])
            cur.execute(
                "SELECT * FROM T_XHMSY WHERE F_GDID = ? AND F_DCJL = ? ORDER BY F_ID ASC",
                (gdid, dc_id),
            )
            rows = cur.fetchall()
            xh_cols = [c[0] for c in cur.description]
            xh_list = [dict(zip(xh_cols, r)) for r in rows]

    return ok({
        "GDXX": gdxx,
        "DCJL": dc_detail,
        "XHMSYJL": xh_list,
    }, "查询成功")

