"""
动物实验报告 PDF 生成模块

流程：
  1. 调用 assemble_animal_test_report 组装数据
  2. 加载 app/templates/animal_test_report_template.docx
  3. 替换简单占位符 {{...}}
  4. 展开循环表格 {{LOOP_JZXX}} / {{LOOP_GCJL_ZB}}
  5. 用 win32com 将 Word 转换为 PDF
  6. 注册到文件中心，回写 T_DWSY_JH.F_BG_PDF

模板占位符说明见 app/crud/create_animal_test_template.py 文件头部注释。
如需修改报告样式，直接编辑 app/templates/animal_test_report_template.docx 即可，
不必改动本文件。
"""
import copy
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from docx import Document

from app.crud.animal_test_report import assemble_animal_test_report

logger = logging.getLogger(__name__)


# ==================== 0. 路径工具 ====================

def get_template_path() -> str:
    return str(Path(__file__).parent.parent / "templates" / "animal_test_report_template.docx")


def get_uploads_dir() -> str:
    return str(Path(__file__).parent.parent.parent / "uploads")


def get_report_dir() -> str:
    d = Path(get_uploads_dir()) / "animal_test_report"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _ensure_template():
    """模板文件不存在时给出明确提示，指引用户运行生成脚本"""
    path = get_template_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"动物实验报告模板文件不存在: {path}\n"
            f"请在项目根目录执行以下命令生成初始模板：\n"
            f"    python scripts/generate_animal_test_template.py"
        )
    return path


# ==================== 1. 简单占位符替换 ====================

def _replace_in_para(paragraph, placeholders: Dict[str, str]):
    """在单个段落中做占位符替换（保留首个 run 的格式）"""
    full_text = paragraph.text
    matched   = False
    for key, val in placeholders.items():
        marker = f"{{{{{key}}}}}"
        if marker in full_text:
            full_text = full_text.replace(marker, val)
            matched = True
    if matched:
        # 用第一个 run 重写，保留其格式
        for i, run in enumerate(paragraph.runs):
            if i == 0:
                run.text = full_text
            else:
                run.text = ""


def replace_simple_placeholders(doc: Document, data: Dict[str, Any]):
    """替换模板中所有简单 {{占位符}} 为实际数据"""
    plan    = data.get("实验计划") or {}
    order   = data.get("培养工单") or {}
    ep      = data.get("终点记录") or {}

    placeholders: Dict[str, str] = {
        "生成时间": data.get("生成时间") or "",
        "报告类型": data.get("报告类型") or "",
    }

    # 实验计划
    for key in ["F_SYBH", "F_SYMC", "F_SYKSRQ", "F_SYR",
                "F_XBSL", "F_JZJND", "F_JZJTJ", "F_SYJHYMD", "F_ZT"]:
        placeholders[f"实验计划.{key}"] = str(plan.get(key) or "")

    # 培养工单
    for key in ["F_GDBH", "F_ZZID", "F_XM", "F_BRBH",
                "F_DC", "F_ZZBW", "F_YBLY", "F_PYSJ"]:
        placeholders[f"培养工单.{key}"] = str(order.get(key) or "")

    # 终点记录
    for key in ["F_SYZQ", "F_YZZZQK", "F_SYJL", "F_JLR", "F_JLRQ"]:
        placeholders[f"终点记录.{key}"] = str(ep.get(key) or "")

    # 快捷信息
    quick = data.get("快捷信息") or {}
    for k, v in quick.items():
        placeholders[f"快捷信息.{k}"] = str(v) if v is not None else ""

    # 遍历段落
    for para in doc.paragraphs:
        _replace_in_para(para, placeholders)

    # 遍历表格
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _replace_in_para(para, placeholders)


# ==================== 2. 循环表格展开 ====================

def _find_loop_table(doc: Document, anchor: str):
    """
    在 doc 所有表格中查找含有 anchor 占位符的行，
    返回 (table, row_index) 或 (None, None)。
    """
    for table in doc.tables:
        for r_idx, row in enumerate(table.rows):
            for cell in row.cells:
                if anchor in cell.text:
                    return table, r_idx
    return None, None


