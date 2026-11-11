"""
样本评分表PDF生成模块
职责：基于Word模板和报告数据生成PDF文件
"""
from typing import Dict, Any
from pathlib import Path
from datetime import datetime
import os
import json

from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT

from app.core.docx_pdf import convert_docx_to_pdf
from app.core.settings import settings
from app.crud.file_center import insert_file_record
from app.crud.preprocess_score_report import assemble_preprocess_score_report


def replace_placeholders(doc: Document, placeholder_map: Dict[str, Any]) -> None:
    """
    替换文档中的占位符
    
    Args:
        doc: Word文档对象
        placeholder_map: 占位符映射字典
    
    注意：此函数会处理占位符被分割成多个run的情况
    """
    def replace_in_paragraph(paragraph):
        """在段落中替换占位符，处理跨run的情况"""
        # 获取段落完整文本
        full_text = paragraph.text
        
        # 检查是否包含任何占位符
        has_placeholder = False
        for key in placeholder_map.keys():
            if key in full_text:
                has_placeholder = True
                break
        
        if not has_placeholder:
            return
        
        # 执行替换
        new_text = full_text
        for key, value in placeholder_map.items():
            if key in new_text:
                new_text = new_text.replace(key, str(value))
        
        # 如果文本有变化，重新设置段落内容
        if new_text != full_text:
            # 清空所有runs
            for run in paragraph.runs:
                run.text = ""
            # 在第一个run中设置新文本（保留格式）
            if paragraph.runs:
                paragraph.runs[0].text = new_text
            else:
                paragraph.add_run(new_text)
    
    # 替换段落中的占位符
    for paragraph in doc.paragraphs:
        replace_in_paragraph(paragraph)
    
    # 替换表格中的占位符
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    replace_in_paragraph(paragraph)


def process_score_details_table(doc: Document, details: list) -> None:
    """
    处理评分详情动态表格（包含所有选项）
    
    Args:
        doc: Word文档对象
        details: 评分详情列表（每项包含label、value、score、options）
    """
    # 查找包含循环标记的表格
    loop_marker = "{{LOOP_SCORE_DETAILS}}"
    target_table = None
    header_row_idx = None
    
    for table in doc.tables:
        for row_idx, row in enumerate(table.rows):
            for cell in row.cells:
                if loop_marker in cell.text:
                    target_table = table
                    header_row_idx = row_idx
                    break
            if target_table:
                break
        if target_table:
            break
    
    if not target_table or header_row_idx is None:
        print(f"警告: 未找到评分详情循环标记 {loop_marker}")
        return
    
    # 移除循环标记
    for cell in target_table.rows[header_row_idx].cells:
        for paragraph in cell.paragraphs:
            paragraph.text = paragraph.text.replace(loop_marker, "")
    
    # 获取模板行（标题行的下一行）
    if header_row_idx + 1 >= len(target_table.rows):
        print("警告: 评分详情表格缺少模板行")
        return
    
    template_row = target_table.rows[header_row_idx + 1]
    
    # 删除模板行
    target_table._element.remove(template_row._element)
    
    # 为每个评分项目添加行
    for idx, detail in enumerate(details):
        label = detail.get("label", "")
        value = detail.get("value") or ""
        score = detail.get("score", 0)
        options = detail.get("options", [])
        
        # 组装选项列表字符串
        options_str = ""
        for opt in options:
            opt_category = opt.get("category", "")
            opt_score = opt.get("score", 0)
            options_str += f"{opt_category}({opt_score}分) "
        options_str = options_str.strip()
        
        # 添加新行
        new_row = target_table.add_row()
        
        # 填充数据：序号、评分项目、当前值、得分、所有选项
        cells_data = [
            str(idx + 1),
            label,
            str(value),
            str(score),
            options_str
        ]
        
        for cell_idx, cell_data in enumerate(cells_data):
            if cell_idx < len(new_row.cells):
                new_row.cells[cell_idx].text = cell_data


