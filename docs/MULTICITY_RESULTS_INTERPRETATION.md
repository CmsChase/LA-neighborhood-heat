# Multicity Results Interpretation / 多城市结果解读

> **Status / 状态：authenticated frozen evaluation / 已认证冻结评估**
> This document interprets the completed evaluation without altering any model,
> threshold, target, or output. The authenticated evaluation artifacts remain
> the numerical source of truth. / 本文只解释已完成结果，不修改任何模型、阈值、目标或
> 输出；所有数字以已认证评估产物为准。

## 1. Bottom line / 核心结论

M2 showed a favorable aggregate signal: equal-city/equal-date MAE was 6.9222 °C,
compared with 9.7381 °C for B1, a 28.916% reduction with a 95% crossed-bootstrap
interval of 14.106%–43.514%. Nevertheless, this was **not a successful
three-city confirmation**. The formal state is `inconclusive_sample_size`; the
sample-support and no-city-degradation point gates failed, and the entire
reliability gate failed.

M2 的整体点估计有利：等城市、等日期 MAE 为 6.9222 °C，B1 为 9.7381 °C，相对降低
28.916%，交叉 bootstrap 95% 区间为 14.106%–43.514%。但这**不是三城确认成功**。
正式状态为 `inconclusive_sample_size`：点预测的样本支撑门和“无城市退化”门失败，完整
可靠性门也失败。

The scientifically useful finding is therefore narrower: the full M2 pipeline
showed transferable predictive signal in some external settings, but performance
was heterogeneous and the frozen model did not know reliably when to abstain.

因此，科学上可支持的结论更窄：完整 M2 流程在部分外部环境中表现出可迁移的预测信号，
但城市间表现不一致，而且冻结模型不能可靠识别何时应该拒绝预测。

## 2. Evidence support / 证据支撑

| City / 城市 | Candidate dates / 候选日期 | Usable dates / 有效日期 | Evaluated rows / 评估行 | 5 km blocks / 空间块 |
|---|---:|---:|---:|---:|
| Phoenix | 22 | 21 | 7,585 | 59 |
| Houston | 21 | 4 | 2,165 | 88 |
| Chicago | 21 | 3 | 1,457 | 33 |
| **Total / 合计** | **64** | **28** | **11,207** | **180** |

All 64 candidate city-dates were physically present. The 36 excluded dates all
failed `insufficient_date_tract_retention`; none failed because a source
footprint was absent. Among 23,748 invalid tract-date rows on failed dates,
21,253 (89.5%) had fewer than 20 valid pixels and 2,495 (10.5%) had less than
60% valid-pixel support. Another 1,734 rows passed tract-level criteria but were
excluded because their date retained fewer than half of the city's tracts.

64 个候选城市—日期均实际存在。36 个排除日期全部属于
`insufficient_date_tract_retention`，没有源覆盖范围缺失。失败日期的 23,748 个无效
tract-date 中，21,253 个（89.5%）有效像元少于 20，2,495 个（10.5%）有效像元比例
低于 60%；另有 1,734 行通过普查区级标准，但因所属日期保留的普查区不足一半而被排除。

This distinction matters: the sample-size failure arose from unchanged
clear-sky/QA support rules, not from silently dropping unavailable city files.
It also means the evaluated sample may favor clear-sky conditions.

这个区别很重要：样本量失败来自不变的晴空/QA 支撑规则，而不是城市文件缺失后被静默
删除；同时，它也提示最终样本可能偏向晴空条件。

## 3. Prespecified point-prediction gates / 预设点预测门

| Gate / 门槛 | Required / 要求 | Observed / 结果 | Status / 状态 |
|---|---|---|---|
| Relative MAE improvement | `R ≥ 10%` | **28.916%** | **PASS / 通过** |
| Bootstrap lower bound | 95% CI lower bound `> 0` | **14.106%** (`CI 14.106%–43.514%`) | **PASS / 通过** |
| No city degradation | M2 no worse in every city | **Phoenix degraded by 57.28%** | **FAIL / 失败** |
| Sample support | ≥30 city-dates and ≥8 per city | **28 total; Phoenix 21, Houston 4, Chicago 3** | **FAIL / 失败** |
| Overall point claim | Every component gate passes | Two of four component gates failed | **NOT MET — `inconclusive_sample_size` / 未通过——样本量不足** |

The positive confidence interval answers a limited aggregate question under the
frozen equal-city/equal-date estimator. It cannot override either the minimum-
date rule or the observed Phoenix degradation.

正的置信区间只回答冻结的等城市、等日期总体估计问题，不能覆盖最低日期数规则，也不能抵消
Phoenix 已观察到的退化。

## 4. City heterogeneity / 城市差异

