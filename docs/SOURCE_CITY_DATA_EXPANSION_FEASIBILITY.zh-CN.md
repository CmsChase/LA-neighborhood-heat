# 补充源城市数据：限范围可行性报告

状态：**Colorado 六日期镜像边界探索性目标已检查；static/calendar 已完成，Daymet 本地入口待用户私下运行，Sentinel PB04 校准证据不足，禁止批量续跑。官方边界未核验；Charlotte 暂停。**
日期：2026-09-17

## 1. 阶段 2 的边界

固定 C1/C2/C3 路线已经关闭，不进入已揭盲四城压力测试，也不修改合同或门槛。C1 在四源整城折外的总体 MAE 比 B1 低 0.0485°C（1.32%），但最差城市指标从 4.6346°C 变为 4.6694°C，按事前合同不合格。**0.0348°C 是点估计差，不是已证实的显著伤害**；本实验未给出足以作该显著性判断的配对不确定性证据。C2/C3 的总体与最差城市指标都不合格。这些结论是反复使用四源城市的开发证据，既不推翻原盲测失败，也没有证明其他数据或模型路线必然无效。详见[阶段 2 报告](FOUR_CITY_ABSOLUTE_ERROR_STAGE_2_SOURCE_VALIDATION.zh-CN.md)。

## 2. 已有非目标输入的覆盖

下表**只读**八城既有 `predictors_46.parquet` 中的海拔、地表、滞后 Sentinel 和滞后 Daymet 列；没有读取目标、QA 或预测误差。静态值先按 `tract_geoid` 去重，再取社区中位数；动态值先取每个完整预测日期的社区中位数，再取日期中位数。动态日期统一限定 5–10 月；LA/Houston/Chicago 为 2020–2024，Phoenix 和后四城为 2025。日期数是**预测变量库存日期**，不是通过 QA 的有效目标日期；跨年份比较仍有气候年际差异。范围比较不能替代联合分布或可用日期审计。

| 城市 | 角色；库存日期 | 海拔中位 m | 不透水面中位 | d−1 Tmax 日期中位 °C | d−1 水汽压日期中位 Pa | lag60 NDVI 日期中位 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Los Angeles | 源；90 | 113 | 0.689 | 27.8 | 835 | 0.193 |
| Phoenix | 源；22 | 363 | 0.580 | 38.2 | 1,373 | 0.116 |
| Houston | 源；81 | 24 | 0.648 | 34.3 | 2,897 | 0.416 |
| Chicago | 源；82 | 184 | 0.732 | 26.0 | 1,651 | 0.291 |
| Denver | 已揭盲；20 | **1,630** | 0.508 | 28.8 | 1,237 | 0.347 |
| Atlanta | 已揭盲；17 | 294 | **0.386** | 30.6 | 2,225 | **0.597** |
| Seattle | 已揭盲；36 | 67 | 0.644 | 22.9 | 958 | 0.472 |
| Miami | 已揭盲；20 | 11 | 0.642 | 31.8 | 3,199 | 0.260 |

**已确认的输入支持缺口。** Denver 的社区海拔第 5–95 百分位约 1,593–1,692 m；四源城市里最高的 Phoenix 第 95 百分位约 475 m，故在此固定非目标地形变量上明显分离。Atlanta 的城市日期 NDVI 中位 0.597，高于四源城市最高的 Houston 0.416；其社区森林覆盖中位 0.051，而四源城市各自的中位均为 0。这里没有断言每一社区都不重叠：Houston 社区森林覆盖第 95 百分位约 0.086，说明局部仍有重合。Seattle 的日期 NDVI 中位 0.472、低温沿海组合，Miami 的水汽压中位 3,199 Pa、近海组合，均没有一个完全匹配的源城市；但单列范围有部分重叠，不能称为整体分布外的证明。海岸距离特征合并海洋与五大湖，Chicago 的湖岸距离不等同 Seattle/Miami 的海洋环境。

