# Competition Narrative / 比赛项目陈述

> **Communication document / 沟通用途文档**  
> This file explains the study for a poster, paper, and oral defense. It does
> not replace the frozen protocol or authorize any data access, model change,
> or evaluation. If wording here conflicts with an authenticated manifest, the
> manifest and locked protocol control.  
> 本文用于论文、展板和答辩陈述，不替代冻结协议，也不授权读取数据、修改模型或
> 执行评估。如本文与认证清单不一致，以认证清单和冻结协议为准。

## 1. Working title / 项目题目

**English:** Can Public Data Predict Neighborhood Surface Heat Across Cities? A
Target-Blind Transfer and Reliability Study

**中文：** 公共数据能否跨城市预测社区尺度地表热？一项基于目标盲测的迁移与可靠性研究

## 2. One-sentence claim / 一句话主线

**中文：** 我们不是再做一张“哪里热”的地图，而是在预测值生成并锁定之后才打开真实
地表温度，检验一个只在洛杉矶训练的公共数据模型，能否在三座气候和城市形态不同的
城市中零样本迁移，并在不可靠时通过不确定性区间选择暂不作答。

**English:** Rather than making another map of where heat has already been
measured, we commit predictions before opening the corresponding temperature
labels, then test whether a public-data model trained only in Los Angeles can
transfer zero-shot to three contrasting cities and identify predictions that
should be withheld.

## 3. Thirty-second pitch / 30 秒介绍

**中文：** 卫星可以测量城市地表温度，但热红外观测受重访周期、云层和缺测限制。本项目
用公开的天气、土地覆盖、地形、日历和滞后 Sentinel-2 非热红外特征，预测“人口普查区 ×
Landsat 过境日期”的白天地表温度。模型只使用洛杉矶 2020–2023 年标签训练，并用洛杉矶
2024 年数据校准不确定性；随后在完全未用于训练或调参的 Phoenix、Houston 和 Chicago
2025 年数据上做一次性盲测。主模型 M2 与只含天气和日历的 B1 基线比较，同时检验 90%
预测区间和拒绝预测规则是否可靠。

**English:** Thermal satellites measure urban surface heat, but revisits,
clouds, and missing observations leave gaps. This project predicts daytime LST
for each census-tract-by-Landsat-date unit using public weather, land cover,
topography, calendar, and lagged non-thermal Sentinel-2 features. Labels from
Los Angeles in 2020–2023 fit the models, Los Angeles 2024 calibrates uncertainty,
and sealed 2025 labels from Phoenix, Houston, and Chicago provide a one-time
zero-shot external test. The primary M2 model is compared with a weather-and-
calendar B1 baseline, while a 90% interval and abstention rule test whether the
system knows when its predictions are unreliable.

## 4. Research question and scope / 研究问题与边界

### Locked primary question / 已冻结的主问题

> Can a target-blind model trained on public weather, land-use, geography, and
> lagged satellite features predict neighborhood-scale daytime land-surface
> temperature on future dates and in unseen U.S. cities, with calibrated
> uncertainty?

> 一个在目标值不可见条件下、使用公开天气、土地利用、地理和滞后卫星特征训练的模型，
> 能否预测未来日期与未见过美国城市的社区尺度白天地表温度，并提供经过校准的不确定性？

The measured outcome is QA-filtered **daytime land-surface temperature (LST)**
from Landsat Collection 2 Level-2. It is a surface-heat hazard proxy. It is not
air temperature, personal exposure, heat illness, mortality, or a causal
estimate of any intervention.

测量目标是经过质量筛选的 Landsat Collection 2 Level-2 **白天地表温度（LST）**。
它是地表热危害的代理量，不等同于空气温度、人体暴露、热疾病或死亡风险，也不是任何
城市干预措施的因果效应。

### Secondary questions / 次级问题

1. Does M2 improve neighborhood-level point prediction over a legal
   weather-and-calendar baseline in every external city?
