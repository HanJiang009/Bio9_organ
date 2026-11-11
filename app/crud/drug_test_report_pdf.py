"""
药物试验报告PDF生成模块

功能：
1. JSON字段解析
2. Word模板占位符替换
3. 动态表格生成
4. 图片插入
5. PDF转换
"""

import json
import os
import tempfile
import shutil
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from pathlib import Path

# 导入文件处理和数据组装函数
from app.crud.file_center import get_file_by_id
from app.crud.drug_test_report import assemble_drug_test_report
from app.core.docx_pdf import convert_docx_to_pdf


# ==================== 0. 配置和辅助函数 ====================

def get_template_path() -> str:
    """
    获取Word模板文件的路径
    
    Returns:
        模板文件的绝对路径
    """
    base_dir = Path(__file__).parent.parent
    template_path = base_dir / "templates" / "drug_test_report_template.docx"
    return str(template_path)


def get_base_upload_dir() -> str:
    """
    获取uploads基础目录
    
    Returns:
        uploads目录的绝对路径
    """
    base_dir = Path(__file__).parent.parent.parent
    return str(base_dir / "uploads")


def get_temp_reports_dir() -> str:
    """
    获取药物试验报告存储目录
    
    Returns:
        药物试验报告目录的绝对路径
    """
    base_dir = Path(__file__).parent.parent.parent
    report_dir = base_dir / "uploads" / "drug_test_report"
    report_dir.mkdir(parents=True, exist_ok=True)
    return str(report_dir)


# ==================== 1. JSON解析函数 ====================

def parse_json_field(json_str: Optional[str]) -> Any:
    """
    安全解析JSON字符串
    
    Args:
        json_str: JSON字符串
        
    Returns:
        解析后的Python对象，如果解析失败返回None
    """
    if not json_str:
        return None
    
    if isinstance(json_str, (dict, list)):
        return json_str
    
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None


def parse_2d_array(json_str: Optional[str]) -> List[List[Any]]:
    """
    解析二维数组JSON字段（如F_XBHL_PJ, F_XBHL等）
    
    Args:
        json_str: JSON字符串
        
    Returns:
        二维数组
    """
    data = parse_json_field(json_str)
    if isinstance(data, list):
        return data
    return []


def parse_object_array(json_str: Optional[str]) -> List[Dict[str, Any]]:
    """
    解析对象数组JSON字段（如F_IC50, F_XTXBH等）
    
    Args:
        json_str: JSON字符串
        
    Returns:
        字典列表
    """
    data = parse_json_field(json_str)
    if isinstance(data, list):
        return data
    return []


def parse_nested_object(json_str: Optional[str]) -> Dict[str, Any]:
    """
    解析嵌套对象JSON字段（如F_SYJL）
    
    Args:
        json_str: JSON字符串
        
    Returns:
        字典对象
    """
    data = parse_json_field(json_str)
    if isinstance(data, dict):
        return data
    return {}


# ==================== 2. 文件处理函数 ====================

def normalize_file_id_list(value: Any) -> List[int]:
    """将 F_FJ 规范为文件 ID 列表（兼容单 int、list、已转换的 URL 对象）。"""
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        try:
            return [int(value)]
        except (TypeError, ValueError):
            return []
    if isinstance(value, dict):
        fid = value.get("id") or value.get("F_ID") or value.get("file_id")
        if fid is None:
            return []
        try:
            return [int(fid)]
        except (TypeError, ValueError):
            return []
    if isinstance(value, list):
        ids: List[int] = []
        for item in value:
            ids.extend(normalize_file_id_list(item))
        return ids
    return []


