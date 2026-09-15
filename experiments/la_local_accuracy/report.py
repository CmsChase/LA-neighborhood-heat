"""Render the completed LA strict-forward experiment as a concise Chinese report."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "exports" / "LA_LOCAL_FORWARD_ACCURACY"
REPORT = ROOT / "docs" / "LA_LOCAL_FORWARD_ACCURACY.zh-CN.md"

LABELS = {
    "relative_current_23": "现有相对模型（18 静态 + 5 Sentinel）",
    "relative_stable_18": "仅稳定空间特征",
    "relative_stable_blend": "固定 1:1 融合",
    "zero_anomaly": "零温差基线",
    "historical_tract_median": "训练期社区历史温差基线",
    "B1": "B1",
    "selected": "逐年向前选择程序",
}


def main() -> None:
    summary = json.loads((OUTPUT / "summary.json").read_text(encoding="utf-8"))
    metrics = pd.read_csv(OUTPUT / "metrics.csv", dtype={"held_year": str})
    overall = metrics.loc[metrics.held_year.eq("all")].set_index("model")
    years = metrics.loc[metrics.held_year.ne("all")]
    selected = overall.loc["selected"]
    current = overall.loc["relative_current_23"]
    history = overall.loc["historical_tract_median"]
    zero = overall.loc["zero_anomaly"]
    stability = summary["stability_diagnostic"]
    checks = summary["promotion_checks"]

    text = [
        "# 洛杉矶社区相对地表温差：逐年向前精度实验",
        "",
        "本轮只使用洛杉矶 2020–2024 年既有 QA4K 历史观测，目标是提高同城未来日期的"
        "社区相对地表温差精度，同时保留锚定的绝对温度输出。没有使用 LA 2025、"
        "四个已揭盲城市或任何新城市目标。",
        "",
        "## 预先固定的设计",
        "",
        "外层测试年份为 2022、2023、2024。每折只用测试年之前的数据；候选选择仅用"
        "紧邻测试年的上一完整年份，更早年份训练。动态特征缺失值处理和模型拟合均在"
        "各训练折内完成。候选仅为现有 23 特征模型、18 个稳定空间特征模型及两者固定"
        " 1:1 融合。社区历史温差只作为训练期诊断基线，不进入任何候选特征。",
        "",
        "## 向前验证结果",
        "",
        "| 模型/基线 | 相对 MAE °C | 完整集合中心化 MAE °C | 热点 AP | 热点召回 | 绝对 MAE °C |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in LABELS:
        row = overall.loc[name]
        text.append(
            f"| {LABELS[name]} | {row.relative_mae_c:.4f} | "
            f"{row.support_centered_relative_mae_c:.4f} | "
            f"{row.hotspot_average_precision:.4f} | {row.hotspot_recall:.4f} | "
            f"{row.absolute_mae_c:.4f} |"
        )
    text += [
        "",
        f"逐年选择程序相对现有模型的相对 MAE 改善为 "
        f"**{summary['relative_improvement']:.2%}**；配对日期 × 5 km 空间块 bootstrap "
        f"的误差改善区间为 **[{summary['bootstrap']['lower_c']:.4f}, "
        f"{summary['bootstrap']['upper_c']:.4f}] °C**。预先固定的升级门槛通过："
        f"**{summary['promotion_passed']}**。",
        "",
        "| 门槛 | 通过 |",
        "|---|---:|",
    ]
    text.extend(f"| {name} | {passed} |" for name, passed in checks.items())
    text += [
        "",
        "## 各测试年份",
        "",
        "| 年份 | 选择候选 | 现有模型 MAE | selected MAE | selected 热点召回 | selected 绝对 MAE |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    selections = {
        str(row["outer_test_year"]): row["selected"]
        for row in summary["outer_selections"]
    }
    for year in sorted(years.held_year.unique()):
        table = years.loc[years.held_year.eq(year)].set_index("model")
        text.append(
            f"| {year} | {selections[year]} | "
            f"{table.loc['relative_current_23', 'relative_mae_c']:.4f} | "
            f"{table.loc['selected', 'relative_mae_c']:.4f} | "
            f"{table.loc['selected', 'hotspot_recall']:.4f} | "
            f"{table.loc['selected', 'absolute_mae_c']:.4f} |"
        )
    text += [
        "",
        "## 稳定空间格局与日期变化",
        "",
        f"训练期社区历史温差基线的向前 MAE 为 **{history.relative_mae_c:.4f} °C**，"
        f"零温差基线为 **{zero.relative_mae_c:.4f} °C**，前者降低 "
        f"**{zero.relative_mae_c - history.relative_mae_c:.4f} °C**。这是真正按训练期"
        "构建的诊断，说明稳定社区差异在未来年份仍有可预测性。",
        f"全 2020–2024 描述性日期两两排序相关的中位数为 "
        f"**{stability['median_pairwise_date_spearman']:.4f}**"
        f"（{stability['date_pair_count']} 对日期）；去除每个社区的全期中位温差后，"
        f"剩余 MAE 为 **{stability['residual_after_stable_tract_median_mae_c']:.4f} °C**。"
        "这项全期分解只描述信号，不作为验证成绩。",
        "",
        "## 中心化与支持",
        "",
        f"外层结果包含 {int(selected.rows):,} 个评分行、{int(selected.dates)} 个独立日期和 "
        f"{int(selected.blocks)} 个 5 km 空间块。主相对 MAE 在评分子集重新中心化；"
        "完整集合列保留先在完整预测 tract 集合中心化的实际输出。selected 两种口径"
        f"相差 **{selected.support_centered_relative_mae_c - selected.relative_mae_c:+.4f} "
        f"°C**，现有模型相差 **"
        f"{current.support_centered_relative_mae_c - current.relative_mae_c:+.4f} °C**。",
        "",
        "绝对输出使用同折 B1 在完整城市日期预测集合上的中位数作为温度水平，再加"
        "相对输出。相对误差改善不自动等于绝对温度改善。",
        "",
        "## 结论与下一步",
        "",
    ]
    if summary["promotion_passed"]:
        text.append(
            "该固定逐年向前选择程序通过本地开发门槛，可作为新的洛杉矶历史开发候选；"
            "它仍不是 LA 2025 的重新验证，也不改变跨城市结论。下一步应冻结候选并"
            "制作完整历史预测/误差地图。"
        )
    else:
        text.append(
            "该固定逐年向前选择程序未通过升级门槛，现有相对模型保持默认。失败只否定"
            "这组‘稳定特征收缩’候选，不表示本地精度已到上限。下一项有依据的实验应"
            "针对训练期社区历史基线仍能解释但当前无 ID 模型未捕获的长期空间结构，"
            "使用不含目标统计量的空间平滑/分层结构，并继续采用相同逐年向前验证。"
        )
    text += [
        "",
        f"完整训练与输入指纹：`{summary['signature']}`。",
        "",
    ]
    REPORT.write_text("\n".join(text), encoding="utf-8")
    (OUTPUT / "REPORT.zh-CN.md").write_text("\n".join(text), encoding="utf-8")
    print("\n".join(text))


if __name__ == "__main__":
    main()
