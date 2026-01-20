"""
IC50 计算与可选绘制工具。

提供：
- load_data: 从 Excel 读取浓度与响应列（过滤浓度<=0，并按浓度升序）。
- calculate_ic50: 使用四参数 Sigmoid（固定 Top=100, Bottom=0）拟合，返回 IC50、斜率、R^2 等。
- plot_curve: 可视化原始点与拟合曲线（可选调用）。
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit


class IC50Analyzer:
    def __init__(self):
        # 设置字体以支持中文，允许回退到 Arial
        plt.rcParams["font.sans-serif"] = ["SimHei", "Arial"]
        plt.rcParams["axes.unicode_minus"] = False

    def load_data(self, file_path: str, conc_col: str, response_col: str):
        """
        从 Excel 读取数据，返回 (浓度数组, 响应数组)；过滤掉浓度<=0。
        """
        try:
            df = pd.read_excel(file_path)
            df = df[df[conc_col] > 0].sort_values(by=conc_col)
            return df[conc_col].values, df[response_col].values
        except Exception as exc:
            # 返回 None, None 由调用方决定如何提示
            print(f"读取失败: {exc}")
            return None, None

    def calculate_ic50(self, x_conc, y_response):
        """
        计算 IC50。
        返回 dict:
        {
            success: bool,
            ic50: float,
            slope: float,
            r2: float,
            params: ndarray,
            model_func: callable
        }
        """
        # 1. 浓度取对数
        x_log = np.log10(x_conc)
        y_data = y_response

        # 2. 定义方程 (Top=100, Bottom=0)
        def prism_model(log_x, log_ic50, hill_slope):
            return 100 / (1 + 10 ** ((log_ic50 - log_x) * hill_slope))

        try:
            # 初始猜测：IC50 在中间，斜率为 -1
            p0 = [np.mean(x_log), -1.0]
            popt, _ = curve_fit(prism_model, x_log, y_data, p0=p0, maxfev=10000)

            log_ic50_fit, slope_fit = popt
            ic50_val = 10 ** log_ic50_fit

            # 计算 R^2
            residuals = y_data - prism_model(x_log, *popt)
            ss_res = np.sum(residuals**2)
            ss_tot = np.sum((y_data - np.mean(y_data))**2)
            r2 = 1 - (ss_res / ss_tot)

            return {
                "success": True,
                "ic50": ic50_val,
                "slope": slope_fit,
                "r2": r2,
                "params": popt,
                "model_func": prism_model,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def plot_curve(self, x_conc, y_response, result):
        """
        绘制原始点与拟合曲线（可选）。
        """
        if not result.get("success"):
            return

        x_log = np.log10(x_conc)
        x_smooth_log = np.linspace(min(x_log) - 0.5, max(x_log) + 0.5, 200)
        x_smooth_real = 10 ** x_smooth_log
        y_smooth = result["model_func"](x_smooth_log, *result["params"])

        plt.figure(figsize=(8, 6))
        plt.scatter(x_conc, y_response, color="red", s=60, label="实验数据", zorder=5)
        plt.plot(x_smooth_real, y_smooth, color="blue", linewidth=2, label="拟合曲线")
        plt.axvline(result["ic50"], color="green", linestyle="--", label=f'IC50 = {result["ic50"]:.2f}')
        plt.axhline(50, color="gray", linestyle=":", alpha=0.5)

        plt.xscale("log")
        plt.xlabel("Concentration (log scale)")
        plt.ylabel("Response (% Viability)")
        plt.title(f'IC50: {result["ic50"]:.4f} | R^2: {result["r2"]:.4f}')
        plt.legend()
        plt.grid(True, which="both", ls="-", alpha=0.2)
        plt.tight_layout()
        plt.show()


__all__ = ["IC50Analyzer"]

