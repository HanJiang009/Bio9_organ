"""
质控记录（T_ZKJL）CRUD模块
负责管理类器官质控记录的创建、更新和查询
"""
import json
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from app.db.factory import get_adapter
from app.crud.file_center import insert_file_record

# T_ZKJL 中存储多附件（JSON数组字符串）的图片字段
_IMG_FIELDS = [
    "F_IMG_D1M4", "F_IMG_D1M20",
    "F_IMG_D3M4", "F_IMG_D3M20",
    "F_IMG_D7M4", "F_IMG_D7M20",
]


def _serialize_img_ids(val) -> Optional[str]:
    """将文件ID列表序列化为JSON字符串存入数据库；None或空列表返回None。"""
    if val is None:
        return None
    if isinstance(val, list):
        ids = [int(x) for x in val if x is not None]
        return json.dumps(ids, ensure_ascii=False) if ids else None
    if isinstance(val, int):
        return json.dumps([val])
    if isinstance(val, str):
        try:
            json.loads(val)  # 已是合法 JSON，直接透传
            return val
        except Exception:
            return None
    return None


def _parse_img_ids(val) -> List[int]:
    """将数据库值（JSON字符串 / 旧版单整数）解析为文件ID列表。"""
    if val is None:
        return []
    if isinstance(val, int):
        return [val]
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return [int(x) for x in parsed if x is not None]
            if isinstance(parsed, int):
                return [parsed]
        except Exception:
            pass
    return []


