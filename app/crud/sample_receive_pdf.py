"""
样本接收质控PDF生成模块
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
from app.crud.sample_receive_report import assemble_sample_receive_report


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


def insert_image_to_placeholder(doc: Document, placeholder: str, image_path: str, width: float = 4.0) -> bool:
    """
    在占位符位置插入图片
    
    Args:
        doc: Word文档对象
        placeholder: 占位符文本
        image_path: 图片路径
        width: 图片宽度（英寸）
        
    Returns:
        是否成功插入图片
    """
    inserted = False
    
    # 检查图片文件是否存在
    if not os.path.exists(image_path):
        print(f"警告: 图片文件不存在: {image_path}")
        return False
    
    # 遍历段落
    for paragraph in doc.paragraphs:
        if placeholder in paragraph.text:
            # 清空段落
            paragraph.text = ""
            # 插入图片
            try:
                run = paragraph.add_run()
                run.add_picture(image_path, width=Inches(width))
                paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                inserted = True
                print(f"成功插入图片到占位符 {placeholder}")
                break
            except Exception as e:
                print(f"插入图片失败: {e}")
                # 清空占位符
                paragraph.text = ""
                return False
    
    # 遍历表格
    if not inserted:
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        if placeholder in paragraph.text:
                            paragraph.text = ""
                            try:
                                run = paragraph.add_run()
                                run.add_picture(image_path, width=Inches(width))
                                paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                                inserted = True
                                print(f"成功插入图片到表格占位符 {placeholder}")
                                break
                            except Exception as e:
                                print(f"插入图片失败: {e}")
                                paragraph.text = ""
                                return False
                    if inserted:
                        break
                if inserted:
                    break
            if inserted:
                break
    
    # 如果没有找到占位符，清空占位符
    if not inserted:
        for paragraph in doc.paragraphs:
            if placeholder in paragraph.text:
                paragraph.text = paragraph.text.replace(placeholder, "")
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        if placeholder in paragraph.text:
                            paragraph.text = paragraph.text.replace(placeholder, "")
    
    return inserted


def generate_sample_receive_pdf(F_ZZID: str) -> Dict[str, Any]:
    """
    生成样本接收质控PDF
    
    Args:
        F_ZZID: 标本号
        
    Returns:
        {"file_id": 文件ID, "file_path": 文件路径, "file_name": 文件名}
        
    Raises:
        ValueError: 业务错误（标本不存在、数据不完整等）
        Exception: 系统错误（文件操作、PDF转换等）
    """
    # 1. 获取报告数据
    try:
        data = assemble_sample_receive_report(F_ZZID)
    except ValueError as e:
        raise ValueError(f"获取报告数据失败: {e}")
    
    # 2. 检查模板文件
    base_dir = Path(__file__).resolve().parents[1]  # Bio9_organ/app
    template_path = base_dir / "templates" / "sample_receive_template.docx"
    if not template_path.exists():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")
    
    print(f"加载模板: {template_path}")
    doc = Document(str(template_path))
    
    # 3. 构建占位符映射
    placeholder_map = {}
    
    # 基本信息
    placeholder_map["{{报告标题}}"] = data.get("报告标题", "")
    placeholder_map["{{生成时间}}"] = data.get("生成时间", "")
    
    # 就诊信息
    jzxx = data.get("就诊信息", {})
    for key, value in jzxx.items():
        placeholder_map[f"{{{{就诊信息.{key}}}}}"] = value
        # 同时支持短格式
        placeholder_map[f"{{{{{key}}}}}"] = value
    
    # 标本信息
    bbxx = data.get("标本信息", {})
    for key, value in bbxx.items():
        placeholder_map[f"{{{{标本信息.{key}}}}}"] = value
    
    # 质控信息（除图片外）
    zkxx = data.get("质控信息", {})
    for key, value in zkxx.items():
        if key != "F_SYTP_URL":  # 图片单独处理
            placeholder_map[f"{{{{质控信息.{key}}}}}"] = value
            # 支持短格式
            if key.startswith("F_"):
                placeholder_map[f"{{{{{key}}}}}"] = value
    
    # 4. 先插入实样图片（必须在替换占位符之前）
    sytp_url = zkxx.get("F_SYTP_URL", "")
    if sytp_url:
        # 转换为绝对路径
        # 数据库中存储的路径可能是: /2025/11/12/xxx.jpg 或 2025/11/12/xxx.jpg
        # 实际文件在: Bio9_organ/uploads/2025/11/12/xxx.jpg
        if sytp_url.startswith("/"):
            sytp_url = sytp_url[1:]  # 去掉前导/
        
        # 使用settings中的upload_dir（绝对路径）
        uploads_dir = Path(settings.upload_dir)
        image_path = uploads_dir / sytp_url
        
        print(f"插入实样图片: {image_path}")
        # 尝试多种占位符格式
        image_inserted = False
        for placeholder in ["{{实样图片}}", "{{F_SYTP_URL}}", "{{质控信息.F_SYTP_URL}}"]:
            if insert_image_to_placeholder(doc, placeholder, str(image_path), width=3.5):
                image_inserted = True
                break
        
        if not image_inserted:
            print("警告: 未找到图片占位符或插入失败，将清空占位符")
            for placeholder in ["{{实样图片}}", "{{F_SYTP_URL}}", "{{质控信息.F_SYTP_URL}}"]:
                placeholder_map[placeholder] = ""
    else:
        print("警告: 未找到实样图片URL，将清空占位符")
        # 清空图片占位符
        for placeholder in ["{{实样图片}}", "{{F_SYTP_URL}}", "{{质控信息.F_SYTP_URL}}"]:
            placeholder_map[placeholder] = ""
    
    # 5. 替换其他占位符
    print(f"替换 {len(placeholder_map)} 个占位符...")
    replace_placeholders(doc, placeholder_map)
    
    # 6. 创建临时目录并保存临时DOCX文件
    temp_dir = base_dir / "temp"
    temp_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_docx_name = f"sample_receive_{F_ZZID}_{timestamp}.docx"
    temp_docx_path = temp_dir / temp_docx_name
    
    print(f"保存临时DOCX: {temp_docx_path}")
    doc.save(str(temp_docx_path))
    
    # 7. 转换为PDF
    pdf_name = f"sample_receive_{F_ZZID}_{timestamp}.pdf"
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
        
        # 10. 保存PDF文件ID到样本表
        try:
            from app.crud.sample_receive_pdf_manager import set_generated_pdf
            set_generated_pdf(F_ZZID, file_id)
            print(f"调试: 已保存PDF文件ID {file_id} 到样本 {F_ZZID}")
        except Exception as e:
            print(f"警告: 保存PDF文件ID到样本表失败: {e}")
        
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