| City / 城市 | B1 MAE °C | M2 MAE °C | Relative improvement / 相对改善 | Coverage / 区间覆盖 | Retention / 保留率 | Median per-date Spearman / 日期相关中位数 |
|---|---:|---:|---:|---:|---:|---:|
| Phoenix | 3.1076 | 4.8878 | **−57.28%** | 43.757% | 71.246% | 0.462 |
| Houston | 16.4312 | 8.4488 | **48.58%** | 63.326% | 20.323% | 0.749 |
| Chicago | 9.6754 | 7.4300 | **23.21%** | 24.571% | 29.581% | 0.276 |

- **Phoenix:** B1 was already strong, while M2 introduced substantial warm-
  season bias on multiple dates. The only city meeting the 60% retention floor
  was also the city where M2 lost to B1. / B1 本身较强，M2 在多个日期引入明显偏差；它是
  唯一达到 60% 保留率的城市，却也是 M2 输给 B1 的城市。
- **Houston:** M2 greatly improved the date-averaged point error and retained
  strong within-date ranking, but this rests on only four usable dates and is
  influenced by a severe post-hoc data-quality alarm described below. / M2 的
  日期平均点误差和日内排序明显更好，但只有 4 个有效日期，且受到下述严重事后数据质量
  警报影响。
- **Chicago:** M2 improved absolute error, but only three dates survived and
  both rank performance and interval coverage were weak. / M2 改善了绝对误差，
  但只有 3 个日期保留，排序能力和区间覆盖都较弱。

These patterns argue against a single universal statement about transfer. The
full M2 pipeline outperformed B1 in Houston and Chicago under the available
dates but underperformed B1 in Phoenix. Because M2 and B1 differ in both model
class and feature set, this comparison does not isolate the effect of any one
feature group.

这些模式不支持“一套模型普遍跨城成功”的说法。在现有日期上，完整 M2 流程在 Houston 和
Chicago 优于 B1，却在 Phoenix 低于 B1。由于 M2 与 B1 同时采用了不同模型类别和特征集，
这一比较不能单独归因于某一组新增特征。

## 5. Reliability and abstention gates / 可靠性与拒绝预测门

| Gate / 门槛 | Required / 要求 | Observed / 结果 | Status / 状态 |
|---|---|---|---|
| Overall 90% interval coverage | 85%–95% | **45.043%** | **FAIL / 失败** |
| Every-city interval coverage | ≥80% in each city | **Phoenix 43.757%; Houston 63.326%; Chicago 24.571%** | **FAIL in all cities / 三城均失败** |
| Every-city retention | ≥60% in each city | **Phoenix 71.246%; Houston 20.323%; Chicago 29.581%** | **FAIL in Houston and Chicago / 两城失败** |
| Accepted-set error improvement | ≥10% | MAE **5.6864→6.2053 °C**; improvement **−9.125%** | **FAIL / 失败** |
| Overall reliability | Every component gate passes | No | **NOT MET / 未通过** |

Interval calibration learned from Los Angeles 2024 did not transfer. More
importantly, wider intervals did not rank external error well enough for the
fixed abstention rule: the accepted subset was less accurate, not more
accurate. This reliability mechanism should not be deployed in its current
form.

基于洛杉矶 2024 年的区间校准没有成功迁移。更关键的是，区间宽度未能充分排序外部误差，
导致冻结拒绝规则保留的子集反而更不准确。当前可靠性机制不应直接部署。

## 6. Houston 2025-07-25: post-hoc alarm / Houston 2025-07-25：事后警报

This date remains in every formal metric. It contained 400 evaluated rows, of
which 43 targets were below 0 °C and 147 were below 20 °C; the minimum was
−26.27 °C. In the sub-zero group, the mean row-level median ST-uncertainty
measure was 6.38 K, and target temperature correlated −0.878 with that
uncertainty measure. These values are physically surprising for daytime summer
surface temperature and raise a QA alarm, but the audit does not by itself
prove that the observations are invalid.

该日期仍包含在全部正式指标中。400 个评估行中，43 个目标值低于 0 °C，147 个低于
20 °C，最小值 −26.27 °C；负值组逐行 ST 不确定性中位数的均值为 6.38 K，目标温度与该
不确定性指标相关为 −0.878。这些数值对夏季白天地表温度而言异常，构成 QA 警报，但审计
本身不能证明观测一定无效。

A post-hoc diagnostic excluding this one date changes aggregate relative
improvement from 28.9% to approximately 36.7%. That value must be labeled
**post-hoc** wherever shown. It cannot replace the 28.916% formal estimate,
change a gate, or turn the result into confirmation.

事后诊断若排除这一日期，总体相对改善会从 28.9% 变为约 36.7%。该数字无论出现在哪里都
必须标为**事后分析**；它不能替代正式的 28.916%，不能修改门槛，也不能把结果变成确认
成功。

## 7. What can and cannot be claimed / 可以与不可以声称什么

**Supported / 可以声称：**