def generate_preprocess_score_pdf(gdid: int) -> Dict[str, Any]:
    """
    生成样本评分表PDF
    
    Args:
        gdid: 工单ID
        
    Returns:
        {"file_id": 文件ID, "file_path": 文件路径, "file_name": 文件名}
        
    Raises:
        ValueError: 业务错误（工单不存在、数据不完整等）
        Exception: 系统错误（文件操作、PDF转换等）
    """
    # 1. 获取报告数据
    try:
        data = assemble_preprocess_score_report(gdid)
    except ValueError as e:
        raise ValueError(f"获取报告数据失败: {e}")
    
    # 2. 检查模板文件
    base_dir = Path(__file__).resolve().parents[1]  # Bio9_organ/app
    template_path = base_dir / "templates" / "preprocess_score_template.docx"
    if not template_path.exists():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")
    
    print(f"加载模板: {template_path}")
    doc = Document(str(template_path))
    
    # 3. 构建占位符映射
    placeholder_map = {}
    
    # 基本信息
    placeholder_map["{{报告标题}}"] = data.get("报告标题", "")
    placeholder_map["{{生成时间}}"] = data.get("生成时间", "")
    
    # 工单信息
    gdxx = data.get("工单信息", {})
    for key, value in gdxx.items():
        placeholder_map[f"{{{{工单信息.{key}}}}}"] = value
        if key.startswith("F_"):
            placeholder_map[f"{{{{{key}}}}}"] = value
    
    # 患者信息
    hzxx = data.get("患者信息", {})
    for key, value in hzxx.items():
        placeholder_map[f"{{{{患者信息.{key}}}}}"] = value
        if key.startswith("F_"):
            placeholder_map[f"{{{{{key}}}}}"] = value
    
    # 标本信息
    bbxx = data.get("标本信息", {})
    for key, value in bbxx.items():
        placeholder_map[f"{{{{标本信息.{key}}}}}"] = value
    
    # 预处理信息
    yclxx = data.get("预处理信息", {})
    for key, value in yclxx.items():
        placeholder_map[f"{{{{预处理信息.{key}}}}}"] = value
    
    # 评分信息（不包括details列表）
    pfxx = data.get("评分信息", {})
    for key, value in pfxx.items():
        if key != "details":
            placeholder_map[f"{{{{评分信息.{key}}}}}"] = value
            # 特殊处理总分
            if key == "total_score":
                placeholder_map["{{总分}}"] = value
    
    # 4. 处理评分详情动态表格（必须在替换占位符之前）
    score_details = pfxx.get("details", [])
    if score_details:
        process_score_details_table(doc, score_details)
    
    # 5. 替换其他占位符
    print(f"替换 {len(placeholder_map)} 个占位符...")
    replace_placeholders(doc, placeholder_map)
    
    # 6. 创建临时目录并保存临时DOCX文件
    temp_dir = base_dir / "temp"
    temp_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    gdbh = data.get("工单信息", {}).get("F_GDBH", str(gdid))
    temp_docx_name = f"preprocess_score_{gdbh}_{timestamp}.docx"
    temp_docx_path = temp_dir / temp_docx_name
    
    print(f"保存临时DOCX: {temp_docx_path}")
    doc.save(str(temp_docx_path))
    
    # 7. 转换为PDF
    pdf_name = f"preprocess_score_{gdbh}_{timestamp}.pdf"
    pdf_path = temp_dir / pdf_name
    
    print(f"开始PDF转换: {temp_docx_path} -> {pdf_path}")
    
    try:
        convert_docx_to_pdf(temp_docx_path, pdf_path)
        print(f"PDF转换成功: {pdf_path}")

        # 8. 移动PDF到uploads目录
        uploads_dir = Path(settings.upload_dir)
        
        # 创建pdf子目录
        pdf_dir = uploads_dir / "pdf"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        
        final_pdf_path = pdf_dir / pdf_name
        pdf_path.rename(final_pdf_path)
        
        # 计算相对路径
        relative_path = str(final_pdf_path.relative_to(uploads_dir)).replace("\\", "/")
        
        # 9. 注册到文件中心
        file_id = insert_file_record(
            path=relative_path,
            original_name=pdf_name
        )
        
        print(f"PDF已保存并注册: ID={file_id}, Path={relative_path}")
        
        # 10. 保存PDF文件ID到预处理表
        try:
            from app.crud.preprocess_score_pdf_manager import set_generated_pdf
            set_generated_pdf(gdid, file_id)
            print(f"调试: 已保存PDF文件ID {file_id} 到工单 {gdid} 的预处理表")
        except Exception as e:
            print(f"警告: 保存PDF文件ID到预处理表失败: {e}")
        
        # 11. 清理临时文件
        try:
            temp_docx_path.unlink()
        except Exception as e:
            print(f"警告: 清理临时DOCX失败: {e}")
        
        return {
            "file_id": file_id,
            "file_path": relative_path,
            "file_name": pdf_name
        }
        
    except Exception as e:
        print(f"PDF转换失败: {e}")
        
        # PDF转换失败，返回DOCX作为备选
        print("将返回DOCX文件作为备选")
        
        uploads_dir = Path(settings.upload_dir)
        docx_dir = uploads_dir / "docx"
        docx_dir.mkdir(parents=True, exist_ok=True)
        
        docx_uploads_path = docx_dir / temp_docx_name
        temp_docx_path.rename(docx_uploads_path)
        
        relative_path = str(docx_uploads_path.relative_to(uploads_dir)).replace("\\", "/")
        file_id = insert_file_record(
            path=relative_path,
            original_name=temp_docx_name
        )
        
        return {
            "file_id": file_id,
            "file_path": relative_path,
            "file_name": temp_docx_name,
            "warning": "PDF转换失败，已生成DOCX文件"
        }