def download_file_to_temp(file_id: int, temp_dir: str, base_upload_dir: str) -> Optional[str]:
    """
    将文件ID对应的文件复制到临时目录
    
    Args:
        file_id: 文件ID
        temp_dir: 临时目录路径
        base_upload_dir: uploads基础目录
        
    Returns:
        临时文件的本地路径，如果失败返回None
    """
    try:
        file_info = get_file_by_id(file_id)
        if not file_info or not file_info.get("F_PATH"):
            print(f"警告: 文件ID {file_id} 未找到或无路径信息")
            return None
        
        relative_path = file_info["F_PATH"]
        # 移除开头的斜杠（如果有）
        if relative_path.startswith("/"):
            relative_path = relative_path[1:]
        
        source_path = os.path.join(base_upload_dir, relative_path)
        
        if not os.path.exists(source_path):
            print(f"警告: 文件不存在: {source_path}")
            return None
        
        # 生成临时文件名（保留扩展名）
        file_ext = os.path.splitext(relative_path)[1]
        temp_filename = f"file_{file_id}{file_ext}"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        # 复制文件
        shutil.copy2(source_path, temp_path)
        return temp_path
        
    except Exception as e:
        print(f"错误: 下载文件 {file_id} 失败: {e}")
        return None


# ==================== 3. 简单占位符替换函数 ====================

def replace_simple_placeholders(doc: Document, data: Dict[str, Any]):
    """
    替换Word文档中的简单占位符（如{{快捷信息.实验名称}}）
    
    Args:
        doc: Document对象
        data: 报告数据
    """
    # 构建占位符映射
    placeholders = {}
    
    # 快捷信息
    quick_info = data.get("快捷信息", {})
    for key, value in quick_info.items():
        placeholders[f"快捷信息.{key}"] = str(value) if value is not None else ""
    
    # 基础信息
    placeholders["报告类型"] = data.get("报告类型", "")
    placeholders["生成时间"] = data.get("生成时间", "")
    
    # 实验计划
    plan = data.get("实验计划", {})
    for key in ["F_SYBH", "F_SYMC", "F_SYJHYMD", "F_XBSL", "F_JZJND", "F_JZJTJ", "F_KBLX", "F_ZT", "F_CJSJ"]:
        value = plan.get(key, "")
        placeholders[f"实验计划.{key}"] = str(value) if value is not None else ""
    
    # 培养工单
    order = data.get("培养工单", {})
    for key in ["F_GDBH", "F_ZZID", "F_XM", "F_BRBH", "F_BBLX", "F_DC", "F_ZZBW", "F_QYFS", "F_YBLY", "F_PYSJ"]:
        value = order.get(key, "")
        placeholders[f"培养工单.{key}"] = str(value) if value is not None else ""
    
    # 质控记录
    qc = data.get("质控记录", {})
    if qc:
        for key in ["F_PYTS", "F_XBSL", "F_XBHL", "F_XT", "F_SFWR", "F_ZYT", "F_JSSJ", "F_KSCLSJ", "F_YBYCLQK", "F_QKMS"]:
            value = qc.get(key, "")
            placeholders[f"质控记录.{key}"] = str(value) if value is not None else ""
        
        # 质检图片URL（这些字段已经是完整URL，直接使用）
        for img_key in ["F_YBYCL_URL", "F_IMG_D1M4_URL", "F_IMG_D1M20_URL", 
                        "F_IMG_D3M4_URL", "F_IMG_D3M20_URL", "F_IMG_D7M4_URL", "F_IMG_D7M20_URL"]:
            placeholders[f"质控记录.{img_key}"] = qc.get(img_key, "")
    
    # 实验记录基本信息
    record = data.get("实验记录", {})
    if record:
        placeholders["实验记录.F_JLR"] = record.get("F_JLR", "")
        placeholders["实验记录.F_JLRQ"] = str(record.get("F_JLRQ", ""))
    
    # 遍历文档所有段落进行替换
    for paragraph in doc.paragraphs:
        for placeholder, value in placeholders.items():
            if f"{{{{{placeholder}}}}}" in paragraph.text:
                paragraph.text = paragraph.text.replace(f"{{{{{placeholder}}}}}", value)
    
    # 遍历文档所有表格进行替换
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    for placeholder, value in placeholders.items():
                        if f"{{{{{placeholder}}}}}" in paragraph.text:
                            paragraph.text = paragraph.text.replace(f"{{{{{placeholder}}}}}", value)