2. Do the frozen 90% prediction intervals maintain coverage after transfer?
3. Can interval width identify cases where abstaining improves retained-set
   accuracy?
4. How well does M2 rank within-date relative hotspots, and where do residuals
   remain spatially clustered?

1. M2 是否在每个外部城市都比合法的天气—日历基线更准确？
2. 冻结的 90% 预测区间跨城市后能否维持覆盖率？
3. 区间宽度能否识别不可靠预测，使保留样本的误差下降？
4. M2 对同一天相对热点的排序能力如何，残差还在哪些地区形成空间聚集？

## 5. Why the study matters / 研究意义

The scientific problem is not whether urban heat exists; it is whether a model
learned in one urban system can retain useful neighborhood-scale information in
different climates and urban forms. A model that performs well only where it
was trained has limited scientific and practical value. A model that also
reports when it is uncertain is more honest and potentially more useful for
screening data gaps.

本研究的核心不是证明“城市存在热区”，而是检验一个城市中学到的规律能否在不同气候和
城市形态中保留社区尺度的信息。只在训练城市有效的模型科学价值和应用价值都有限；能够
同时说明“何时不可信”的模型，更适合用于缺测筛查和后续观测优先级排序。

The project contributes three linked ideas:

- **external generalization:** Los Angeles supplies labels; three other cities
  supply the confirmation test;
- **target-blind evidence:** predictions are committed before external thermal
  targets are opened; and
- **reliability, not accuracy alone:** point error, calibrated intervals, and
  an abstention rule are evaluated together.

### The competition story / 比赛叙事主线

1. The original Los Angeles holdout showed a large favorable point estimate,
   but its confidence interval crossed zero. / 洛杉矶留出测试的点估计很有前景，但
   置信区间跨过零，不能宣称已经得到完整确认。
2. Instead of tuning until the result looked stronger, the study asks a harder
   question: does the signal survive in unseen cities? / 项目没有继续调参追求更漂亮的
   数字，而是提出更难的问题：规律能否在从未见过的城市中成立？
3. Los-Angeles-specific geography was replaced by a portable four-city
   feature-and-support contract. / 项目建立了统一、可迁移的四城特征与空间支撑合同，
   而不是把洛杉矶专用变量硬套到其他城市。
4. External predictions and uncertainty decisions were fixed before external
   labels were available to the evaluator. / 外部真实温度进入评估前，预测、区间和拒绝
   决策已经固定。
5. The final contribution is therefore an honest test of both transfer and
   model self-awareness, whether it succeeds or fails. / 因此，无论最终是否通过门槛，
   研究都给出了对“跨城市迁移”和“模型是否知道自己不确定”的可审计检验。

## 6. Hypotheses / 假设

### H1 — Point prediction / 点预测假设

M2 will reduce equal-city, equal-date MAE by at least 10% relative to B1 across
Phoenix, Houston, and Chicago. The prespecified confirmation also requires the
95% confidence-interval lower bound for the relative improvement to exceed
zero, no individual city to become worse, at least 30 usable city-dates in
total, and at least eight usable dates per city.

M2 在 Phoenix、Houston 和 Chicago 的等城市、等日期加权 MAE 将比 B1 至少降低 10%。
同时，95% 置信区间下界必须大于 0、任何单个城市都不能退化、总有效城市—日期不少于
30，且每城不少于 8 个有效日期，才能判定主点预测假设通过。

### H2 — Reliability / 可靠性假设

The conformalized interval system will achieve 85%–95% overall coverage, at
least 80% coverage in every external city, retain at least 60% of predictions
in every external city, and reduce accepted-set MAE by at least 10% relative
to using all predictions.

经保形校准的区间系统应达到总体 85%–95% 覆盖率、每个外部城市至少 80% 覆盖率、每个
外部城市至少保留 60% 的预测，并使保留样本的 MAE 比全部预测至少降低 10%。

