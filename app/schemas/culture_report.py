"""
类器官培养工单详情报告相关Schema定义
"""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class CultureDetailReportRequest(BaseModel):
    """培养工单详情报告请求"""
    F_GDID: int = Field(..., description="工单ID")


class CultureDetailReportDataOut(BaseModel):
    """培养工单详情报告数据输出（完整的嵌套JSON结构）"""
    meta: Dict[str, Any] = Field(default_factory=dict, description="元数据（工单ID、编号、状态等）")
    工单基本信息: Dict[str, Any] = Field(default_factory=dict, description="工单基本信息")
    样本信息: Dict[str, Any] = Field(default_factory=dict, description="样本信息")
    预处理信息: Dict[str, Any] = Field(default_factory=dict, description="预处理信息（含图片URL）")
    培养详情: Dict[str, Any] = Field(default_factory=dict, description="培养详情（含基质胶、消化酶使用记录等）")
    观察记录: List[Dict[str, Any]] = Field(default_factory=list, description="观察记录列表（含附件图片URL）")

    class Config:
        json_schema_extra = {
            "example": {
                "meta": {
                    "gdid": 123,
                    "gdbh": "GD20251110001",
                    "zkzt": "审核通过",
                    "gdzt": "进行中",
                    "generated_at": "2025-11-10 15:30:00"
                },
                "工单基本信息": {
                    "F_GDBH": "GD20251110001",
                    "F_ZZID": "ZZ20251110001",
                    "F_XM": "张三",
                    "F_BRBH": "BR001",
                    "F_BBLX": "组织",
                    "F_DC": "P0",
                    "F_DCID": "1"
                },
                "样本信息": {
                    "F_XB": "男",
                    "F_NL": 45,
                    "F_ZYH": "ZY001",
                    "F_CXSJ": "2025.11.10 10:00"
                },
                "预处理信息": {
                    "T_YCL.F_LTSJ": "2025.11.10 11:00",
                    "T_YCL.F_KSCLSJ": "2025.11.10 11:30",
                    "T_YCL.F_YBYCL": "/uploads/2025/11/10/xxx.jpg"
                },
                "培养详情": {
                    "操作信息": {
                        "操作人": "李四",
                        "操作时间": "2025.11.10 12:00"
                    },
                    "基质胶信息": [
                        {"F_JZJ": "Matrigel", "F_YL": "100μL", "F_ND": "100%"}
                    ],
                    "消化酶使用记录": [
                        {"F_XHM": "胰酶", "F_YL": "1mL", "F_ND": "0.25%"}
                    ]
                },
                "观察记录": [
                    {
                        "T_GCJL.F_GCSJ_FMT": "2025.11.11 10:00",
                        "T_GCJL.F_PYTS": 1,
                        "T_GCJL.F_PYJZT": "完好",
                        "T_GCJL.F_FJ": ["/uploads/2025/11/11/obs1.jpg"],
                        "T_GCJL.F_FJ_TEXT": "有"
                    }
                ]
            }
        }


class CultureDetailPDFRequest(BaseModel):
    """培养工单详情PDF生成请求"""
    F_GDID: int = Field(..., description="工单ID")


class CultureDetailPDFOut(BaseModel):
    """培养工单详情PDF生成输出"""
    file_id: int = Field(..., description="文件ID")
    filename: str = Field(..., description="文件名")
    file_path: str = Field(..., description="文件相对路径")
    file_url: str = Field(..., description="文件访问URL")
    gdid: int = Field(..., description="工单ID")
    gdbh: str = Field(..., description="工单编号")
    format: Optional[str] = Field(default="pdf", description="文件格式（pdf或docx）")
    note: Optional[str] = Field(default=None, description="备注信息（如PDF转换失败时的说明）")
    created_at: str = Field(..., description="生成时间")

    class Config:
        json_schema_extra = {
            "example": {
                "file_id": 1001,
                "filename": "culture_detail_GD20251110001_20251110153000.pdf",
                "file_path": "pdf/culture_detail_GD20251110001_20251110153000.pdf",
                "file_url": "/pdf/culture_detail_GD20251110001_20251110153000.pdf",
                "gdid": 123,
                "gdbh": "GD20251110001",
                "format": "pdf",
                "created_at": "2025-11-10 15:30:00"
            }
        }