# ==================== 4. 循环表格处理函数 ====================

def process_loop_table_zbxx(doc: Document, data: Dict[str, Any]):
    """
    处理组别信息循环表格（{{LOOP_ZBXX}}）
    
    Args:
        doc: Document对象
        data: 报告数据
    """
    plan = data.get("实验计划", {})
    zbxx_list = plan.get("ZBXX", [])
    
    # 查找包含{{LOOP_ZBXX}}的表格
    target_table = None
    template_row_idx = None
    
    for table in doc.tables:
        for row_idx, row in enumerate(table.rows):
            for cell in row.cells:
                if "{{LOOP_ZBXX}}" in cell.text:
                    target_table = table
                    template_row_idx = row_idx
                    break
            if target_table:
                break
        if target_table:
            break
    
    if not target_table or template_row_idx is None:
        print("警告: 未找到组别信息表格模板")
        return
    
    if not zbxx_list:
        print("警告: 组别信息为空，删除模板行")
        target_table._element.remove(target_table.rows[template_row_idx]._element)
        return
    
    # 为每个组别添加行
    for idx, item in enumerate(zbxx_list):
        new_row = target_table.add_row()
        
        # 序号
        new_row.cells[0].text = str(idx + 1)
        
        # 组别名称
        new_row.cells[1].text = str(item.get("F_ZBMC", ""))
        
        # 组别类型
        new_row.cells[2].text = str(item.get("F_ZBLX", ""))
        
        # 复孔数
        new_row.cells[3].text = str(item.get("F_FKSSZ", ""))
        
        # 浓度梯度（数组转为字符串）
        nd_list = item.get("F_ND", [])
        nd_str = ", ".join(str(nd) for nd in nd_list) if nd_list else ""
        new_row.cells[4].text = nd_str
        
        # 用药信息（解析F_YY数组）
        yy_list = item.get("F_YY", [])
        yy_texts = []
        for yy in yy_list:
            pch = yy.get("F_PCH", "")
            yl = yy.get("F_YL", "")
            yy_texts.append(f"{pch}({yl})")
        new_row.cells[5].text = ", ".join(yy_texts)
    
    # 删除模板行
    target_table._element.remove(target_table.rows[template_row_idx]._element)


def process_loop_table_kwxx(doc: Document, data: Dict[str, Any]):
    """
    处理孔位信息循环表格（{{LOOP_KWXX}}）
    
    Args:
        doc: Document对象
        data: 报告数据
    """
    plan = data.get("实验计划", {})
    kwxx_list = plan.get("KWXX", [])
    
    # 查找包含{{LOOP_KWXX}}的表格
    target_table = None
    template_row_idx = None
    
    for table in doc.tables:
        for row_idx, row in enumerate(table.rows):
            for cell in row.cells:
                if "{{LOOP_KWXX}}" in cell.text:
                    target_table = table
                    template_row_idx = row_idx
                    break
            if target_table:
                break
        if target_table:
            break
    
    if not target_table or template_row_idx is None:
        print("警告: 未找到孔位信息表格模板")
        return
    
    if not kwxx_list:
        print("警告: 孔位信息为空，删除模板行")
        target_table._element.remove(target_table.rows[template_row_idx]._element)
        return
    
    # 为每个孔位添加行
    for item in kwxx_list:
        new_row = target_table.add_row()
        
        new_row.cells[0].text = str(item.get("F_WZ", ""))
        new_row.cells[1].text = str(item.get("F_ZBMC", ""))
        new_row.cells[2].text = str(item.get("F_ND", ""))
        new_row.cells[3].text = str(item.get("F_JYTJ", ""))
        new_row.cells[4].text = str(item.get("F_XSFA", ""))
    
    # 删除模板行
    target_table._element.remove(target_table.rows[template_row_idx]._element)