Failure of either gate is a scientific result, not permission to change a
threshold, replace a city, or rerun the test with a retuned model.

任一门槛未通过都属于有效科学结果，不能因此改阈值、替换城市或调参后重新声称盲测。

## 7. Study frame and public data / 研究框架与公开数据

The analysis unit is one **city × 2020 Census tract × physical Landsat overpass
date**. All cities use one harmonized Census 2020 geography and ESA WorldCover
2020 valid non-water support. WorldCover defines where aggregation is valid;
it is not a predictor.

分析单位为一个**城市 × 2020 人口普查区 × Landsat 实际过境日期**。四城使用统一的
Census 2020 地理边界和 ESA WorldCover 2020 有效非水域支撑。WorldCover 只定义汇总
范围，不作为模型特征。

| City / 城市 | Tracts / 普查区 | Role / 角色 | Dates or rows fixed before external scoring / 冻结规模 |
|---|---:|---|---:|
| Los Angeles | 1,096 | Train 2020–2023; calibrate 2024 / 训练与校准 | 98,640 source tract-date keys |
| Phoenix | 375 | 2025 zero-shot external test / 外部盲测 | part of 64 external city-dates |
| Houston | 651 | 2025 zero-shot external test / 外部盲测 | part of 64 external city-dates |
| Chicago | 780 | 2025 zero-shot external test / 外部盲测 | part of 64 external city-dates |
| **Total** | **2,902** | Four-city predictor universe / 四城预测总体 | **136,941 rows; 46 predictors** |

The frozen candidate cohorts contain 73,432 Los Angeles 2020–2023 predictor
rows, 25,208 Los Angeles 2024 predictor rows, and 38,301 predictor-only external
rows. These counts describe the predictor/support universe; the rows entering
model fitting, calibration, or formal evaluation can be lower after unchanged
Landsat QA and date-retention rules are applied.

冻结候选队列包含 73,432 条洛杉矶 2020–2023 预测特征候选行、25,208 条洛杉矶 2024
预测特征候选行，以及 38,301 条只含预测信息的外部城市行。这里是预测支撑规模；经过不变的
Landsat 质量控制和日期保留规则后，实际进入拟合、校准或正式评估的样本数可能更少。

### Frozen predictor families / 冻结特征组

| Family / 特征组 | Information / 信息 | Timing rule / 时间规则 |
|---|---|---|
| Land cover and imperviousness / 土地覆盖与不透水面 | NLCD class fractions and impervious summaries | Static public layers / 静态公开图层 |
| Topography and water proximity / 地形与水体距离 | Elevation, slope, distance to qualifying ocean or Great Lakes shoreline | Same frozen national algorithm in all cities / 四城同一算法 |
| Calendar / 日历 | Sine and cosine of day of year | Known without target access / 无需目标值 |
| Weather / 天气 | 21 Daymet summaries of day length, precipitation, solar radiation, maximum/minimum temperature, vapor pressure, and solar energy | Previous 1, 3, and 7 days only / 仅前 1、3、7 天 |
| Non-thermal satellite / 非热红外卫星 | Lagged Sentinel-2 NDVI, EVI, NDWI, NDBI, and albedo proxy | Acquisitions from `d−60` through `d−1`; never target-day / 仅 `d−60` 至 `d−1` |

Forbidden model inputs include Landsat thermal values and QA, target-day
optical bands, future observations, city ID, tract GEOID, raw coordinates, and
target-city LST summaries or climatology.

模型禁止使用 Landsat 热红外值及其 QA、目标当天光学波段、未来观测、城市 ID、普查区
GEOID、原始坐标，以及任何目标城市 LST 汇总或气候态。

## 8. Target-blind experimental design / 目标盲测设计

The logic is deliberately one-way:

1. Freeze geography, support, 46 predictor names, timing rules, and all keys.
2. Freeze the model classes, parameters, cohorts, metrics, success gates,
   bootstrap, output schema, and planned figures.
