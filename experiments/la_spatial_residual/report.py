"""Render the bounded LA spatial-residual experiment in Chinese."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "exports" / "LA_SPATIAL_RESIDUAL"
REPORT = ROOT / "docs" / "LA_SPATIAL_RESIDUAL_DEVELOPMENT.zh-CN.md"
COMPARATOR = "relative_current_23"
CANDIDATE = "current_plus_coarse_spatial_residual"


def value(value: float | None) -> str:
    return "不可评估" if value is None else f"{value:.4f}"


def main() -> None:
    summary = json.loads((OUTPUT / "summary.json").read_text(encoding="utf-8"))
    metrics = pd.read_csv(OUTPUT / "metrics.csv", dtype={"held_year": str})
    overall = metrics.loc[metrics.held_year.eq("all")].set_index("model")
    current = overall.loc[COMPARATOR]
    candidate = overall.loc[CANDIDATE]
    lines = [
        "# 洛杉矶低自由度空间残差实验",
        "",
        "本轮继续以 LA 本地社区相对地表温差精度为主，同时保留固定的绝对温度水平输出。"
        "由于上一轮 2022–2024 结果参与了本轮设计，本结果是迭代开发证据，不是新的独立确认。",
        "",
        "## 固定设计与边界",
        "",
        "每个外层训练窗口内部先生成严格时间向前的折外预测残差。诊断分别检查社区偏差跨年"
        "Spearman，以及每个折外日期固定 6 近邻社区残差的同方向结构；外层测试年份不用于选择尺度。"
        "唯一候选是在未改动的现有 23 特征模型上增加 5 项城市尺度二次空间基函数"
        "（x、y、x²、xy、y²），Ridge α 固定为 1000，不搜索。",
        "",
        "仓库历史全局特征契约禁止原始坐标。本轮没有修改该契约，而是明确记录了一个只适用于"
        "已有 LA 本地残差修正的实验例外。坐标来自公开 tract 几何；tract ID、历史目标统计、"
        "邻居真实温度均不进入预测。LA 2025、已揭盲外城和新城市目标均未读取。",
        "",
        "## 训练窗口内残差诊断",
        "",
        "| 外层测试年 | 折外残差年 | 跨年社区偏差 Spearman | "
        "6近邻残差 Spearman | 稳定成分方差占比 | 启用修正 |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for row in summary["training_window_diagnostics"]:
        years = ", ".join(str(year) for year in row["oof_years"]) or "无"
        fraction = row.get("stable_variance_fraction")
        lines.append(
            f"| {row['outer_test_year']} | {years} | "
            f"{value(row.get('median_cross_year_spearman'))} | "
            f"{value(row.get('median_neighbor_spearman'))} | "
            f"{value(fraction)} | {row['supported']} |"
        )
    lines += [
        "",
        "长期空间偏差由折外残差的社区中位数描述；去除该分量后的剩余误差用于描述逐日变化。"
        "这一区分不把总体社区排序稳定解释成动态预测能力。",
        "",
        "## 严格时间向前结果",
        "",
        "| 模型 | 相对 MAE °C | 完整集合中心化 MAE °C | 热点召回 | 绝对 MAE °C |",
        "|---|---:|---:|---:|---:|",
        f"| 现有 23 特征模型 | {current.relative_mae_c:.4f} | "
        f"{current.support_centered_relative_mae_c:.4f} | {current.hotspot_recall:.4f} | "
        f"{current.absolute_mae_c:.4f} |",
        f"| 现有模型 + 粗尺度空间残差 | {candidate.relative_mae_c:.4f} | "
        f"{candidate.support_centered_relative_mae_c:.4f} | {candidate.hotspot_recall:.4f} | "
        f"{candidate.absolute_mae_c:.4f} |",
        "",
        f"总体相对 MAE 改善为 **{summary['relative_improvement']:.2%}**，配对日期 × 空间块"
        f"区间为 **[{summary['bootstrap']['lower_c']:.4f}, "
        f"{summary['bootstrap']['upper_c']:.4f}] °C**。启用空间修正的测试年份为 "
        f"**{summary['candidate_active_test_years']}**；升级门槛通过："
        f"**{summary['promotion_passed']}**。",
        "",
        "| 测试年 | 现有模型 MAE | 空间修正 MAE | 改善 °C |",
        "|---:|---:|---:|---:|",
    ]
    yearly = metrics.loc[metrics.held_year.ne("all")]
    for year in sorted(yearly.held_year.unique()):
        table = yearly.loc[yearly.held_year.eq(year)].set_index("model")
        lines.append(
            f"| {year} | {table.loc[COMPARATOR, 'relative_mae_c']:.4f} | "
            f"{table.loc[CANDIDATE, 'relative_mae_c']:.4f} | "
            f"{summary['per_year_relative_gain_c'][year]:+.4f} |"
        )
    lines += ["", "## 结论", ""]
    if summary["promotion_passed"]:
        lines.append(
            "该单一候选通过预先固定的开发门槛，可保留为 LA 本地开发候选；这仍不是独立确认，"
            "也不支持跨城市迁移主张。下一步应冻结实现，不再依据同一批年份调整空间尺度。"
        )
    else:
        lines.append(
            "空间修正路线未通过停止条件，现有模型继续保持默认。本轮只否定这项固定的低自由度"
            "粗尺度修正，不代表精度达到上限。折外残差在单个日期内具有很强的邻近空间一致性，"
            "但同一社区的偏差跨年稳定性弱；这不符合纯独立随机噪声，也不支持继续拟合固定社区"
            "偏差。当前证据无法区分缺失的日期变化空间场与空间相关的观测/支持误差。下一步需要"
            "核对目标前可用的局地气象梯度、气溶胶/云与观测几何，以及现有 uncertainty、"
            "cloud-distance、valid-fraction 等 QA 信息；在区分机制前不再用同一批年份调空间尺度。"
        )
    lines += ["", f"训练与输入签名：`{summary['signature']}`。", ""]
    text = "\n".join(lines)
    REPORT.write_text(text, encoding="utf-8")
    (OUTPUT / "REPORT.zh-CN.md").write_text(text, encoding="utf-8")
    # The tracked UTF-8 report is authoritative; avoid legacy Windows console
    # encodings failing on mathematical superscripts.


if __name__ == "__main__":
    main()