def process_loop_table_gcjl(doc: Document, data: Dict[str, Any]):
    """
    处理观察记录循环表格（{{LOOP_GCJL}}）
    
    Args:
        doc: Document对象
        data: 报告数据
    """
    gcjl_list = data.get("观察记录", [])
    
    # 查找包含{{LOOP_GCJL}}的表格
    target_table = None
    template_row_idx = None
    
    for table in doc.tables:
        for row_idx, row in enumerate(table.rows):
            for cell in row.cells:
                if "{{LOOP_GCJL}}" in cell.text:
                    target_table = table
                    template_row_idx = row_idx
                    break
            if target_table:
                break
        if target_table:
            break
    
    if not target_table or template_row_idx is None:
        print("警告: 未找到观察记录表格模板")
        return
    
    if not gcjl_list:
        print("警告: 观察记录为空，删除模板行")
        target_table._element.remove(target_table.rows[template_row_idx]._element)
        return
    
    # 为每条记录添加行
    for item in gcjl_list:
        new_row = target_table.add_row()
        
        new_row.cells[0].text = str(item.get("F_GCSJ", ""))
        new_row.cells[1].text = str(item.get("F_PYTS", ""))
        _pyjzt = item.get("F_PYJZT", "")
        if isinstance(_pyjzt, list):
            _pyjzt = ", ".join(str(x) for x in _pyjzt if x)
        new_row.cells[2].text = str(_pyjzt or "")
        new_row.cells[3].text = "是" if item.get("F_SFWR") else "否"
        new_row.cells[4].text = str(item.get("F_PJZJ", ""))
        new_row.cells[5].text = str(item.get("F_SFHX", ""))
        new_row.cells[6].text = "是" if item.get("F_SFHY") else "否"
        new_row.cells[7].text = str(item.get("F_HYJL", ""))
        new_row.cells[8].text = str(item.get("F_XBSL", ""))
        new_row.cells[9].text = str(item.get("F_JLR", ""))
    
    # 删除模板行
    target_table._element.remove(target_table.rows[template_row_idx]._element)


# ==================== 5. 动态JSON表格处理函数 ====================

def process_dynamic_table_2d_array(doc: Document, marker: str, json_str: Optional[str], title: str = ""):
    """
    处理二维数组动态表格（如F_XBHL_PJ, F_XBHL等）
    
    Args:
        doc: Document对象
        marker: 占位符标记（如"{{DYNAMIC_TABLE_XBHL_PJ}}"）
        json_str: JSON字符串
        title: 表格标题（可选）
    """
    # 解析二维数组
    matrix = parse_2d_array(json_str)
    
    if not matrix:
        print(f"警告: {marker} 数据为空")
        # 查找并删除占位符
        for paragraph in doc.paragraphs:
            if marker in paragraph.text:
                paragraph.text = paragraph.text.replace(marker, "[数据为空]")
        return
    
    # 查找占位符位置
    target_paragraph = None
    for paragraph in doc.paragraphs:
        if marker in paragraph.text:
            target_paragraph = paragraph
            break
    
    if not target_paragraph:
        print(f"警告: 未找到占位符 {marker}")
        return
    
    # 在占位符位置插入表格
    # 获取段落位置
    p_element = target_paragraph._element
    parent = p_element.getparent()
    p_idx = parent.index(p_element)
    
    # 创建表格
    rows_count = len(matrix)
    cols_count = max(len(row) for row in matrix) if matrix else 0
    
    table = doc.add_table(rows=rows_count, cols=cols_count)
    table.style = 'Light Grid Accent 1'
    
    # 填充数据
    for row_idx, row_data in enumerate(matrix):
        for col_idx, cell_value in enumerate(row_data):
            if col_idx < len(table.rows[row_idx].cells):
                cell_text = str(cell_value) if cell_value is not None else ""
                table.rows[row_idx].cells[col_idx].text = cell_text
    
    # 将表格插入到占位符位置之后
    table_element = table._element
    parent.insert(p_idx + 1, table_element)
    
    # 删除占位符
    target_paragraph.text = target_paragraph.text.replace(marker, "")