- Predictions were committed before external target access, and the model was
  not refit or recalibrated on external labels. / 预测在外部目标开放前锁定，模型没有用
  外部标签重拟合或重校准。
- M2 had a favorable aggregate point estimate and positive crossed-bootstrap
  interval in the available external evidence. / 在现有外部证据中，M2 的总体点估计有利，
  交叉 bootstrap 区间为正。
- Transfer differed sharply by city, and the uncertainty/abstention system did
  not transfer reliably. / 迁移表现存在显著城市差异，不确定性与拒绝预测系统未可靠迁移。

**Not supported / 不可以声称：**

- “M2 succeeded across all three cities.” / “M2 在三城均成功。”
- “The external study confirmed the primary hypothesis.” / “外部研究确认了主假设。”
- “The interval system knows when predictions are wrong.” / “区间系统知道何时预测错误。”
- “The Houston alarm may simply be deleted.” / “Houston 异常日可以直接删除。”
- “LST predictions measure air temperature or human heat risk.” / “LST 预测就是气温或人体
  热风险。”

## 8. Limitations / 局限

1. **Insufficient and uneven date support.** Houston and Chicago have only four
   and three usable dates; 36 of 64 candidates failed the unchanged retention
   rule. / 日期支撑不足且不均衡，两城分别只有 4 和 3 个有效日期。
2. **Clear-sky selection.** Landsat QA and tract-retention rules preferentially
   preserve dates with adequate clear pixels. / Landsat QA 与日期保留规则可能偏向晴空日期。
3. **Target-quality warning.** The Houston alarm can materially affect effect
   size, but its cause has not been independently resolved. / Houston 警报会明显影响效应量，
   但原因尚未独立查明。
4. **City shift.** A Los-Angeles-trained mean model and Los-Angeles-calibrated
   intervals need not transfer to desert, humid, or continental climates. /
   洛杉矶训练的均值模型和区间校准不必然适用于沙漠、湿热或大陆性气候。
5. **Only three fixed external cities.** They are contrasting case studies,
   not a probability sample of U.S. cities. / 三城是固定对照案例，不是美国城市的概率样本。
6. **Surface heat only.** The endpoint is clear-sky daytime LST at census-tract
   scale, not personal exposure, morbidity, or causal intervention effect. /
   终点是普查区尺度晴空白天 LST，不是个人暴露、疾病或干预因果效应。

## 9. Next experiment / 下一实验

The current claim must remain closed. A new, separately frozen experiment
should proceed in this order:

当前主张必须保持封存。新的、单独冻结的实验应按以下顺序进行：

1. **Resolve target QA independently.** Reproduce low-temperature Houston
   pixels from source scenes, inspect QA/ST-uncertainty and scaling, compare
   overlapping scenes or an independent thermal product, and preregister any
   physically motivated exclusion before scoring a new cohort. / 独立复核 Houston
   低温像元、QA/ST 不确定性与缩放，并用重叠场景或独立热产品核验；任何新排除规则必须在
   新队列评分前预注册。
2. **Increase temporal support.** Add multiple years or a prospectively
   collected season so every city can meet the minimum-date requirement under
   the same QA rules. / 增加年份或开展前瞻季节采集，使各城在同一 QA 规则下都能满足最低
   日期数。
3. **Redesign uncertainty transfer.** Test climate-stratified or multi-source
   calibration and a separately learned error-risk score; validate abstention
   on development cities only. / 测试分气候或多源城市校准，并单独学习误差风险分数；拒绝
   规则只能在开发城市验证。
4. **Use new untouched cities for confirmation.** After formally converting
   the current three-city labels to development data, freeze a new model and
   evaluate once on cities whose targets remain unopened. / 当前三城标签正式转为开发数据后，
   冻结新模型，并在目标仍未开放的新城市上进行一次性确认。
5. **Separate a real-time extension.** If building an operational tool, replace
   observation-based weather with issue-time archived forecasts and run a
   prospective commitment protocol. / 若开发实时工具，应使用带发布时间的历史预报代替观测型
   天气，并采用前瞻预测锁定协议。

## 10. Auditable sources / 可审计来源

- `data/processed/multicity/external_evaluation/EXTERNAL_EVALUATION_COMPLETE.json`
- `data/processed/multicity/external_evaluation/summary.json`
- `data/processed/multicity/external_evaluation/bootstrap.json`
- `data/processed/multicity/external_evaluation/city_metrics.parquet`
- `data/processed/multicity/external_evaluation/date_metrics.parquet`
- `manifests/multicity/targets/THREE_CITY_EXTERNAL_TARGETS_COMPLETE.json`
- `data/processed/multicity/external_evaluation_report/EXTERNAL_EVALUATION_EVIDENCE.json`

Formal numbers should be regenerated from these authenticated artifacts rather
than copied from screenshots. / 正式数字应从这些已认证产物重新生成，而不是从截图抄录。