def _copy_row_style(src_row, dst_row):
    """将源行的单元格格式（背景色、字体等）复制到目标行"""
    for s_cell, d_cell in zip(src_row.cells, dst_row.cells):
        # 复制段落格式
        if s_cell.paragraphs and d_cell.paragraphs:
            src_para = s_cell.paragraphs[0]
            dst_para = d_cell.paragraphs[0]
            if src_para.runs and dst_para.runs:
                src_run = src_para.runs[0]
                dst_run = dst_para.runs[0]
                dst_run.bold             = src_run.bold
                dst_run.font.size        = src_run.font.size
                dst_run.font.color.rgb   = src_run.font.color.rgb if src_run.font.color and src_run.font.color.type else None


def process_loop_table_jzxx(doc: Document, data: Dict[str, Any]):
    """展开动物接种信息循环表格 {{LOOP_JZXX}}"""
    jzxx: List[Dict] = data.get("动物接种信息") or []
    table, tmpl_idx = _find_loop_table(doc, "{{LOOP_JZXX}}")
    if table is None:
        logger.warning("[动物实验报告] 未找到接种信息表格锚点 {{LOOP_JZXX}}")
        return

    if not jzxx:
        # 删除锚点行
        table._element.remove(table.rows[tmpl_idx]._element)
        return

    keys = ["F_DWBH", "F_XB", "F_NL", "F_LH", "F_ZB",
            "F_JZRQ", "F_JZXBS", "F_JZBW", "F_JZCZY"]

    for seq, item in enumerate(jzxx, 1):
        new_row = table.add_row()
        new_row.cells[0].text = str(seq)
        for j, key in enumerate(keys):
            new_row.cells[j + 1].text = str(item.get(key) or "")

    # 删除锚点行
    table._element.remove(table.rows[tmpl_idx]._element)


def process_loop_table_gcjl_zb(doc: Document, data: Dict[str, Any]):
    """
    展开观察记录循环表格 {{LOOP_GCJL_ZB}}。
    策略：每条（观察会话 × 组别）记录展平为一行。
    列顺序：序号|观察时间|培养天数|记录日期|记录人|组别|
            小鼠饮食|活动度|小鼠异常|体重|肿瘤大小|体积计算|移植部位异常|备注
    """
    gcjl_list: List[Dict] = data.get("观察记录") or []
    table, tmpl_idx = _find_loop_table(doc, "{{LOOP_GCJL_ZB}}")
    if table is None:
        logger.warning("[动物实验报告] 未找到观察记录表格锚点 {{LOOP_GCJL_ZB}}")
        return

    # 展平所有 (观察会话, 组别) 组合
    flat_rows: List[tuple] = []
    for gcjl in gcjl_list:
        base = (
            gcjl.get("F_GCSJ")  or "",
            gcjl.get("F_PYDS")  or "",
            gcjl.get("F_JLRQ")  or "",
            gcjl.get("F_JLR")   or "",
        )
        zbxx: List[Dict] = gcjl.get("ZBXX") or []
        if zbxx:
            for zb in zbxx:
                flat_rows.append(base + (
                    zb.get("F_ZB")        or "",
                    zb.get("F_XSYS")      or "",
                    zb.get("F_HDD")       or "",
                    zb.get("F_YCBX")      or "",
                    zb.get("F_XSTZ")      or "",
                    zb.get("F_ZLDX")      or "",
                    zb.get("F_TJJS")      or "",
                    "是" if zb.get("F_YZWZSFYC") else "否",
                    zb.get("F_BZ")        or "",
                ))
        else:
            # 该观察会话没有组别数据，保留会话行
            flat_rows.append(base + ("", "", "", "", "", "", "", "", ""))

    if not flat_rows:
        table._element.remove(table.rows[tmpl_idx]._element)
        return

    # 列映射：0=序号, 1~4=会话字段, 5~13=组别字段
    for seq, row_vals in enumerate(flat_rows, 1):
        new_row = table.add_row()
        new_row.cells[0].text = str(seq)
        for j, val in enumerate(row_vals):
            new_row.cells[j + 1].text = str(val)

    # 删除锚点行
    table._element.remove(table.rows[tmpl_idx]._element)


