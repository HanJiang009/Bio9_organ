"""基于 Word 模板生成 PDF：

方案：使用 python-docx 渲染 DOCX（文本占位替换），然后调用本地安装的 Microsoft Word 或 LibreOffice 将 DOCX 转换为 PDF。

说明：
- Windows 环境可优先使用 Word COM（需要安装 Office）；
- 若无 Office，可回退到 LibreOffice 的 soffice 命令行（需预装 LibreOffice）。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict

from docx import Document  # pip install python-docx
from typing import Any, Optional

from .settings import settings


PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render_docx_template(template_path: Path, context: Dict[str, str], output_docx: Path) -> Path:
    doc = Document(str(template_path))
    # 简易占位符替换：仅处理段落与表格单元格中的文本
    def replace_in_run(run_text: str) -> str:
        def _repl(m: re.Match[str]) -> str:
            key = m.group(1)
            return str(context.get(key, m.group(0)))
        return PLACEHOLDER_PATTERN.sub(_repl, run_text)

    for p in doc.paragraphs:
        for run in p.runs:
            run.text = replace_in_run(run.text)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for run in p.runs:
                        run.text = replace_in_run(run.text)

    output_docx.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_docx))
    return output_docx


def convert_docx_to_pdf(input_docx: Path, output_pdf: Path) -> Path:
    # 优先尝试 Word COM（仅 Windows + Office）
    try:
        import win32com.client  # type: ignore
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(str(input_docx))
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        doc.SaveAs(str(output_pdf), FileFormat=17)  # 17=wdFormatPDF
        doc.Close()
        word.Quit()
        return output_pdf
    except Exception:
        pass

    # 回退到 LibreOffice
    soffice = os.environ.get("SOFFICE_PATH", "soffice")
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        soffice,
        "--headless",
        "--convert-to", "pdf",
        "--outdir", str(output_pdf.parent),
        str(input_docx),
    ]
    subprocess.check_call(cmd)
    return output_pdf


def render_pdf(template_name: str, context: Dict[str, str], out_basename: str) -> Path:
    base = Path(__file__).resolve().parents[2]
    templates_dir = base / settings.templates_dir
    upload_dir = base / settings.upload_dir
    template_path = templates_dir / template_name
    tmp_docx = upload_dir / "_tmp" / f"{out_basename}.docx"
    out_pdf = upload_dir / "pdf" / f"{out_basename}.pdf"

    render_docx_template(template_path, context, tmp_docx)
    convert_docx_to_pdf(tmp_docx, out_pdf)
    return out_pdf


def render_culture_detail_pdf_with_tables(template_name: str, context: Dict[str, Any], out_basename: str) -> Path:
    """专门为培养工单详情渲染PDF - 程序化创建表格避免 {%tr for %} 问题
    
    Args:
        template_name: 模板文件名
        context: 上下文数据
        out_basename: 输出文件基础名
        
    Returns:
        生成的 PDF 文件路径
    """
    from docx import Document
    from docx.shared import Pt, Inches
    
    base = Path(__file__).resolve().parents[2]
    templates_dir = base / settings.templates_dir
    upload_dir = base / settings.upload_dir
    
    # 直接创建新文档，不使用模板
    doc = Document()
    
    # 设置默认字体
    style = doc.styles['Normal']
    style.font.name = '宋体'
    style.font.size = Pt(10.5)
    
    # 标题
    doc.add_heading('类器官（组织细胞）培养工单表', level=1)
    
    # 工单信息
    doc.add_heading('培养工单信息', level=2)
    info_table = doc.add_table(rows=6, cols=4)
    info_table.style = 'Table Grid'
    info_data = [
        ['培养工单号：', context.get('GDXX_F_GDBH', ''), '', ''],
        ['标本号：', context.get('GDXX_F_ZZID', ''), '组织部位：', context.get('GDXX_F_ZZBW', '')],
        ['代次：', context.get('GDXX_F_DC', ''), '代次ID：', context.get('GDXX_F_DCID', '')],
        ['取样方式：', context.get('GDXX_F_QYFS', ''), '取样部位：', context.get('GDXX_F_ZZBW', '')],
        ['样本来源：', context.get('GDXX_F_YBLY', ''), '样本规格：', context.get('GDXX_F_YBGG', '')],
        ['培养人：', context.get('GDXX_F_SQR', ''), '开始培养日期：', context.get('GDXX_F_PYSJ', '')],
    ]
    for i, row_data in enumerate(info_data):
        for j, cell_text in enumerate(row_data):
            info_table.rows[i].cells[j].text = str(cell_text)
    
    # 实验过程记录
    doc.add_heading('类器官培养实验过程记录', level=2)
    process_table = doc.add_table(rows=6, cols=4)
    process_table.style = 'Table Grid'
    process_data = [
        ['操作人：', context.get('PYMX_CZR', ''), '操作时间：', context.get('PYMX_CZSJ', '')],
        ['离体时间：', context.get('YCLXX_F_LTSJ', ''), '开始处理时间：', context.get('YCLXX_F_KSCLSJ', '')],
        ['培养间温度：', context.get('PYMX_PYJ_WD', ''), '培养间湿度：', context.get('PYMX_PYJ_SD', '')],
        ['清洗液成分：', context.get('YCLXX_F_QXYCF', ''), '组织碎片尺寸（mm）：', context.get('YCLXX_F_ZZSPCC', '')],
        ['细胞数量与活性：', context.get('PYMX_XH_XBSL', ''), '样本预处理情况:', context.get('YCLXX_F_YBYCLQK', '')],
        ['样本预处理：', context.get('YCLXX_F_FJ', ''), '', ''],
    ]
    for i, row_data in enumerate(process_data):
        for j, cell_text in enumerate(row_data):
            process_table.rows[i].cells[j].text = str(cell_text)
    
    # 消化步骤
    doc.add_heading('消化步骤', level=3)
    doc.add_paragraph(f"酶解方案：{context.get('YCLXX_F_MJFA', '')}")
    doc.add_paragraph(f"中止酶解方案：{context.get('YCLXX_F_ZZMJFA', '')}")
    
    # 消化酶表格 - 动态创建
    xhmsyjl = context.get('XHMSYJL', [])
    if xhmsyjl:
        xhm_table = doc.add_table(rows=len(xhmsyjl)+1, cols=7)
        xhm_table.style = 'Table Grid'
        # 表头
        headers = ['序号', '消化酶（用量、浓度）', '消化温度（℃）', '操作方式', '消化时间（min）', '备注', '操作人']
        for i, h in enumerate(headers):
            xhm_table.rows[0].cells[i].text = h
        # 数据行
        for idx, xhm in enumerate(xhmsyjl, 1):
            row = xhm_table.rows[idx]
            row.cells[0].text = str(xhm.get('F_ID', idx))
            row.cells[1].text = f"{xhm.get('F_XHM', '')} {xhm.get('F_YL', '')} {xhm.get('F_ND', '')}"
            row.cells[2].text = str(xhm.get('F_XHWD', ''))
            row.cells[3].text = str(xhm.get('F_CZFS', ''))
            row.cells[4].text = str(xhm.get('F_XHSJ', ''))
            row.cells[5].text = str(xhm.get('F_BZ', ''))
            row.cells[6].text = str(context.get('XHMSYJL_SYR', ''))
    
    doc.add_paragraph(f"筛网孔径（μm）：{context.get('YCLXX_F_LWKJ', '')}    细胞数量（个）{context.get('PYMX_XH_XBSL', '')}")
    doc.add_paragraph(f"台盼蓝拒染率（%）：        细胞得率（%）：{context.get('YCLXX_F_XBHL', '')}")
    
    # 铺板步骤
    doc.add_heading('铺板步骤', level=3)
    doc.add_paragraph(f"培养板编号：{context.get('PYMX_PB_PYBBH', '')}")
    
    # 基质胶表格 - 动态创建
    jzjxx = context.get('JZJXX', [])
    if jzjxx:
        jzj_table = doc.add_table(rows=len(jzjxx)+1, cols=4)
        jzj_table.style = 'Table Grid'
        # 表头
        headers = ['序号', '基质胶', '用量（ml）', '浓度']
        for i, h in enumerate(headers):
            jzj_table.rows[0].cells[i].text = h
        # 数据行
        for idx, jzj in enumerate(jzjxx, 1):
            row = jzj_table.rows[idx]
            row.cells[0].text = str(jzj.get('F_ID', idx))
            row.cells[1].text = str(jzj.get('F_JZJ', ''))
            row.cells[2].text = str(jzj.get('F_YL', ''))
            row.cells[3].text = str(jzj.get('F_ND', ''))
    
    doc.add_paragraph(f"接种孔板数：{context.get('PYMX_PB_JZKS', '')}    单孔接种体积（μl/孔）：{context.get('PYMX_PB_DKJZTJ', '')}")
    doc.add_paragraph(f"成胶温度（℃）：{context.get('PYMX_PB_CJWD', '')}    成胶时间（min）：{context.get('PYMX_PB_CJSJ', '')}")
    
    # 培养步骤
    doc.add_heading('培养步骤', level=3)
    doc.add_paragraph(f"添加培养基（μL）：{context.get('PYMX_PY_TJPYJ', '')}    培养箱条件：{context.get('PYMX_PY_PYX_TEMP', '')}℃、{context.get('PYMX_PY_PYX_CO2', '')}、{context.get('PYMX_PY_PYX_HUM', '')}")
    doc.add_paragraph(f"放入培养箱时间：{context.get('PYMX_PY_PYX_FRSJ', '')}    附件：{context.get('PYMX_FJ', '')}")
    
    # 观察记录
    doc.add_heading('类器官培养实验观察记录', level=2)
    gcjl = context.get('GCJL', [])
    if gcjl:
        obs_table = doc.add_table(rows=len(gcjl)+1, cols=11)
        obs_table.style = 'Table Grid'
        # 表头
        headers = ['序号', '观察日期（培养天数）', '附件', '培养基状态', '细胞状态', '是否换液', '换液记录', '上清吸取量', '下清补充量', '备注', '记录人 记录日期']
        for i, h in enumerate(headers):
            obs_table.rows[0].cells[i].text = h
        # 数据行
        for idx, obs in enumerate(gcjl, 1):
            row = obs_table.rows[idx]
            row.cells[0].text = str(idx)
            row.cells[1].text = str(obs.get('F_GCSJ', ''))
            row.cells[2].text = str(obs.get('F_FJ', ''))
            row.cells[3].text = str(obs.get('F_PYJZT', ''))
            row.cells[4].text = str(obs.get('F_XBZT', ''))
            row.cells[5].text = str(obs.get('F_SFHY', ''))
            row.cells[6].text = str(obs.get('F_HYJL', ''))
            row.cells[7].text = str(obs.get('F_QXQL', ''))
            row.cells[8].text = str(obs.get('F_QXBL', ''))
            row.cells[9].text = str(obs.get('F_BZ', ''))
            row.cells[10].text = f"{obs.get('F_JLR', '')} {obs.get('F_JLSJ', '')}"
    
    # 保存临时 DOCX
    tmp_docx = upload_dir / "_tmp" / f"{out_basename}.docx"
    out_pdf = upload_dir / "pdf" / f"{out_basename}.pdf"
    tmp_docx.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    
    doc.save(str(tmp_docx))
    
    # 转换为 PDF
    convert_docx_to_pdf(tmp_docx, out_pdf)
    
    return out_pdf


def render_pdf_with_docxtpl(template_name: str, context: Dict[str, Any], out_basename: str) -> Path:
    """使用 docxtpl 渲染模板(支持 Jinja2 语法如循环)并转换为 PDF
    
    Args:
        template_name: 模板文件名
        context: 上下文数据(支持复杂数据结构)
        out_basename: 输出文件基础名(不含扩展名)
        
    Returns:
        生成的 PDF 文件路径
    """
    try:
        from docxtpl import DocxTemplate
        from jinja2 import Environment
    except ImportError:
        raise RuntimeError("需要安装 docxtpl: pip install docxtpl")
    
    base = Path(__file__).resolve().parents[2]
    templates_dir = base / settings.templates_dir
    upload_dir = base / settings.upload_dir
    template_path = templates_dir / template_name
    
    if not template_path.exists():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")
    
    tmp_docx = upload_dir / "_tmp" / f"{out_basename}.docx"
    out_pdf = upload_dir / "pdf" / f"{out_basename}.pdf"
    tmp_docx.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        # 加载模板
        tpl = DocxTemplate(str(template_path))
        
        # 调试：打印上下文中的列表数据
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"JZJXX 数量: {len(context.get('JZJXX', []))}")
        logger.info(f"XHMSYJL 数量: {len(context.get('XHMSYJL', []))}")
        logger.info(f"GCJL 数量: {len(context.get('GCJL', []))}")
        
        # 重要：确保所有列表至少有一个空项，避免 docxtpl 的 {%tr for %} bug
        if not context.get('JZJXX'):
            context['JZJXX'] = []
        if not context.get('XHMSYJL'):
            context['XHMSYJL'] = []
        if not context.get('GCJL'):
            context['GCJL'] = []
        
        # docxtpl 默认就支持 Jinja2，不需要额外配置 autoescape
        tpl.render(context)
        
        # 保存临时 DOCX
        tpl.save(str(tmp_docx))
        
        # 转换为 PDF
        convert_docx_to_pdf(tmp_docx, out_pdf)
        
        return out_pdf
        
    except Exception as e:
        # 如果是 Jinja2 模板错误，提供更详细的信息
        error_msg = str(e)
        if "Unexpected end of template" in error_msg or "endfor" in error_msg:
            raise RuntimeError(
                f"模板语法错误: {error_msg}\n"
                "请检查 Word 模板中的所有循环标签是否正确配对:\n"
                "- 表格行循环: {%tr for ... %} ... {% endtr %}\n"
                "- 普通循环: {% for ... %} ... {% endfor %}\n"
                "提示: Word 可能会自动分割标签，建议重新手动输入所有 Jinja2 标签"
            )
        raise


# 使用 docxtpl 支持图片占位渲染；若模板不存在则自动生成一个简易模板
def render_pdf_with_images(template_name: str, context: Dict[str, Any], image_fields: Dict[str, Optional[Path]], out_basename: str) -> Path:
    try:
        from docxtpl import DocxTemplate, InlineImage  # type: ignore
        from docx.shared import Cm  # type: ignore
    except Exception as e:
        # 回退到纯文本渲染
        return render_pdf(template_name, {k: str(v) for k, v in context.items()}, out_basename)

    base = Path(__file__).resolve().parents[2]
    templates_dir = base / settings.templates_dir
    upload_dir = base / settings.upload_dir
    template_path = templates_dir / template_name

    # 若模板不存在，自动生成基础模板，包含所有 context 与 image_fields 占位符
    if not template_path.exists():
        templates_dir.mkdir(parents=True, exist_ok=True)
        # 使用 python-docx 生成一个带占位符的模板
        doc = Document()
        doc.add_heading("质控审核单", level=1)
        doc.add_paragraph("基本信息：")
        for k in sorted([k for k in context.keys() if isinstance(context.get(k), (str, int, float))]):
            doc.add_paragraph(f"{k}: {{\{{ {k} \}}}}")
        doc.add_paragraph("图片：")
        for img_key in sorted(image_fields.keys()):
            doc.add_paragraph(f"{img_key}: {{\{{ {img_key} \}}}}")
        doc.save(str(template_path))

    # 加载模板并渲染
    tpl = DocxTemplate(str(template_path))
    ctx: Dict[str, Any] = {}
    # 文本上下文
    for k, v in context.items():
        ctx[k] = "" if v is None else v
    # 图片上下文（将路径替换为 InlineImage），统一缩放宽度 8cm（可按需调整）
    for img_key, p in image_fields.items():
        if p and p.exists():
            try:
                ctx[img_key] = InlineImage(tpl, str(p), width=Cm(8))
            except Exception:
                ctx[img_key] = ""
        else:
            ctx[img_key] = ""

    tmp_docx = upload_dir / "_tmp" / f"{out_basename}.docx"
    out_pdf = upload_dir / "pdf" / f"{out_basename}.pdf"
    tmp_docx.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    tpl.render(ctx)
    tpl.save(str(tmp_docx))
    convert_docx_to_pdf(tmp_docx, out_pdf)
    return out_pdf

