"""
类器官培养工单详情PDF生成模块（优化版）
参考干细胞系统的成熟方案，使用模板标记方式

主要改进：
1. 使用模板标记（{{LOOP_XXX}}）方式处理动态表格
2. 支持HTTP/HTTPS URL和本地相对路径的图片
3. PDF转换失败时自动降级为DOCX
4. 改进执行顺序（先处理图片，再替换文本）
5. 完善临时文件清理
"""
from typing import Dict, Any, List
import os
import time
import shutil
from pathlib import Path
from datetime import datetime

from docx import Document
from docx.shared import Inches
import requests

from app.crud.culture_detail_report import assemble_culture_detail_report
from app.core.settings import settings
from app.core.docx_pdf import convert_docx_to_pdf
from app.crud.file_center import insert_file_record


def download_image(url: str, save_path: str, base_upload_dir: str) -> bool:
    """
    复制或下载图片到指定路径
    
    Args:
        url: 图片URL或相对路径
        save_path: 保存路径
        base_upload_dir: uploads基础目录
        
    Returns:
        是否成功
    """
    try:
        # 判断是本地文件路径还是HTTP URL
        if url.startswith('http://') or url.startswith('https://'):
            # HTTP URL，需要下载
            print(f"调试: 从HTTP下载图片: {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            with open(save_path, 'wb') as f:
                f.write(response.content)
            print(f"调试: HTTP下载成功，保存到: {save_path}")
            return True
        else:
            # 本地文件路径
            # 移除开头的斜杠
            relative_path = url.lstrip('/')
            source_path = Path(base_upload_dir) / relative_path
            
            print(f"调试: 本地图片路径")
            print(f"  - 原始URL: {url}")
            print(f"  - 相对路径: {relative_path}")
            print(f"  - 源文件: {source_path}")
            print(f"  - 目标文件: {save_path}")
            print(f"  - 源文件存在: {source_path.exists()}")
            
            if source_path.exists():
                # 复制文件
                shutil.copy(str(source_path), save_path)
                print(f"调试: 本地图片复制成功")
                return True
            else:
                print(f"错误: 图片文件不存在: {source_path}")
                return False
    except Exception as e:
        print(f"处理图片失败: {url}, 错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def replace_text_in_paragraph(paragraph, placeholder: str, value: str):
    """
    替换段落中的占位符
    
    Args:
        paragraph: docx段落对象
        placeholder: 占位符（包含{{}}）
        value: 替换值
    """
    if placeholder in paragraph.text:
        # 保持格式，逐个run替换
        full_text = ''.join(run.text for run in paragraph.runs)
        if placeholder in full_text:
            new_text = full_text.replace(placeholder, str(value) if value is not None else "")
            
            # 清空所有runs并在第一个run中写入新文本
            for i, run in enumerate(paragraph.runs):
                if i == 0:
                    run.text = new_text
                else:
                    run.text = ""


def replace_text_in_table_cell(cell, placeholder: str, value: str):
    """
    替换表格单元格中的占位符
    
    Args:
        cell: docx表格单元格对象
        placeholder: 占位符（包含{{}}）
        value: 替换值
    """
    for paragraph in cell.paragraphs:
        replace_text_in_paragraph(paragraph, placeholder, value)


def replace_placeholders(doc: Document, data: Dict[str, Any]):
    """
    替换Word文档中的简单占位符（跳过图片占位符）
    
    Args:
        doc: Document对象
        data: 报告数据
    """
    # 构建占位符映射表
    placeholder_map = {}
    
    # 元数据
    meta = data.get("meta", {})
    for key, value in meta.items():
        placeholder_map[f"{{{{meta.{key}}}}}"] = value
    
    # 工单基本信息
    gdxx = data.get("工单基本信息", {})
    for key, value in gdxx.items():
        placeholder_map[f"{{{{{key}}}}}"] = value
    
    # 样本信息
    ybxx = data.get("样本信息", {})
    for key, value in ybxx.items():
        placeholder_map[f"{{{{样本信息.{key}}}}}"] = value
    
    # 预处理信息（跳过图片字段 T_YCL.F_YBYCL）
    yclxx = data.get("预处理信息", {})
    for key, value in yclxx.items():
        # 跳过图片字段，由 insert_images() 处理
        if key != "T_YCL.F_YBYCL":
            placeholder_map[f"{{{{预处理信息.{key}}}}}"] = value
    
    # 培养详情（嵌套结构）- 支持多种占位符格式
    pymx = data.get("培养详情", {})
    for section_name, section_data in pymx.items():
        if isinstance(section_data, dict):
            for key, value in section_data.items():
                # 完整路径格式：{{培养详情.铺板信息.F_PYBBH}}
                placeholder_map[f"{{{{培养详情.{section_name}.{key}}}}}"] = value
                # 简短路径格式：{{铺板信息.F_PYBBH}}
                placeholder_map[f"{{{{{section_name}.{key}}}}}"] = value
                # 直接字段格式（用于操作信息等）：{{操作人}}
                if section_name == "操作信息":
                    placeholder_map[f"{{{{{key}}}}}"] = value
        elif isinstance(section_data, list):
            # 附件列表 - 将文件名拼接为字符串
            if section_name == "附件":
                file_names = [item.get("F_NAME", "") for item in section_data if isinstance(item, dict)]
                placeholder_map[f"{{{{附件.F_NAME}}}}"] = ", ".join(file_names) if file_names else ""
    
    # 回收记录
    hsjl = data.get("回收记录", {})
    for key, value in hsjl.items():
        placeholder_map[f"{{{{回收记录.{key}}}}}"] = value
    
    # 替换文档中的所有占位符
    # 1. 替换段落中的占位符
    for paragraph in doc.paragraphs:
        for placeholder, value in placeholder_map.items():
            replace_text_in_paragraph(paragraph, placeholder, value)
    
    # 2. 替换表格中的占位符（非动态表格）
    for table in doc.tables:
        # 检查是否是动态表格（包含LOOP标记）
        has_loop_marker = False
        for row in table.rows:
            for cell in row.cells:
                cell_text = cell.text
                if "{{LOOP_" in cell_text:
                    has_loop_marker = True
                    break
            if has_loop_marker:
                break
        
        # 非动态表格才替换占位符
        if not has_loop_marker:
            for row in table.rows:
                for cell in row.cells:
                    for placeholder, value in placeholder_map.items():
                        replace_text_in_table_cell(cell, placeholder, value)


def process_dynamic_table_gcjl(doc: Document, data: Dict[str, Any], temp_dir: str, base_upload_dir: str):
    """
    处理观察记录动态表格
    
    Args:
        doc: Document对象
        data: 报告数据
        temp_dir: 临时目录路径
        base_upload_dir: uploads基础目录
    """
    gcjl_list = data.get("观察记录", [])
    print(f"调试: 观察记录数量 = {len(gcjl_list)}")
    
    # 查找包含{{LOOP_GCJL}}的表格
    target_table = None
    template_row_idx = None
    
    for table in doc.tables:
        for row_idx, row in enumerate(table.rows):
            for cell in row.cells:
                if "{{LOOP_GCJL}}" in cell.text:
                    target_table = table
                    template_row_idx = row_idx
                    print(f"调试: 找到观察记录表格，模板行索引 = {row_idx}")
                    break
            if target_table:
                break
        if target_table:
            break
    
    if not target_table or template_row_idx is None:
        print("警告: 未找到观察记录表格模板（模板中没有包含{{LOOP_GCJL}}的表格）")
        return
    
    if not gcjl_list:
        print("警告: 观察记录数据为空，删除模板行")
        target_table._element.remove(target_table.rows[template_row_idx]._element)
        return
    
    # 字段顺序（17列）- 按照模板中的列顺序
    # 观察时间、培养天数、附件、培养基状态、是否污染、形态是否均一、平均直径、黑心比例、散开比例、细胞数量、是否换液、换液记录、上清吸取量、上清补充量、备注、记录日期、记录人
    fields = [
        "T_GCJL.F_GCSJ_FMT",   # 观察时间
        "T_GCJL.F_PYTS",       # 培养天数
        "T_GCJL.F_FJ",         # 附件（第3列）
        "T_GCJL.F_PYJZT",      # 培养基状态
        "T_GCJL.F_SFWR",       # 是否污染
        "T_GCJL.F_SFJY",       # 形态是否均一
        "T_GCJL.F_PJZJ",       # 平均直径
        "T_GCJL.F_SFHX",       # 黑心比例
        "T_GCJL.F_SFSK",       # 散开比例
        "T_GCJL.F_XBSL",       # 细胞数量
        "T_GCJL.F_SFHY",       # 是否换液
        "T_GCJL.F_HYJL",       # 换液记录
        "T_GCJL.F_SQXQL",      # 上清吸取量
        "T_GCJL.F_SQBCL",      # 上清补充量
        "T_GCJL.F_BZ",         # 备注
        "T_GCJL.F_JLRQ_FMT",   # 记录日期
        "T_GCJL.F_JLR"         # 记录人
    ]
    
    # 附件列的索引（F_FJ是第3列，从0开始计数是第2列）
    fj_col_idx = 2
    
    # 为每条观察记录添加行
    for i, record in enumerate(gcjl_list):
        # 复制模板行
        new_row = target_table.add_row()
        
        # 填充数据
        for col_idx, field in enumerate(fields):
            if col_idx < len(new_row.cells):
                value = record.get(field, "")
                
                # 特殊处理附件列（F_FJ）
                if field == "T_GCJL.F_FJ":
                    print(f"调试: 观察记录第{i+1}行，F_FJ字段值类型: {type(value)}, 长度: {len(value) if isinstance(value, list) else 'N/A'}, 内容: {value}")
                    
                    if isinstance(value, list) and len(value) > 0:
                        # 有附件图片，插入图片
                        cell = new_row.cells[col_idx]
                        # 清空单元格
                        cell.text = ""
                        img_success_count = 0
                        
                        # 插入每张图片
                        for img_idx, img_url in enumerate(value[:3]):  # 最多显示3张
                            try:
                                print(f"调试: 观察记录第{i+1}行尝试处理附件{img_idx+1}: {img_url}")
                                temp_img_path = os.path.join(temp_dir, f"obs_{i}_{img_idx}_{int(time.time())}.jpg")
                                if download_image(img_url, temp_img_path, base_upload_dir):
                                    paragraph = cell.paragraphs[0] if len(cell.paragraphs) > 0 else cell.add_paragraph()
                                    run = paragraph.add_run()
                                    run.add_picture(temp_img_path, width=Inches(1.5))
                                    if img_idx < len(value) - 1:
                                        paragraph.add_run("\n")  # 多张图片换行
                                    img_success_count += 1
                                    print(f"调试: 观察记录第{i+1}行成功插入附件图片{img_idx+1}")
                                else:
                                    print(f"警告: 观察记录第{i+1}行附件{img_idx+1}下载失败")
                            except Exception as e:
                                print(f"警告: 观察记录第{i+1}行附件{img_idx+1}插入失败: {str(e)}")
                                continue
                        
                        # 如果有超过3张，显示提示
                        if len(value) > 3:
                            cell.add_paragraph(f"...还有{len(value)-3}张")
                        
                        # 如果没有成功插入任何图片，显示文本提示
                        if img_success_count == 0:
                            cell.text = record.get("T_GCJL.F_FJ_TEXT", "")
                            print(f"警告: 观察记录第{i+1}行附件全部插入失败，使用文本显示")
                    else:
                        # 没有附件图片，使用F_FJ_TEXT
                        text_value = record.get("T_GCJL.F_FJ_TEXT", "")
                        new_row.cells[col_idx].text = text_value
                        print(f"调试: 观察记录第{i+1}行无附件图片，使用文本: {text_value}")
                else:
                    # 普通字段，直接填充文本
                    text_value = str(value) if value else ""
                    new_row.cells[col_idx].text = text_value
        
        print(f"调试: 已添加观察记录第 {i+1} 行")
    
    # 删除模板行
    target_table._element.remove(target_table.rows[template_row_idx]._element)
    print(f"调试: 已删除观察记录模板行，总共添加了 {len(gcjl_list)} 行数据")


def process_dynamic_table_xhmsy(doc: Document, data: Dict[str, Any]):
    """
    处理消化酶使用记录动态表格
    
    Args:
        doc: Document对象
        data: 报告数据
    """
    pymx = data.get("培养详情", {})
    xhmsy_list = pymx.get("消化酶使用记录", [])
    print(f"调试: 消化酶使用记录数量 = {len(xhmsy_list)}")
    
    # 查找包含{{LOOP_XHMSY}}的表格
    target_table = None
    template_row_idx = None
    
    for table in doc.tables:
        for row_idx, row in enumerate(table.rows):
            for cell in row.cells:
                if "{{LOOP_XHMSY}}" in cell.text:
                    target_table = table
                    template_row_idx = row_idx
                    print(f"调试: 找到消化酶表格，模板行索引 = {row_idx}")
                    break
            if target_table:
                break
        if target_table:
            break
    
    if not target_table or template_row_idx is None:
        print("警告: 未找到消化酶使用记录表格模板（模板中没有包含{{LOOP_XHMSY}}的表格）")
        return
    
    if not xhmsy_list:
        print("警告: 消化酶使用记录数据为空，删除模板行")
        target_table._element.remove(target_table.rows[template_row_idx]._element)
        return
    
    # 字段顺序（8列）
    fields = [
        "F_XHM", "F_YL", "F_ND", "F_XHWD",
        "F_CZFS", "F_LXSL", "F_XHSJ", "F_BZ"
    ]
    
    # 为每条记录添加行
    for i, record in enumerate(xhmsy_list):
        # 复制模板行
        new_row = target_table.add_row()
        
        # 填充数据
        for col_idx, field in enumerate(fields):
            if col_idx < len(new_row.cells):
                value = record.get(field, "")
                new_row.cells[col_idx].text = str(value) if value else ""
        
        print(f"调试: 已添加消化酶记录第 {i+1} 行")
    
    # 删除模板行
    target_table._element.remove(target_table.rows[template_row_idx]._element)
    print(f"调试: 已删除消化酶模板行，总共添加了 {len(xhmsy_list)} 行数据")


def process_dynamic_table_jzj(doc: Document, data: Dict[str, Any]):
    """
    处理基质胶信息动态表格
    
    Args:
        doc: Document对象
        data: 报告数据
    """
    pymx = data.get("培养详情", {})
    jzj_list = pymx.get("基质胶信息", [])
    print(f"调试: 基质胶信息数量 = {len(jzj_list)}")
    
    # 查找包含{{LOOP_JZJ}}的表格
    target_table = None
    template_row_idx = None
    
    for table in doc.tables:
        for row_idx, row in enumerate(table.rows):
            for cell in row.cells:
                if "{{LOOP_JZJ}}" in cell.text:
                    target_table = table
                    template_row_idx = row_idx
                    print(f"调试: 找到基质胶表格，模板行索引 = {row_idx}")
                    break
            if target_table:
                break
        if target_table:
            break
    
    if not target_table or template_row_idx is None:
        print("警告: 未找到基质胶信息表格模板（模板中没有包含{{LOOP_JZJ}}的表格）")
        return
    
    if not jzj_list:
        print("警告: 基质胶信息数据为空，删除模板行")
        target_table._element.remove(target_table.rows[template_row_idx]._element)
        return
    
    # 字段顺序（3列）
    fields = ["F_JZJ", "F_YL", "F_ND"]
    
    # 为每条记录添加行
    for i, record in enumerate(jzj_list):
        # 复制模板行
        new_row = target_table.add_row()
        
        # 填充数据
        for col_idx, field in enumerate(fields):
            if col_idx < len(new_row.cells):
                value = record.get(field, "")
                new_row.cells[col_idx].text = str(value) if value else ""
        
        print(f"调试: 已添加基质胶记录第 {i+1} 行")
    
    # 删除模板行
    target_table._element.remove(target_table.rows[template_row_idx]._element)
    print(f"调试: 已删除基质胶模板行，总共添加了 {len(jzj_list)} 行数据")


def insert_images(doc: Document, data: Dict[str, Any], temp_dir: str, base_upload_dir: str):
    """
    插入所有图片（预处理图片）
    
    Args:
        doc: Document对象
        data: 报告数据
        temp_dir: 临时目录路径
        base_upload_dir: uploads基础目录
    """
    # 处理预处理图片
    yclxx = data.get("预处理信息", {})
    ybycl_url = yclxx.get("T_YCL.F_YBYCL")
    
    print(f"调试: 预处理图片URL = {ybycl_url}")
    
    placeholder = "{{预处理信息.T_YCL.F_YBYCL}}"
    img_inserted = False
    
    if ybycl_url:
        temp_img_path = os.path.join(temp_dir, f"preprocess_{int(time.time())}.jpg")
        
        print(f"调试: 尝试处理预处理图片...")
        if download_image(ybycl_url, temp_img_path, base_upload_dir):
            print(f"调试: 预处理图片处理成功，临时文件: {temp_img_path}")
            # 查找占位符并替换为图片
            found = False
            for paragraph in doc.paragraphs:
                if placeholder in paragraph.text:
                    paragraph.text = paragraph.text.replace(placeholder, "")
                    run = paragraph.add_run()
                    run.add_picture(temp_img_path, width=Inches(3.0))
                    print(f"调试: 已在段落中插入预处理图片")
                    found = True
                    img_inserted = True
            
            # 也需要在表格中查找
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for paragraph in cell.paragraphs:
                            if placeholder in paragraph.text:
                                paragraph.text = paragraph.text.replace(placeholder, "")
                                run = paragraph.add_run()
                                run.add_picture(temp_img_path, width=Inches(2.0))
                                print(f"调试: 已在表格中插入预处理图片")
                                found = True
                                img_inserted = True
            
            if not found:
                print(f"警告: 在文档中未找到预处理图片占位符 {placeholder}")
        else:
            print(f"警告: 预处理图片处理失败，将清除占位符留白")
    else:
        print(f"调试: 预处理图片URL为空，将清除占位符留白")
    
    # 如果图片未插入成功，清除占位符（留白）
    if not img_inserted:
        for paragraph in doc.paragraphs:
            if placeholder in paragraph.text:
                paragraph.text = paragraph.text.replace(placeholder, "")
                print(f"调试: 已清除段落中的预处理图片占位符")
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        if placeholder in paragraph.text:
                            paragraph.text = paragraph.text.replace(placeholder, "")
                            print(f"调试: 已清除表格中的预处理图片占位符")


def generate_culture_detail_pdf(gdid: int, operator: str = None) -> Dict[str, Any]:
    """
    生成培养工单详情PDF（优化版）
    
    Args:
        gdid: 工单ID
        operator: 操作人（可选）
        
    Returns:
        包含文件信息的字典
        
    Raises:
        ValueError: 工单状态不符合或工单不存在
        FileNotFoundError: 模板文件不存在
    """
    # 1. 获取报告数据
    report_data = assemble_culture_detail_report(gdid, allow_unqualified=True)
    
    # 2. 检查模板文件
    base_dir = Path(__file__).resolve().parents[1]  # Bio9_organ/app
    template_path = base_dir / "templates" / "culture_detail_template.docx"
    if not template_path.exists():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")
    
    # 3. 创建临时目录
    temp_dir = base_dir / "temp"
    temp_dir.mkdir(exist_ok=True)
    
    # 获取uploads基础目录
    upload_dir = Path(settings.upload_dir)
    
    # 4. 加载模板
    doc = Document(str(template_path))
    
    # 5. 先插入图片（避免被文本占位符替换掉）
    insert_images(doc, report_data, str(temp_dir), str(upload_dir))
    
    # 6. 替换简单占位符
    replace_placeholders(doc, report_data)
    
    # 7. 处理动态表格
    process_dynamic_table_gcjl(doc, report_data, str(temp_dir), str(upload_dir))
    process_dynamic_table_xhmsy(doc, report_data)
    process_dynamic_table_jzj(doc, report_data)
    
    # 8. 保存为临时docx
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    gdbh = report_data.get("meta", {}).get("gdbh", str(gdid))
    temp_docx_name = f"culture_detail_{gdbh}_{timestamp}.docx"
    temp_docx_path = temp_dir / temp_docx_name
    doc.save(str(temp_docx_path))
    
    # 9. 转换为PDF（Windows Word COM 或 LibreOffice）
    pdf_name = f"culture_detail_{gdbh}_{timestamp}.pdf"
    pdf_dir = upload_dir / "pdf"
    pdf_dir.mkdir(exist_ok=True)
    pdf_path = pdf_dir / pdf_name

    try:
        convert_docx_to_pdf(temp_docx_path, pdf_path)

        relative_path = f"pdf/{pdf_name}"
        file_id = insert_file_record(
            path=relative_path,
            original_name=pdf_name
        )

        try:
            from app.crud.order_pdf_manager import set_generated_pdf
            set_generated_pdf(gdid, file_id)
            print(f"调试: 已保存PDF文件ID {file_id} 到工单 {gdid}")
        except Exception as e:
            print(f"警告: 保存PDF文件ID到工单失败: {e}")

        try:
            os.remove(str(temp_docx_path))
            for f in temp_dir.glob("preprocess_*.jpg"):
                os.remove(str(f))
            for f in temp_dir.glob("obs_*.jpg"):
                os.remove(str(f))
        except Exception:
            pass

        return {
            "file_id": file_id,
            "filename": pdf_name,
            "file_path": relative_path,
            "file_url": f"/{relative_path}",
            "gdid": gdid,
            "gdbh": gdbh,
            "format": "pdf",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        print(f"PDF转换失败: {str(e)}，返回docx文件")
        doc_dir = upload_dir / "docs"
        doc_dir.mkdir(exist_ok=True)
        final_docx_path = doc_dir / temp_docx_name

        shutil.move(str(temp_docx_path), str(final_docx_path))

        relative_path = f"docs/{temp_docx_name}"
        file_id = insert_file_record(
            path=relative_path,
            original_name=temp_docx_name
        )

        return {
            "file_id": file_id,
            "filename": temp_docx_name,
            "file_path": relative_path,
            "file_url": f"/{relative_path}",
            "gdid": gdid,
            "gdbh": gdbh,
            "format": "docx",
            "note": f"PDF转换失败({str(e)})，生成的是Word文档",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