3. Build Los Angeles 2020–2024 targets on the new common support.
4. Fit B1/M2 on Los Angeles 2020–2023 and calibrate intervals on Los Angeles
   2024 only.
5. Generate and cryptographically commit all 38,301 external predictions,
   intervals, and abstention flags while external thermal targets remain
   sealed.
6. Issue one append-only claim for the indivisible Phoenix–Houston–Chicago
   target cohort and build all external targets.
7. Authenticate the complete three-city target build, execute the evaluator
   once, authenticate its completion, and only then inspect or publish metrics.
8. Preserve tables, figures, manifests, hashes, software versions, and the
   decision log as the read-only evidence package.

这条单向流程的意义是：外部城市的真实温度不能参与特征选择、调参、插补、区间校准或
阈值选择。任何单城、单日期的部分结果都不用于评分或决策。Los Angeles 2025 是已知的
历史背景，只能作上下文，不能计入新的三城确认性证据。

## 9. Models / 模型

### B1-Transfer — diagnostic baseline / 诊断基线

- Ridge regression, `alpha = 10`.
- Inputs: two calendar features plus 21 lagged Daymet weather features.
- Purpose: ask whether M2 adds useful neighborhood differentiation beyond
  date and weather.
- It is a comparison baseline, not the deployment candidate.

B1 是 Ridge 回归，只使用 2 个日历和 21 个滞后 Daymet 天气特征。它故意缺少土地利用、
地形和 Sentinel 信息，用来检验 M2 是否真正增加了社区空间差异，而不是作为最终工具。

### M2-Transfer — primary point model / 主点预测模型

- Histogram Gradient Boosting with all 46 portable predictors.
- Frozen settings: absolute-error loss, learning rate 0.05, 300 iterations,
  31 leaves, minimum leaf size 50, L2 regularization 1.0, seed 20260719, and no
  random-row early stopping.
- Training weights first give every Los Angeles training date equal total
  weight; all learned preprocessing is fit on Los Angeles 2020–2023 only.

M2 是使用全部 46 个可迁移特征的直方图梯度提升模型。模型类别、参数、样本权重和预处理
都在外部目标开放前冻结。

### CQR and abstention / 保形分位回归与拒绝预测

Two models with 0.05 and 0.95 quantile loss produce an initial interval. Los
Angeles 2024 provides an equal-date-weighted conformal correction to form the
nominal 90% interval. The abstention threshold is the frozen 80th percentile
of corrected interval width in Los Angeles 2024. An external prediction is
withheld only when its width is **strictly greater** than that threshold.

两个 0.05/0.95 分位模型先产生区间，再用洛杉矶 2024 年数据进行等日期加权的保形修正。
修正后区间宽度的第 80 百分位数被固定为拒绝阈值；外部区间宽度严格大于阈值时才标记为
暂不作答。

## 10. Evaluation / 评估

For each city and date, MAE is calculated across eligible tracts. Dates receive
equal weight within a city, and Phoenix, Houston, and Chicago receive equal
weight regardless of their tract or date counts. The primary relative effect is

`R = 1 − external_MAE(M2) / external_MAE(B1)`.

评估先在每个城市—日期内计算普查区 MAE，再在城市内给予每个日期相同权重，最后给予三
座城市相同权重，避免“普查区更多”或“晴天更多”的城市主导结论。

Uncertainty is estimated with 10,000 fixed-seed, city-stratified crossed
complete-date × 5 km spatial-block bootstrap draws. Tract rows are not treated
as independent observations.

不确定性使用固定随机种子的 10,000 次分城市交叉“完整日期 × 5 km 空间块”bootstrap，
不会把同日、邻近的普查区错误地视为相互独立。

Secondary evidence includes per-city MAE/RMSE and signed error, within-date
anomaly MAE, median per-date Spearman correlation, exact top-20% hotspot
metrics, interval coverage and width, weighted interval score, risk–coverage
curves, and predeclared subgroup/spatial diagnostics. Secondary findings cannot
override a failed primary gate.