**关联线索而非机制验证。** 地形支持缺口与既有 Denver 失败机制假设相容；Atlanta 的绿量/不透水面差异可能影响迁移。此处没有按外城预测误差挑选新城市、没有拟合、没有检验新增数据会改善表现。Daymet 为约 1 km 日尺度估计；相同城市气候类标签也不等于社区级天气及地表支持一致。[Daymet 官方说明](https://daymet.ornl.gov/overview)。

**尚未知。** 候选新城市的合同内社区分布、联合输入支持、2020–2024 固定日期库存、WorldCover 有效土地支持、Landsat ST 可用像元和 QA4K 合格日期数，以及新增训练城市是否改变误差，均未核实。后者必须在未来获授权实验中检验，不能由本轮推断。

## 3. 最小扩充假设：先筛两城，不预定采集

只给两个**待元数据筛查**的候选，不把它们认定为合格源城市：

| 候选 | 基于非目标信息的补足假设 | 相对原四源的新增信息 | 必须先证实 |
| --- | --- | --- | --- |
| Colorado Springs, CO（2020 Census place `0816000`） | 高海拔城市支持；NOAA 的机场站海拔 1,884 m，仅作为初筛点位 | 原源城市中位海拔最高 363 m；若完整社区支持落在高海拔带，才构成新训练条件 | 2020 固定城市边界内社区海拔分布、合适的城市范围、2020–2024 Landsat/QA 支持；机场点位不能代替社区分布 |
| Charlotte, NC（2020 Census place `3712000`） | 内陆、温暖湿润且可能有较高植被的城市组合 | Houston 提供高湿但较低城市中位 NDVI；Chicago 提供内陆条件但相对较低的暖季湿度 | 用同版本 NLCD/Sentinel 与 Daymet 元数据证实森林/NDVI/不透水面和天气联合分布确实补位；不能凭地名或气候印象认定 |

城市标识见 [Census Colorado Springs](https://geocoding.geo.census.gov/geocoder/geographies/coordinates?benchmark=4&vintage=4&x=-104.7390646&y=38.8446174) 与 [Census Charlotte 2020 place 表](https://tigerweb.geo.census.gov/tigerwebmain/Files/bas26/tigerweb_bas26_incplace_2020_tab20_nc.html)。[NOAA Colorado Springs 站点元数据](https://www.ncei.noaa.gov/cdo-web/datasets/LCD/stations/WBAN%3A93037/detail)只说明该机场位置，不说明整个城市的地形；NOAA [1991–2020 气候常值](https://www.ncei.noaa.gov/products/land-based-station/us-climate-normals)也只是筛查背景，与模型当前日期的 Daymet 输入不是同一统计量。

选择规则必须先于任何新目标读取：用**相同 2020 place/tract、相同 2016 NLCD、SRTM、2020 WorldCover、滞后 Sentinel/Daymet 定义**，核对候选的完整社区海拔、森林/不透水面、湿度/温度、海岸距离的分布和交集；仅当相对于四源有实质、非冗余的共同支持补足，并且元数据与固定 QA 的采集可行，才提采集合同。若 Charlotte 不补足，停止该候选；不根据外城误差临时换城。两城也不能覆盖所有环境：Seattle 的凉湿海洋性与 Miami 的近海高湿条件仍可能缺位。本报告未推荐第三城来假装一次解决全部缺口。

## 4. 数据与管线可行性

| 环节 | 已核实 | 尚需候选逐城核实 / 风险 |
| --- | --- | --- |
| 固定边界和土地分母 | [Census 2020 TIGER/Line](https://www.census.gov/cgi-bin/geo/shapefiles/index.php?layergroup=Census+Tracts&year=2020)提供 tract 边界；原协议用 [ESA WorldCover 2020 v100](https://esa-worldcover.org/en/data-access)非水类固定 eligible-land，保持跨日期不变 | 新 place 与 tract 相交、特殊用途过滤、30 m 重投影、WorldCover 缺测及 ≥98% 城市支持尚未执行；不可直接沿用其他城市土地分母 |
| 静态/地形 | 本管线冻结 [NLCD **2016** 土地覆盖与不透水面](../configs/multicity/portable_predictor_source_evidence_v1.toml)、[SRTM GL1](https://portal.opentopography.org/datasetMetadata?otCollectionID=OT.042013.4326.1) 与海岸线定义，不是 NLCD 2021 | 新城市相同版本像元覆盖、投影、坡度和岸线几何未核实；不可换较新产品而仍称同一特征合同 |
| 天气 | [Daymet V4](https://daymet.ornl.gov/overview)覆盖北美日尺度、约 1 km，历史年份在产品范围内 | 新城市固定 tract/cell 权重、缺失、版本和每个预定日期的窗口完整性未核实；采集日早于目标不等于产品在目标日前已发布 |
| 非热卫星 | [Sentinel-2 L2A](https://documentation.dataspace.copernicus.eu/Data/SentinelMissions/Sentinel2.html)是表面反射率，历史 2020 产品可用；原特征要求在目标日前结束的 lag60 合成 | 实际候选城市各窗口的晴空观测、tile 对齐、云掩膜与 5 个指数可用率未查；2020 历史档案曾重处理，[版本/发布时间](https://documentation.dataspace.copernicus.eu/Data/Others/Sentinel2_L2A_baseline.html)需记录 |
| 目标来源与 QA | [USGS Landsat C2 L2 ST](https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature)与 [QA_PIXEL、QA_RADSAT、ST_QA](https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands)产品类型存在；原 QA4K 合同还要求云距、像元数/比例、城市覆盖和日期保留率 | **只确认产品类别，不确认城市/日期可用量**；云、缺失 ASTER GED 与 ST 近云问题可致通过量大降。下一次也只能先查 scene 元数据/计数；不得为本报告读取目标像元或保证合格日期数 |
| 执行代码 | 地理/特征处理函数有复用基础 | `configs/multicity/experiment.toml` 与 `m3_source_joint_nested_loso_v1.py` 把原四源身份及四折写死；新城市**不能直接运行**，须单独合同、配置、最小适配与授权，不修改冻结历史实验 |

原固定 QA4K 不因新增城市放宽：同一过境合并、ST_QA≤4 K、固定非水 eligible-land、每社区有效像元≥20 且有效比例≥0.60、足迹≥0.90、城市并集覆盖≥0.98、日期社区保留≥0.50，并遵守原云/饱和/物理范围过滤。具体口径见[原协议](../configs/multicity/m3_development_protocol_v1.toml)。WorldCover 2020 与当前可取的历史再处理 Sentinel/Daymet 只支持**历史重建**；没有逐产品的预目标发布时间证据，不主张实时预测。[USGS ST 限制与缺失说明](https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature)。

## 5. 下一步验证边界与决定

若未来两城元数据筛查通过、另立采集合同并取得合法新源目标，它们才加入新开发集合。应在**新合同**中预先冻结完整城市留出：每个外层留出一整个源城市，所有预处理/选择只在其余城市内完成；同时报告旧四源与新增城市各自表现，避免新增城市数量改变等城市权重时掩盖退化。原四源已反复使用，扩源后的 LOSO 仍是开发证据，不是独立确认。C1/C2/C3 的已关闭结果不因扩源重新激活；如要比较新方案，需新的固定问题与合同。

改善 Seattle/Denver/Atlanta/Miami **已有城市未来日期**的主张，需要在新模型、QA、指标与停止规则冻结后获取这些城市从未用于选择的完整新过境日期；预测先锁定，目标后揭示。原 2025 四城已揭盲日期仍只是历史压力测试，不能变成新盲测。声称对**新城市**泛化，则需要不同于训练/选城所用城市、从未参与开发的独立整城确认集。LA 2025 继续冻结。两种主张的样本量应根据相应逐日期相关性与 QA 通过率另算，不沿用 LA 的 106 日期规划，也不保证固定年限。

**决策 B。** 通用产品存在且高海拔输入缺口明确，值得做两个城市的低成本元数据筛查；但没有候选城市的社区级联合覆盖、逐城 Landsat/Sentinel 清单和 QA 可用量，尚不足以批准科学数据采集，更不能预期模型改善。最小下一工作量是 **2 城 × 1 次只读元数据包**：确认 2020 place/tract 标识与边界来源、同版本产品覆盖目录、目标日前 Sentinel 与 Landsat 场景目录，得到可能过境日期的**上界**。它不读取影像像元，因而不能计算固定土地分母、社区预测变量分布或 QA4K 合格日期。若元数据上界仍值得继续，须另行授权限定范围的非目标静态/天气核查与 QA 支持审计，之后才决定是否制定目标采集合同。主要未知为有效日期数、云与缺测损失、Charlotte 是否真正新增联合支持、扩源对原城市的影响。本轮不下载、不训练、不读目标、不改默认模型。

## 6. 追加：Colorado Springs / Charlotte 一次性元数据筛查（2026-09-16）

**查询范围与复现。** 读取 [Census 2020 place 图层](https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer/26?f=pjson)与[tract 图层](https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer/6?f=pjson)的定义（2020-01-01 边界），实际几何查询因本机访问官方 TIGERweb 出现 TLS EOF，使用原管线登记的 [Esri 2020 Census place 镜像](https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Census_2020_Redistricting_Incorporated_Places/FeatureServer/0)及[tract 镜像](https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Census_2020_Redistricting_Tracts/FeatureServer/0)，**未证明镜像与官方几何逐顶点一致**。若将来试采，以官方几何或正式已审核镜像复核为前置门槛；不换用城市化地区、都会区或 2026 行政边界。规则仍为 place 相交的 2020 tract，在 EPSG:5070 中交叠面积占原 tract ≥0.50，排除 98xxxx 特殊 tract，并将入选 tract 按 place 裁切；完整预测集合必须是此固定集合与固定 WorldCover 非水 eligible-land 的结果，不按目标可用性删社区。机器可读的所选 GEOID、边界几何哈希、逐年确切查询 JSON、日期/场景 ID、QA 试采草案在本地 ignored `exports/SOURCE_CITY_METADATA_SCREEN/summary.json`；复现代码是 `experiments/source_city_metadata_screen.py`（无资产 href 请求）。查询 UTC 时间写在摘要中；目录为 [Planetary Computer STAC](https://planetarycomputer.microsoft.com/docs/reference/stac/) 的 USGS/Copernicus/ESA 产品镜像，不冒充 USGS 官方清单。

| 城市；固定 place GEOID | 镜像 bbox 相交 2020 tract | 依既有规则入选 tract | 2020 WorldCover v100 目录瓦片 | 目录瓦片并集覆盖 place 几何 | 结论 |
| --- | ---: | ---: | --- | ---: | --- |
| Colorado Springs `0816000` | 154 | 110 | `N36W105`, `N39W105` | ≈100% | **A：元数据满足，可设计小规模 QA 核查**；镜像/官方几何核对需先做 |
| Charlotte `3712000` | 322 | 237 | `N33W081`, `N33W084` | ≈100% | **A：元数据满足，可设计小规模 QA 核查**；镜像/官方几何核对需先做 |

瓦片几何覆盖不等于 land-cover 值完整，也不等于固定非水土地分母已算出。海拔、森林、NDVI、湿度的**社区级联合分布**没有读取，Charlotte 是否补足原源城市信息仍未经证明。

### 产品契约与目录实查的区别

| 环节 | 同规格产品；分辨率/时间；获取方式 | 本轮目录结果和管线缺口 |
| --- | --- | --- |
| 热目标和 QA | [USGS Landsat Collection 2 Level-2 ST](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products)，L8/L9 T1 L2SP，30 m，2020–2024 暖季 5–10 月；[QA_PIXEL / QA_RADSAT / ST_QA](https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands)，加 ST_CDIST；目录为项目现用 STAC 镜像，未来小样本仅按独立授权访问 QA COG | 两城逐年场景和候选日期见下表；**所有 80/160 个候选场景**目录内各自有 `lwir11, qa_pixel, qa_radsat, qa, cdist` 的资产键（只投影键的 `type`，未请求 href）；另有 Charlotte 3 个相交但不属候选的场景未经逐资产键复核。资产键不证明像元非空、QA 通过或远端字节可读。热波段不在本轮读取范围 |
| 固定土地与静态 | [ESA WorldCover **2020 v100**](https://esa-worldcover.org/en/data-access) 10 m，两城各两片在目录中存在，几何覆盖 place；[NLCD **2016** land cover/impervious](https://www.usgs.gov/publications/conterminous-united-states-land-cover-change-patterns-2001-2016-2016-national-land) 30 m 覆盖 CONUS；[SRTM GL1 v3](https://www.earthdata.nasa.gov/s3fs-public/2025-05/SRTM_Quick_Guide.pdf) 1 arc-second，另有固定海岸距离定义 | WorldCover 瓦片实际检索到；NLCD/SRTM 为官方覆盖声明，未逐瓦片验证、未打开像元。必须新建候选城市的 2020 land mask/固定 tract 分母、地形/地表与岸线交集；不能复用旧城市分母或换成 NLCD 2021 |
| 天气 | [Daymet V4/R1](https://daymet.ornl.gov/overview) 北美约 1 km 日尺度，自 1980 起；原 d−1 与历史窗口，用现有 NASA/ORNL 元数据/服务 | **官方覆盖声明**支持两城与 2020–24；未逐日期/网格单元实查，未请求 Daymet 值。新增 tract-cell 权重和版本/窗口核验仍需单独授权；不声称目标日前已有正式产品发布 |
| 非热卫星 | [Sentinel-2 L2A](https://documentation.dataspace.copernicus.eu/Data/SentinelMissions/Sentinel2.html) 表面反射率 10/20/60 m，项目 lag60 至 d−1；STAC `sentinel-2-l2a` | 两城 2020–24 每年 3–10 月均实际检索到与 place 相交的 L2A 元数据；每年抽一个目录 item，九个冻结资产键齐全。仍未核查每个 60 天窗口有无足够清晰像元、重处理 cohort/可用时刻，不得将 item 数当可用指数数 |

**Landsat 目录统计。** 查询条件为 place bbox、各年本地 5 月 1 日—10 月 31 日、L8/L9、C2 L2、T1、L2SP；先按真实 place 几何剔除 bbox-only item，再将同一平台/本地日期、WRS 相邻且相差 ≤15 分钟的场景合并为一次物理过境；候选还须场景并集覆盖 place ≥0.98、无同日歧义。日期并非 QA 合格。`原始`为 bbox 目录结果，`相交`为真正几何相交场景；`合并`为任何覆盖率的物理过境；`候选`为 ≥98%/无歧义的**独立日期**。

| 城市 | 年份 | 原始 | 相交 | 合并 | 候选日期 | Sentinel L2A 相交 item（3–10 月） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Colorado Springs | 2020 | 12 | 12 | 12 | 12 | 392 |
|  | 2021 | 10 | 10 | 10 | 10 | 362 |
|  | 2022 | 15 | 15 | 15 | 15 | 648 |
|  | 2023 | 23 | 23 | 23 | 23 | 668 |
|  | 2024 | 20 | 20 | 20 | 20 | 380 |
| Charlotte | 2020 | 23 | 22 | 12 | 12 | 388 |
|  | 2021 | 22 | 20 | 10 | 10 | 390 |
|  | 2022 | 40 | 33 | 18 | 16 | 676 |
|  | 2023 | 50 | 45 | 23 | 23 | 665 |
|  | 2024 | 51 | 43 | 23 | 22 | 384 |

因此五年元数据候选日期为 **80 / 83**，不是合格训练日期。Charlotte 2022、2024 有同日多过境或覆盖不足，合并数不能直接变为候选日期数。没有用场景云量选日期；目录云量、键存在、产品声明都无法确认 WorldCover 非水像元、ST 缺失（如 ASTER GED）、真实 ST_QA≤4K、云距、每 tract ≥20 有效像元/≥0.60 有效比例、footprint ≥0.90 或日期 ≥0.50 社区保留。2020–24 目录中的 2024 年 Sentinel item 高于/低于其他年的变化未作可用像元推断。

### 可审阅、**未执行**的最小 QA 试采合同

若用户另行授权，先核验官方与镜像边界/tract 身份和几何，冻结上述 110/237 个裁切社区；任何不一致先暂停，而不是用另一边界暗中替换。时间与场景集合已在本轮**按目录次序、与温度/云量/误差无关**固定：两城每年各取 ≥98% 非歧义候选的最早与最晚一次，2020–24 各 **10 次物理过境**；Colorado Springs 10 景、Charlotte 18 景，确切 28 个 scene ID 和 20 日期见机器摘要。并集优先，双 WRS 不作两次独立日期。跨五年且跨暖季开端与末端；小样本不能代表全年、更不能保证足够完整训练日期。

仅允许后续单独授权按这些 ID **窗口化读取** WorldCover 2020 v100 土地分类，以及 Landsat `qa_pixel`, `qa_radsat`, `qa`(ST_QA), `cdist`，连同场景 footprint 与元数据；不得读取 `lwir11`、任何热目标值、LA 2025 或已揭盲外城目标。先冻结固定 eligible-land 非水分母，再按原 QA4K 云/阴影/雪/饱和、ST_QA≤4K、云距 ≥1 km、每 tract 有效像元≥20 与有效比例≥0.60、footprint≥0.90、城市并集≥0.98、日期社区保留≥0.50 核查**QA 上界**。单靠 QA 无法证明 ST_B10 真有非缺失温度像元，也无法验证温度物理范围；不读热目标就不能认证最终合格日期。预登记成功规则：每城十次中 ≥6 次达到上述 QA 支持，且五个年份每年至少一次；任何一年无通过、固定分母不可构造、或许可/范围不符则停止、仅报告观察到的缺口，不临时补日期。通过只允许提出下一份目标采集合同，不晋级模型。

体量估计依据（**不是实际下载量**）：按 place 镜像 `AREALAND` Colorado Springs 506.1 km²、Charlotte 798.5 km²，四个 30 m uint16 QA 层 ×10 次，窗口内未压缩像元阶数约 **45 MB / 71 MB**；一次 10 m uint8 WorldCover 固定 land mask 约 **5 / 8 MB**。WRS 双景覆盖重叠、COG 的 block/range 扩大、压缩率与服务策略均不在式内，故先只读 `Content-Length`/窗口请求估价并设下载预算上限，不承诺可控制到该体量，尤其不得为了省流量筛掉困难场景。此试采是下一项**独立授权**，当前所有权限仍关闭；代码还需增加两个城市配置、边界审核、可恢复的 QA-only 小样本入口，冻结既有 QA 与旧模型不能被改写。

**逐城决定：Colorado Springs=A；Charlotte=A，均只到“小规模 QA 可行性检查”的元数据准入。** WorldCover 有效类别、QA 空间支持/通过量、真正 ST 缺失以及 Charlotte 的联合输入是否补位仍未知。下一次最小数据操作是先核对官方/镜像边界，然后只针对已冻结 20 个日期/28 景开展另立权限的 WorldCover 与四类 QA 窗口试采；本轮未执行。

## 7. QA 试采授权后的前置边界审计：停止于边界门槛（2026-09-16）

用户授权了**先官方—镜像边界核对、通过后才窗口化试采**。本轮仅完成前置边界审计；**没有开展 QA 试采**，不得将上节的元数据 A 解读为本轮 QA 通过。可复现程序 `experiments/source_city_qa_boundary_audit.py` 及本地 ignored 机器结果 `exports/SOURCE_CITY_QA_PILOT/boundary_audit.json`（SHA-256 `f54b75516b29c88ff33d49e3ce55401b4b3ff30d94b93963b4fd69643774135d`）记录官方与镜像的确切 URL、冻结的 20 日期/28 景、产品与四个 QA 资产键、逐城结果和原始元数据摘要哈希。没有持久化签名 URL 或任何热波段内容。

**比较规则在调用官方几何前固定。** 官方 [2020-01-01 Census incorporated place 第 26 层](https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer/26?f=pjson)、[tract 第 6 层](https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer/6?f=pjson)及原 2020 Census 镜像，均验证 GEOID/年份/多边形属性后按原交叠≥0.50、排除 98xxxx、裁切 tract 规则处理。投影到各城市 UTM（Colorado Springs EPSG:32613；Charlotte EPSG:32617），在相同 30 m、15 m 锚点的格网上检查：place Hausdorff 距离≤**15 m**（目标 30 m 像元半宽），入选 tract GEOID 集合**完全相同**，两个版本的 30 m 像元中心所落 tract 编码**零差异**。WKB 或文件字节不同本身不构成失败；只要土地支持栅格单元不同就不静默接受。几何差异的对称差面积也记录，供解释。该规则用于边界前置筛查，不能替代后续 WorldCover 有效非水像元检验。

| 城市 | 镜像复核 | 官方数据尝试 | 几何/窗口/分母影响 | 本轮日期状态 | 本轮结论 |
| --- | --- | --- | --- | --- | --- |
| Colorado Springs | 2020 GEOID `0816000`、110 入选 tract 和冻结镜像几何哈希均一致 | 官方 TIGERweb 在本机反复访问失败；机器审计记录 place 元数据 `ConnectTimeout`，其他尝试出现 TLS EOF；未得到官方几何 | **未知，无法比较**；30 m 窗口与固定分母不得据镜像单独认证 | 通过 0、科学 QA 失败 0、**无法判断 10**（每年 2） | **B：先解决官方边界获取/核验阻塞** |
| Charlotte | 2020 GEOID `3712000`、237 入选 tract 和冻结镜像几何哈希均一致 | 同一官方服务返回 `SSLError`（TLS EOF）；未得到官方几何 | **未知，无法比较**；相邻 WRS 仍按一次过境、没有改日期 | 通过 0、科学 QA 失败 0、**无法判断 10**（每年 2） | **B：先解决官方边界获取/核验阻塞** |

官方图层在线说明可读取，但几何查询在本机失败；Census 官方 2020 TIGER/Line ZIP 下载端点在本机又返回 HTTP 403。两者说明**本机获取受阻**，不说明官方数据不存在，也不是已经观察到几何不一致。本轮没有看到足以判定 tract 选择、栅格窗口或土地分母不变的官方几何；已冻结边界镜像身份、产品版本、20 次日期/28 景和允许的 QA 资产键，**栅格窗口须待官方几何核对通过后才能固定**，不得凭未认证边界虚构窗口。故未打开 WorldCover 或 QA 资产。科学栅格下载 **0 byte**；成功/缺失 QA 资产均为“未尝试”，不能记为科学 QA 不合格。现有固定 QA4K 门槛、日期和源城市模型原封不动。

**下一次最小动作：**取得官方 2020 place/tract 几何（例如官方服务恢复可访问，或提供 Census 原版 2020 TIGER/Line place 与 tract 文件及来源记录），先用上述脚本同投影、同像元尺度比较；只有两城各自通过，才为其**单独打开**固定 10 次过境的 WorldCover 2020 v100 与 `qa_pixel`、`qa_radsat`、`qa`、`cdist` 窗口读取权限。不得按云量替换失败日期，也不得碰 `lwir11`。本轮阶段权限已关闭、没有提交/推送；未得到 QA 有效支持之前，不对 A 或 C 的科学采集价值作判断。

## 8. 官方获取故障的限定排查与续跑结论（2026-09-16）

本次续跑**没有重跑整个边界审计或更换冻结输入**。先在 ignored `data/`、`exports/` 查找带来源记录的候选几何：没有 Colorado/NC 的 2020 原版 place/tract ZIP 或完整精度文件。现有 LA 专用文件和 `2020_cb_tract_500k.parquet` 不是两城同规格的官方几何，不能代替。

| 路径 | 本机限定检查 | 判断 |
| --- | --- | --- |
| 原 Census TIGERweb 2020 MapServer | `requests` 对第 26 层 `f=json` 的 TLS 握手读超时；Windows `curl` 对官方 `Census2020` 同版服务握手关闭。无 `HTTP_PROXY`、`HTTPS_PROXY`、`REQUESTS_CA_BUNDLE` 设置，WinHTTP 为 direct；本机 DNS 对 TIGERweb 返回 `198.18.0.49`（保留的内部映射地址） | 请求**未到有效 HTTP 响应**，不是服务器回 403，也不是已观察到的证书信任链失败；不关闭 TLS 校验，不改全局 DNS/代理 |
| 官方 [2020 TIGER/Line](https://www.census.gov/geographies/mapping-files/2020/geo/tiger-line-file.html) 的 `TIGER2020/PLACE`、`TIGER2020/TRACT`，及 `TIGER2020PL/LAYER/PLACE/2020` | `www2.census.gov` 的文件 HEAD 返回 Cloudflare HTTP **403**；浏览器打开官方目录也明确显示访问被阻止。无登录/CAPTCHA 操作 | 服务端访问限制，不能将 403 当成几何差异；不绕过网站安全拦截。官方说明确认原版 2020 TIGER/Line 法定边界年份为 2020-01-01 |
| 既有 Esri 2020 镜像 | 与上轮冻结的两城 GEOID 集合和 place 几何哈希一致 | 仅证明镜像未漂移；没有独立官方内容一致性证据，**不通过**官方核对门槛 |

两个城市仍分别为 **B（官方几何获取受阻）**，不是边界几何不一致，也不是科学 QA 不合格。沿用上轮 15 m/30 m 像元及 exact GEOID/zone 比较规则；**没有可用于比较的官方 place/tract 几何，因此两城 0/0/10（通过/科学失败/无法判断），各年份仍各 2 次无法判断**。QA/WorldCover/热目标读取与下载均为零；没有新的土地分母、QA 样本或新增城市的科学结论。

可恢复的最小输入是从 Census 官方原版 2020 TIGER/Line 获取并保存带来源记录的四个文件：`tl_2020_08_place.zip`、`tl_2020_08_tract.zip`、`tl_2020_37_place.zip`、`tl_2020_37_tract.zip`（或者官方同版服务可访问的对应 place/tract 完整几何）。须记录下载 URL、时间、文件大小与 SHA-256，核实 shapefile 的年份、GEOID、字段、CRS、记录完整性；随后按**既定**比较规则逐城核对，只有通过的城市才能执行已固定的 QA 窗口试采。用户不需要重选日期，也不能以其他年份、简化制图版或缺少官方一致性证据的镜像替代。当前临时权限已关闭，未提交或推送。

## 9. 明示合同修订：冻结镜像上的探索性 QA 支持试采（2026-09-16）

**原合同不覆盖、不作追溯性改写。** 用户现明确授权：在官方 2020 place/tract 几何的获取受阻、原官方等价门槛仍为**未完成**时，以已核对冻结 tract GEOID 清单及 place 哈希的 Esri 2020 Census 镜像作为**暂定分析边界**，进行两城各原定 10 次物理过境的非热 QA 支持可行性试采。理由是先判断固定日期的 QA 支持是否值得后续官方边界及热目标核验；它既不能认证镜像等价于官方，也不能产出正式合格日期或训练标签。

允许的输入仅为镜像 place/tract 几何、已冻结的 20 日期/28 景及其元数据、ESA WorldCover **2020 v100** 的 `map`，和 Landsat C2 L2 T1 L2SP 的 `qa_pixel`、`qa_radsat`、`qa`（ST_QA）、`cdist` 四类资产。只按城市窗口读取；不请求 `lwir11` 或其他热/目标资产，不加城市、日期、产品，不改原 QA4K 门槛、30 m/15 m 锚点、WorldCover mode 对齐、非水类别 `{0,80}`、scene 合并规则。完整镜像截取社区集合及每 tract 2020 WorldCover 非水分母一次固定，跨日期不变。观测覆盖、逐层 QA 保留、每 tract 像元数/比例与日期门槛全部按原协议；由于本轮不读 ST DN/物理温度，结果是 **QA 支持上界**，不能称作正式 target_available 或日期通过。逐项技术失败独立列出，不记成科学 QA 不合格。

执行前须再次核对镜像图层/2020 来源、GEOID 集合与先前哈希、CRS、几何有效性、缺失和重复，任何城市自身失败即停止该城。机器结果及以下所有统计须注明：**“基于冻结镜像边界的探索性结果，官方等价性待核验”**。原官方几何比较的 15 m Hausdorff、exact GEOID、零 30 m zone 单元差异门槛继续保持未完成；以后若通过且不改变分析支持，按证据复用缓存；若改变窗口或土地分母，重算受影响部分。官方核验和热目标可用性均完成之前，本轮数据不得用于正式训练标签验收。无训练、评分、LA 2025、默认模型变更或 Git 提交推送。

## 10. 冻结镜像探索性 QA 试采实测结果（2026-09-16）

复现入口为 `experiments/source_city_qa_mirror_pilot.py`，机器结果保存在本地 ignored `exports/SOURCE_CITY_QA_PILOT/mirror_exploratory/summary.json`（SHA-256 `7c1919f3d190f1f171456c6bb670da2b8a76cf6abe4753a2e162e1ea5280b3ce`）；各城原始镜像响应哈希、固定分母与逐日期/逐 tract 统计在同目录。**以下均为“基于冻结镜像边界的探索性结果，官方等价性待核验”。** 原官方边界门槛依旧未完成。本轮没有读取热波段、ST DN、目标值或 LA 2025，没有训练、预测或评分模型。实际 COG 网络传输字节未被本地 rasterio 精确计量，不能把窗口解码体量当成实际下载量。

Colorado Springs 的 2020 Esri place/tract 响应为 EPSG:4326，原始几何有效、GEOID 无缺失或重复；110 个入选 tract 与预冻结清单一致。按 30 m、15 m 锚点构建固定栅格，WorldCover 2020 v100 非水 eligible-land 合计 **431,384 个 30 m 单元**，110 个 tract 均有非零固定分母，整个试采期间不随日期改变。两个相邻 WorldCover 瓦片各自 mode 重采样到同一 30 m 栅格时，在纬度 39° 接缝仅有 **70 个类别冲突单元**；QA 读取前，限定在此种相邻接缝按 30 m 单元中心所属瓦片确定类别，其他分类、窗口与门槛不变，冲突数和规则已写入 `support.json`。这不是官方几何认证；若后续官方边界改变分析支持，仍须重算受影响部分。

下表过滤步骤的数值均是“保留社区数/固定 110 社区”，即社区保留率；观测足迹一步为 110/110。景数均为 1。同一阶段的像元保留率另见逐日期机器记录，不能把社区保留率当像元比例。

| Colorado Springs 固定过境日期 | 城市观测覆盖 | QA_PIXEL | QA_RADSAT | QA/CDIST 非缺失 | 云距 ≥1 km | ST_QA≤4 K（最终） | 暂定支持条件 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2020-05-08 | 100% | 69/110 | 69/110 | 69/110 | 24/110 | 24/110 | 不足 |
| 2020-10-31 | 100% | 110/110 | 110/110 | 110/110 | 107/110 | 107/110 | 达到 |
| 2021-05-27 | 100% | 110/110 | 110/110 | 110/110 | 99/110 | 99/110 | 达到 |
| 2021-10-18 | 100% | 110/110 | 110/110 | 110/110 | 95/110 | 95/110 | 达到 |
| 2022-05-06 | 100% | 0/110 | 0/110 | 0/110 | 0/110 | 0/110 | 不足 |
| 2022-10-29 | 100% | 110/110 | 110/110 | 110/110 | 101/110 | 101/110 | 达到 |
| 2023-05-01 | 100% | 49/110 | 49/110 | 49/110 | 31/110 | 31/110 | 不足 |
| 2023-10-24 | 100% | 105/110 | 105/110 | 105/110 | 83/110 | 83/110 | 达到 |
| 2024-05-03 | 100% | 110/110 | 110/110 | 110/110 | 102/110 | 102/110 | 达到 |
| 2024-10-26 | 100% | 66/110 | 66/110 | 66/110 | 54/110 | 54/110 | 不足 |

每一行的最终社区数同时满足固定非水分母、足迹≥0.90、有效像元≥20、有效比例≥0.60；整城覆盖≥0.98 且保留社区比例≥0.50 才算“达到”。`qa_radsat` 地形遮蔽过滤、QA/CDIST 非缺失、ST_QA≤4 K 在本样本中没有进一步减少社区数；完整逐步骤保留像元数、比例和每 tract 明细见机器结果。Colorado 为 **6/10 达到、4/10 不足、0 技术读取失败**，五个年份各有至少一次达到，满足预登记的**探索性** 6/10+逐年门槛。该样本按年份首尾过境预选，不能把 6/10 外推为 80 个目录候选日期的通过率；QA 支持不证明 ST 热目标可用。

Charlotte 的同版镜像 place 虽仅返回一条 EPSG:4326 要素，但原始多边形因 `Nested shells` **无效**；自动 `make_valid` 会改变几何解释，且没有官方几何作独立裁决。因此按第 9 节停止该城，未构建正式或暂定土地分母，也**未读取 WorldCover/QA**；预定 10 次全部“未评价”，不是 10 次科学失败。原始响应与无效原因已存 `mirror_geometry_preflight.json`，不得用早期元数据筛查中已自动修复的 237 个 tract 身份，冒充几何有效或官方等价。

**后续决定。** Colorado 的探索性 QA 支持足以支持继续争取 2020 官方 place/tract 几何并按原 15 m/exact-GEOID/零 zone 差异规则核验；通过且分析支持不变时可有证据地复用本轮缓存，改变窗口或分母则重算，之后仍需独立授权检查热目标可用性。Charlotte 必须先取得可核验的有效官方几何，解决镜像无效歧义，再决定是否进行原定 QA 试采；目前不能判断其 QA 可行性。两城均未成为正式训练数据来源。本轮结束后权限关闭，无提交或推送。

## 11. 明示合同修订：Colorado 六次过境探索性目标可用性与 Charlotte 本地几何诊断（2026-09-16；读取热值前冻结）

**本节不覆盖前述合同或官方边界失败记录。** 用户现单独授权 Colorado Springs 在已冻结 Esri 2020 镜像几何上，针对上一轮仅由 QA 支持选定的以下六次物理过境，窗口化读取构建目标所必需的 Landsat `lwir11`（ST_B10）及其缩放元数据。六个日期/单景固定为：2020-10-31 `LC08_L2SP_033033_20201031_02_T1`；2021-05-27 `LC08_L2SP_033033_20210527_02_T1`；2021-10-18 `LC08_L2SP_033033_20211018_02_T1`；2022-10-29 `LC09_L2SP_033033_20221029_02_T1`；2023-10-24 `LC08_L2SP_033033_20231024_02_T1`；2024-05-03 `LC08_L2SP_033033_20240503_02_T1`。其余四日期维持 QA 不足记录，不读取热目标，不按温度替换日期。

输入锚点：上一轮 QA 摘要 SHA-256 `7c1919f3d190f1f171456c6bb670da2b8a76cf6abe4753a2e162e1ea5280b3ce`；Colorado 固定支持 `fixed_support.npz` SHA-256 `06b64ebd0f7088dac8223687349f0f5056b97f2b9a193995522a90e37b6aafcc`；六次 QA 日期记录均指向同一格网 SHA-256 `c08ee85e1873c23b1289ea18475c5eeac92621aa1da1ca766fcc35b4391bdae7`；原 M3 QA/支持协议 `configs/multicity/m3_development_protocol_v1.toml` SHA-256 `65087095142e86d4b5ee4f190ac14951f18679506738253571df160200cbb821`；温度缩放/ DN/固定门槛配置 `configs/research.toml` SHA-256 `a2d4f7300d8a264c77c3ddc15a730546945a2d7f6ed253ff2f49e352102c60b9`。WorldCover/固定分母从已缓存 `fixed_support.npz` 读取；上一轮只持久化 QA 统计、没有逐像元 QA 掩膜，故仅允许对上述同六景重读 `qa_pixel`、`qa_radsat`、`qa`、`cdist` 恢复相同掩膜与窗口，并与旧逐 tract QA 统计核对。不得再下载 WorldCover、扩日期或产品。

目标有效性沿用原 C2 L2 ST 产品：DN 范围 293–61440；`0.00341802 × DN + 149 − 273.15` 转摄氏；QA_PIXEL 排除位 `{0,1,2,3,4,5,7}`，RADSAT 地形遮蔽位 11、ST_QA/CDIST 非填充值、云距≥1 km、ST_QA≤4 K；原有宽物理合理区间 −30 至 80°C 仅作为既有目标合同的有效性检查，不新设截断。每 tract 的 2020 WorldCover 非水土地分母固定不变，足迹≥0.90、有效像元≥20、有效/固定分母≥0.60；日期整城覆盖≥0.98 且合格 tract≥0.50。六景各为单景，无新增拼接次序；若产品缩放/单位/栅格支持不一致，停止对应日期为技术未判，不用温度大小筛除日期。产物仅在 ignored 隔离目录，标记**“探索性开发目标，基于镜像边界，官方几何核验待完成”**。热值一经读取即是开发资料，不能重新标成独立确认，也不得在本轮训练、预测或计算模型误差。

Charlotte 本轮只使用已存本地原始镜像响应，在副本中诊断 `Nested shells` 的位置、标准修复的面积/对称差/tract 成员及栅格支持影响；不覆盖原几何，不读取该城 WorldCover、QA 或热值。修复后有效也不等于官方一致。官方边界门槛对两城继续为**未完成**，本轮结果不作正式训练标签验收。ACTIVE_STAGE 仅临时开放上述 Colorado 必要读取，完成后关闭；不访问 LA 2025、不改默认模型、不提交或推送。

## 12. 六景探索性目标可用性与 Charlotte 本地几何诊断结果（2026-09-16）

**“探索性开发目标，基于镜像边界，官方几何核验待完成”。** 可复现入口为 `experiments/source_city_target_availability.py`；本地 ignored 机器摘要 `exports/SOURCE_CITY_TARGET_AVAILABILITY/exploratory_mirror/summary.json`（SHA-256 `eec87608885630d9c05bc725bbfbddedb4f489fefa04decbb22d4d4a5dbcbe85`），每日期 JSON 与隔离的 `*_development_labels.csv` 保留输入哈希、固定分母、热值缩放身份和逐 tract 标签/缺失记录。六景 STAC `lwir11` 的单波段元数据均在读取前逐景检查为 `uint16`、30 m、Kelvin、scale `0.00341802`、offset `149.0`、nodata `0`；`lwir11` 与原四类 QA 的栅格足迹一致，重读 QA 的逐 tract 像元数与上一轮冻结记录完全相同。使用原 DN 范围、QA4K、−30 至 80°C 宽物理有效性及相同 110 tract 固定非水土地分母，没有新温度筛除规则。六日期均为单景，无场景拼接歧义。

| Colorado 固定日期 | QA4K 合格土地像元 | 新增热值无效像元（DN/物理） | 有标签 tract / 110 | 暂定日期支持 |
| --- | ---: | ---: | ---: | --- |
| 2020-10-31 | 402,562 | 0（0/0） | 107 | 达到 |
| 2021-05-27 | 371,327 | 0（0/0） | 99 | 达到 |
| 2021-10-18 | 377,910 | 0（0/0） | 95 | 达到 |
| 2022-10-29 | 378,372 | 0（0/0） | 101 | 达到 |
| 2023-10-24 | 312,904 | 0（0/0） | 83 | 达到 |
| 2024-05-03 | 381,274 | 0（0/0） | 102 | 达到 |

结论是**此前 QA 暂定支持的 6/6 次过境，加入热目标实际有效性后仍达到相同暂定日期门槛**，共形成 587 个探索性 tract-date 温度标签。六次 QA 合格土地像元中的热 DN/物理有效性新增损失均为零；各日期社区标签数恰与 QA 上界相同。这只能说明所试六日期的目标可用，不能推论另 74 个 Colorado 目录候选日期、也不能称正式训练日期。其余四个预选 QA 不足日期未读热值。所有已读目标从此属于**开发资料而非独立确认**；本轮未训练、预测、评分或按温度大小选择日期。

Charlotte 的唯一既存本地 place GeoJSON（SHA-256 `981a192f71d1646dc053e6b5b0626628e32ef6176c57d8e3217b7326a77bb876`）在 `[-80.9691772758687, 35.1642058631841]` 附近有 `Nested shells`，原始几何无效。副本上标准 `make_valid` 结果与 `buffer(0)` 都有效，但前者面积 **802.938 km²**、后者 **812.697 km²**；两者对称差 **9.759 km²**（相对于前者约 1.22%），同一 30 m/15 m 锚点上有 **10,841 个城市支持单元不同**。冻结元数据的 place 哈希对应 `make_valid` 分支，但本地只留有入选的 237 个 tract GEOID 清单、没有 Charlotte tract 几何，故无法只凭现存文件重算“修复选择是否改变 tract 成员或各 tract 固定土地分母”；这些部分明确为**未知**，不能以旧 GEOID 清单作等价证明。可见城市栅格支持已随标准修复方法变化，**不存在目前可证明不改变实质分析范围的修复方案**。原文件未覆盖，Charlotte 未读 WorldCover、QA 或热值。细节在机器摘要的 `charlotte_nc` 字段。

**决策。** Colorado 的 6/6 结果支持继续争取可核验的官方 2020 place/tract 几何，并在保持原门槛前提下提出另行审批的完整源城市采集/构建合同；但正式构建仍缺官方边界等价或重算判定、未试目录日期的 QA 与目标可用性、以及完整非目标预测变量的城市级构建与审核。若官方几何不改变分析支持，按证据复用此探索性缓存；若改变窗口或土地分母，重算受影响部分。Charlotte 先解决有效官方几何及 tract 支持差异，不能进入目标采集。两城均未被本轮认证为训练城市。ACTIVE_STAGE 权限关闭，无 LA 2025、模型训练或 Git 提交推送。

## 13. Colorado 六日期预测变量试构建合同（2026-09-16；新预测值读取前冻结）

本轮仅对上一节已固定的 `2020-10-31, 2021-05-27, 2021-10-18, 2022-10-29, 2023-10-24, 2024-05-03` 建立 **110 个冻结镜像 tract × 6 日期 = 660 行完整预测集合**。不以已有 587 个标签决定预测键或空间汇总支持。镜像 place/tract、30 m EPSG:32613 格网、431,384 个固定非水土地单元和每 tract 固定分母复用 QA 试采原件；官方等价性仍未核验。产物只标为“基于冻结镜像边界的探索性开发预测变量，官方等价性待核验”，不能直接纳入正式训练。

特征完全沿用 `configs/multicity/m3_development_protocol_v1.toml`：B1 的 23 项为日历 sin/cos 和 Daymet V4/R1 的 dayl、prcp、srad、tmax、tmin、vp 派生的前 1/3/7 天汇总；M3 再加原 NLCD **2016** 土地覆盖及不透水率、SRTM GL1 V003 地形、GSHHG L1 海洋/大湖岸线共 18 项静态，以及 Sentinel-2 L2A **目标前 d−60 至 d−1** 的 NDVI/EVI/NDWI/NDBI/albedo proxy 五项。Daymet 最早需要 d−7，卫星窗口绝不含目标日。沿用原缺失含义和 Sentinel 最低有效获取数，不添加指数、天气窗口、交互或事后目标质量特征；本轮不拟合任何缺失填补器或标准化器。

只读取 Colorado 六日期所需的 2020–2024 年 Daymet 局部子集、这六个 60 日窗口内相交 Sentinel 公开非热波段/场景分类、一次性的 Colorado 窗口 NLCD 2016 与 SRTM，以及既有 GSHHG 本地归档；优先复用相同产品/网格缓存。静态源理论最低未压缩像元阶数：NLCD 两层各约 109 万个 30 m 单元（约 2–5 MB，不含原服务块扩大）及约两个 SRTM 1° 瓦片（各约 26 MB）；Daymet 需求为六变量 × 五年份的小窗口子集，Sentinel 物理过境数和真实网络体量需由受限目录查询后报告，不得凭六日期推算多年全量。任何单个静态源意外要求全州下载、Sentinel 访问越过六窗口或无法核对版本时停下并记录，不自行扩大范围。

最后仅按 `city_id, tract_geoid, target_date` 连接既有探索标签的**键和是否有标签**，不读取标签温度值、调用模型或计算误差。逐特征/逐日期报告缺失率、完整输入行数及 587 标签键连接数；缺失保持缺失。天气观测日早于目标日不等于当时已经发布，无法找到逐产品发布时间证据时仅主张历史重建。Charlotte 不操作；其既有几何阻塞保留。未试目录日期与官方 Colorado 2020 几何仍是后续独立门槛。

## 14. 六日期预测变量试构建实测与完整采集准备判断（2026-09-16）

**结论：本轮只完成可复用的部分试构建，没有得到完整既有模型输入。** 基于镜像边界的 110 tract × 6 日期完整键共 **660 行**，与既有开发标签只按键和可用标志连接，**587 个有标签键**（逐日期 107、99、95、101、83、102），另 73 行保留为无标签的完整预测集合；没有读取标签温度数值或调用模型。机器审计为 ignored `exports/SOURCE_CITY_PREDICTOR_TRIAL/exploratory_mirror/colorado_springs_co/summary.json`；复现入口 `experiments/source_city_predictor_trial.py`。46 列 parquet 是**显式空值的部分试构建**，不得误读成完整预测变量或训练表。

| 合同部分 | 冻结范围与实测 | 660 行缺失 | 判定 |
|---|---|---:|---|
| 静态 18 项 | NLCD **2016** 土地覆盖、不透水面各 1,574,844 B；SRTM GL1 V003 `N38W105` 11,315,364 B、`N39W105` 10,758,426 B；原 GSHHG 2.3.7 本地归档复用。合计新静态文件 **25,223,478 B**，110 tract 聚合完成。 | 全部 0 | 技术试构建通过；仍受镜像边界限制 |
| 日历 2 项 | 六日期、110 tract，沿用已有 DOY sin/cos。 | 全部 0 | 完成 |
| Daymet V4/R1 21 项 | CMR 找到 2020–2024 **5 年 × 6 变量 = 30** 个官方年 granule；冻结 Colorado **39×32** 原生格网子窗口，前 1/3/7 日定义未变。官方局部 OPeNDAP 测试返回 **HTTP 401**，本进程没有 Earthdata 凭据；没有下载天气值。 | 每项 660 | B1 与 M3 都受阻；不得替换天气产品或默填 |
| Sentinel-2 L2A 5 项 | 严格六个 d−60:d−1 窗口，各 24 次、合计 **144 次物理获取、562 个选中瓦片条目**；原 8 类光学/分类资产对应约 **4,496 次窗口读取**。首个获取已在原缓存规则下成功；停止批量进程后只认证 **1/144**，未完成的第二次获取不计完成，也未编译六日期合成值。 | 每项 660 | M3 受阻；已成功缓存的获取保留 |

Sentinel 20 m 光学格网为 **1775×1384**，8 个 uint16 全格网一轮约 **39.3 MB 未压缩内部数组**；144 次若都按此规模处理约 **5.7 GB** 的累积数组量级，**不是下载量预测**。COG 块/range、压缩、重复窗口与重处理会改变真实网络流量，故没有用 4,496 次请求乘一个假定字节数。Daymet 30 个 39×32×约365 日×约4 B 子集的数组阶数约 **55 MB 未压缩**，真实传输和响应开销同样未知。因 Daymet 明确 401，继续数小时级光学批量传输也无法构成完整 B1/M3 输入，所以在首个光学获取通过后停止，保留缓存而未虚称“六日期完成”。

**时间/来源核查。** Daymet 所需观测仅 d−7 至 d−1；Sentinel 成员 `lag_days` 均为 1–60。当前选中 Sentinel 处理中有 **26/144** 个 cohort 的生成日期严格晚于其对应目标日期；同日生成的另外 3 个亦无目标前发布时间证明。CMR 中 30 个 Daymet granule 的最近更新时间均为 2026-05-22，且这不是当年首次发布时间的凭证。因此当前只能主张**历史重建**，不能声称目标日前实时可用。NLCD 2016（2019 版）和旧 SRTM/GSHHG 与目标年份不同，继续按既有静态特征合同使用；WorldCover 2020 固定土地支持的当时发布时间未证明，进一步限定历史重建。没有更换版本或更改空间支持/QA。

**完整源城市的下一次最小交付与工作量。** 若只完成当前六日期**探索性**试构建，不必先把镜像改称官方：需在本地运行进程中提供有效 Earthdata 凭据以获取冻结 30 个 V4/R1 子集，不写入仓库或日志；经另行授权后从首个已缓存 Sentinel 获取继续完成原 **144** 次并编译六日期，而不是重建队列或改变窗口。若要**正式**验收源城市，外部还须提供原始完整官方 **2020 Colorado TIGER/Line `tl_2020_08_place.zip` 与 `tl_2020_08_tract.zip`**，附可核验 Census 来源 URL、获取时间、文件大小与 SHA-256；无需再重试已受阻接口。按原 15 m Hausdorff、110 GEOID 完全一致、30 m zone 零差异规则比较；分析支持变化则重算受影响部分，不能直接沿用本轮预测变量/标签。完整多年采集不能由 6/6 试采外推：Colorado 目录约 **80** 次候选过境，10 次 QA 试采已知 6 充分、4 不足，仍约 **70 次未检验**；先按原 QA 核查这些日期，仅对 QA 足够者读取热目标，再按最终保留日期的**窗口并集**规划 Daymet/Sentinel（静态仅一次）。需要记录真实目录、重叠、窗口流量与 QA 淘汰，才能给出可信总字节/时长。Charlotte 原几何阻塞维持暂停。

这轮成功证明 Colorado 镜像支持上的静态、键空间、Daymet 元数据与 Sentinel 单次光学获取**可技术执行**；未证明完整 23/46 特征、未证明全部候选日期、未通过官方边界，也不构成模型改善或正式训练城市验收。

## 15. 六日期试构建续行：认证排查与 Sentinel 成本样本预设（2026-09-17）

本节在新网络/预测值读取前记录本次有限续行；第 13–14 节原合同及部分结果不变。Daymet 仅检查项目原 `EARTHDATA_TOKEN` / `NASA_EARTHDATA_TOKEN` / `EDL_TOKEN` 进程内 bearer 方式及本机相关配置的存在性，不记录或输出凭据。仅在有可用凭据时，对冻结的 30 个局部子集中的一个作最小验证，再继续六日期原天气构建；若无凭据则不重复匿名 401 请求。401 本身不区分未加载、过期和端点不匹配。

Sentinel 成本样本只新增两次已冻结物理获取：`sentinel-2a|2021-10-05T17:42:11.024000Z|R98|GS2A_20211005T174211_032842`（1 item、城市覆盖约 0.9565）及 `sentinel-2a|2020-09-10T17:39:11.024000Z|R98|GS2A_20200910T173911_027265`（4 items、完整城市覆盖）。前者是冻结目录中近全覆盖的单瓦片获取，后者是最早的完整四瓦片获取；未使用温度、目标可用性或误差选样。原有首个已认证四瓦片获取保持缓存，不重算。分别记录新获取的 GDAL `/vsicurl/` 实际 HTTP 方法数/下载字节、Python 元数据/签名请求数与响应字节、处理耗时及本地缓存增量；只保存聚合计数，不保存签名 URL。以冻结清单核对唯一 item/资产、目标窗口成员和重叠读取，若没有确定且安全的重复工作可消除，不为优化而改科学算法。成本推算使用剩余唯一资产及两个规模样本，给出范围而非宣称精确全量耗时。完成这两次后停止，绝不自动处理剩余 Sentinel、编译六日期合成或训练评分。

**实际认证排查。** 原已成功使用的项目实现通过单个进程内 bearer 环境变量调用官方 Daymet DAP4 局部子集，另有终端隐藏输入方式；没有采用 Windows Credential Manager。检查当前进程、用户级、机器级的三个约定变量，以及本机项目相关的常见 `.netrc` / `_netrc` / `.env` 文件存在性，均未发现配置；没有读取或打印任何凭据。此前匿名 401 因而只说明**此进程未携带项目约定的凭据**，不能判定某个既往 token 已过期，也未验证带 token 的端点行为。没有重复请求 Daymet、没有天气值文件，21 项仍缺。用户需在自己的浏览器 [Earthdata Login](https://urs.earthdata.nasa.gov/documentation/for_users/user_token) 登录并生成新 token，用 PowerShell 隐藏输入，仅设置当前会话的 `EARTHDATA_TOKEN`；用现有 `experiments/source_city_predictor_trial.py daymet_access_probe` 对冻结 2020/dayl 局部子集做一次带认证请求，仅读取 NetCDF 文件头 8 字节、不保存值或凭据。显示 `DAYMET_ACCESS_NETCDF_OK` 才能证明此端点与当前凭据可用；401/403 则需在本地检查 token 有效期/授权，不循环重试。此前在聊天中暴露的 token 不应复用，建议撤销并换新。

**Sentinel 两次预设样本的真实结果。** 单瓦片样本在读取 04.00 基线产品 XML 时失败：XML 有 BOA 量化值却缺少每波段 `BOA_ADD_OFFSET`，原校准合同拒绝猜测偏移。首次诊断请求留下 51,560 B 的 XML，发生在计数器启用前，请求数不详；启用计数器后的缓存重跑为 0 次**新增** HTTP 请求、0 光学波段读取、0 有效 acquisition，不能把它当作速度样本。四瓦片 02.12 基线样本成功并通过现有 cache-lock 校验：**82.446 秒**，GDAL `/vsicurl/` 记录 **32 HEAD + 120 GET、GET 响应体 113,082,421 B**；Python 层 2 次请求、响应体 690 B；GDAL 配置重试数为 0，未观察到应用层重试。新增持久缓存 36,947 B（该获取的部分 XML 已在先前中断运行中存在）；这不是把 113 MB COG 范围响应存到磁盘。HTTP 头及底层传输开销未计入这些响应体字节。现有有效缓存由 1 增至 **2/144**；失败单瓦片保持 pending。机器记录为 ignored `sentinel_cost_samples.json` 和 `sentinel_cost_audit.json`，剩余 142 次的冻结清单为 ignored `sentinel_remaining_acquisitions.csv`，均在原 `exports/SOURCE_CITY_PREDICTOR_TRIAL/exploratory_mirror/colorado_springs_co/` 下。

**重复读取与条件性成本。** 冻结目录的 144 次获取对应 562 个 item，`item_id` 无重复，8 类资产的 **4,496 个 href 均唯一**；144 条目标窗口成员也各指向不同获取。本轮没有发现同一资产跨 tract 或日期重新下载的队列重复项。一次波段的平均与饱和最大值会在同一打开的 GDAL 数据集上各执行重投影，但 GDAL 块缓存是否完全消除底层重复范围请求不能从聚合计数分离；不能擅自只做一次重投影而改变饱和语义。故本轮**没有安全的执行优化可实施，也不存在优化前后数值对照**；保留原算法及已认证缓存。剩余为 **142 次 / 554 个唯一 item / 4,432 个唯一资产**。仅将这一个成功的四瓦片样本按 item 线性外推，约 **11,419 秒（3.17 小时）/ 15.66 GB GDAL GET 响应体**；按 0.5–2 倍列出 **1.6–6.3 小时 / 7.8–31.3 GB** 只是工程情景，**不是实测置信区间或保证**。现有 9 份 XML 共 461,975 B、两份有效获取缓存共 85,207 B；按这些文件量级推算，剩余 Sentinel 持久元数据/逐获取输出约 35 MB，预留 30–100 MB 较稳妥，**不包含未来 Daymet 子集、临时内存或失败重试**。估算也不乘到全年。剩余基线构成为 02.12:46、03.00:23、04.00:14、05.09:12、05.10:47；仅一份 04.00 XML 已证实缺 offset，不把全部同基线或 05.xx 宣告失败。校准缺口解决前，上述全量成本仅为条件性估算，不能据此启动批量读取。

**执行边界与下一步。** 静态 18、日历 2、660 完整键与 587 已有标签键连接保持完成；Daymet 21 和 Sentinel 5 的六日期值仍未完成，部分 parquet 不可训练。要继续，先由用户在本地提供新的进程内 Earthdata token 并让最小探针通过；另需以可核验的官方产品元数据确定 04.00 缺失 offset 的处理，且验证不改变原反射率合同，不能凭假定的 −1000 或跳过困难获取。官方 Colorado 2020 几何等价门槛仍未完成，Charlotte 暂停。未读取新目标、LA 2025 或外城目标，未训练或评分。
## 16. 六日期 Daymet 安全入口与 Sentinel BOA 校准审计（2026-09-17）

本轮只允许 Colorado Springs 已冻结六日期、30 个 Daymet 年×变量子集的本地隐藏凭据探针及成功后的构建；Sentinel 只检查此前失败的 2021-10-05 单 tile 04.00 产品和此前成功的 2020-09-10 02.12 样本的元数据及最小栅格窗口。不得启动其余 142 次光学获取、读取新热目标、模型训练或评分。镜像边界仍为探索性，官方等价性未核验；Charlotte 暂停。凭据不写入参数、文件或日志；本轮不使用聊天中提供的凭据。

Daymet 本地入口可以在未来由用户主动运行，但必须先验证同一冻结清单、固定支持和单独的最小授权，再隐式读取凭据；独立探针成功不构成后续进程授权。任何失败保留既有有效缓存。本节追加记录，不替换此前的合同和负结果。

### 执行结果与停止点

新增复用 `experiments/source_city_predictor_trial.py` 的 `daymet_local` 模式：交互终端隐藏输入一次，先请求冻结的 2020/dayl 小窗口并检查 NetCDF magic，成功才逐一获取冻结 2020–2024×六变量的 30 个子集；每个已有子集先验证，不删有效缓存；全部通过后以原 1/3/7 天窗口和固定土地支持编译 660 行×21 项 Daymet 特征。凭据仅保留于此次 Python 进程内，不经参数、环境变量、文件或子进程传递；进程结束即失效。广义阶段权限关闭，只保留受冻结 inventory SHA-256 `ac88d1e5a11fbfc9e2243a6e76e6a8f732ae44f60ce73f2542b7a4266f624db8` 约束的用户主动本地运行入口。本次未输入本地凭据、未运行真实认证或下载，Daymet 特征仍未完成。

Sentinel 实际失败资产是 Microsoft Planetary Computer `sentinel-2-l2a` 的 `S2A_MSIL2A_20211005T174211_R098_T13SED_20220512T201134`，产品 URI 标记 `N0400`，缓存产品 XML SHA-256 `d930089a32474963a07fca9164570730ab19aa33ffe9085ee884d07cbfd9e3d2`。XML 的 `PROCESSING_BASELINE=04.00`、`BOA_QUANTIFICATION_VALUE=10000`，但全文件没有 `BOA_ADD_OFFSET` 标签；STAC B04 没有 `raster:bands` 校准字段；实际 B04 COG 是 uint16，nodata=0，GDAL scale=1、offset=0、空 band tags。只读取了一个 8×8 像元窗口（DN 92–3400），未读取剩余获取。这个 GDAL identity transform **不能**证明提供方已调和，也不能证明应使用零 BOA offset。Copernicus [产品规格](https://sentiwiki.copernicus.eu/web/s2-products) 明确 04.00+ 逐波段 `(DN+BOA_ADD_OFFSET)/QUANTIFICATION_VALUE` 和 DN=0 无数据；[Planetary Computer 的 2022 公告](https://github.com/microsoft/PlanetaryComputer/discussions/40) 只说明改用 Sen2Cor 2.10，没有为本资产给出统一数值变换。因此这是本资产所需校准元数据缺失、且未找到可核验的提供方替代约定（第三种情形）；现有解析器按设计拒绝，不改成猜测的 −1000 或零。

对照的 2020-09-10 `N0212` 产品 XML SHA-256 `6fc3c93e6474680afa58b6e13b84353e1e8d2c8ef881e404889f6bd532142d21` 正常解析量化值 10000 和七个零 offset，校准哈希 `27af16e52667366cf1896a495cd7f6c3d944e025801344fc02d8b1a334db7254`；此前完整四 tile 获取仍是有效缓存。聚焦测试覆盖 04.00 缺/部分 offset 拒绝、已知 offset 的转换、nodata/饱和掩膜、SCL 4/5 与指数分母，以及本地 Daymet 探针失败不启动下载。此轮未重写任何已有 Sentinel 缓存。

同一 `src/la_heat/sentinel_feature_builder.py` 被原 source/portable 管线调用，因此若盲目改变解码约定，历史源特征合同和缓存也可能受影响；本轮转换代码未变，历史输入无需重算。只读扫描历史 `data/raw/sentinel/product_metadata` 的 449 份 XML：02.12 无 offset 128、03.00 无 offset 49；04.00 有 offset 52、05.09 有 20、05.10 有 166、05.11 有 34，**历史 PB≥04 元数据缺 offset 为 0/272**。这限定了当前已发现的缺证据问题属于 Colorado 新试采产品，不意味着其他未读取产品都安全。未完成清单中 14 个 04.00、12 个 05.09、47 个 05.10（共 73 次 PB≥04）需要逐产品校准证据，不能假定都缺失。其余 142 次光学获取未启动，批量续跑条件**未满足**。如能取得对应产品正确的逐波段 offset 元数据或提供方对这些具体资产已转换的可核验声明，先做同一失败样本的聚焦修复和回归，再考虑批量；否则保持不可处理。官方 Colorado 2020 边界等价性仍待核验，Charlotte 暂停。

## 17. 六日期 Sentinel 元数据全清单预检与条件续跑授权（2026-09-17）

本次用户授权仅对第 13 节冻结的六个 d−60:d−1 合成窗口及已有 144 次物理获取/562 条 item 做预检。先按同一产品 URI 去重，仅获取必要 STAC/产品 XML/资产校准元数据；记录每个产品转换信息完整、经产品基线证明无需新增 offset、缺少必需 offset、或读取失败的分类，以及受影响的六日期。不得因单个失败样本宣告全批失败。对缺失者可查询可信原始产品或可核验镜像的**同一产品**完整元数据，产品 URI、平台、时刻、tile、baseline、处理版本须逐项匹配；保留原 XML 和来源证据，不猜值、不借相邻产品、不静默换重处理版本。

仅在全部所需产品有可核验转换规则、原失败样本的最小窗口和成功样本回归通过、日期/产品/QA/统计合同未变、资源足够时，才从现有缓存续跑剩余获取并编译六日期 Sentinel5。监测真实流量相对先前约 15.66 GB 的单样本粗估；异常时暂停定位，不删除/重建缓存。若仍有必需元数据无法恢复，不启动批量，列出阻断产品和目标日期；不得填造、缩短窗口或遗漏困难观测。Daymet 仅检查是否已有用户本地完成的 30 子集和 660×21 输出；未完成则等待本地隐藏输入入口，不索取聊天凭据。即使两者完成，也只连接 660 全键并核对 587 既有标签**键**，不读新目标值、不训练评分。镜像边界仍为探索性，官方 Colorado 几何待核验，Charlotte 暂停，LA 2025 与外城目标不开放。不提交或推送。

### 实际预检与来源恢复（已完成）

复用 `experiments/source_city_predictor_trial.py sentinel_metadata_preflight`，逐一核对冻结 STAC snapshot 哈希、产品 URI/平台/获取时刻/tile/processing baseline/生成时刻与同产品 XML，并用原逐波段解析器分类。562 条选中 item 恰为 **562 个唯一产品**；XML 缓存现有 562 份，机器结果为 ignored `exports/SOURCE_CITY_PREDICTOR_TRIAL/exploratory_mirror/colorado_springs_co/sentinel_metadata_preflight.json`（SHA-256 `0ba2c695bd709b1913ddcd827ec96c04b38519abca9b6d7985ecf974a47b3243`）。选中 item CSV SHA-256 `281621dabd2fc274069674269a5c4000b219db5b5b67cb9972eac694dfb40fd4`，窗口成员 CSV SHA-256 `09bdf24aeac3cbae32e25a32593e3f3e04b85b96b0cb00c2d2e95f8c66b9d7e2`。本步**没有新增科学栅格读取**。

| 元数据分类 | 唯一产品数 | 科学判断 |
|---|---:|---|
| PB<04，按原合同不需新增 BOA offset | 276 | 产品身份与量化值已核实 |
| PB≥04，逐波段转换信息完整 | 285 | 产品身份和所需 offset 已核实 |
| 必需 offset 缺失 | **1** | 不可处理，不用零或常见值代替 |
| 读取/身份核对失败 | 0 | 无此类待重试项 |

按冻结成员映射：2020-10-31（96 个）和 2021-05-27（94 个）全为旧基线；**2021-10-18 为 86 个旧基线加 1 个缺失**；2022-10-29、2023-10-24、2024-05-03 各为 95、94、96 个转换完整产品。故已证实的缺失范围是 **1/562 产品、1/144 物理获取、1/6 目标合成日期**；不能由此认定整批都缺元数据，也不能把其余五个元数据齐全窗口误报为已构建特征。

唯一阻断产品仍是 `S2A_MSIL2A_20211005T174211_N0400_R098_T13SED_20220512T201134.SAFE`（tile 13SED，2021-10-05 17:42:11 UTC，Sentinel-2A，PB04.00，生成 2022-05-12）。从当前 Planetary Computer 同一 STAC item 再查询，产品 URI 与 metadata href 均与冻结记录一致；实时重新读取相同 product-metadata XML 为 HTTP 200、51,560 B，SHA-256 仍为 `d930089a32474963a07fca9164570730ab19aa33ffe9085ee884d07cbfd9e3d2`，仍无 BOA offset。该同产品 granule XML（552,865 B）与 datastrip XML（22,395,761 B）也无 BOA offset；后者的普通 `VOLTAGE_OFFSET` 不能充当光学辐射校准。按完全相同产品名在 [Copernicus 官方 OData 目录](https://documentation.dataspace.copernicus.eu/APIs/OData.html) 查询，HTTP 200、结果 0 条；[Planetary Computer 发布说明](https://github.com/microsoft/PlanetaryComputer/discussions/40)说明其 L2A 有 Sen2Cor 生产环节，因此不能把另一重处理版本的官方 XML 视为当前 COG 的同一产品校准。没有找到经身份核对且含所需 offset 的替代元数据；**没有追加转换覆盖、没有覆盖旧 XML，也没有声称恢复成功**。

先前失败样本的 B04 8×8 DN/COG 标签检查及 PB02.12 已认证四 tile 回归仍为第 16 节结果；由于缺少实际 offset，不能完成该 PB04 样本的数值转换验证，故本次条件授权的批量门槛未通过。**剩余 142 次影像获取没有启动，Sentinel5 未编译**；2021-10-18 合成不得跳过这一获取或改短窗口。Daymet 本地输出 `daymet_build.json` 和 660×21 特征文件均不存在，30 个子集文件为 0；仍等待用户私下运行入口。当前仅 static18/calendar2/完整 660 键/587 标签键连接已有旧审计，46 列部分表仍非完整输入。所需最小外部信息是该**同一产品**可核验的完整 BOA 校准元数据，或 Planetary Computer 对此具体资产编码/转换的正式说明；没有它不能放行批量。本轮不改变官方边界待核验、Charlotte 暂停及禁止模型/目标读取的状态。

## 18. 用户完成 Daymet 后的六日期输入核验与 Sentinel 恢复门槛（2026-09-17）

以上第 17 节记录的是当时状态；此后用户在本地运行隐藏输入入口，返回 `COLORADO_DAYMET_21_COMPLETE 660`。本轮进一步核验产物，而非只依赖终端字符串：冻结 `daymet_build.json` SHA-256 为 `6f851aff2e3ec793ad82063624fea3830e1112fc16ca3b733a582f7e79620e67`，其中输出 SHA-256 与实际 `daymet_features.parquet` 的 `b483a07aaf3e7a6b986c9ab6bdecc921225b6cbc7b103da838e0f55954a6ce77` 一致，inventory SHA-256 仍为 `ac88d1e5a11fbfc9e2243a6e76e6a8f732ae44f60ce73f2542b7a4266f624db8`。30 份缓存 NetCDF（合计 23,818,149 B）均重新通过冻结变量、39×32 网格和子集规格校验；六日期各 110 个唯一社区键，合计 660；21 项既有 d−1/d−3/d−7 特征逐项无缺失或非有限值。未读取凭据，未重复下载。

Sentinel 元数据预检仍为 562 个唯一产品：276 个原合同无需新增 BOA offset，285 个转换信息完整，**1 个必需 offset 缺失**，读取或身份失败 0。缺口只影响 2021-10-18 的一个物理获取，即 `S2A_MSIL2A_20211005T174211_N0400_R098_T13SED_20220512T201134.SAFE`。除第 17 节的同产品 Planetary Computer XML、granule、datastrip 和 Copernicus 精确名称查询外，还核查了公开 Sentinel Hub AWS 同 tile/日期产品目录：可取得的分别为 N0301 与 N0500，不能充作当前 N0400 COG 的校准元数据；相同产品名的尝试路径及 GCP 公共 Sentinel 路径未得到该精确产品。没有发现可核验的同产品逐波段 BOA offset 或该具体资产已变换的提供方声明，因此失败样本无法进行有依据的数值转换验证，批量放行门槛仍未满足。原有 2/144 获取缓存保持有效，剩余 **142** 次未启动；五项 Sentinel 合成特征均未构建。不得因只有一项元数据缺失，就删去它、缩短 2021-10-18 的 d−60:d−1 窗口或猜测 offset。

重新运行只读键/模式审计后，`summary.json` SHA-256 为 `ded6701fdb2df6a8412450d54b27ce11bebedf4be26f971b9318612208632e12`，状态为 `partial_trial_sentinel_calibration_blocked`。暂存表有 **660 行、46 项合同特征**，其中静态 18、日历 2、Daymet 21 各项均无缺失；Sentinel 5 各缺 660 行，共 3,300 个缺失单元。六日期各 110 行且全键唯一；既有探索标签仅连接 **587 个键**，不读取或分析标签温度值。输入时间仍为原 Daymet 滞后及 Sentinel d−60:d−1，不能据此声称产品在目标前已发布。当前六日期输入不完整，也不能把探索性镜像边界产物当作正式训练数据。

**下一决策。** 先获得上述精确 N0400 产品对应的可信完整校准元数据，或 Planetary Computer 对该具体 COG 数值处理的可核验声明；随后在失败样本最小窗口和已成功样本上验证数值转换、nodata、掩膜及指数，才可恢复冻结清单的 142 次获取。即使六日期 Sentinel 完成，正式源城市构建还需官方 2020 几何等价核验，并对尚未检查的其他目录日期独立确认 QA、目标与预测变量可用性。Charlotte 仍暂停；本轮没有新增热目标、模型训练或评分。

## 19. 按目标日期隔离后的实际五日期 Sentinel 构建（2026-09-17）

本节是第 18 节之后的新执行结果，不回写此前的负结果。复核冻结 `sentinel_metadata_preflight.json` 与成员清单：六个目标窗口各恰有 24 次不同的物理获取，窗口间不共享；缺 BOA offset 的 `S2A_MSIL2A_20211005T174211_N0400_R098_T13SED_20220512T201134.SAFE` 只属于 **2021-10-18**。因此仅其余五日期的 120 次获取获本轮执行授权，问题日期的完整 110 个预测键不删除，也不跳过此产品合成。日期窗口、产品、QA、波段、反射率缩放和合成统计均沿用原合同，原始六日期缓存锁不变。

五日期获取最终 **120/120 认证完成，0 个遗留获取失败**。一次 2021-04-21 B02 的远程 COG 瓦片短读曾产生 `WarpOperationError`；保留已完成缓存后，仅对此类远程读取错误增加一次有限、单资产线程重试，未更换产品或数值算法。读取耗时记录约 6,787 秒，不代表完整年度采集成本。第一次纯缓存合成在写出 lineage 时因重复插入已有 `city_id` 而停止；修复列写出后仅从同一 120 份认证缓存重编译，没有重读影像。

ignored 完成记录 `exports/SOURCE_CITY_PREDICTOR_TRIAL/exploratory_mirror/colorado_springs_co/sentinel_compiled_five_dates/FIVE_DATE_COMPLETE.json` SHA-256 为 `a8f9181847bf9f9778edce3605c54eaf0eb9dfbe7ebcc2a108c68716d2d2c559`。其三个输出的字节数、行数与 SHA-256 均复核一致：Sentinel 特征 550 行，逐社区/日期审计 550 行，物理获取 lineage 13,200 行。五个日期各 110 个唯一社区键，五项 Sentinel 特征每行均有有限值；lineage 的来源时差为 1–59 天，全部早于目标日期。这里的“无缺失”仅针对这五个已完成日期；没有改变原 QA 或挑选易得社区。

重新生成完整预测集合审计：仍为 **660 行×46 项合同特征**、六日期各 110 个唯一键。2020-10-31、2021-05-27、2022-10-29、2023-10-24、2024-05-03 共 **550 行所有 46 项均有限**；2021-10-18 的 110 行静态 18、日历 2、Daymet 21 项有值，Sentinel 5 项各缺 110 行，共 550 个“校准阻塞、未计算”单元，不是正常云覆盖缺失。现有 587 个探索标签仅作键连接，未读取温度数值或分析误差。机器摘要 `summary.json` SHA-256 为 `772a4b8b8f625a4808ad2a5f08202cefdd82b6890e99303ab8f40109ad5cb8c8`，状态 `partial_trial_one_date_sentinel_calibration_blocked`，按日期区分五个 `calculated_complete` 与一个 `calibration_blocked_not_computed`。五日期完整不等于六日期完成，也不能据此正式验收镜像边界训练数据。

剩余准确阻塞仍为上述 **同一 N0400 产品**的可信逐波段 BOA offset，或该具体 Planetary Computer COG 已执行何种转换的可核验声明；不得借用同瓦片的 N0301/N0500 版本或按常见值填入。取得证据后需先在该产品最小窗口验证转换、nodata、掩膜和指数，再考虑仅剩的 2021-10-18 日期。正式源城市构建另外仍需官方 2020 边界等价性核验，以及其余目录日期的 QA/目标/预测变量可用性检查。Charlotte 继续暂停；本轮不训练、不评分、不读取新增热目标、LA 2025 或四评估城市目标。