def process_dynamic_table_xtxbh(doc: Document, data: Dict[str, Any], temp_dir: str, base_upload_dir: str):
    """
    处理形态学变化图文混排表格（{{DYNAMIC_TABLE_XTXBH}}）
    
    Args:
        doc: Document对象
        data: 报告数据
        temp_dir: 临时目录
        base_upload_dir: uploads基础目录
    """
    marker = "{{DYNAMIC_TABLE_XTXBH}}"
    record = data.get("实验记录", {})
    if not record:
        return
    
    xtxbh_list = parse_object_array(record.get("F_XTXBH"))
    
    if not xtxbh_list:
        print(f"警告: {marker} 数据为空")
        for paragraph in doc.paragraphs:
            if marker in paragraph.text:
                paragraph.text = paragraph.text.replace(marker, "[数据为空]")
        return
    
    # 查找占位符位置
    target_paragraph = None
    for paragraph in doc.paragraphs:
        if marker in paragraph.text:
            target_paragraph = paragraph
            break
    
    if not target_paragraph:
        print(f"警告: 未找到占位符 {marker}")
        return
    
    # 获取段落位置
    p_element = target_paragraph._element
    parent = p_element.getparent()
    p_idx = parent.index(p_element)
    
    # 创建横向表格（每个组别一列）
    table = doc.add_table(rows=3, cols=len(xtxbh_list))
    table.style = 'Light Grid Accent 1'
    
    # 填充数据
    for col_idx, item in enumerate(xtxbh_list):
        # 第一行：组别名称
        table.rows[0].cells[col_idx].text = str(item.get("F_ZBMC", ""))
        
        # 第二行：图片
        fj_list = normalize_file_id_list(item.get("F_FJ"))
        if fj_list:
            file_id = fj_list[0]
            temp_img_path = download_file_to_temp(file_id, temp_dir, base_upload_dir)
            if temp_img_path:
                # 清空单元格
                table.rows[1].cells[col_idx].text = ""
                # 插入图片
                paragraph = table.rows[1].cells[col_idx].paragraphs[0]
                run = paragraph.add_run()
                try:
                    run.add_picture(temp_img_path, width=Inches(1.5))
                except Exception as e:
                    print(f"错误: 插入图片失败: {e}")
                    table.rows[1].cells[col_idx].text = "[图片加载失败]"
            else:
                table.rows[1].cells[col_idx].text = "[图片未找到]"
        else:
            table.rows[1].cells[col_idx].text = "[无图片]"
        
        # 第三行：倍率和形态
        file_type = item.get("fileType", [])
        file_type_str = file_type[0] if file_type else ""
        xt = item.get("F_XT", "")
        table.rows[2].cells[col_idx].text = f"{file_type_str}\n形态: {xt}"
    
    # 将表格插入到占位符位置之后
    table_element = table._element
    parent.insert(p_idx + 1, table_element)
    
    # 删除占位符
    target_paragraph.text = target_paragraph.text.replace(marker, "")


