"""
IC50 分析器测试脚本

测试两种场景：
1. 正常抑制场景（有明显IC50）
2. 实际数据场景（截图中的奥沙利铂数据）
"""

import numpy as np
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from app.utils.ic50_analyzer import IC50Analyzer


def test_case_1_normal_inhibition():
    """测试案例1：正常的药物抑制数据（理想情况）"""
    print("=" * 80)
    print("测试案例1：正常药物抑制数据")
    print("=" * 80)
    
    # 模拟数据：浓度从低到高，活率从高到低
    concentrations = np.array([0.01, 0.1, 1, 10, 100])  # μM
    responses = np.array([95, 85, 50, 20, 5])  # 细胞活率 %
    
    print(f"浓度 (μM): {concentrations}")
    print(f"细胞活率 (%): {responses}")
    print()
    
    analyzer = IC50Analyzer()
    result = analyzer.calculate_ic50(concentrations, responses)
    
    if result["success"]:
        print(f"✅ IC50 计算成功")
        print(f"   IC50 = {result['ic50']:.4f} μM")
        print(f"   斜率 = {result['slope']:.4f}")
        print(f"   R² = {result['r2']:.4f}")
        
        # 绘制曲线
        print("\n正在生成拟合曲线图...")
        analyzer.plot_curve(concentrations, responses, result)
    else:
        print(f"❌ IC50 计算失败: {result.get('error')}")
    
    print()


def test_case_2_actual_data():
    """测试案例2：截图中的奥沙利铂实际数据"""
    print("=" * 80)
    print("测试案例2：奥沙利铂组实际数据（截图数据）")
    print("=" * 80)
    
    # 截图中的实际数据
    concentrations = np.array([5.04, 2.52, 1.26, 0.63, 0.315, 0.1575])  # μM
    responses = np.array([90.51, 97.28, 99.54, 100.15, 107.16, 99.84])  # %
    
    print(f"浓度 (μM): {concentrations}")
    print(f"细胞活率 (%): {responses}")
    print()
    
    print("⚠️  注意：这组数据的活率都在 90% 以上")
    print("   理论上 IC50 应该在更高的浓度范围")
    print()
    
    analyzer = IC50Analyzer()
    result = analyzer.calculate_ic50(concentrations, responses)
    
    if result["success"]:
        print(f"✅ IC50 计算成功（但可能不准确）")
        print(f"   IC50 = {result['ic50']:.4f} μM")
        print(f"   斜率 = {result['slope']:.4f}")
        print(f"   R² = {result['r2']:.4f}")
        
        # 检查 IC50 是否在测量范围内
        if result['ic50'] < min(concentrations) or result['ic50'] > max(concentrations):
            print(f"\n⚠️  警告：IC50 值 ({result['ic50']:.4f}) 超出测量范围")
            print(f"   建议浓度范围：{min(concentrations):.4f} - {max(concentrations):.4f} μM")
        
        # 绘制曲线
        print("\n正在生成拟合曲线图...")
        analyzer.plot_curve(concentrations, responses, result)
    else:
        print(f"❌ IC50 计算失败: {result.get('error')}")
    
    print()


def test_case_3_extended_concentration():
    """测试案例3：扩展浓度范围（添加高浓度点）"""
    print("=" * 80)
    print("测试案例3：扩展浓度范围（推荐方案）")
    print("=" * 80)
    
    # 在原数据基础上扩展更高浓度的假设数据
    concentrations = np.array([0.1575, 0.315, 0.63, 1.26, 2.52, 5.04, 10, 20, 50])
    responses = np.array([99.84, 107.16, 100.15, 99.54, 97.28, 90.51, 75, 45, 15])
    
    print(f"浓度 (μM): {concentrations}")
    print(f"细胞活率 (%): {responses}")
    print("（注：高浓度数据为推测值，用于演示完整的IC50曲线）")
    print()
    
    analyzer = IC50Analyzer()
    result = analyzer.calculate_ic50(concentrations, responses)
    
    if result["success"]:
        print(f"✅ IC50 计算成功")
        print(f"   IC50 = {result['ic50']:.4f} μM")
        print(f"   斜率 = {result['slope']:.4f}")
        print(f"   R² = {result['r2']:.4f}")
        
        # 绘制曲线
        print("\n正在生成拟合曲线图...")
        analyzer.plot_curve(concentrations, responses, result)
    else:
        print(f"❌ IC50 计算失败: {result.get('error')}")
    
    print()


def test_case_4_5fu_data():
    """测试案例4：5-FU 组数据（从截图推测）"""
    print("=" * 80)
    print("测试案例4：5-FU 组数据")
    print("=" * 80)
    
    # 假设 5-FU 组有更好的抑制效果
    concentrations = np.array([29.36, 14.68, 7.34, 3.67, 1.835, 0.9175])  # μM
    responses = np.array([20.21, 61.84, 77.49, 89.76, 93.21, 100.16])  # %（假设值）
    
    print(f"浓度 (μM): {concentrations}")
    print(f"细胞活率 (%): {responses}")
    print("（注：这是根据截图中5-FU浓度推测的示例数据）")
    print()
    
    analyzer = IC50Analyzer()
    result = analyzer.calculate_ic50(concentrations, responses)
    
    if result["success"]:
        print(f"✅ IC50 计算成功")
        print(f"   IC50 = {result['ic50']:.4f} μM")
        print(f"   斜率 = {result['slope']:.4f}")
        print(f"   R² = {result['r2']:.4f}")
        
        # 绘制曲线
        print("\n正在生成拟合曲线图...")
        analyzer.plot_curve(concentrations, responses, result)
    else:
        print(f"❌ IC50 计算失败: {result.get('error')}")
    
    print()


if __name__ == "__main__":
    print("\n🧪 IC50 分析器测试\n")
    
    # 运行所有测试
    test_case_1_normal_inhibition()
    test_case_2_actual_data()
    test_case_3_extended_concentration()
    test_case_4_5fu_data()
    
    print("=" * 80)
    print("📊 测试总结")
    print("=" * 80)
    print("1. ✅ 测试案例1：正常抑制数据 - 应该能正确计算 IC50")
    print("2. ⚠️  测试案例2：截图实际数据 - 浓度范围不足，IC50 可能不准确")
    print("3. ✅ 测试案例3：扩展浓度范围 - 推荐的实验设计")
    print("4. ✅ 测试案例4：5-FU 数据 - 演示更好的抑制效果")
    print()
    print("💡 建议：")
    print("   - 实验时应确保浓度范围覆盖 IC50 值")
    print("   - 最好有数据点在 20%-80% 活率范围内")
    print("   - 如果所有活率都 >90%，说明浓度不够高")
    print()