# ==================== 3. 主入口 ====================

def generate_animal_test_report_pdf(
    syjh_id: int,
    template_path: Optional[str]  = None,
    output_pdf_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    生成动物实验报告 PDF，注册到文件中心，回写 T_DWSY_JH.F_BG_PDF。

    Returns:
        {
            "success": bool,
            "pdf_path": str | None,
            "file_id":  int | None,
            "message":  str,
        }
    """
    logger.info(f"[动物实验报告PDF] 开始生成，计划ID={syjh_id}")

    try:
        # ===== 0. 路径准备 =====
        if template_path is None:
            template_path = _ensure_template()
        else:
            if not os.path.exists(template_path):
                raise FileNotFoundError(f"指定的模板文件不存在: {template_path}")

        if output_pdf_path is None:
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            output_pdf_path = os.path.join(
                get_report_dir(),
                f"animal_test_report_{syjh_id}_{ts}.pdf",
            )

        # ===== 1. 组装数据 =====
        print("步骤1: 获取报告数据...")
        report_data = assemble_animal_test_report(syjh_id)

        # ===== 2. 创建临时目录 =====
        temp_dir = tempfile.mkdtemp()
        print(f"步骤2: 创建临时目录: {temp_dir}")

        # ===== 3. 加载 Word 模板 =====
        print(f"步骤3: 加载模板: {template_path}")
        doc = Document(template_path)

        # ===== 4. 替换简单占位符 =====
        print("步骤4: 替换简单占位符...")
        replace_simple_placeholders(doc, report_data)

        # ===== 5. 展开循环表格 =====
        print("步骤5: 展开循环表格...")
        process_loop_table_jzxx(doc, report_data)
        process_loop_table_gcjl_zb(doc, report_data)

        # ===== 6. 保存临时 Word =====
        temp_docx = os.path.join(temp_dir, f"animal_test_report_{syjh_id}.docx")
        doc.save(temp_docx)
        print(f"步骤6: 临时Word已保存: {temp_docx}")

        # ===== 7. Word → PDF =====
        print("步骤7: 转换为PDF...")
        from win32com import client as wc
        word = wc.Dispatch("Word.Application")
        word.Visible = False
        try:
            wdoc = word.Documents.Open(os.path.abspath(temp_docx))
            wdoc.SaveAs(os.path.abspath(output_pdf_path), FileFormat=17)
            wdoc.Close()
        finally:
            word.Quit()
        print(f"✅ PDF生成成功: {output_pdf_path}")

        # ===== 8. 注册到文件中心 =====
        print("步骤8: 注册PDF到文件中心...")
        from app.crud.file_center import insert_file_record
        base_abs = os.path.abspath(get_uploads_dir())
        pdf_abs  = os.path.abspath(output_pdf_path)
        try:
            rel_path = os.path.relpath(pdf_abs, base_abs).replace("\\", "/")
        except Exception:
            rel_path = os.path.basename(output_pdf_path)
        file_id = insert_file_record(rel_path, f"动物实验报告_{syjh_id}.pdf")
        print(f"✅ PDF已注册到文件中心: file_id={file_id}")

        # ===== 9. 回写 T_DWSY_JH.F_BG_PDF =====
        print("步骤9: 回写文件ID到数据库...")
        if file_id:
            try:
                from app.crud.animal_test_report_pdf_manager import set_generated_pdf
                set_generated_pdf(syjh_id, int(file_id))
                print(f"✅ 文件ID已回写到T_DWSY_JH.F_BG_PDF")
            except Exception as e:
                logger.warning(f"[动物实验报告PDF] 回写F_BG_PDF失败（不影响文件生成）: {e}")

        # ===== 10. 清理临时目录 =====
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

        return {
            "success":  True,
            "pdf_path": output_pdf_path,
            "file_id":  int(file_id) if file_id else None,
            "message":  "PDF生成成功",
        }

    except Exception as e:
        logger.error(f"[动物实验报告PDF] 生成失败: {e}", exc_info=True)
        return {
            "success":  False,
            "pdf_path": None,
            "file_id":  None,
            "message":  f"生成PDF失败: {e}",
        }
