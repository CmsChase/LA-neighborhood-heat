"""Render the fixed LA d-1 Tmax-gradient experiment in Chinese."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "exports" / "LA_TMAX_GRADIENT"
REPORT = ROOT / "docs" / "LA_TMAX_GRADIENT_DEVELOPMENT.zh-CN.md"
COMPARATOR = "relative_current_23"
CANDIDATE = "relative_current_23_plus_dminus1_tmax_gradient"


def metric_row(table: pd.DataFrame, year: str, model: str) -> pd.Series:
    return table.loc[table.held_year.eq(year) & table.model.eq(model)].iloc[0]


def main() -> None:
    summary = json.loads((OUTPUT / "summary.json").read_text(encoding="utf-8"))
    metrics = pd.read_csv(OUTPUT / "metrics.csv", dtype={"held_year": str})
    current = metric_row(metrics, "all", COMPARATOR)
    candidate = metric_row(metrics, "all", CANDIDATE)
    time = summary["time_availability"]
    gradient = summary["gradient"]

    lines = [
        "# 洛杉矶 d−1 最高气温城内梯度固定候选实验",
        "",
        "## 结论",
        "",
        "**候选未通过升级门槛，现有 23 特征相对模型继续保持默认；这条固定 d−1 Tmax",
        "梯度路线到此停止。** 候选总体相对 MAE 改善 1.53%，低于预设 5%；配对日期 ×",
        f"空间块区间为 [{summary['bootstrap']['lower_c']:.4f}, "
        f"{summary['bootstrap']['upper_c']:.4f}] °C，包含零。改善主要集中在 2024，2022 反而",
        "退化，因此不能视为可靠升级。失败只否定这一固定形式，不证明其他天气信息无用。",
        "",
        "## 预先固定的合同",
        "",
        "唯一候选把现有模型从 23 列扩为 24 列。新增列定义为：",
        "",
        "`gradient(i,d) = Tmax(i,d−1) − median[Tmax(完整固定 LA 预测社区集合,d−1)]`",
        "",
        f"每个日期均使用完整 {gradient['tracts_per_date']} 个社区计算中位数，共 "
        f"{gradient['prediction_dates']} 个预测日期；最大绝对日期中位数为 "
        f"{gradient['maximum_absolute_date_median']:.2e} °C。评分子集、目标值和 QA 均未参与",
        "中心化。这是相对城市背景的气温差，不是空间导数。测试日期只读取自身合法预测变量。",
        "",
        "候选是现有 HGB 的精确算法与超参数克隆，训练权重不变；原有 18 个静态和 5 个",
        "lagged-Sentinel 特征、动态特征训练折中位数插补均不变。新增梯度无缺失，因此没有",
        "新增插补参数。没有天气窗口、尺度、交互项、模型或额外候选搜索。B1 水平模型保持",
        "原有逐折训练合同；候选绝对输出等于该固定合同产生的 B1 城市日水平加候选相对输出，",
        "所以相对输出变化会反映在绝对 MAE 中。",
        "",
        "## 时间含义核查",
        "",
        f"本地 Daymet 审计覆盖 {time['rows']:,} 行、{time['dates']} 个日期。逐行核查确认",
        "prev_1d 的 source start 与 source end 都严格等于目标日减一，期望和实际完整天数均为",
        "1，且所有主要窗口完整。因此字段没有日历日期错位。",
        "",
        "但审计中没有产品发布时间或发布日期字段，无法证明产品在目标日前已经发布。因此该",
        "特征只能称为**历史 hindcast 重建可用**，不能声称支持实时预测。",
        "",
        "## 严格时间向前结果",
        "",
        "训练严格早于测试年：2022 使用 2020–2021，2023 使用 2020–2022，2024 使用",
        "2020–2023。评分行、权重、QA4K 目标、评分子集中心化、完整支持集中心化与原实验",
        "一致。共 47,142 行、49 个独立日期、71 个空间块。",
        "",
        "| 测试年 | 模型 | 相对 MAE °C | 完整支持中心化 MAE °C | 热点召回 | 绝对 MAE °C |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for year in ["2022", "2023", "2024", "all"]:
        label = "整体" if year == "all" else year
        for model, model_label in ((COMPARATOR, "现有 23 特征"), (CANDIDATE, "固定 24 特征")):
            row = metric_row(metrics, year, model)
            lines.append(
                f"| {label} | {model_label} | {row.relative_mae_c:.4f} | "
                f"{row.support_centered_relative_mae_c:.4f} | "
                f"{row.hotspot_recall:.4f} | {row.absolute_mae_c:.4f} |"
            )

    lines += [
        "",
        "逐年候选相对 MAE 相对现有模型的变化为：2022 **+0.0217°C**（退化）、2023",
        "**−0.0011°C**（几乎不变）、2024 **−0.0694°C**（改善）。整体从 "
        f"{current.relative_mae_c:.4f} 降至 {candidate.relative_mae_c:.4f}°C。完整支持中心化",
        f"MAE 从 {current.support_centered_relative_mae_c:.4f} 降至 "
        f"{candidate.support_centered_relative_mae_c:.4f}°C，但相对评分子集中心化的差距分别为 "
        f"{current.support_centered_relative_mae_c-current.relative_mae_c:+.4f} 和 "
        f"{candidate.support_centered_relative_mae_c-candidate.relative_mae_c:+.4f}°C。",
        "",
        f"热点召回从 {current.hotspot_recall:.4f} 降至 {candidate.hotspot_recall:.4f}；"
        f"绝对 MAE 从 {current.absolute_mae_c:.4f} 降至 {candidate.absolute_mae_c:.4f}°C。"
        "这些次要指标未触发退化阈值，但不能弥补主门槛与区间失败。",
        "",
        "| 升级检查 | 通过 |",
        "|---|---:|",
    ]
    for name, passed in summary["promotion_checks"].items():
        lines.append(f"| `{name}` | {passed} |")

    lines += [
        "",
        "## 收口与证据角色",
        "",
        "`daymet_tmax_c_mean_prev_1d` 是根据同一批 2022–2024 残差筛选出的，因此严格",
        "时间向前训练并不能消除特征选择的适应性。本结果仍是复用年份的开发证据。候选失败",
        "后没有拟合或保存全历史候选模型；只保留折外预测、指标、配置和输入/代码签名用于",
        "复现。现有模型不变。不要在同一批年份上改用 3 日/7 日窗口、变换或交互项，也不要",
        "把结果解释为局地气象的因果作用。",
        "",
        f"复现签名：`{summary['signature']}`。",
        "",
    ]
    text = "\n".join(lines)
    REPORT.write_text(text, encoding="utf-8")
    (OUTPUT / "REPORT.zh-CN.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