## 11. Results language / 结果陈述规则

### Authenticated historical anchor — Los Angeles only / 已认证历史背景

The earlier, separate Los Angeles 2025 study found equal-date MAE of 3.1165 °C
for B1 and 2.1650 °C for M2, a 30.53% point reduction. However, the frozen 95%
interval for relative improvement was −10.13% to 58.46%, crossing zero. The
correct statement is **promising held-out predictive signal without full
protocol-level confirmation**. This result motivated the harder transfer test,
but it is excluded from all new success gates.

此前独立的洛杉矶 2025 测试中，B1 与 M2 的等日期 MAE 分别为 3.1165 °C 和 2.1650 °C，
点估计降低 30.53%；但预先规定的相对改善 95% 区间为 −10.13% 至 58.46%，跨过 0。
因此只能表述为“具有前景的留出集预测信号，但未达到完整协议确认”。它是新研究的动机，
不是三城外推成功的证据。

### Authenticated external confirmation / 已认证三城外部评估

The indivisible Phoenix–Houston–Chicago evaluation completed and passed its
read-only integrity authentication. Predictions were committed before target
access, no external model was refit or recalibrated, and all values below come
from the authenticated frozen evaluator—not from a partial run or a post-hoc
replacement analysis.

Phoenix、Houston、Chicago 不可拆分的三城评估已经完成，并通过只读完整性认证。预测在
目标值开放前已经锁定，外部模型没有重新拟合或校准；下列数字全部来自已认证的冻结评估器，
不是运行中间值，也不是事后替代分析。

| Required statement / 必须报告项 | Authenticated result / 已认证结果 |
|---|---|
| Evaluated support / 评估支撑 | **11,207 rows; 28 usable city-dates; 180 5 km blocks / 11,207 行；28 个有效城市—日期；180 个 5 km 空间块** |
| B1 equal-city/equal-date MAE / B1 主 MAE | **9.7381 °C** |
| M2 equal-city/equal-date MAE / M2 主 MAE | **6.9222 °C** |
| Relative improvement `R` / 相对改善 | **28.916%** |
| 95% crossed-bootstrap CI / 95% 交叉 bootstrap 区间 | **14.106% to 43.514%** |
| Usable dates by city / 各城有效日期 | **Phoenix 21/22; Houston 4/21; Chicago 3/21** |
| City direction / 各城方向 | **Phoenix degraded; Houston and Chicago improved / Phoenix 退化；Houston、Chicago 改善** |
| Overall interval coverage / 总体区间覆盖率 | **45.043%** |
| Per-city interval coverage / 各城区间覆盖率 | **Phoenix 43.757%; Houston 63.326%; Chicago 24.571%** |
| Overall retention / 总体保留率 | **55.992%** |
| Per-city retention / 各城保留率 | **Phoenix 71.246%; Houston 20.323%; Chicago 29.581%** |
| Accepted-set MAE / 保留样本 MAE | **6.2053 °C versus 5.6864 °C for all predictions; −9.125% improvement / 相比全部预测的 5.6864 °C，保留样本为 6.2053 °C；改善率 −9.125%** |
| Point-prediction conclusion / 点预测结论 | **NOT MET — `inconclusive_sample_size`; the sample-size and no-city-degradation gates failed / 未通过——样本量不足；样本量门和“无城市退化”门失败** |
| Reliability conclusion / 可靠性结论 | **NOT MET / 未通过** |

The favorable pooled point estimate and confidence interval passed two
component gates: improvement was at least 10%, and the bootstrap lower bound
was above zero. They do **not** establish three-city success. Only 28 of the
required 30 city-dates were usable, Houston and Chicago had fewer than eight
dates each, and M2 was worse than B1 in Phoenix. The reliability system also
failed: coverage was far below its target, Houston and Chicago retention fell
below 60%, and abstention increased rather than reduced MAE.

