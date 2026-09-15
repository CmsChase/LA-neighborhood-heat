"""Render the bounded LA residual diagnostics in Chinese."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "exports" / "LA_RESIDUAL_DIAGNOSTICS"
REPORT = ROOT / "docs" / "LA_RESIDUAL_DIAGNOSTICS.zh-CN.md"


def number(value: float | None, digits: int = 3) -> str:
    return "不可计算" if value is None else f"{value:.{digits}f}"


def year_values(row: dict) -> str:
    return " / ".join(number(item["median_spearman"]) for item in row["year_summaries"])


def main() -> None:
    summary = json.loads((OUTPUT / "summary.json").read_text(encoding="utf-8"))
    neighbor = summary["neighbor_check"]
    signed = summary["signed_residual"]
    scene = summary["scene_and_geometry_availability"]
    blocks = pd.read_csv(OUTPUT / "signed_residual_blocks.csv")
    stable_blocks = blocks.loc[blocks.years.eq(3) & blocks.dates.ge(25)].copy()
    hot = stable_blocks.nlargest(3, "median_signed_residual_c")
    cold = stable_blocks.nsmallest(3, "median_signed_residual_c")

    per_date = pd.DataFrame(neighbor["per_date"])
    yearly_neighbor = per_date.groupby("year").spearman.median().to_dict()
    quality = summary["quality"]
    weather = summary["weather"]
    weather_ranked = sorted(
        weather,
        key=lambda row: abs(row["overall_median_date_spearman"] or 0.0),
        reverse=True,
    )
    quality_signals = [row for row in quality if row["same_direction_signal"]]
    lines = [
        "# 洛杉矶严格时间向前残差诊断",
        "",
        "本轮只复用现有 2022–2024 严格时间向前折外预测与本地已有数据，目标是判断下一步",
        "需要什么信息。没有训练或选择新模型、没有下载数据、没有更改 QA4K 门槛、目标定义、",
        "eligible-land 分母或评分行，也没有读取 LA 2025 与外城目标。由于这些年份已反复用于",
        "开发，本报告属于探索分析，不是新的独立确认。统计单位以 49 个日期和 71 个空间块为主，",
        "不把 47,142 个 tract-date 行当作独立样本。",
        "",
        "## 6 近邻统计究竟比较什么",
        "",
        "对每个日期，向量一是每个有折外观测社区的有符号残差（观测相对温差减现有模型预测）；",
        "向量二是该社区最近六个**其他**社区中当日有观测者的平均有符号残差。每个日期单独计算",
        "Spearman，再取日期中位数。近邻不含自身，单个焦点社区内没有重复邻居；但不同焦点的",
        "邻居集合会重叠，因此社区行不是独立重复。",
        "",
        f"观测日期中位 Spearman 为 **{neighbor['observed_median_spearman']:.3f}**；分年中位数为 "
        f"2022 **{yearly_neighbor[2022]:.3f}**、2023 **{yearly_neighbor[2023]:.3f}**、"
        f"2024 **{yearly_neighbor[2024]:.3f}**。在每个日期内把残差随机重新分配给社区位置的 "
        f"200 次参照中，中位数为 {neighbor['permutation']['median']:.3f}，95% 范围 "
        f"[{neighbor['permutation']['lower_2_5']:.3f}, "
        f"{neighbor['permutation']['upper_97_5']:.3f}]；单侧加一比例为 "
        f"{neighbor['permutation']['one_sided_fraction_at_least_observed']:.4f}。",
        "这排除了由包含自身、重复记录或单纯聚合重叠造成 0.89 相关的解释，但不说明该结构一定",
        "来自物理过程，也不授权继续试空间模型。",
        "",
        "## 有符号残差：长期区域偏差与逐日空间场",
        "",
        f"总残差 MAE 为 {signed['total_residual_mae_c']:.3f} °C。49 个日期的城市平均有符号偏差",
        f"绝对值均值为 {signed['date_level_mean_signed_residual_mae_c']:.3f} °C；"
        "去掉各日期城市均值",
        f"后，日期内社区残差 MAE 仍为 {signed['within_date_centered_residual_mae_c']:.3f} °C，",
        f"日期内空间 MAD 的日期中位数为 {signed['median_date_spatial_mad_c']:.3f} °C。",
        f"同一社区跨日期残差 MAD 的社区中位数为 {signed['median_tract_temporal_mad_c']:.3f} °C。",
        "因此剩余误差主要不是单个城市日整体偏移。",
        "",
        f"用社区全期残差中位数描述的长期分量只占总方差约 "
        f"**{signed['stable_variance_fraction']:.1%}**。社区年度偏差的跨年 Spearman 为 "
        f"{signed['tract_cross_year'][0]['spearman']:.3f}、"
        f"{signed['tract_cross_year'][1]['spearman']:.3f}、"
        f"{signed['tract_cross_year'][2]['spearman']:.3f}；空间块对应值为 "
        f"{signed['block_cross_year'][0]['spearman']:.3f}、"
        f"{signed['block_cross_year'][1]['spearman']:.3f}、"
        f"{signed['block_cross_year'][2]['spearman']:.3f}。固定区域偏热/偏冷只弱稳定，且已有固定",
        "粗尺度空间修正已触发停止条件。以下仅列至少覆盖 3 年、25 个日期的描述性极端空间块：",
        "",
        "| 方向 | 空间块 | 残差中位数 °C | 日期数 | 正残差日期比例 |",
        "|---|---|---:|---:|---:|",
    ]
    for direction, table in (("偏热", hot), ("偏冷", cold)):
        for row in table.itertuples():
            lines.append(
                f"| {direction} | `{row.spatial_block}` | "
                f"{row.median_signed_residual_c:+.3f} | {row.dates} | "
                f"{row.positive_date_fraction:.3f} |"
            )

    lines += [
        "",
        "## 绝对残差与既有观测质量",
        "",
        "所有连续 QA 关联均在每个日期内部计算，再汇总日期相关；目标场景信息只用于误差诊断，",
        "不是预测特征，也不改变评分样本。",
        "",
        "| 字段 | 缺失行 | 日期中位 Spearman | 2022 / 2023 / 2024 | "
        "高四分位−低四分位误差 °C | 跨年规则 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in quality:
        lines.append(
            f"| `{row['field']}` | {row['missing_rows']} | "
            f"{number(row['overall_median_date_spearman'])} | {year_values(row)} | "
            f"{number(row['overall_median_top_minus_bottom_c'])} | "
            f"{row['same_direction_signal']} |"
        )

    platform_mae = scene["platform_date_mean_absolute_residual_c"]
    lines += [
        "",
        f"没有质量字段通过预设的跨年同方向规则（通过数 {len(quality_signals)}）。"
        f"Landsat-8 覆盖 {scene['platform_date_counts']['landsat-8']} 个日期、日期平均绝对残差 "
        f"{platform_mae['landsat-8']:.3f} °C；Landsat-9 覆盖 "
        f"{scene['platform_date_counts']['landsat-9']} 个日期、"
        f"{platform_mae['landsat-9']:.3f} °C。",
        "平台差异没有随机或配对日期设计，不能解释为平台效应。每个日期的 overpass ID 和场景集合",
        "都唯一，全部来自相同 041036+041037 path-row 组合、每条记录均为 2 个源场景，",
        "footprint_fraction 恒为 1。现有表没有视角或太阳角字段；因此场景/过境来源及观测几何",
        "无法与日期条件分离。eligible-land 像元数和身份在社区内保持不变。",
        "",
        "## 既有气象的可用性与城内分辨率",
        "",
        "本地共有 21 个 Daymet 字段，原生约 1 km，并按 eligible land 汇总到社区；所有窗口都在",
        "目标日前一日结束，因此属于预测时可取得的信息。本地没有目标日或过境后的气象字段。",
        "下表列按日期中位相关绝对值最大的字段；关联是提出实验的依据，不是因果或预测增益证明。",
        "",
        "| 字段 | 日期内 SD 中位数 | 残差 Spearman 中位数 | 2022 / 2023 / 2024 | "
        "高−低四分位残差 °C | 通过规则 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in weather_ranked[:10]:
        lines.append(
            f"| `{row['field']}` | {number(row['median_within_date_sd'])} | "
            f"{number(row['overall_median_date_spearman'])} | {year_values(row)} | "
            f"{number(row['overall_median_top_minus_bottom_c'])} | "
            f"{row['same_direction_signal']} |"
        )

    lines += [
        "",
        "通过规则的字段为 `daymet_tmax_c_mean_prev_1d`、"
        "`daymet_dayl_s_mean_prev_3d` 和 `daymet_dayl_s_mean_prev_7d`。后两者高度冗余，且日长",
        "的城内梯度主要是稳定位置代理；结合固定空间修正已失败，它们不应成为下一轮候选。",
        "前一日最高气温在三个年份同方向，分年相关为 0.232 / 0.129 / 0.325，日期内 SD",
        "中位数约 1.97 °C，而且它不在现有 18 静态 + 5 lagged-Sentinel 相对模型中。",
        "",
        "## 证据表",
        "",
        "| 假设 | 支持证据 | 反例/限制 | 独立规模 | 预测时可取得 | 下一步最小验证 |",
        "|---|---|---|---|---|---|",
    ]
    evidence_rows = [
        (
            "6近邻统计来自自身、重复或重叠结构",
            "邻居集合确有重叠，焦点社区统计不独立。",
            "自身=0、焦点内重复=0；观测中位 ρ=0.890，置换 97.5% 上界=0.013。",
            "公开几何可用，但这里只作诊断。",
            "无需；计算结构不足以解释观测值。",
        ),
        (
            "模型遗漏固定长期社区偏差",
            f"社区中位残差映射方差占比={signed['stable_variance_fraction']:.3f}。",
            "社区跨年 ρ 中位仅 0.165，固定粗尺度空间修正也已失败。",
            "几何可用；预测时不能读取目标历史。",
            "停止固定空间修正，不再调尺度。",
        ),
        (
            "既有观测质量差异解释较大误差",
            "跨年一致字段：无。",
            "场景/过境与日期混淆，且缺少视角和太阳角。",
            "目标场景 QA 为观测后信息，仅供诊断。",
            "未来用重复观测核查测量与支持。",
        ),
        (
            "目标日前 Daymet 空间梯度包含遗漏的动态信号",
            "前一日 tmax 在三个年份同方向；日长字段亦过阈值但更像位置代理。",
            "重复使用三个开发年份，关联不能证明增益或因果。",
            "可用；审计窗口全部结束于 d-1。",
            "只做一个固定的 d-1 tmax 梯度实验。",
        ),
    ]
    for hypothesis, support, counterexample, availability, validation in evidence_rows:
        lines.append(
            f"| {hypothesis} | {support} | {counterexample} | 49 日期 / 71 块 | "
            f"{availability} | {validation} |"
        )

    lines += [
        "",
        "## 唯一建议：A",
        "",
        "有一个跨年同方向且预测时可用的新增信号，所以下一步可以提出**一个固定小实验**：保持",
        "现有相对模型为默认、绝对温度水平模型固定，只增加一个由训练折定义和标准化的",
        "`daymet_tmax_c_mean_prev_1d` 城内梯度项，并继续用完全相同的严格时间向前外层年份、",
        "评分行、权重、中心化和 5% 升级门槛。只设现有模型与这个单一候选，不搜索窗口、模型",
        "或空间尺度。日长字段不进入候选。即使该实验获胜，也仍是复用年份的开发证据，需要未来",
        "独立年份确认。当前模型在该实验前继续保持默认。",
        "",
    ]
    text = "\n".join(lines)
    REPORT.write_text(text, encoding="utf-8")
    (OUTPUT / "REPORT.zh-CN.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
