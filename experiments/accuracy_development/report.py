"""Describe completed nested results, including neighborhood hotspot ranking."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "exports/RELATIVE_ACCURACY_DEVELOPMENT"


def main():
    summary = json.loads((OUTPUT / "summary.json").read_text())
    rows = pd.read_parquet(OUTPUT / "nested_oof.parquet")
    spec = importlib.util.spec_from_file_location(
        "relative_metrics", ROOT / "experiments/m3_relative_temperature/run.py"
    )
    relative = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relative)
    frames = []
    for name in ["B1", "relative_static", "relative_context", "relative_blend", "selected"]:
        frame = rows[["city_id", "tract_geoid", "target_date", "observed", name]].rename(
            columns={"observed": "observed_lst_c", name: "predicted_lst_c"}
        )
        frame["evidence_role"] = "source_nested_development"
        frame["model_id"] = name
        frames.append(frame)
    dates = relative.calculate_date_metrics(pd.concat(frames, ignore_index=True), 0.2)
    cities, overall = relative.aggregate_metrics(dates)
    dates.to_csv(OUTPUT / "ranking_by_date.csv", index=False)
    cities.to_csv(OUTPUT / "ranking_by_city.csv", index=False)
    overall.to_csv(OUTPUT / "ranking_overall.csv", index=False)
    metrics = pd.DataFrame(summary["metrics"])
    aggregate = metrics[metrics.city == "equal_city"].set_index("model")
    interval = summary["bootstrap"]
    text = [
        "# 相对温差精度开发结果",
        "",
        "2026-09-13。目标：优先提高同城同日社区相对地表温差精度，同时保留绝对温度。",
        "本轮为源城市事后开发；没有使用 LA 2025 或四个已揭盲压力城市。",
        "",
        "## 结果",
        "",
        "| 模型 | 相对 MAE °C | 完整预测集合中心化敏感性 MAE °C | 绝对 MAE °C |",
        "|---|---:|---:|---:|",
    ]
    for name, row in aggregate.iterrows():
        text.append(
            f"| {name} | {row.anomaly_mae_c:.4f} | "
            f"{row.support_sensitivity_mae_c:.4f} | {row.absolute_mae_c:.4f} |"
        )
    text += [
        "",
        "relative_static 是现有 23 特征相对 HGB；relative_context 增加天气与季节，"
        "使用 46 特征；relative_blend 为两者固定平均。selected 是每个外层折仅用"
        "其余城市内部验证选出的候选，才是本次选择程序的主评估。",
        "",
        f"主比较相对现有模型改善 {summary['relative_improvement']:.2%}。"
        f"升级门槛通过：{summary['promotion_passed']}。",
        f"旧相对模型减 selected 的配对 MAE 差 95% 区间："
        f"[{interval['lower_c']:.4f}, {interval['upper_c']:.4f}] °C。",
        "该区间独立重采样每城的完整日期与 5 km 空间块，条件限定于这四座城市，"
        "不包含重新选模型的不确定性，也不是新城市总体的置信区间。",
        "",
        "## 逐城主比较",
        "",
        "| 城市 | 原相对 MAE | selected MAE | 改善 °C |",
        "|---|---:|---:|---:|",
    ]
    for city, gain in summary["city_gain_c"].items():
        table = metrics[metrics.city == city].set_index("model")
        text.append(
            f"| {city} | {table.loc['relative_static', 'anomaly_mae_c']:.4f} | "
            f"{table.loc['selected', 'anomaly_mae_c']:.4f} | {gain:.4f} |"
        )
    text += [
        "",
        "## 热点识别",
        "",
        "最热 20% 以评分样本定义，排序并列按 tract 标识固定打破；标识没有进入模型。",
        "| 模型 | 城市中位 Spearman 的中位数 | 热点平均精度 AP | 热点召回率 |",
        "|---|---:|---:|---:|",
    ]
    for row in overall.itertuples():
        text.append(
            f"| {row.model_id} | {row.median_city_spearman:.4f} | "
            f"{row.hotspot_average_precision:.4f} | {row.hotspot_recall:.4f} |"
        )
    text += [
        "",
        "## 使用边界与产物",
        "",
        "96,061 行，132 个城市—日期，254 个空间块；各城与各日期等权。"
        "评分是有效观测样本内的相对差异，完整城市真实中位数不可观测。"
        "敏感性列保留完整预测集合中心化输出，但仍以评分样本目标为参照。",
        "",
        "绝对输出 = B1 预测的同城同日中位数 + 相对输出。相对误差的改善并不意味着"
        "绝对误差也改善，且绝对输出仍是地表温度历史重建，不是人体热暴露。",
        "",
        f"全源数据研究候选为 `{summary['final_development_candidate']}`，按四个外层"
        "固定候选分数选择并重新拟合。该候选的选择分数带有选择偏差，"
        "不能替代 selected 程序的外层评估或新的独立验证。",
        "",
        "模型：development_model.joblib；模型校验：model_metadata.json；"
        "完整输入/代码指纹：provenance.json；逐行外层结果：nested_oof.parquet。"
        "若升级门槛失败，此文件仅保留研究候选，不表示已取得可靠升级。",
        "",
    ]
    text += [f"本轮训练指纹：`{summary['signature']}`。", ""]
    report = "\n".join(text)
    (OUTPUT / "REPORT.zh-CN.md").write_text(report, encoding="utf-8")
    (ROOT / "docs/M3_RELATIVE_ACCURACY_DEVELOPMENT.zh-CN.md").write_text(report, encoding="utf-8")
    paths = [
        Path(__file__),
        ROOT / "experiments/m3_relative_temperature/run.py",
        OUTPUT / "summary.json",
        OUTPUT / "nested_oof.parquet",
        OUTPUT / "REPORT.zh-CN.md",
        OUTPUT / "ranking_by_date.csv",
        OUTPUT / "ranking_by_city.csv",
        OUTPUT / "ranking_overall.csv",
    ]
    fingerprints = {}
    for path in paths:
        with path.open("rb") as handle:
            fingerprints[str(path.relative_to(ROOT))] = hashlib.file_digest(
                handle, "sha256"
            ).hexdigest()
    (OUTPUT / "report_provenance.json").write_text(
        json.dumps({"training_signature": summary["signature"], "files": fingerprints}, indent=2),
        encoding="utf-8",
    )
    print(aggregate.to_string())
    print(overall.to_string(index=False))


if __name__ == "__main__":
    main()
