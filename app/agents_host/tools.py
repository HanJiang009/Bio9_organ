from __future__ import annotations

import logging
from typing import Any, Optional

from app.crud import sample_receive as crud
from app.crud.code_rule import generate_code, peek_code
from app.schemas.sample_receive import PatientInfo, QualityInfo, SpecimenInfo

logger = logging.getLogger(__name__)


def _build_zzid_context(jz: dict, bb: dict, yb: dict) -> dict:
    ctx: dict = {}
    for src in (jz, bb, yb):
        if src:
            for key, value in src.items():
                if value is not None and key not in ("F_ZZID", "F_ID", "__null_fields__", "F_ZZID_CUSTOM"):
                    ctx.setdefault(key, value)
    return ctx


class OrganHostTools:
    """包装现有 CRUD；organ_agent 只通过此端口写库。"""

    def check_zzid(self, zzid: str) -> Optional[dict]:
        if not zzid or not str(zzid).strip():
            return None
        return crud.check_zzid_exists(str(zzid).strip())

    def peek_zzid(self, jz: dict, bb: dict, yb: dict) -> tuple[Optional[str], str]:
        ctx = _build_zzid_context(jz or {}, bb or {}, yb or {})
        try:
            fzzid = peek_code("F_ZZID", ctx)
            if fzzid:
                return str(fzzid), "code_rule"
        except Exception as exc:
            logger.warning("agent peek_code 失败(%s)，尝试兜底", exc)
        try:
            from app.crud.code_rule import (
                _peek_sequence_placeholders as _psp,
                _replace_datetime_placeholders as _rdt,
            )

            tmpl = _rdt("ZZ-{YYYYMMDD}-{SEQ:4}")
            fzzid = _psp(tmpl, "__FALLBACK_ZZID__", {})
            return (str(fzzid), "fallback") if fzzid else (None, "fallback")
        except Exception as exc:
            logger.error("agent peek 兜底失败: %s", exc)
            return None, "fallback"

    def generate_zzid(self, jz: dict, bb: dict, yb: dict) -> Optional[str]:
        ctx = _build_zzid_context(jz or {}, bb or {}, yb or {})
        try:
            fzzid = generate_code("F_ZZID", ctx)
            if fzzid:
                return str(fzzid)
            raise ValueError("字典编码规则生成的标本号为空")
        except Exception as exc:
            logger.warning("agent generate_code 失败(%s)，尝试兜底", exc)
        try:
            from app.crud.code_rule import (
                _replace_datetime_placeholders as _rdt,
                _replace_sequence_placeholders as _rsp,
            )

            tmpl = _rdt("ZZ-{YYYYMMDD}-{SEQ:4}")
            fzzid = _rsp(tmpl, "__FALLBACK_ZZID__", {})
            return str(fzzid) if fzzid else None
        except Exception as exc:
            logger.error("agent generate 兜底失败: %s", exc)
            return None

    def save_all(self, jz: dict, bb: dict, yb: dict) -> None:
        crud.save_all(jz, bb, yb)

    def validate_payload(self, payload: dict) -> tuple[bool, str]:
        try:
            PatientInfo.model_validate((payload or {}).get("JZXX") or {})
            SpecimenInfo.model_validate((payload or {}).get("BBXX") or {})
            QualityInfo.model_validate((payload or {}).get("YBZJ") or {})
            return True, ""
        except Exception as exc:
            return False, f"草稿未通过 schema 校验：{exc}"

    def resolve_operator(self, user: dict) -> str:
        return str((user or {}).get("F_Ktype") or (user or {}).get("F_YSM") or "")

    def register_file(self, stored_path: str, original_name: str) -> int:
        import shutil
        from pathlib import Path

        from app.core.storage import build_upload_path
        from app.crud.file_center import insert_file_record

        src = Path(stored_path)
        if not src.is_file():
            raise RuntimeError(f"附件不存在：{original_name}")
        dst, rel = build_upload_path(original_name)
        shutil.copy2(src, dst)
        file_id = insert_file_record(rel, original_name)
        if file_id is None:
            raise RuntimeError(f"写入文件中心失败：{original_name}")
        return int(file_id)

    def list_culture_staff(self) -> list[dict]:
        from app.crud import user as user_crud

        items = user_crud.list_users(skip=0, limit=500, status="是", role="organoid_culture_tissue")
        if not items:
            items = user_crud.list_users(skip=0, limit=500, status="是", role=None)
        out: list[dict] = []
        for user in items or []:
            uid = user.get("F_ID")
            if uid is None:
                continue
            name = str(user.get("F_Ktype") or user.get("F_YSM") or "").strip()
            out.append({"id": int(uid), "name": name, "code": str(user.get("F_YSM") or "")})
        return out

    def list_model_types(self) -> list[dict]:
        # 与打包前端 getModelList 一致，不走字典。
        return [
            {"value": "PDO", "label": "PDO"},
            {"value": "PDC", "label": "PDC"},
            {"value": "PDX", "label": "PDX"},
        ]

    def list_work_order_types(self) -> list[dict]:
        # 与打包前端 getWorkOrderList 一致，不走字典。
        return [
            {"value": "组织", "label": "组织"},
            {"value": "血液", "label": "血液"},
        ]

    def create_distribute(
        self,
        zzid: str,
        pyry: int,
        mxlx: str,
        creator: str,
        gdlx: str | None = None,
        qywc: int = 0,
    ) -> dict:
        return crud.create_distribute(
            zzid, int(pyry), str(mxlx), creator, gdlx, qywc=qywc or 0
        )

    def list_distributes(self, zzid: str) -> dict:
        return crud.list_distributes(zzid, None, None, skip=0, limit=200)

    def get_unqualified_status(self, zzid: str) -> dict:
        if not zzid or not str(zzid).strip():
            return {"F_BHG": None, "F_BHGYY": ""}
        from app.db.factory import get_adapter

        adapter = get_adapter()
        try:
            rows, cols = adapter.fetch_all(
                "SELECT F_BHG, F_BHGYY FROM T_YBZJ WHERE F_ZZID = ?",
                [str(zzid).strip()],
            )
        except Exception:
            return {"F_BHG": None, "F_BHGYY": ""}
        if not rows:
            return {"F_BHG": None, "F_BHGYY": ""}
        rec = dict(zip(cols, rows[0]))
        return {"F_BHG": rec.get("F_BHG"), "F_BHGYY": rec.get("F_BHGYY") or ""}

    def mark_unqualified(self, zzid: str, reason: str, creator: str) -> dict:
        from organ_agent.models import AgentError

        reason = str(reason or "").strip()
        if not reason:
            raise AgentError("请填写不合格原因", code="invalid_draft")
        status = self.get_unqualified_status(zzid)
        val = str(status.get("F_BHG") or "").strip().lower()
        if val == "pending":
            raise AgentError("标本已在不合格审批中，不能再提交", code="invalid_draft")
        if val in {"true", "1", "是", "不合格"}:
            raise AgentError("标本已不合格，不能再提交", code="invalid_draft")
        qc_id = crud.mark_unqualified(zzid, reason, creator)
        status = self.get_unqualified_status(zzid)
        if qc_id:
            status["qc_audit_id"] = qc_id
        return status

    def _ybzj_id(self, zzid: str) -> Optional[int]:
        if not zzid or not str(zzid).strip():
            return None
        from app.db.factory import get_adapter

        adapter = get_adapter()
        try:
            rows, cols = adapter.fetch_all(
                "SELECT F_ID FROM T_YBZJ WHERE F_ZZID = ? ORDER BY F_ID DESC",
                [str(zzid).strip()],
            )
        except Exception:
            return None
        if not rows:
            return None
        rec = dict(zip(cols, rows[0]))
        try:
            return int(rec.get("F_ID"))
        except (TypeError, ValueError):
            return None

    def list_direct_freezes(self, zzid: str) -> dict:
        if not zzid or not str(zzid).strip():
            return {"items": []}
        from app.db.factory import get_adapter

        adapter = get_adapter()
        try:
            rows, cols = adapter.fetch_all(
                """
                SELECT F_ID, F_SGBH, F_ZZID, F_YBZJ_ID, F_PYGD_ID, F_YBLX, F_YBGLX,
                       F_CCXS, F_FGS, F_TJ, F_HZH, F_WZ, F_YSZT, F_CJSJ
                FROM T_YBFZ WHERE F_ZZID = ? ORDER BY F_ID DESC
                """,
                [str(zzid).strip()],
            )
        except Exception:
            return {"items": []}
        items = []
        for row in rows or []:
            rec = dict(zip(cols, row))
            pygd = rec.get("F_PYGD_ID")
            if pygd not in (None, "", 0, "0"):
                continue
            rec["F_PYGD_ID"] = None
            items.append(rec)
        return {"items": items}

    def freeze_direct(
        self,
        zzid: str,
        fgs: int,
        user: dict,
        *,
        yblx: str | None = None,
        ybglx: str | None = None,
        ccxs: str | None = None,
        tj: str | None = None,
    ) -> dict:
        from organ_agent.models import AgentError
        from app.crud import sample_linkage as linkage

        try:
            count = int(fgs)
        except (TypeError, ValueError):
            raise AgentError("请填写分管数", code="invalid_draft") from None
        if count < 1:
            raise AgentError("分管数至少为 1", code="invalid_draft")
        ybzj_id = self._ybzj_id(zzid)
        if not ybzj_id:
            raise AgentError("没有标本登记记录，不能冻存", code="invalid_draft")
        fzxx: dict[str, Any] = {"F_FGS": count, "F_SFYS": False}
        if yblx and str(yblx).strip():
            fzxx["F_YBLX"] = str(yblx).strip()
        if ybglx and str(ybglx).strip():
            fzxx["F_YBGLX"] = str(ybglx).strip()
        if ccxs and str(ccxs).strip():
            fzxx["F_CCXS"] = str(ccxs).strip()
        if tj and str(tj).strip():
            fzxx["F_TJ"] = str(tj).strip()
        payload = {
            "YBZJ_IDS": [ybzj_id],
            "PYGD_IDS": [],
            "FZXX": [fzxx],
            "BOXES": [],
        }
        result = linkage.draft_sample_allocation(payload, user or {})
        if result.get("transport"):
            raise AgentError("直接冻存不应创建运输单", code="tool_failed")
        listed = self.list_direct_freezes(zzid)
        for item in listed.get("items") or []:
            if item.get("F_PYGD_ID") not in (None, "", 0, "0"):
                raise AgentError("冻存结果带有培养工单，已拒绝这条路径", code="tool_failed")
        return {
            "inserted": result.get("inserted") or [],
            "count": result.get("count") or 0,
            "items": listed.get("items") or [],
        }