def process_dynamic_table_ic50(doc: Document, data: Dict[str, Any], temp_dir: str, base_upload_dir: str):
    """
    处理IC50曲线图表格（{{DYNAMIC_TABLE_IC50}}）
    
    Args:
        doc: Document对象
        data: 报告数据
        temp_dir: 临时目录
        base_upload_dir: uploads基础目录
    """
    marker = "{{DYNAMIC_TABLE_IC50}}"
    record = data.get("实验记录", {})
    if not record:
        return
    
    ic50_list = parse_object_array(record.get("F_IC50"))
    
    if not ic50_list:
        print(f"警告: {marker} 数据为空")
        for paragraph in doc.paragraphs:
            if marker in paragraph.text:
                paragraph.text = paragraph.text.replace(marker, "[数据为空]")
        return
    
    # 构建组别ID到名称的映射
    plan = data.get("实验计划", {})
    zbxx_list = plan.get("ZBXX", [])
    zb_id_to_name = {item["F_ID"]: item.get("F_ZBMC", "") for item in zbxx_list}
    
    # 查找占位符位置
    target_paragraph = None
    for paragraph in doc.paragraphs:
        if marker in paragraph.text:
            target_paragraph = paragraph
            break
    
    if not target_paragraph:
        print(f"警告: 未找到占位符 {marker}")
        return
    
    # 获取段落位置
    p_element = target_paragraph._element
    parent = p_element.getparent()
    p_idx = parent.index(p_element)
    
    # 创建表格（每个组别一行）
    table = doc.add_table(rows=len(ic50_list), cols=2)
    table.style = 'Light Grid Accent 1'
    
    # 填充数据
    for row_idx, item in enumerate(ic50_list):
        zbid = item.get("F_ZBID")
        zbmc = zb_id_to_name.get(zbid, f"组别{zbid}")
        
        # 第一列：组别名称
        table.rows[row_idx].cells[0].text = zbmc
        
        # 第二列：IC50图片
        fj_list = normalize_file_id_list(item.get("F_FJ"))
        if fj_list:
            file_id = fj_list[0]
            temp_img_path = download_file_to_temp(file_id, temp_dir, base_upload_dir)
            if temp_img_path:
                # 清空单元格
                table.rows[row_idx].cells[1].text = ""
                # 插入图片
                paragraph = table.rows[row_idx].cells[1].paragraphs[0]
                run = paragraph.add_run()
                try:
                    run.add_picture(temp_img_path, width=Inches(3.0))
                except Exception as e:
                    print(f"错误: 插入IC50图片失败: {e}")
                    table.rows[row_idx].cells[1].text = "[图片加载失败]"
            else:
                table.rows[row_idx].cells[1].text = "[图片未找到]"
        else:
            table.rows[row_idx].cells[1].text = "[无图片]"
    
    # 将表格插入到占位符位置之后
    table_element = table._element
    parent.insert(p_idx + 1, table_element)
    
    # 删除占位符
    target_paragraph.text = target_paragraph.text.replace(marker, "")


def process_dynamic_table_syjl(doc: Document, data: Dict[str, Any], temp_dir: str, base_upload_dir: str):
    """
    处理实验结论复合对象（{{DYNAMIC_TABLE_SYJL_JGSJ}}, {{实验记录.F_SYJL.JLWB}}, {{DYNAMIC_IMAGE_SYJL_FJ}}）
    
    Args:
        doc: Document对象
        data: 报告数据
        temp_dir: 临时目录
        base_upload_dir: uploads基础目录
    """
    record = data.get("实验记录", {})
    if not record:
        return
    
    syjl_obj = parse_nested_object(record.get("F_SYJL"))
    
    if not syjl_obj:
        print("警告: F_SYJL 数据为空")
        return
    
    # 1. 处理结果数据表格（JGSJ）
    jgsj_marker = "{{DYNAMIC_TABLE_SYJL_JGSJ}}"
    jgsj_data = syjl_obj.get("JGSJ")
    if jgsj_data:
        process_dynamic_table_2d_array(doc, jgsj_marker, json.dumps(jgsj_data), "实验结果数据")
    
    # 2. 处理结论文本（JLWB）
    jlwb_marker = "{{实验记录.F_SYJL.JLWB}}"
    jlwb_text = syjl_obj.get("JLWB", "")
    for paragraph in doc.paragraphs:
        if jlwb_marker in paragraph.text:
            paragraph.text = paragraph.text.replace(jlwb_marker, str(jlwb_text))
    
    # 3. 处理结论附件（F_FJ）
    fj_marker = "{{DYNAMIC_IMAGE_SYJL_FJ}}"
    fj_list = normalize_file_id_list(syjl_obj.get("F_FJ"))
    
    target_paragraph = None
    for paragraph in doc.paragraphs:
        if fj_marker in paragraph.text:
            target_paragraph = paragraph
            break
    
    if target_paragraph and fj_list:
        # 获取段落位置
        p_element = target_paragraph._element
        parent = p_element.getparent()
        p_idx = parent.index(p_element)
        
        # 为每个附件插入图片
        for file_id in fj_list:
            temp_img_path = download_file_to_temp(file_id, temp_dir, base_upload_dir)
            if temp_img_path:
                # 创建新段落插入图片
                new_para = doc.add_paragraph()
                run = new_para.add_run()
                try:
                    run.add_picture(temp_img_path, width=Inches(4.0))
                    # 将新段落插入到占位符位置之后
                    para_element = new_para._element
                    parent.insert(p_idx + 1, para_element)
                    p_idx += 1
                except Exception as e:
                    print(f"错误: 插入结论附件失败: {e}")
        
        # 删除占位符
        target_paragraph.text = target_paragraph.text.replace(fj_marker, "")