整体点估计及其置信区间通过了两个组成门槛：改善达到 10%，且 bootstrap 下界高于 0；但这
**不能**表述为三城成功。有效城市—日期只有 28 个，未达到 30 个；Houston 和 Chicago
各自不足 8 个日期；M2 在 Phoenix 反而劣于 B1。可靠性系统也失败：区间覆盖率远低于目标，
Houston 和 Chicago 的保留率不足 60%，而且拒绝预测后 MAE 不降反升。

### City-level results / 分城市结果

| City / 城市 | Dates / 日期 | Rows / 行 | Blocks / 空间块 | B1 MAE °C | M2 MAE °C | Relative change / 相对改善 | Median date Spearman / 日期 Spearman 中位数 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Phoenix | 21 | 7,585 | 59 | 3.1076 | 4.8878 | **−57.28% (degradation / 退化)** | 0.462 |
| Houston | 4 | 2,165 | 88 | 16.4312 | 8.4488 | **48.58% improvement / 改善** | 0.749 |
| Chicago | 3 | 1,457 | 33 | 9.6754 | 7.4300 | **23.21% improvement / 改善** | 0.276 |

Equal-city weighting prevents Phoenix's larger date count from dominating the
primary estimate, but it cannot repair thin date support. Houston and Chicago
therefore provide suggestive, not independently conclusive, city-level
evidence.

等城市权重避免 Phoenix 因日期更多而主导主效应，但不能弥补 Houston 和 Chicago 日期支撑
过少的问题。因此，两城的改善是提示性证据，不能作为各自独立确认。

### QA support audit / QA 支撑审计

All 64 candidate city-dates were present, so no date disappeared because of a
missing source footprint. Twenty-eight dates passed the unchanged QA and
retention rules; all 36 exclusions were classified as
`insufficient_date_tract_retention`. Across failed dates, 23,748 invalid
tract-date rows comprised 21,253 (89.5%) with fewer than 20 valid pixels and
2,495 (10.5%) with valid-pixel fraction below 60%. Separately, 1,734 rows met
the tract-level criteria but were excluded because their entire date retained
fewer than 50% of tracts.

64 个候选城市—日期全部存在，没有任何日期因源覆盖范围缺失而消失。28 个日期通过不变的
QA 与保留规则；其余 36 个全部因 `insufficient_date_tract_retention` 被排除。失败日期中
共有 23,748 个无效 tract-date：21,253 个（89.5%）因有效像元少于 20，2,495 个
（10.5%）因有效像元比例低于 60%。此外，另有 1,734 行本身达到普查区级标准，但因整日
保留的普查区不足 50% 而随该日期一同排除。

### Post-hoc data-quality alarm / 事后数据质量警报

Houston on 2025-07-25 remained part of the formal result under the frozen
rules. Among its 400 evaluated rows, 43 targets were below 0 °C, 147 were below
20 °C, and the minimum was −26.27 °C. In the sub-zero group, the mean of the
row-level median Landsat surface-temperature uncertainty measure was 6.38 K;
the recorded target–uncertainty correlation was −0.878. A **post-hoc** removal
of this date would change the overall point improvement from 28.9% to about
36.7%. This is a diagnostic alarm, not evidence that the date is definitively
wrong, and it does not replace, revise, or rescue the formal result.

按冻结规则，Houston 的 2025-07-25 仍保留在正式结果中。该日 400 行里有 43 个目标值低于
0 °C、147 个低于 20 °C，最小值为 −26.27 °C；负值组逐行 Landsat 地表温度不确定性
中位数的均值为 6.38 K，记录到的目标值—不确定性相关为 −0.878。**事后**删除该日期会使
总体点改善从 28.9% 变为约 36.7%。这只是需要后续核查的数据质量警报，不能证明该日期
必然错误，也不能替代、修改或“挽救”正式结果。

## 12. Competition-safe conclusion / 可用于比赛的准确结论

