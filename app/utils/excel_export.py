"""
Excel 导出工具模块
提供通用的 Excel 文件生成功能
"""
from typing import List, Dict, Any, Optional
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from datetime import datetime
import os


class ExcelExporter:
    """Excel 导出器"""
    
    def __init__(self, title: str = "导出数据"):
        """
        初始化导出器
        
        Args:
            title: 工作表标题
        """
        self.wb = Workbook()
        self.ws = self.wb.active
        self.ws.title = title
        self.current_row = 1
        
    def set_header_style(self):
        """设置表头样式"""
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        alignment = Alignment(horizontal="center", vertical="center")
        
        for cell in self.ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = alignment
            
    def set_border(self, start_row: int, end_row: int, start_col: int, end_col: int):
        """设置单元格边框"""
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        for row in range(start_row, end_row + 1):
            for col in range(start_col, end_col + 1):
                self.ws.cell(row=row, column=col).border = thin_border
                
    def auto_adjust_column_width(self):
        """自动调整列宽"""
        for column in self.ws.columns:
            max_length = 0
            column_letter = get_column_letter(column[0].column)
            
            for cell in column:
                try:
                    if cell.value:
                        cell_length = len(str(cell.value))
                        if cell_length > max_length:
                            max_length = cell_length
                except:
                    pass
                    
            adjusted_width = min(max_length + 2, 50)  # 最大宽度50
            self.ws.column_dimensions[column_letter].width = adjusted_width
            
    def write_headers(self, headers: List[str]):
        """
        写入表头
        
        Args:
            headers: 表头列表
        """
        for col_num, header in enumerate(headers, start=1):
            cell = self.ws.cell(row=1, column=col_num)
            cell.value = header
        self.current_row = 2
        
    def write_row(self, data: List[Any]):
        """
        写入一行数据
        
        Args:
            data: 数据列表
        """
        for col_num, value in enumerate(data, start=1):
            cell = self.ws.cell(row=self.current_row, column=col_num)
            cell.value = value
            # 居中对齐
            cell.alignment = Alignment(horizontal="center", vertical="center")
        self.current_row += 1
        
    def write_rows(self, rows: List[List[Any]]):
        """
        批量写入多行数据
        
        Args:
            rows: 数据行列表
        """
        for row_data in rows:
            self.write_row(row_data)
            
    def save(self, filepath: str):
        """
        保存 Excel 文件
        
        Args:
            filepath: 保存路径
        """
        # 确保目录存在
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        # 应用样式
        self.set_header_style()
        if self.current_row > 1:
            self.set_border(1, self.current_row - 1, 1, self.ws.max_column)
        self.auto_adjust_column_width()
        
        # 保存文件
        self.wb.save(filepath)
        

def export_culture_orders_to_excel(
    data: List[Dict[str, Any]],
    filepath: str,
    title: str = "培养工单列表"
) -> str:
    """
    导出培养工单列表到 Excel
    
    Args:
        data: 工单数据列表
        filepath: 保存路径
        title: 工作表标题
        
    Returns:
        保存的文件路径
    """
    exporter = ExcelExporter(title=title)

    from app.core.pseudonym import apply_pseudonym
    data = apply_pseudonym(data) or []
    
    # 定义表头
    headers = [
        "工单编号",
        "标本号",
        "姓名",
        "病人编号",
        "标本类型",
        "代次",
        "代次ID",
        "组织部位",
        "血液种类",
        "取样方式",
        "样本来源",
        "样本规格",
        "传代前细胞总数",
        "复苏前细胞总数",
        "工单类型",
        "工单状态",
        "审核状态",
        "申请人",
        "培养时间",
        "回收时间",
        "细胞数量",
        "培养天数",
        "创建时间",
    ]
    
    # 写入表头
    exporter.write_headers(headers)
    
    # 处理数据(扁平化树形结构)
    flat_data = []
    
    def flatten_tree(items: List[Dict[str, Any]], level: int = 0):
        """递归扁平化树形结构"""
        for item in items:
            # 添加层级标识(用于代次显示)
            row_data = [
                item.get("F_GDBH", ""),
                item.get("F_ZZID", ""),
                item.get("F_XM", ""),
                item.get("F_BRBH", ""),
                item.get("F_BBLX", ""),
                ("  " * level) + str(item.get("F_DC", "")),  # 缩进显示层级
                item.get("F_DCID", ""),
                item.get("F_ZZBW", ""),
                item.get("F_XYZL", ""),
                item.get("F_QYFS", ""),
                item.get("F_YBLY", ""),
                item.get("F_YBGG", ""),
                item.get("F_CDQXBZS", ""),
                item.get("F_FSQXBZS", ""),
                item.get("F_GDLX", ""),
                item.get("F_GDZT", ""),
                item.get("F_SHZT", ""),
                item.get("F_SQR", ""),
                item.get("F_PYSJ", ""),
                item.get("F_HSSJ", ""),
                item.get("F_HSSL", ""),
                item.get("F_PYTS", ""),
                item.get("F_CJSJ", ""),
            ]
            flat_data.append(row_data)
            
            # 递归处理子节点
            if item.get("children"):
                flatten_tree(item["children"], level + 1)
    
    flatten_tree(data)
    
    # 写入数据
    exporter.write_rows(flat_data)
    
    # 保存文件
    exporter.save(filepath)
    
    return filepath


def generate_export_filename(prefix: str = "export", ext: str = "xlsx") -> str:
    """
    生成导出文件名
    
    Args:
        prefix: 文件名前缀
        ext: 文件扩展名
        
    Returns:
        文件名(不含路径)
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}.{ext}"