def create_or_update_qc_record(
    gdid: int,
    qc_data: Dict[str, Any],
    operator: str = None,
    from_pipeline_template: bool = False,
) -> int:
    """
    创建或更新质控记录
    
    Args:
        gdid: 工单ID
        qc_data: 质控数据字典，包含以下字段：
            - F_QKMS: 类器官生长情况描述
            - F_IMG_D1M4: 接种第一天明场4倍图（文件ID）
            - F_IMG_D1M20: 接种第一天明场20倍图（文件ID）
            - F_IMG_D3M4: 接种第三天明场4倍图（文件ID）
            - F_IMG_D3M20: 接种第三天明场20倍图（文件ID）
            - F_IMG_D7M4: 最终明场4倍图（文件ID，预填最后一次观察）
            - F_IMG_D7M20: 最终明场20倍图（文件ID，预填最后一次观察）
            - F_PYTS: 培养天数
            - F_XT: 形态
            - F_SFWR: 是否污染
            - F_XBSL: 细胞数量
            - F_XBHL: 细胞活率
            - F_ZYT: 支原体检测结果
            - F_PDF: PDF文件ID（可选）
        operator: 操作人
        
    Returns:
        质控记录ID
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 检查是否已存在记录
        cur.execute(
            "SELECT F_ID, F_PDF FROM T_ZKJL WHERE F_GDID = ? ORDER BY F_ID DESC",
            (gdid,)
        )
        cur_paginated, cur_extra = adapter.paginate(
            "SELECT F_ID, F_PDF FROM T_ZKJL WHERE F_GDID = ? ORDER BY F_ID DESC",
            0, 1
        )
        rows, cols = adapter.fetch_all(cur_paginated, [gdid] + cur_extra)
        existing = dict(zip(cols, rows[0])) if rows else None
        
        # 准备数据（图片字段统一序列化为JSON数组字符串）
        f_qkms = qc_data.get("F_QKMS")
        f_img_d1m4  = _serialize_img_ids(qc_data.get("F_IMG_D1M4"))
        f_img_d1m20 = _serialize_img_ids(qc_data.get("F_IMG_D1M20"))
        f_img_d3m4  = _serialize_img_ids(qc_data.get("F_IMG_D3M4"))
        f_img_d3m20 = _serialize_img_ids(qc_data.get("F_IMG_D3M20"))
        f_img_d7m4  = _serialize_img_ids(qc_data.get("F_IMG_D7M4"))
        f_img_d7m20 = _serialize_img_ids(qc_data.get("F_IMG_D7M20"))
        f_pyts = qc_data.get("F_PYTS")
        f_xt = qc_data.get("F_XT")
        f_sfwr = qc_data.get("F_SFWR")
        f_xbsl = qc_data.get("F_XBSL")
        f_xbhl = qc_data.get("F_XBHL")
        f_zyt = qc_data.get("F_ZYT")
        
        # 处理F_PDF字段
        f_pdf = None
        if qc_data.get("F_PDF"):
            # 如果传入了PDF文件ID，需要更新或初始化F_PDF JSON
            pdf_data = {"generated": None, "uploaded": []}
            if existing and existing.get("F_PDF"):
                try:
                    pdf_data = json.loads(existing["F_PDF"])
                except Exception:
                    pass
            pdf_data["generated"] = qc_data["F_PDF"]
            f_pdf = json.dumps(pdf_data, ensure_ascii=False)
        elif existing and existing.get("F_PDF"):
            # 保留原有的F_PDF值
            f_pdf = existing["F_PDF"]
        
        if existing:
            # 更新现有记录（用户保存 → F_YHBC=1）
            zkjl_id = existing["F_ID"]
            try:
                cur.execute(
                    f"""
                    UPDATE T_ZKJL SET
                        F_QKMS = ?,
                        F_IMG_D1M4 = ?,
                        F_IMG_D1M20 = ?,
                        F_IMG_D3M4 = ?,
                        F_IMG_D3M20 = ?,
                        F_IMG_D7M4 = ?,
                        F_IMG_D7M20 = ?,
                        F_PYTS = ?,
                        F_XT = ?,
                        F_SFWR = ?,
                        F_XBSL = ?,
                        F_XBHL = ?,
                        F_ZYT = ?,
                        F_PDF = ?,
                        F_GXSJ = {adapter.now()},
                        F_YHBC = 1
                    WHERE F_ID = ?
                    """,
                    (
                        f_qkms, f_img_d1m4, f_img_d1m20, f_img_d3m4, f_img_d3m20,
                        f_img_d7m4, f_img_d7m20, f_pyts, f_xt, f_sfwr, f_xbsl,
                        f_xbhl, f_zyt, f_pdf, zkjl_id
                    )
                )
            except Exception:
                cur.execute(
                    f"""
                    UPDATE T_ZKJL SET
                        F_QKMS = ?,
                        F_IMG_D1M4 = ?,
                        F_IMG_D1M20 = ?,
                        F_IMG_D3M4 = ?,
                        F_IMG_D3M20 = ?,
                        F_IMG_D7M4 = ?,
                        F_IMG_D7M20 = ?,
                        F_PYTS = ?,
                        F_XT = ?,
                        F_SFWR = ?,
                        F_XBSL = ?,
                        F_XBHL = ?,
                        F_ZYT = ?,
                        F_PDF = ?,
                        F_GXSJ = {adapter.now()}
                    WHERE F_ID = ?
                    """,
                    (
                        f_qkms, f_img_d1m4, f_img_d1m20, f_img_d3m4, f_img_d3m20,
                        f_img_d7m4, f_img_d7m20, f_pyts, f_xt, f_sfwr, f_xbsl,
                        f_xbhl, f_zyt, f_pdf, zkjl_id
                    )
                )
            print(f"更新质控记录: ZKJL_ID={zkjl_id}, GDID={gdid}")
        else:
            # 插入新记录；流水线模板占位不写 F_GXSJ，便于进度展示区分「未开始」与「用户暂存」
            # F_YHBC：模板=0，用户=1（列不存在时降级）
            from app.utils.yhbc import yhbc_flag
            yhbc = yhbc_flag(from_pipeline_template)
            if from_pipeline_template:
                try:
                    cur.execute(
                        """
                        INSERT INTO T_ZKJL (
                            F_GDID, F_QKMS, F_IMG_D1M4, F_IMG_D1M20, F_IMG_D3M4, F_IMG_D3M20,
                            F_IMG_D7M4, F_IMG_D7M20, F_PYTS, F_XT, F_SFWR, F_XBSL, F_XBHL,
                            F_ZYT, F_PDF, F_JLR, F_YHBC
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            gdid, f_qkms, f_img_d1m4, f_img_d1m20, f_img_d3m4, f_img_d3m20,
                            f_img_d7m4, f_img_d7m20, f_pyts, f_xt, f_sfwr, f_xbsl, f_xbhl,
                            f_zyt, f_pdf, operator, yhbc
                        )
                    )
                except Exception:
                    cur.execute(
                        """
                        INSERT INTO T_ZKJL (
                            F_GDID, F_QKMS, F_IMG_D1M4, F_IMG_D1M20, F_IMG_D3M4, F_IMG_D3M20,
                            F_IMG_D7M4, F_IMG_D7M20, F_PYTS, F_XT, F_SFWR, F_XBSL, F_XBHL,
                            F_ZYT, F_PDF, F_JLR
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            gdid, f_qkms, f_img_d1m4, f_img_d1m20, f_img_d3m4, f_img_d3m20,
                            f_img_d7m4, f_img_d7m20, f_pyts, f_xt, f_sfwr, f_xbsl, f_xbhl,
                            f_zyt, f_pdf, operator
                        )
                    )
            else:
                try:
                    cur.execute(
                        f"""
                        INSERT INTO T_ZKJL (
                            F_GDID, F_QKMS, F_IMG_D1M4, F_IMG_D1M20, F_IMG_D3M4, F_IMG_D3M20,
                            F_IMG_D7M4, F_IMG_D7M20, F_PYTS, F_XT, F_SFWR, F_XBSL, F_XBHL,
                            F_ZYT, F_PDF, F_JLR, F_GXSJ, F_YHBC
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {adapter.now()}, ?)
                        """,
                        (
                            gdid, f_qkms, f_img_d1m4, f_img_d1m20, f_img_d3m4, f_img_d3m20,
                            f_img_d7m4, f_img_d7m20, f_pyts, f_xt, f_sfwr, f_xbsl, f_xbhl,
                            f_zyt, f_pdf, operator, yhbc
                        )
                    )
                except Exception:
                    cur.execute(
                        f"""
                        INSERT INTO T_ZKJL (
                            F_GDID, F_QKMS, F_IMG_D1M4, F_IMG_D1M20, F_IMG_D3M4, F_IMG_D3M20,
                            F_IMG_D7M4, F_IMG_D7M20, F_PYTS, F_XT, F_SFWR, F_XBSL, F_XBHL,
                            F_ZYT, F_PDF, F_JLR, F_GXSJ
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {adapter.now()})
                        """,
                        (
                            gdid, f_qkms, f_img_d1m4, f_img_d1m20, f_img_d3m4, f_img_d3m20,
                            f_img_d7m4, f_img_d7m20, f_pyts, f_xt, f_sfwr, f_xbsl, f_xbhl,
                            f_zyt, f_pdf, operator
                        )
                    )
            cur.execute("SELECT MAX(F_ID) FROM T_ZKJL WHERE F_GDID = ?", (gdid,))
            zkjl_id = cur.fetchone()[0]
            print(f"创建质控记录: ZKJL_ID={zkjl_id}, GDID={gdid}")
        
        conn.commit()
        return int(zkjl_id)


def get_qc_record_by_gdid(gdid: int) -> Optional[Dict[str, Any]]:
    """
    根据工单ID查询质控记录
    
    Args:
        gdid: 工单ID
        
    Returns:
        质控记录字典，如果不存在则返回None
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        sql = "SELECT * FROM T_ZKJL WHERE F_GDID = ? ORDER BY F_ID DESC"
        paginated, extra = adapter.paginate(sql, 0, 1)
        rows, cols = adapter.fetch_all(paginated, [gdid] + extra)
        
        if not rows:
            return None
        
        record = dict(zip(cols, rows[0]))

        # 解析图片字段为文件ID列表（兼容旧版单整数和新版JSON数组字符串）
        for field in _IMG_FIELDS:
            record[field] = _parse_img_ids(record.get(field))

        # 解析F_PDF字段（质控单PDF）
        if record.get("F_PDF"):
            try:
                record["F_PDF_PARSED"] = json.loads(record["F_PDF"])
            except Exception:
                record["F_PDF_PARSED"] = {"generated": None, "uploaded": []}
        else:
            record["F_PDF_PARSED"] = {"generated": None, "uploaded": []}
        
        # 解析F_PDF_FXJC字段（放行检测PDF）
        if record.get("F_PDF_FXJC"):
            try:
                record["F_PDF_FXJC_PARSED"] = json.loads(record["F_PDF_FXJC"])
            except Exception:
                record["F_PDF_FXJC_PARSED"] = {"generated": None, "uploaded": []}
        else:
            record["F_PDF_FXJC_PARSED"] = {"generated": None, "uploaded": []}
        
        return record


def update_qc_record_pdf(gdid: int, pdf_file_id: int):
    """
    更新质控记录的PDF文件ID
    
    Args:
        gdid: 工单ID
        pdf_file_id: PDF文件ID
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 获取现有记录
        record = get_qc_record_by_gdid(gdid)
        if not record:
            # 如果没有质控记录，创建一个空记录
            create_or_update_qc_record(gdid, {"F_PDF": pdf_file_id})
            return
        
        # 更新PDF字段
        pdf_data = record.get("F_PDF_PARSED", {"generated": None, "uploaded": []})
        pdf_data["generated"] = pdf_file_id
        
        cur.execute(
            f"UPDATE T_ZKJL SET F_PDF = ?, F_GXSJ = {adapter.now()} WHERE F_GDID = ?",
            (json.dumps(pdf_data, ensure_ascii=False), gdid)
        )
        conn.commit()
        print(f"更新质控记录PDF: GDID={gdid}, PDF_ID={pdf_file_id}")


def update_release_inspection_pdf(gdid: int, pdf_file_id: int):
    """
    更新放行检测PDF文件ID（JSON格式，支持后续人为上传）
    
    Args:
        gdid: 工单ID
        pdf_file_id: 放行检测PDF文件ID
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 检查质控记录是否存在
        paginated, extra = adapter.paginate(
            "SELECT F_PDF_FXJC FROM T_ZKJL WHERE F_GDID = ? ORDER BY F_ID DESC",
            0, 1
        )
        rows, cols = adapter.fetch_all(paginated, [gdid] + extra)
        
        # 构建JSON格式的PDF数据
        if rows and rows[0][0]:
            # 已有数据，更新generated字段
            try:
                pdf_data = json.loads(rows[0][0])
                if not isinstance(pdf_data, dict):
                    pdf_data = {"generated": None, "uploaded": []}
            except Exception:
                pdf_data = {"generated": None, "uploaded": []}
        else:
            # 初始化
            pdf_data = {"generated": None, "uploaded": []}
        
        pdf_data["generated"] = pdf_file_id
        pdf_json = json.dumps(pdf_data, ensure_ascii=False)
        
        if not rows:
            # 如果没有质控记录，创建一个基本记录
            cur.execute(
                "INSERT INTO T_ZKJL (F_GDID, F_PDF_FXJC) VALUES (?, ?)",
                (gdid, pdf_json)
            )
            print(f"创建质控记录并设置放行检测PDF（JSON格式）: GDID={gdid}, PDF_ID={pdf_file_id}")
        else:
            # 更新放行检测PDF字段
            cur.execute(
                f"UPDATE T_ZKJL SET F_PDF_FXJC = ?, F_GXSJ = {adapter.now()} WHERE F_GDID = ?",
                (pdf_json, gdid)
            )
            print(f"更新放行检测PDF（JSON格式）: GDID={gdid}, PDF_ID={pdf_file_id}")
        
        conn.commit()


# ---------------------------------------------------------------------------
# QC 概览数据（与 /qc/overview-by-gdid 路由共用）
# ---------------------------------------------------------------------------

def get_qc_overview(gdid: int) -> Optional[Dict[str, Any]]:
    """
    获取工单的质控概览数据，供 /qc/overview-by-gdid 路由和快捷工位"验证"节点复用。

    返回结构：
      {
        "GDXX":     工单基础信息,
        "YBZKJL":   样本质控信息（接收时间 / 预处理时间 / 样本附件）,
        "LQGZKJL":  类器官质控表单（已提交则用 T_ZKJL 数据，未提交则从观察记录预填图片）,
        "ZKJL":     T_ZKJL 原始记录（未提交时为 {}）,
      }
    工单不存在时返回 None。
    """
    from app.crud import organoid_culture_tissue as _crud_order
    from app.crud import preprocess as _crud_pre
    from app.crud import organoid_observation as _crud_obs
    from app.crud.sample_receive import query_by_ids
    from app.crud.file_center import get_files_by_ids

    # ── 1. 工单信息 ────────────────────────────────────────────────────────
    order = _crud_order.get_culture_order_detail(gdid)
    if not order:
        return None

    # ── 2. 当前培养天数 ────────────────────────────────────────────────────
    current_pyts: Optional[int] = None
    pysj = order.get("F_PYSJ")
    if pysj:
        dt = None
        if isinstance(pysj, str):
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(pysj[:len(fmt)], fmt)
                    break
                except Exception:
                    continue
            if dt is None:
                try:
                    dt = datetime.fromisoformat(pysj)
                except Exception:
                    pass
        elif hasattr(pysj, "date"):
            dt = pysj
        if dt is not None:
            current_pyts = (datetime.now().date() - dt.date()).days

    # ── 3. 样本接收时间 ────────────────────────────────────────────────────
    sample_qc: Dict[str, Any] = {}
    zzid = order.get("F_ZZID")
    if zzid:
        try:
            base = query_by_ids(zzid, None) or {}
            bbxx = base.get("BBXX") or {}
            if bbxx.get("F_JSSJ") is not None:
                sample_qc["F_JSSJ"] = bbxx["F_JSSJ"]
        except Exception:
            pass

    # ── 4. 预处理信息 ──────────────────────────────────────────────────────
    try:
        preprocess: Dict[str, Any] = _crud_pre.fetch_preprocess_by_gdid(gdid) or {}
    except Exception:
        preprocess = {}

    # ── 5. 观察记录 → 第1/3天槽位 + 最后一次观察作为「最终」图 ──────────
    # 最终槽位字段名仍是 F_IMG_D7*（历史列名），语义是最后一天，不是必须第7天。
    wanted_mags = {4, 20}
    try:
        obs = _crud_obs.list_observations_by_gdid(gdid)
    except Exception:
        obs = []

    def _parse_dt(s: Any) -> Optional[datetime]:
        if s is None:
            return None
        if isinstance(s, datetime):
            return s
        try:
            return datetime.fromisoformat(str(s))
        except Exception:
            try:
                return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None

    def _obs_day(it: Dict[str, Any]) -> Optional[int]:
        day = it.get("F_PYTS")
        if day is None:
            return None
        try:
            return int(day)
        except Exception:
            return None

    def _obs_is_newer(new_it: Dict[str, Any], old_it: Dict[str, Any]) -> bool:
        ts_new = _parse_dt(new_it.get("F_GCSJ"))
        ts_old = _parse_dt(old_it.get("F_GCSJ"))
        if ts_new and ts_old and ts_new > ts_old:
            return True
        if ts_new and not ts_old:
            return True
        if not ts_new and not ts_old and (new_it.get("F_ID") or 0) > (old_it.get("F_ID") or 0):
            return True
        return False

    def _picks_by_mag(record: Dict[str, Any], mag: int) -> List[int]:
        picks: List[int] = []
        for e in (record.get("F_FJ") or []):
            try:
                bs_str = str(e.get("F_BS") or "")
                m = re.match(r"^(\d+)", bs_str)
                if m and int(m.group(1)) == mag and e.get("F_WJ_ID") is not None:
                    picks.append(int(e["F_WJ_ID"]))
            except Exception:
                continue
        return picks

    latest_by_day: Dict[int, Dict[str, Any]] = {}
    last_obs: Optional[Dict[str, Any]] = None
    last_obs_pyts: Optional[int] = None
    for it in obs:
        day_int = _obs_day(it)
        if day_int is None:
            continue
        if last_obs is None:
            last_obs = it
            last_obs_pyts = day_int
        else:
            last_day = _obs_day(last_obs) or 0
            if day_int > last_day or (day_int == last_day and _obs_is_newer(it, last_obs)):
                last_obs = it
                last_obs_pyts = day_int
        if day_int not in (1, 3):
            continue
        cur_rec = latest_by_day.get(day_int)
        if not cur_rec or _obs_is_newer(it, cur_rec):
            latest_by_day[day_int] = it

    need_ids: List[int] = []
    obs_slot_ids: Dict[Tuple[int, int], List[int]] = {}
    for day in (1, 3):
        record = latest_by_day.get(day)
        if not record:
            continue
        for mag in sorted(wanted_mags):
            picks = _picks_by_mag(record, mag)
            if picks:
                need_ids.extend(picks)
                obs_slot_ids[(day, mag)] = picks
    if last_obs:
        for mag in sorted(wanted_mags):
            picks = _picks_by_mag(last_obs, mag)
            if picks:
                need_ids.extend(picks)
                obs_slot_ids[(7, mag)] = picks

    id_to_path: Dict[int, str] = {}
    if need_ids:
        files = get_files_by_ids(sorted({i for i in need_ids if i is not None}))
        for f in files:
            try:
                id_to_path[int(f.get("F_ID"))] = f.get("F_PATH", "")
            except Exception:
                pass

    file_ids_by_day_mag: Dict[Tuple[int, int], List[int]] = obs_slot_ids
    urls_by_day_mag: Dict[Tuple[int, int], List[str]] = {}
    for key, ids in obs_slot_ids.items():
        slot_urls = [
            str(id_to_path[fid]).replace("\\", "/").lstrip("/")
            for fid in ids if fid in id_to_path
        ]
        if slot_urls:
            urls_by_day_mag[key] = slot_urls

    # ── 6. T_ZKJL 质控记录 ────────────────────────────────────────────────
    zkjl: Dict[str, Any] = {}
    try:
        zkjl_record = get_qc_record_by_gdid(gdid)
        if zkjl_record:
            zkjl = {
                "F_ID":      zkjl_record.get("F_ID"),
                "F_GDID":    zkjl_record.get("F_GDID"),
                "F_QKMS":    zkjl_record.get("F_QKMS"),
                "F_IMG_D1M4":  zkjl_record.get("F_IMG_D1M4"),
                "F_IMG_D1M20": zkjl_record.get("F_IMG_D1M20"),
                "F_IMG_D3M4":  zkjl_record.get("F_IMG_D3M4"),
                "F_IMG_D3M20": zkjl_record.get("F_IMG_D3M20"),
                "F_IMG_D7M4":  zkjl_record.get("F_IMG_D7M4"),
                "F_IMG_D7M20": zkjl_record.get("F_IMG_D7M20"),
                "F_PYTS":    zkjl_record.get("F_PYTS"),
                "F_XT":      zkjl_record.get("F_XT"),
                "F_SFWR":    zkjl_record.get("F_SFWR"),
                "F_XBSL":    zkjl_record.get("F_XBSL"),
                "F_XBHL":    zkjl_record.get("F_XBHL"),
                "F_ZYT":     zkjl_record.get("F_ZYT"),
                "F_PDF":     zkjl_record.get("F_PDF_PARSED"),
                "F_PDF_FXJC": zkjl_record.get("F_PDF_FXJC_PARSED"),
                "F_JLR":     zkjl_record.get("F_JLR"),
                "F_JLSJ":    str(zkjl_record["F_JLSJ"]) if zkjl_record.get("F_JLSJ") else None,
                "F_GXSJ":    str(zkjl_record["F_GXSJ"]) if zkjl_record.get("F_GXSJ") else None,
            }
            # 图片字段 ID → URL
            _all_ids: List[int] = []
            for _f in _IMG_FIELDS:
                _all_ids.extend(zkjl.get(_f) or [])
            _id_to_url: Dict[int, str] = {}
            if _all_ids:
                for _ff in get_files_by_ids(sorted(set(_all_ids))):
                    try:
                        _id_to_url[int(_ff["F_ID"])] = str(_ff.get("F_PATH", "")).replace("\\", "/").lstrip("/")
                    except Exception:
                        pass
            for _img_field in _IMG_FIELDS:
                _ids = zkjl.get(_img_field) or []
                zkjl[f"{_img_field}_URLS"] = [_id_to_url[fid] for fid in _ids if fid in _id_to_url]
    except Exception as exc:
        import traceback
        print(f"[qc_overview] 查询质控记录失败: {exc}\n{traceback.format_exc()}")

    # ── 7. 组装返回结构 ────────────────────────────────────────────────────
    def _build_file_objects(ids: list, urls: list) -> list:
        return [{"id": fid, "url": urls[i] if i < len(urls) else None} for i, fid in enumerate(ids)]

    def _img_slot(zkjl_ids, zkjl_urls, obs_key: Tuple[int, int]) -> list:
        ids  = zkjl_ids  if isinstance(zkjl_ids, list)  and zkjl_ids  else []
        urls = zkjl_urls if isinstance(zkjl_urls, list) and zkjl_urls else []
        if not ids:
            return _build_file_objects(
                file_ids_by_day_mag.get(obs_key) or [],
                urls_by_day_mag.get(obs_key) or [],
            )
        return _build_file_objects(ids, urls)

    gdxx: Dict[str, Any] = {
        "F_ID":    order.get("F_ID"),
        "F_GDBH":  order.get("F_GDBH"),
        "F_ZZID":  order.get("F_ZZID"),
        "F_XM":    order.get("F_XM"),
        "F_BRBH":  order.get("F_BRBH"),
        "F_BBLX":  order.get("F_BBLX"),
        "F_DC":    order.get("F_DC"),
        "F_DCID":  order.get("F_DCID"),
        "F_ZZBW":  order.get("F_ZZBW"),
        "F_XYZL":  order.get("F_XYZL"),
        "F_QYFS":  order.get("F_QYFS"),
        "F_YBLY":  order.get("F_YBLY"),
        "F_YBGG":  order.get("F_YBGG"),
        "F_CDQXBZS": order.get("F_CDQXBZS"),
        "F_FSQXBZS": order.get("F_FSQXBZS"),
        "F_SQR":   order.get("F_SQR"),
        "F_PYSJ":  order.get("F_PYSJ"),
        "F_AZ":    order.get("F_AZ"),
    }

    ybzkjl: Dict[str, Any] = {
        "F_JSSJ":       sample_qc.get("F_JSSJ"),
        "F_KSCLSJ":     preprocess.get("F_KSCLSJ"),
        "F_YBYCLQK":    preprocess.get("F_YBYCLQK"),
        "F_YBYCL_FILES": preprocess.get("F_YBYCL_FILES"),
    }

    default_pyts = last_obs_pyts if last_obs_pyts is not None else current_pyts

    if zkjl:
        lqgzkjl: Dict[str, Any] = {
            "F_PYTS":    zkjl.get("F_PYTS") if zkjl.get("F_PYTS") is not None else default_pyts,
            "F_SZQK":    zkjl.get("F_QKMS"),
            "F_XT":      zkjl.get("F_XT"),
            "F_SFWR":    zkjl.get("F_SFWR"),
            "F_XBSL":    zkjl.get("F_XBSL"),
            "F_XBHL":    zkjl.get("F_XBHL"),
            "F_ZYTJCJG": zkjl.get("F_ZYT"),
            "F_IMG_D1M4":  _img_slot(zkjl.get("F_IMG_D1M4"),  zkjl.get("F_IMG_D1M4_URLS"),  (1, 4)),
            "F_IMG_D1M20": _img_slot(zkjl.get("F_IMG_D1M20"), zkjl.get("F_IMG_D1M20_URLS"), (1, 20)),
            "F_IMG_D3M4":  _img_slot(zkjl.get("F_IMG_D3M4"),  zkjl.get("F_IMG_D3M4_URLS"),  (3, 4)),
            "F_IMG_D3M20": _img_slot(zkjl.get("F_IMG_D3M20"), zkjl.get("F_IMG_D3M20_URLS"), (3, 20)),
            "F_IMG_D7M4":  _img_slot(zkjl.get("F_IMG_D7M4"),  zkjl.get("F_IMG_D7M4_URLS"),  (7, 4)),
            "F_IMG_D7M20": _img_slot(zkjl.get("F_IMG_D7M20"), zkjl.get("F_IMG_D7M20_URLS"), (7, 20)),
        }
        # 整理 ZKJL：合并 ID 和 _URLS，移除冗余字段
        for _f in _IMG_FIELDS:
            _ids  = zkjl.get(_f) or []
            _urls = zkjl.pop(f"{_f}_URLS", []) or []
            zkjl[_f] = _build_file_objects(_ids, _urls)
    else:
        # 未提交验证单：最终图/培养天数取最后一次观察记录
        lqgzkjl = {
            "F_PYTS":    default_pyts,
            "F_IMG_D1M4":  _img_slot([], [], (1, 4)),
            "F_IMG_D1M20": _img_slot([], [], (1, 20)),
            "F_IMG_D3M4":  _img_slot([], [], (3, 4)),
            "F_IMG_D3M20": _img_slot([], [], (3, 20)),
            "F_IMG_D7M4":  _img_slot([], [], (7, 4)),
            "F_IMG_D7M20": _img_slot([], [], (7, 20)),
        }

    return {
        "GDXX":     gdxx,
        "YBZKJL":   ybzkjl,
        "LQGZKJL":  lqgzkjl,
        "ZKJL":     zkjl,
    }