**English:** The frozen Los-Angeles-trained M2 reduced equal-city/equal-date
MAE by 28.9% relative to B1 (95% crossed-bootstrap CI 14.1% to 43.5%) across
the available external evidence. However, the preregistered point-prediction
claim was inconclusive because only 28 usable city-dates remained, Houston and
Chicago each had fewer than eight, and Phoenix degraded. The frozen interval
and abstention system also failed its reliability gate. No external labels were
used to refit or recalibrate the model.

**中文：** 在现有外部证据中，冻结的洛杉矶训练 M2 相比 B1 将等城市、等日期 MAE 降低了
28.9%（交叉 bootstrap 95% 区间 14.1%–43.5%）。但是，预注册的点预测主张属于样本量
不足下的未定论：最终只有 28 个有效城市—日期，Houston 和 Chicago 各自少于 8 个，且
Phoenix 出现退化。冻结的不确定性区间与拒绝预测系统也未通过可靠性门槛。外部标签没有
用于重新拟合或校准模型。

Do not shorten this to “the model succeeded across three cities.” The honest
result is a favorable aggregate signal accompanied by failed prespecified
confirmation and reliability gates.

不得将其缩写为“模型在三座城市外推成功”。准确结论是：整体信号有利，但预先规定的确认门
和可靠性门均未通过。

## 13. Limitations / 局限

1. **LST is not air temperature or human heat risk.** Surface temperature is
   scientifically useful but cannot substitute for exposure or health data.
2. **This is a historical hindcast, not real-time forecasting.** Daymet is an
   observation-based product. A current/next-day tool would require a separate
   forecast-time weather protocol with issue timestamps.
3. **Three cities are fixed case studies, not a random national sample.** The
   conclusion can describe Phoenix, Houston, and Chicago, not every U.S. city.
4. **Satellite availability is selective.** Clouds, QA, and revisit timing can
   reduce usable dates and may favor clear-sky conditions.
5. **Census-tract aggregation hides within-tract variation.** Results depend on
   the chosen spatial support and do not locate individual exposure.
6. **Transfer can fail under climate or urban-form shift.** Los Angeles 2024
   interval calibration may not remain calibrated in all external cities.
7. **The study is predictive, not causal.** Feature associations do not show
   that changing a land-cover variable would cause the predicted temperature
   change.
8. **Residual spatial dependence may remain.** Spatial-block inference reduces
   false precision but does not guarantee that all spatial structure is modeled.

## 14. Poster and oral-defense structure / 展板与答辩结构

### Poster reading order / 展板阅读顺序

1. **Problem:** thermal observations have neighborhood-scale gaps; transfer is
   harder and more useful to test than another in-city fit.
2. **Question and target:** predict QA-filtered daytime LST, not air temperature
   or health risk.
3. **Blind design:** show the one-way train → calibrate → commit predictions →
   open targets → evaluate sequence.
4. **Data:** one compact map of four cities plus the 46-feature family table.
5. **Models:** one visual contrast—B1 weather/calendar versus M2 all public
   predictors; CQR supplies intervals and abstention.
6. **Primary result:** one B1-versus-M2 city MAE figure with the relative-effect
   interval and explicit gate status.
7. **Reliability:** coverage and risk–coverage figure; say whether abstention
   actually helped.
8. **Limitations and next step:** distinguish historical LST estimation from a
   future prospective forecast product.

### Five-minute oral arc / 五分钟答辩主线

- **0:00–0:40 — Motivation:** “We can map observed heat; the harder question is
  whether a public-data relationship transfers when the answer is hidden.”
- **0:40–1:20 — Target and data:** define LST, tract-date units, four cities,
  and legal lagged inputs.
- **1:20–2:10 — Scientific control:** explain the frozen protocol and prediction
  commitment before external target access.
- **2:10–3:00 — Models:** explain what B1 controls for, what M2 adds, and how CQR
  creates an abstention signal.