# ==================== 6. 主生成函数 ====================

def generate_drug_test_report_pdf(
    syjh_id: int, 
    template_path: Optional[str] = None, 
    output_pdf_path: Optional[str] = None, 
    base_upload_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    生成药物试验报告PDF
    
    Args:
        syjh_id: 实验计划ID
        template_path: Word模板路径（可选，默认使用app/templates/drug_test_report_template.docx）
        output_pdf_path: 输出PDF路径（可选，默认生成到temp_reports目录）
        base_upload_dir: uploads基础目录（可选，默认使用项目根目录下的uploads）
        
    Returns:
        结果字典：{
            "success": bool,
            "pdf_path": str,
            "file_id": int,
            "message": str
        }
    """
    temp_dir = None
    temp_docx_path = None
    
    try:
        print(f"开始生成药物试验报告PDF: 实验计划ID={syjh_id}")
        
        # 0. 处理默认参数
        if template_path is None:
            template_path = get_template_path()
            print(f"使用默认模板路径: {template_path}")
        
        if base_upload_dir is None:
            base_upload_dir = get_base_upload_dir()
            print(f"使用默认uploads目录: {base_upload_dir}")
        
        if output_pdf_path is None:
            temp_reports_dir = get_temp_reports_dir()
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            pdf_filename = f"drug_test_report_{syjh_id}_{timestamp}.pdf"
            output_pdf_path = os.path.join(temp_reports_dir, pdf_filename)
            print(f"使用默认输出路径: {output_pdf_path}")
        
        # 检查模板是否存在
        if not os.path.exists(template_path):
            error_msg = f"模板文件不存在: {template_path}"
            print(f"错误: {error_msg}")
            return {
                "success": False,
                "pdf_path": None,
                "file_id": None,
                "message": error_msg
            }
        
        # 1. 获取报告数据
        print("步骤1: 获取报告数据...")
        report_data = assemble_drug_test_report(syjh_id)
        if not report_data:
            error_msg = "无法获取报告数据"
            print(f"错误: {error_msg}")
            return {
                "success": False,
                "pdf_path": None,
                "file_id": None,
                "message": error_msg
            }
        
        # 2. 创建临时目录
        temp_dir = tempfile.mkdtemp()
        print(f"步骤2: 创建临时目录: {temp_dir}")
        
        # 3. 加载Word模板
        print("步骤3: 加载Word模板...")
        doc = Document(template_path)
        
        # 4. 替换简单占位符
        print("步骤4: 替换简单占位符...")
        replace_simple_placeholders(doc, report_data)
        
        # 5. 处理循环表格
        print("步骤5: 处理循环表格...")
        process_loop_table_zbxx(doc, report_data)
        process_loop_table_kwxx(doc, report_data)
        process_loop_table_gcjl(doc, report_data)
        
        # 6. 处理动态JSON表格
        print("步骤6: 处理动态JSON表格...")
        record = report_data.get("实验记录", {})
        if record:
            # 二维数组表格
            process_dynamic_table_2d_array(doc, "{{DYNAMIC_TABLE_XBHL_PJ}}", record.get("F_XBHL_PJ"), "平均细胞存活率")
            process_dynamic_table_2d_array(doc, "{{DYNAMIC_TABLE_XBHL}}", record.get("F_XBHL"), "细胞存活率")
            process_dynamic_table_2d_array(doc, "{{DYNAMIC_TABLE_XBHL_WC}}", record.get("F_XBHL_WC"), "细胞存活率标准误差")
            process_dynamic_table_2d_array(doc, "{{DYNAMIC_TABLE_GMD}}", record.get("F_GMD"), "光敏度")
            process_dynamic_table_2d_array(doc, "{{DYNAMIC_TABLE_JGSJ}}", record.get("F_JGSJ"), "结果数据")
            
            # 图文混排表格
            process_dynamic_table_xtxbh(doc, report_data, temp_dir, base_upload_dir)
            process_dynamic_table_ic50(doc, report_data, temp_dir, base_upload_dir)
            
            # 实验结论
            process_dynamic_table_syjl(doc, report_data, temp_dir, base_upload_dir)
        
        # 7. 保存临时Word文档
        temp_docx_path = os.path.join(temp_dir, "temp_report.docx")
        print(f"步骤7: 保存临时Word文档: {temp_docx_path}")
        doc.save(temp_docx_path)
        
        # 8. 转换为PDF（Windows Word COM 或 LibreOffice）
        print("步骤8: 转换为PDF...")
        os.makedirs(os.path.dirname(os.path.abspath(output_pdf_path)), exist_ok=True)
        convert_docx_to_pdf(Path(temp_docx_path), Path(output_pdf_path))
        print(f"✅ PDF生成成功: {output_pdf_path}")
        
        # 9. 将PDF注册到文件中心
        print("步骤9: 注册PDF到文件中心...")
        from app.crud.file_center import insert_file_record
        
        # 将绝对路径转换为相对于uploads目录的相对路径
        base_upload_abs = os.path.abspath(base_upload_dir)
        pdf_abs = os.path.abspath(output_pdf_path)
        
        # 计算相对路径
        try:
            relative_path = os.path.relpath(pdf_abs, base_upload_abs)
            # 统一使用正斜杠
            relative_path = relative_path.replace("\\", "/")
        except Exception as e:
            print(f"警告: 无法计算相对路径: {e}")
            relative_path = os.path.basename(output_pdf_path)
        
        file_id = insert_file_record(relative_path, f"药物试验报告_{syjh_id}.pdf")
        
        if file_id:
            print(f"✅ PDF已注册到文件中心: file_id={file_id}")
            
            # 10. 回写到T_SYJH.F_BG_PDF
            print("步骤10: 回写文件ID到数据库...")
            try:
                from app.crud.drug_test_report_pdf_manager import set_generated_pdf
                set_generated_pdf(syjh_id, file_id)
                print(f"✅ 文件ID已回写到T_SYJH.F_BG_PDF")
            except Exception as e:
                print(f"⚠️ 回写文件ID到数据库失败: {e}")
        else:
            print("⚠️ PDF注册到文件中心失败，但文件已生成")
        
        return {
            "success": True,
            "pdf_path": output_pdf_path,
            "file_id": file_id,
            "message": "PDF生成成功"
        }
        
    except Exception as e:
        error_msg = f"生成PDF失败: {e}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "pdf_path": None,
            "file_id": None,
            "message": error_msg
        }
        
    finally:
        # 清理临时目录
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                print(f"清理临时目录: {temp_dir}")
            except Exception as e:
                print(f"警告: 清理临时目录失败: {e}")


# ==================== 7. 调试/测试函数 ====================

def test_generate_report(syjh_id: int = 20):
    """
    测试函数：生成指定实验计划的报告
    
    Args:
        syjh_id: 实验计划ID，默认20
    """
    result = generate_drug_test_report_pdf(syjh_id=syjh_id)
    
    if result["success"]:
        print(f"\n🎉 测试成功！")
        print(f"   PDF路径: {result['pdf_path']}")
        print(f"   文件ID: {result['file_id']}")
        print(f"   消息: {result['message']}")
    else:
        print(f"\n❌ 测试失败！")
        print(f"   错误: {result['message']}")


if __name__ == "__main__":
    # 运行测试
    test_generate_report(20)
