"""
类器官放行检测PDF生成模块

职责：基于Word模板生成放行检测PDF

逻辑：完全复用 culture_detail_pdf.py 的实现
差异：
1. 使用不同的模板 (release_inspection_template.docx)
2. 使用不同的数据源 (assemble_release_inspection_report)
3. 保存到不同的字段 (T_ZKJL.F_PDF_FXJC)
"""
from typing import Dict, Any
from pathlib import Path
from datetime import datetime
from docx import Document

from app.core.settings import settings
from app.core.docx_pdf import convert_docx_to_pdf
from app.crud.file_center import insert_file_record
from app.crud.release_inspection_report import assemble_release_inspection_report
from app.crud.culture_detail_report import check_report_conditions

# 复用 culture_detail_pdf.py 的所有辅助函数
from app.crud.culture_detail_pdf import (
    replace_placeholders,
    process_dynamic_table_gcjl,
    process_dynamic_table_xhmsy,
    process_dynamic_table_jzj,
    insert_images
)


def generate_release_inspection_pdf(gdid: int, operator: str = None) -> Dict[str, Any]:
    """
    生成放行检测PDF
    
    Args:
        gdid: 工单ID
        operator: 操作人（可选）
        
    Returns:
        {
            "file_id": 文件ID,
            "file_path": 文件访问路径,
            "file_name": 文件名
        }
        
    Raises:
        ValueError: 如果工单不存在或质控状态不符合
        FileNotFoundError: 如果模板文件不存在
    """
    # 1. 检查条件
    allowed, status_msg = check_report_conditions(gdid)
    if not allowed:
        raise ValueError(status_msg)
    
    print(f"开始生成放行检测PDF: 工单ID={gdid}, {status_msg}")
    
    # 2. 获取数据
    data = assemble_release_inspection_report(gdid)
    
    # 3. 加载模板
    base_dir = Path(__file__).resolve().parent.parent
    template_path = base_dir / "templates" / "release_inspection_template.docx"
    
    if not template_path.exists():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")
    
    print(f"加载模板: {template_path}")
    doc = Document(str(template_path))
    
    # 4. 处理所有动态表格（观察记录、消化酶、基质胶）
    # 必须在替换占位符之前处理
    
    # 创建临时目录用于存储图片
    temp_dir = base_dir / "temp"
    temp_dir.mkdir(exist_ok=True)
    
    # 获取上传目录的绝对路径
    if settings.upload_dir.startswith('/'):
        base_upload_dir = settings.upload_dir
    else:
        base_upload_dir = str(Path(settings.upload_dir).resolve())
    
    # 4.1 处理观察记录动态表格
    gcjl_list = data.get("观察记录", [])
    if gcjl_list:
        print(f"处理观察记录动态表格，共 {len(gcjl_list)} 条记录")
        process_dynamic_table_gcjl(doc, data, str(temp_dir), base_upload_dir)
    
    # 4.2 处理消化酶使用记录动态表格
    # 从培养详情中获取消化酶记录
    pymx = data.get("培养详情", {})
    xhmsy_list = pymx.get("消化酶使用记录", [])
    if xhmsy_list:
        print(f"处理消化酶使用记录动态表格，共 {len(xhmsy_list)} 条记录")
        process_dynamic_table_xhmsy(doc, data)
    
    # 4.3 处理基质胶信息动态表格
    jzj_list = pymx.get("基质胶信息", [])
    if jzj_list:
        print(f"处理基质胶信息动态表格，共 {len(jzj_list)} 条记录")
        process_dynamic_table_jzj(doc, data)
    
    # 注意：回收记录是字典，不是列表，不需要动态表格处理
    # 回收记录的字段会在 replace_placeholders 中处理
    
    # 6. 插入图片（预处理图片、质控图片）
    # 必须在替换占位符之前处理
    temp_dir = base_dir / "temp"
    temp_dir.mkdir(exist_ok=True)
    
    # 获取上传目录的绝对路径
    if settings.upload_dir.startswith('/'):
        base_upload_dir = settings.upload_dir
    else:
        base_upload_dir = str(Path(settings.upload_dir).resolve())
    
    # 6.1 单独处理预处理图片（支持多种占位符变体）
    print("处理预处理图片（支持多种占位符格式）...")
    yclxx = data.get("预处理信息", {})
    ybycl_url = yclxx.get("T_YCL.F_YBYCL")
    
    if ybycl_url:
        from docx.shared import Inches
        import time
        from app.crud.culture_detail_pdf import download_image
        
        # 生成所有可能的预处理图片占位符变体
        preprocess_img_variants = [
            # 完整路径，各种字段名
            "{{预处理信息.T_YCL.F_YBYCL}}",
            "{{ 预处理信息.T_YCL.F_YBYCL }}",
            "{{预处理信息.T_YCL.YBYCL}}",
            "{{ 预处理信息.T_YCL.YBYCL }}",
            "{{预处理信息. T_YCL.YBYCL}}",
            "{{ 预处理信息. T_YCL.YBYCL }}",
            "{{预处理信息.T_YCL.YBYCL }}",
            "{{ 预处理信息.T_YCL.YBYCL}}",
            # 简化路径
            "{{预处理信息.YBYCL}}",
            "{{ 预处理信息.YBYCL }}",
            "{{预处理信息. YBYCL}}",
            "{{ 预处理信息. YBYCL }}",
            "{{预处理信息.F_YBYCL}}",
            "{{ 预处理信息.F_YBYCL }}",
            "{{预处理信息. F_YBYCL}}",
            "{{ 预处理信息. F_YBYCL }}",
            # 无前缀
            "{{YBYCL}}",
            "{{ YBYCL }}",
            "{{F_YBYCL}}",
            "{{ F_YBYCL }}",
        ]
        
        temp_img_path = str(Path(temp_dir) / f"preprocess_{int(time.time())}.jpg")
        if download_image(ybycl_url, temp_img_path, base_upload_dir):
            print(f"预处理图片下载成功: {temp_img_path}")
            inserted_count = 0
            
            # 在表格中查找并插入图片
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for paragraph in cell.paragraphs:
                            # 尝试所有占位符变体
                            for placeholder in preprocess_img_variants:
                                if placeholder in paragraph.text:
                                    paragraph.text = paragraph.text.replace(placeholder, "")
                                    run = paragraph.add_run()
                                    run.add_picture(temp_img_path, width=Inches(2.0))
                                    inserted_count += 1
                                    print(f"在表格中插入预处理图片，占位符: {placeholder}")
                                    break  # 找到一个匹配就跳出
            
            # 也在段落中查找
            for paragraph in doc.paragraphs:
                for placeholder in preprocess_img_variants:
                    if placeholder in paragraph.text:
                        paragraph.text = paragraph.text.replace(placeholder, "")
                        run = paragraph.add_run()
                        run.add_picture(temp_img_path, width=Inches(3.0))
                        inserted_count += 1
                        print(f"在段落中插入预处理图片，占位符: {placeholder}")
                        break
            
            print(f"预处理图片插入完成，共插入 {inserted_count} 次")
        else:
            print(f"警告: 预处理图片下载失败，URL: {ybycl_url}")
    else:
        print("预处理图片URL为空，跳过")
    
    # 6.2 调用原有的insert_images（处理其他图片，如果有的话）
    insert_images(doc, data, str(temp_dir), base_upload_dir)
    
    # 7. 替换其他占位符（无空格格式）
    print(f"开始替换占位符（无空格格式）...")
    replace_placeholders(doc, data)
    
    # 7.5 手动处理所有字段的带空格格式 + 字段别名映射
    print(f"处理带空格格式的占位符和字段别名...")
    from app.crud.culture_detail_pdf import replace_text_in_paragraph
    
    space_placeholder_map = {}
    
    def add_all_variants(section_name, key, value):
        """为每个字段生成所有可能的占位符变体"""
        val_str = str(value) if value else ""
        patterns = [
            f"{{{{{section_name}.{key}}}}}",              # 无空格
            f"{{{{ {section_name}.{key} }}}}",            # 外围空格
            f"{{{{{section_name}. {key}}}}}",             # 点后空格
            f"{{{{ {section_name}. {key} }}}}",           # 外围+点后空格
            f"{{{{{section_name}.{key} }}}}",             # 字段名后空格
            f"{{{{ {section_name}.{key}}}}}",             # 前面空格
            f"{{{{{section_name} .{key}}}}}",             # 点前空格
            f"{{{{ {section_name} .{key} }}}}",           # 外围+点前空格
            f"{{{{{section_name}. {key} }}}}",            # 点后+字段后空格
            f"{{{{ {section_name}. {key}}}}}",            # 外围前+点后空格
        ]
        for p in patterns:
            space_placeholder_map[p] = val_str
    
    # meta字段
    meta = data.get("meta", {})
    for key, value in meta.items():
        add_all_variants("meta", key, value)
    
    # 工单基本信息 + 字段别名
    gdxx = data.get("工单基本信息", {})
    for key, value in gdxx.items():
        add_all_variants("工单基本信息", key, value)
        # 简短格式（无section前缀）
        val_str = str(value) if value else ""
        space_placeholder_map[f"{{{{{key}}}}}"] = val_str
        space_placeholder_map[f"{{{{ {key} }}}}"] = val_str
        space_placeholder_map[f"{{{{{key} }}}}"] = val_str
        space_placeholder_map[f"{{{{ {key}}}}}"] = val_str
    
    # 培养人字段别名（模板用F_PYR，数据是F_SQR）
    if "F_SQR" in gdxx:
        pyr_value = str(gdxx["F_SQR"])
        add_all_variants("工单基本信息", "F_PYR", pyr_value)
        space_placeholder_map[f"{{{{F_PYR}}}}"] = pyr_value
        space_placeholder_map[f"{{{{ F_PYR }}}}"] = pyr_value
        space_placeholder_map[f"{{{{F_PYR }}}}"] = pyr_value
    
    # 样本信息
    ybxx = data.get("样本信息", {})
    for key, value in ybxx.items():
        add_all_variants("样本信息", key, value)
    
    # 预处理信息（跳过图片字段，图片已在步骤6.1单独处理）
    yclxx = data.get("预处理信息", {})
    for key, value in yclxx.items():
        # 跳过所有图片相关字段，避免与图片插入冲突
        if key not in ["T_YCL.F_YBYCL", "YBYCL", "F_YBYCL"]:
            add_all_variants("预处理信息", key, value)
    
    # 培养详情（嵌套结构）
    pymx = data.get("培养详情", {})
    for section_name, section_data in pymx.items():
        if isinstance(section_data, dict):
            for key, value in section_data.items():
                add_all_variants(f"培养详情.{section_name}", key, value)
                add_all_variants(section_name, key, value)  # 简短格式
    
    # 回收记录
    hsjl = data.get("回收记录", {})
    for key, value in hsjl.items():
        add_all_variants("回收记录", key, value)
    
    # 替换段落中的占位符
    for paragraph in doc.paragraphs:
        for placeholder, value in space_placeholder_map.items():
            if placeholder in paragraph.text:
                replace_text_in_paragraph(paragraph, placeholder, value)
    
    # 替换表格中的占位符
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    for placeholder, value in space_placeholder_map.items():
                        if placeholder in paragraph.text:
                            replace_text_in_paragraph(paragraph, placeholder, value)
    
    print(f"带空格格式占位符处理完成，共 {len(space_placeholder_map)} 个映射")
    
    # 8. 手动处理质控记录（文本字段+图片插入）
    zkjl = data.get("质控记录", {})
    if zkjl:
        print(f"处理质控记录占位符，共 {len(zkjl)} 个字段")
        print(f"质控记录数据样本: F_PYTS={zkjl.get('F_PYTS')}, F_XT={zkjl.get('F_XT')}, F_XBHL={zkjl.get('F_XBHL')}")
        
        # 处理字段别名
        if "F_ZYT" in zkjl and zkjl["F_ZYT"]:
            zkjl["F_ZYTJCJG"] = zkjl["F_ZYT"]
        
        # 8.1 先处理质控图片插入（图片URL字段）
        from docx.shared import Inches
        import time
        
        img_url_fields = [
            ("F_IMG_D1M4_URL", 2.5),
            ("F_IMG_D1M20_URL", 2.5),
            ("F_IMG_D3M4_URL", 2.5),
            ("F_IMG_D3M20_URL", 2.5),
            ("F_IMG_D7M4_URL", 2.5),
            ("F_IMG_D7M20_URL", 2.5),
        ]
        
        from app.crud.culture_detail_pdf import download_image
        inserted_count = 0
        for field_name, width in img_url_fields:
            img_url = zkjl.get(field_name)
            if img_url:
                # 生成所有可能的占位符变体
                placeholder_variants = [
                    f"{{{{{field_name}}}}}",                # 无空格
                    f"{{{{ {field_name} }}}}",              # 外围空格
                    f"{{{{ {field_name}}}}}",               # 前面空格
                    f"{{{{{field_name} }}}}",               # 后面空格
                    f"{{{{质控记录.{field_name}}}}}",       # 带前缀无空格
                    f"{{{{ 质控记录.{field_name} }}}}",     # 带前缀外围空格
                    f"{{{{质控记录. {field_name}}}}}",      # 带前缀点后空格
                    f"{{{{ 质控记录. {field_name} }}}}",    # 带前缀点后+外围空格
                ]
                
                temp_img_path = str(Path(temp_dir) / f"qc_{field_name}_{int(time.time())}.jpg")
                if download_image(img_url, temp_img_path, base_upload_dir):
                    # 在表格中查找并插入图片
                    for table in doc.tables:
                        for row in table.rows:
                            for cell in row.cells:
                                for paragraph in cell.paragraphs:
                                    # 尝试所有占位符变体
                                    for placeholder in placeholder_variants:
                                        if placeholder in paragraph.text:
                                            paragraph.text = paragraph.text.replace(placeholder, "")
                                            run = paragraph.add_run()
                                            run.add_picture(temp_img_path, width=Inches(width))
                                            inserted_count += 1
                                            break  # 找到一个匹配就跳出
        print(f"插入了 {inserted_count} 张质控图片")
        
        # 8.2 然后处理文本字段替换
        zkjl_placeholder_map = {}
        for key, value in zkjl.items():
            # 跳过图片URL字段（已处理）
            if key.endswith("_URL"):
                continue
            str_value = str(value) if value is not None else ""
            
            # 生成所有可能的占位符变体（与上面的add_all_variants逻辑一致）
            section_name = "质控记录"
            patterns = [
                f"{{{{{section_name}.{key}}}}}",              # 无空格
                f"{{{{ {section_name}.{key} }}}}",            # 外围空格
                f"{{{{{section_name}. {key}}}}}",             # 点后空格
                f"{{{{ {section_name}. {key} }}}}",           # 外围+点后空格
                f"{{{{{section_name}.{key} }}}}",             # 字段名后空格
                f"{{{{ {section_name}.{key}}}}}",             # 前面空格
                f"{{{{{section_name} .{key}}}}}",             # 点前空格
                f"{{{{ {section_name} .{key} }}}}",           # 外围+点前空格
                f"{{{{{section_name}. {key} }}}}",            # 点后+字段后空格
                f"{{{{ {section_name}. {key}}}}}",            # 外围前+点后空格
            ]
            for pattern in patterns:
                zkjl_placeholder_map[pattern] = str_value
            
            # 对于所有质控记录字段，也添加无前缀的简短版本
            simple_variants = [
                f"{{{{{key}}}}}",                # 无空格
                f"{{{{ {key} }}}}",              # 外围空格
                f"{{{{ {key}}}}}",               # 前面空格
                f"{{{{{key} }}}}",               # 后面空格
            ]
            for variant in simple_variants:
                zkjl_placeholder_map[variant] = str_value
        
        # 导入辅助函数
        from app.crud.culture_detail_pdf import replace_text_in_paragraph
        
        # 调试：输出占位符总数和示例
        print(f"生成了 {len(zkjl_placeholder_map)} 个占位符映射")
        print(f"质控记录占位符示例（F_PYTS字段的所有格式）：")
        pyts_placeholders = {k: v for k, v in zkjl_placeholder_map.items() if 'F_PYTS' in k}
        for key, value in list(pyts_placeholders.items())[:8]:
            print(f"  '{key}' => '{value}'")
        
        # 替换段落中的占位符
        replaced_count = 0
        for paragraph in doc.paragraphs:
            for placeholder, value in zkjl_placeholder_map.items():
                if placeholder in paragraph.text:
                    replace_text_in_paragraph(paragraph, placeholder, value)
                    replaced_count += 1
        print(f"段落中替换了 {replaced_count} 个占位符")
        
        # 替换表格中的占位符
        table_replaced_count = 0
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    # 遍历单元格中的所有段落
                    for paragraph in cell.paragraphs:
                        for placeholder, value in zkjl_placeholder_map.items():
                            if placeholder in paragraph.text:
                                replace_text_in_paragraph(paragraph, placeholder, value)
                                table_replaced_count += 1
        print(f"表格中替换了 {table_replaced_count} 个占位符")
    
    # 9. 创建临时目录并保存临时DOCX文件
    temp_dir = base_dir / "temp"
    temp_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    gdbh = data.get("工单基本信息", {}).get("F_GDBH", str(gdid))
    temp_docx_name = f"release_inspection_{gdbh}_{timestamp}.docx"
    temp_docx_path = temp_dir / temp_docx_name
    
    print(f"保存临时DOCX: {temp_docx_path}")
    doc.save(str(temp_docx_path))
    
    # 9. 转换为PDF
    pdf_name = f"release_inspection_{gdbh}_{timestamp}.pdf"
    pdf_path = temp_dir / pdf_name
    
    print(f"开始PDF转换: {temp_docx_path} -> {pdf_path}")
    
    try:
        convert_docx_to_pdf(temp_docx_path, pdf_path)
        print(f"PDF转换成功: {pdf_path}")
    except Exception as e:
        print(f"警告: PDF转换失败，将返回DOCX文件: {e}")
        pdf_path = temp_docx_path
        pdf_name = temp_docx_name
    
    # 10. 移动到正式目录
    pdf_dir = Path(settings.upload_dir) / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    
    final_pdf_path = pdf_dir / pdf_name
    
    # 如果目标文件已存在，先删除
    if final_pdf_path.exists():
        final_pdf_path.unlink()
    
    # 移动文件
    import shutil
    shutil.move(str(pdf_path), str(final_pdf_path))
    
    print(f"PDF已移动到: {final_pdf_path}")
    
    # 11. 注册到文件中心
    relative_path = f"pdf/{pdf_name}"
    file_id = insert_file_record(relative_path, pdf_name)
    
    if not file_id:
        raise Exception("文件注册失败")
    
    print(f"PDF已注册: ID={file_id}, Path={relative_path}")
    
    # 12. 保存到 T_ZKJL.F_PDF_FXJC 字段
    try:
        from app.crud.qc_record import update_release_inspection_pdf
        update_release_inspection_pdf(gdid, file_id)
        print(f"已更新 T_ZKJL.F_PDF_FXJC: GDID={gdid}, PDF_ID={file_id}")
    except Exception as e:
        print(f"警告: 更新 T_ZKJL.F_PDF_FXJC 失败: {e}")
    
    # 13. 清理临时文件
    try:
        temp_docx_path.unlink()
    except Exception as e:
        print(f"警告: 清理临时DOCX失败: {e}")
    
    # 14. 返回结果
    result = {
        "file_id": file_id,
        "file_path": f"/{relative_path}",
        "file_name": pdf_name
    }
    
    # 如果返回的是DOCX（PDF转换失败），添加警告
    if pdf_name.endswith('.docx'):
        result["warning"] = "PDF转换失败，返回DOCX格式"
    
    return result