- **3:00–4:10 — Results:** report sample support first, then primary effect and
  CI, per-city direction, and reliability gates. Never lead with a secondary
  metric.
- **4:10–5:00 — Meaning:** state what passed or failed, limitations, and the
  separately designed future real-time extension.

## 15. Likely judge questions / 常见答辩问题

**Why predict heat if Landsat already measures it? / Landsat 已经测温，为何还要预测？**  
Landsat thermal observations are intermittent and cloud-sensitive. This study
tests whether non-target public information contains transferable spatial
signal that can help screen gaps. It does not claim to replace validated
thermal observations.

**Why not use a random train/test split? / 为什么不用随机切分？**  
Random tract rows from the same dates and nearby places are correlated and can
make accuracy look artificially high. Time separation, unseen cities, and 5 km
spatial blocks create a harder, more realistic generalization test.

**Why keep B1 if it gives similar values across neighborhoods? / B1 空间差异很小，为什么保留？**  
That is its purpose. B1 represents what date and recent weather alone can do.
M2 earns its scientific value only if land-use, geography, and lagged satellite
features improve on that legal baseline.

**How did you prevent leakage? / 如何避免泄漏？**  
Target-day/future inputs and identifiers were prohibited; preprocessing used
Los Angeles 2020–2023 only; the protocol and predictions were committed before
external thermal targets were opened; and partial external results cannot be
used for tuning.

**Why these three cities? / 为什么选择这三座城市？**  
They provide prespecified contrasts—hot arid Phoenix, hot humid Houston, and
continental/Great Lakes Chicago—against coastal Mediterranean Los Angeles.
They are contrasting case studies, not a representative sample of all cities.

**What makes the project more than a website? / 项目是否只是一个网站？**  
The website is only a presentation layer. The research contribution is the
harmonized public-data pipeline, zero-shot target-blind experiment, fixed
external success gate, spatially aware uncertainty, and auditable evidence
chain.

**What happens if the result fails? / 如果结果不通过怎么办？**  
The failure is reported under the original rule. It identifies where transfer
or calibration breaks and motivates a separately labeled adaptation study; it
does not permit changing this test.

**Can this become a live tool? / 能否做成实时工具？**  
Potentially, but not from this experiment alone. A live version needs archived
forecast-time weather such as HRRR, issue-time and freshness checks, prospective
prediction commitments, and a new evaluation protocol.

## 16. Evidence checklist before submission / 提交前证据清单

Before exporting a poster, paper, or public result, confirm that:

- the three-city target completion record authenticates all 64 external
  overpasses and all three city compiles;
- the external evaluator completion record passes its read-only integrity check;
- prediction, target, evaluation, table, and figure hashes agree with their
  manifests;
- reported rows, city-dates, and spatial blocks appear beside every metric;
- all six planned evidence figures were generated from authenticated outputs;
- the public Atlas payload matches the same final evaluation; and
- the conclusion uses the prespecified point and reliability gate states, not
  visual impression or secondary metrics.

提交前必须保证三城目标完成记录、最终评估完成记录、数据和图表哈希均通过只读认证；所有
数字都附有样本行数、城市—日期数和空间块数；网站、论文、展板与答辩使用同一份最终证据。

## 17. Source-of-truth references / 规范来源

- Frozen cross-city design: `docs/MULTICITY_GENERALIZATION_PROTOCOL.md`
- Operational gates and evidence: `docs/MULTICITY_METHODS_AND_EVIDENCE.md`
- Protocol/model lock: `manifests/multicity/evaluation/PROTOCOL_MODEL_LOCK.json`
- Portable feature/model contract:
  `manifests/multicity/reviews/portable_predictor_contract/PORTABLE_PREDICTOR_CONTRACT.json`
- Current resumable stage: `docs/PROJECT_HANDOFF.md` and
  `manifests/multicity/ACTIVE_STAGE.json`
- Historical Los Angeles result: `reports/FINAL_EVALUATION_REPORT.md`
