"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import TractDetailExplorer from "./components/TractDetailExplorer";

type ModelId = "B1" | "M2";
type MapMode = "observed" | "prediction" | "residual";
type Scope = "date" | "all";
type EvaluationRecord = [
  tractIndex: number,
  observedLstC: number,
  b1PredictedLstC: number,
  m2PredictedLstC: number,
  validFraction: number,
  medianStUncertaintyK: number,
  sentinelAvailable: number,
];

type Tract = {
  id: string;
  name: string;
  block: string;
  neighborhood: string;
  neighborhoodShare: number;
  neighborhoodCoverage: number;
  neighborhoods: Array<[name: string, coveredShare: number]>;
  path: string;
};

type PixelGrid = {
  columns: number;
  rows: number;
  pixelCount: number;
  cells: Array<[column: number, row: number, tractIndex: number]>;
};

type TractPayload = {
  viewBox: [number, number, number, number];
  tractCount: number;
  neighborhoodCount: number;
  pixelGrid: PixelGrid;
  tracts: Tract[];
};

type DateEvaluation = {
  date: string;
  sensor: string;
  rowCount: number;
  records: EvaluationRecord[];
};

type EvaluationPayload = {
  evaluationRowCount: number;
  independentDateCount: number;
  defaultDate: string;
  dates: DateEvaluation[];
};

type MetricRecord = {
  model_id: ModelId;
  equal_date_weighted_mae_c: number;
  pooled_rmse_c: number;
  pooled_oos_r2: number;
  equal_date_weighted_within_date_anomaly_mae_c: number;
  median_per_date_spearman: number;
  independent_date_count: number;
  independent_spatial_block_count: number;
  tract_date_row_count: number;
};

type PerDateMetric = {
  model_id: ModelId;
  target_date: string;
  mae_c: number;
  rmse_c: number;
  spearman_rho: number;
  tract_date_row_count: number;
};

type HotspotMetric = {
  model_id: ModelId;
  independent_date_count: number;
  mean_per_date_average_precision: number;
  mean_per_date_precision_at_k: number;
};

type DateAudit = {
  target_date: string;
  sensor: string;
  date_usable: boolean;
  evaluation_cohort_count: number;
  date_exclusion_reason: string | null;
  relative_endpoint_coverage_pass: boolean;
};

type MetricsPayload = {
  modelMetrics: MetricRecord[];
  perDateMetrics: PerDateMetric[];
  protocolGates: Array<{
    gate_id: string;
    observed_value: number;
    passed: boolean;
    required_for_protocol_success: boolean;
  }>;
  bootstrap: {
    baseline_point_mae_c: number;
    target_model_point_mae_c: number;
    absolute_mae_improvement_c: number;
    relative_mae_improvement_percent: number;
    relative_mae_improvement_ci_lower_percent: number;
    relative_mae_improvement_ci_upper_percent: number;
    bootstrap_replicates: number;
    probability_improvement_gt_zero: number;
  };
  hotspotSummary: HotspotMetric[];
  dateAudit: DateAudit[];
};

type DisplayManifest = {
  state: string;
  scientificIdentity: {
    claimId: string;
    evidenceZipSha256: string;
    packageRepositoryGitHead: string;
  };
};

type SiteBundle = {
  tracts: TractPayload;
  evaluation: EvaluationPayload;
  metrics: MetricsPayload;
  manifest: DisplayManifest;
};

const MODEL_LABELS: Record<ModelId, string> = {
  B1: "B1 · climatology baseline",
  M2: "M2 · primary model",
};

const ASSET_BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
const HERO_DATE = "2025-09-03";

const HEAT_STOPS: Array<[number, string]> = [
  [28, "#f5f0e6"],
  [35, "#eac78d"],
  [42, "#e98a5f"],
  [49, "#bd4d45"],
  [56, "#542436"],
];

const RESIDUAL_STOPS: Array<[number, string]> = [
  [-8, "#244a68"],
  [-4, "#91b7ca"],
  [0, "#f5f0e6"],
  [4, "#df8a6e"],
  [8, "#8d3035"],
];

function mixHex(start: string, end: string, amount: number) {
  const from = start.match(/\w\w/g)?.map((part) => Number.parseInt(part, 16)) ?? [0, 0, 0];
  const to = end.match(/\w\w/g)?.map((part) => Number.parseInt(part, 16)) ?? [0, 0, 0];
  const channels = from.map((channel, index) =>
    Math.round(channel + (to[index] - channel) * amount),
  );
  return `#${channels.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;
}

function colorFromStops(value: number, stops: Array<[number, string]>) {
  if (value <= stops[0][0]) return stops[0][1];
  if (value >= stops[stops.length - 1][0]) return stops[stops.length - 1][1];
  for (let index = 1; index < stops.length; index += 1) {
    const [rightValue, rightColor] = stops[index];
    const [leftValue, leftColor] = stops[index - 1];
    if (value <= rightValue) {
      return mixHex(leftColor, rightColor, (value - leftValue) / (rightValue - leftValue));
    }
  }
  return stops[stops.length - 1][1];
}

function formatDate(date: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${date}T00:00:00Z`));
}

function compactDate(date: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${date}T00:00:00Z`));
}

function formatSensor(sensor: string) {
  return sensor.replace("landsat-", "Landsat ");
}

function valueForRecord(record: EvaluationRecord | undefined, mode: MapMode, model: ModelId) {
  if (!record) return null;
  const predicted = model === "M2" ? record[3] : record[2];
  if (mode === "observed") return record[1];
  if (mode === "prediction") return predicted;
  return predicted - record[1];
}

function ModelToggle({
  value,
  onChange,
}: {
  value: ModelId;
  onChange: (model: ModelId) => void;
}) {
  return (
    <div className="segmented-control" aria-label="Prediction model">
      {(["M2", "B1"] as ModelId[]).map((model) => (
        <button
          className={value === model ? "active" : ""}
          key={model}
          onClick={() => onChange(model)}
          type="button"
        >
          {model}
        </button>
      ))}
    </div>
  );
}

function HeroPixelMap({
  pixelGrid,
  tracts,
  records,
  date,
}: {
  pixelGrid: PixelGrid;
  tracts: Tract[];
  records: Map<number, EvaluationRecord>;
  date: string;
}) {
  const [activeCell, setActiveCell] = useState<number | null>(null);
  const activeTractIndex =
    activeCell === null ? null : pixelGrid.cells[activeCell]?.[2];
  const activeTract =
    activeTractIndex === null || activeTractIndex === undefined
      ? null
      : tracts[activeTractIndex];
  const activeRecord =
    activeTractIndex === null || activeTractIndex === undefined
      ? undefined
      : records.get(activeTractIndex);
  const activePrediction = activeRecord?.[3];
  const neighborhoodList = activeTract?.neighborhoods
    .filter(([, share]) => share >= 0.1)
    .map(([name]) => name)
    .join(" / ");

  return (
    <aside className="hero-pixel-map" aria-label="Pixel mosaic of M2 predicted surface heat">
      <div className="hero-pixel-heading">
        <span>{formatDate(date)}</span>
        <strong>M2 predicted daytime LST</strong>
        <div className="hero-pixel-scale">
          <i />
          <small>Darker color = hotter</small>
        </div>
      </div>
      <svg
        aria-label="Los Angeles rendered as equal display squares colored by tract-level M2 prediction. Focus and use arrow keys to inspect squares."
        onFocus={() => setActiveCell((current) => current ?? 0)}
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown", "Home", "End"].includes(event.key)) {
            return;
          }
          event.preventDefault();
          const current = activeCell ?? 0;
          if (event.key === "Home") setActiveCell(0);
          else if (event.key === "End") setActiveCell(pixelGrid.cells.length - 1);
          else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
            setActiveCell(Math.max(0, current - 1));
          } else {
            setActiveCell(Math.min(pixelGrid.cells.length - 1, current + 1));
          }
        }}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        tabIndex={0}
        viewBox={`0 0 ${pixelGrid.columns} ${pixelGrid.rows}`}
      >
        {pixelGrid.cells.map(([column, row, tractIndex], cellIndex) => {
          const tract = tracts[tractIndex];
          const prediction = records.get(tractIndex)?.[3];
          const fill =
            prediction === undefined ? "#d8b1a7" : colorFromStops(prediction, HEAT_STOPS);
          return (
            <rect
              className="hero-pixel"
              fill={fill}
              height={0.78}
              key={`${column}-${row}`}
              onPointerDown={() => setActiveCell(cellIndex)}
              onPointerEnter={() => setActiveCell(cellIndex)}
              rx={0.08}
              width={0.78}
              x={column + 0.11}
              y={row + 0.11}
            >
              <title>
                {tract.neighborhood} · {tract.name}
                {prediction === undefined ? "" : ` · ${prediction.toFixed(2)}°C`}
              </title>
            </rect>
          );
        })}
      </svg>
      <div className="hero-pixel-readout" aria-live="polite">
        {activeTract ? (
          <>
            <span>{neighborhoodList || activeTract.neighborhood}</span>
            <strong>
              {activePrediction === undefined ? "No evaluated value" : `${activePrediction.toFixed(2)}°C`}
            </strong>
            <small>{activeTract.name}</small>
          </>
        ) : (
          <>
            <span>{pixelGrid.pixelCount} equal squares</span>
            <strong>Hover to identify a place</strong>
            <small>Mapping L.A. neighborhood labels</small>
          </>
        )}
        <span className="hero-pixel-heat-note">Darker color = hotter</span>
      </div>
    </aside>
  );
}

function MapPanel({
  title,
  kicker,
  mode,
  model,
  tracts,
  viewBox,
  records,
  activeTract,
  onHover,
  onSelect,
  className = "",
}: {
  title: string;
  kicker: string;
  mode: MapMode;
  model: ModelId;
  tracts: Tract[];
  viewBox: [number, number, number, number];
  records: Map<number, EvaluationRecord>;
  activeTract: number | null;
  onHover: (index: number | null) => void;
  onSelect: (index: number) => void;
  className?: string;
}) {
  const isResidual = mode === "residual";
  return (
    <article className={`map-card ${className}`}>
      <div className="map-card-header">
        <div>
          <span>{kicker}</span>
          <h3>{title}</h3>
        </div>
        <span className="map-unit">°C</span>
      </div>
      <svg
        aria-label={`${title} census tract map`}
        className="tract-map"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        viewBox={viewBox.join(" ")}
      >
        {tracts.map((tract, index) => {
          const value = valueForRecord(records.get(index), mode, model);
          const fill =
            value === null
              ? "#ddd9cf"
              : colorFromStops(value, isResidual ? RESIDUAL_STOPS : HEAT_STOPS);
          return (
            <path
              className={activeTract === index ? "tract-shape active" : "tract-shape"}
              d={tract.path}
              fill={fill}
              fillRule="evenodd"
              key={tract.id}
              onClick={() => onSelect(index)}
              onMouseEnter={() => onHover(index)}
              onMouseLeave={() => onHover(null)}
              vectorEffect="non-scaling-stroke"
            />
          );
        })}
      </svg>
      <div className={`map-legend ${isResidual ? "residual" : "heat"}`}>
        <div className="legend-gradient" />
        <div className="legend-labels">
          {isResidual ? (
            <>
              <span>−8 under</span>
              <span>0</span>
              <span>+8 over</span>
            </>
          ) : (
            <>
              <span>28</span>
              <span>42</span>
              <span>56</span>
            </>
          )}
        </div>
      </div>
    </article>
  );
}

function TractReadout({
  tract,
  record,
  model,
  date,
}: {
  tract: Tract | null;
  record?: EvaluationRecord;
  model: ModelId;
  date: DateEvaluation;
}) {
  if (!tract) return null;
  const prediction = record ? (model === "M2" ? record[3] : record[2]) : null;
  const residual = record && prediction !== null ? prediction - record[1] : null;
  return (
    <aside className="tract-readout" aria-live="polite">
      <div className="readout-identity">
        <span className="eyebrow">Selected neighborhood</span>
        <h3>{tract.neighborhood}</h3>
        <p>
          {tract.name} <i /> GEOID {tract.id} <i /> Block {tract.block}
        </p>
      </div>
      {record ? (
        <div className="readout-values">
          <div>
            <span>Observed</span>
            <strong>{record[1].toFixed(2)}°</strong>
          </div>
          <div>
            <span>{model} predicted</span>
            <strong>{prediction?.toFixed(2)}°</strong>
          </div>
          <div>
            <span>Residual</span>
            <strong className={residual && residual > 0 ? "warm" : "cool"}>
              {residual && residual > 0 ? "+" : ""}
              {residual?.toFixed(2)}°
            </strong>
          </div>
          <div>
            <span>Valid pixels</span>
            <strong>{Math.round(record[4] * 100)}%</strong>
          </div>
        </div>
      ) : (
        <div className="no-observation">
          No evaluated Landsat observation for this tract on {formatDate(date.date)}.
        </div>
      )}
    </aside>
  );
}

function ScatterPlot({
  evaluation,
  currentDate,
  model,
  scope,
}: {
  evaluation: EvaluationPayload;
  currentDate: DateEvaluation;
  model: ModelId;
  scope: Scope;
}) {
  const records = useMemo(() => {
    const raw =
      scope === "date"
        ? currentDate.records
        : evaluation.dates.flatMap((date) => date.records);
    const stride = Math.max(1, Math.ceil(raw.length / 1900));
    return raw.filter((_, index) => index % stride === 0);
  }, [currentDate, evaluation, scope]);

  const width = 640;
  const height = 390;
  const pad = { left: 55, right: 20, top: 20, bottom: 48 };
  const low = 28;
  const high = 56;
  const x = (value: number) =>
    pad.left + ((value - low) / (high - low)) * (width - pad.left - pad.right);
  const y = (value: number) =>
    height - pad.bottom - ((value - low) / (high - low)) * (height - pad.top - pad.bottom);
  const ticks = [30, 35, 40, 45, 50, 55];

  return (
    <div>
      <svg
        aria-label={`Observed versus ${model} predicted surface temperature scatter plot`}
        className="scatter-chart"
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line className="chart-grid" x1={x(tick)} x2={x(tick)} y1={pad.top} y2={y(low)} />
            <line className="chart-grid" x1={x(low)} x2={x(high)} y1={y(tick)} y2={y(tick)} />
            <text className="chart-tick" textAnchor="middle" x={x(tick)} y={height - 22}>
              {tick}
            </text>
            <text className="chart-tick" textAnchor="end" x={43} y={y(tick) + 4}>
              {tick}
            </text>
          </g>
        ))}
        <line className="identity-line" x1={x(low)} x2={x(high)} y1={y(low)} y2={y(high)} />
        {records.map((record, index) => {
          const predicted = model === "M2" ? record[3] : record[2];
          return (
            <circle
              className={`scatter-point ${model.toLowerCase()}`}
              cx={x(record[1])}
              cy={y(predicted)}
              key={`${record[0]}-${index}`}
              r={scope === "date" ? 2.35 : 1.7}
            />
          );
        })}
        <text className="axis-label" textAnchor="middle" x={width / 2} y={height - 2}>
          Observed Landsat LST (°C)
        </text>
        <text
          className="axis-label"
          textAnchor="middle"
          transform={`translate(14 ${height / 2}) rotate(-90)`}
        >
          Predicted LST (°C)
        </text>
      </svg>
      <p className="chart-note">
        {scope === "all"
          ? `A deterministic display sample of ${records.length.toLocaleString()} points; metrics use all ${evaluation.evaluationRowCount.toLocaleString()} rows.`
          : `All ${currentDate.rowCount.toLocaleString()} formally evaluated tracts on this date.`}
      </p>
    </div>
  );
}

function PerformanceChart({
  metrics,
  selectedDate,
  onSelectDate,
}: {
  metrics: PerDateMetric[];
  selectedDate: string;
  onSelectDate: (date: string) => void;
}) {
  const [metric, setMetric] = useState<"mae_c" | "spearman_rho">("mae_c");
  const dates = Array.from(new Set(metrics.map((row) => row.target_date))).sort();
  const lookup = new Map(metrics.map((row) => [`${row.target_date}-${row.model_id}`, row]));
  const width = 920;
  const height = 310;
  const pad = { left: 48, right: 18, top: 25, bottom: 55 };
  const max = metric === "mae_c" ? 13 : 1;
  const x = (index: number) =>
    pad.left + (index / (dates.length - 1)) * (width - pad.left - pad.right);
  const y = (value: number) =>
    height - pad.bottom - (value / max) * (height - pad.top - pad.bottom);
  const ticks = metric === "mae_c" ? [0, 3, 6, 9, 12] : [0, 0.25, 0.5, 0.75, 1];
  const line = (model: ModelId) =>
    dates
      .map((date, index) => {
        const row = lookup.get(`${date}-${model}`);
        return `${index === 0 ? "M" : "L"}${x(index)},${y(Number(row?.[metric] ?? 0))}`;
      })
      .join(" ");

  return (
    <div>
      <div className="chart-toolbar">
        <div className="segmented-control small" aria-label="Performance metric">
          <button
            className={metric === "mae_c" ? "active" : ""}
            onClick={() => setMetric("mae_c")}
            type="button"
          >
            MAE
          </button>
          <button
            className={metric === "spearman_rho" ? "active" : ""}
            onClick={() => setMetric("spearman_rho")}
            type="button"
          >
            Rank agreement
          </button>
        </div>
        <div className="chart-key">
          <span className="key-b1" /> B1
          <span className="key-m2" /> M2
        </div>
      </div>
      <svg
        aria-label="Model performance across independent evaluation dates"
        className="performance-chart"
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line className="chart-grid" x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} />
            <text className="chart-tick" textAnchor="end" x={38} y={y(tick) + 4}>
              {metric === "mae_c" ? tick : tick.toFixed(2)}
            </text>
          </g>
        ))}
        <path className="performance-line b1" d={line("B1")} />
        <path className="performance-line m2" d={line("M2")} />
        {dates.map((date, index) => (
          <g
            className={date === selectedDate ? "date-point selected" : "date-point"}
            key={date}
            onClick={() => onSelectDate(date)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") onSelectDate(date);
            }}
            role="button"
            tabIndex={0}
          >
            <rect
              className="date-hit-area"
              height={height - pad.top - 12}
              width={34}
              x={x(index) - 17}
              y={pad.top}
            />
            {(["B1", "M2"] as ModelId[]).map((model) => {
              const row = lookup.get(`${date}-${model}`);
              return (
                <circle
                  className={`performance-dot ${model.toLowerCase()}`}
                  cx={x(index)}
                  cy={y(Number(row?.[metric] ?? 0))}
                  key={model}
                  r={date === selectedDate ? 6 : 4}
                />
              );
            })}
            <text
              className="date-label"
              textAnchor="end"
              transform={`translate(${x(index) + 4} ${height - 35}) rotate(-50)`}
            >
              {compactDate(date)}
            </text>
          </g>
        ))}
        <text className="axis-caption" x={pad.left} y={16}>
          {metric === "mae_c" ? "Mean absolute error (°C)" : "Per-date Spearman ρ"}
        </text>
      </svg>
      <p className="chart-note">
        Each point is one independent Landsat overpass date. Select a date to update the maps.
      </p>
    </div>
  );
}

function ConfidenceInterval({ bootstrap }: { bootstrap: MetricsPayload["bootstrap"] }) {
  const low = -20;
  const high = 70;
  const width = 900;
  const height = 150;
  const pad = 38;
  const x = (value: number) =>
    pad + ((value - low) / (high - low)) * (width - 2 * pad);
  const ticks = [-20, 0, 20, 40, 60];
  return (
    <div className="ci-wrap">
      <svg
        aria-label="Bootstrap confidence interval for relative MAE improvement"
        className="ci-chart"
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <line className="ci-axis" x1={x(low)} x2={x(high)} y1={70} y2={70} />
        <rect className="ci-negative" height={36} width={x(0) - x(low)} x={x(low)} y={52} />
        <line className="ci-zero" x1={x(0)} x2={x(0)} y1={28} y2={104} />
        <line
          className="ci-range"
          x1={x(bootstrap.relative_mae_improvement_ci_lower_percent)}
          x2={x(bootstrap.relative_mae_improvement_ci_upper_percent)}
          y1={70}
          y2={70}
        />
        <line
          className="ci-cap"
          x1={x(bootstrap.relative_mae_improvement_ci_lower_percent)}
          x2={x(bootstrap.relative_mae_improvement_ci_lower_percent)}
          y1={57}
          y2={83}
        />
        <line
          className="ci-cap"
          x1={x(bootstrap.relative_mae_improvement_ci_upper_percent)}
          x2={x(bootstrap.relative_mae_improvement_ci_upper_percent)}
          y1={57}
          y2={83}
        />
        <circle
          className="ci-point"
          cx={x(bootstrap.relative_mae_improvement_percent)}
          cy={70}
          r={8}
        />
        {ticks.map((tick) => (
          <g key={tick}>
            <line className="ci-tick" x1={x(tick)} x2={x(tick)} y1={91} y2={98} />
            <text className="chart-tick" textAnchor="middle" x={x(tick)} y={119}>
              {tick > 0 ? "+" : ""}
              {tick}%
            </text>
          </g>
        ))}
        <text className="ci-label point" textAnchor="middle" x={x(bootstrap.relative_mae_improvement_percent)} y={37}>
          +{bootstrap.relative_mae_improvement_percent.toFixed(1)}% point estimate
        </text>
        <text className="ci-label zero" textAnchor="middle" x={x(0)} y={142}>
          no improvement
        </text>
      </svg>
      <div className="ci-summary">
        <strong>
          95% CI {bootstrap.relative_mae_improvement_ci_lower_percent.toFixed(1)}% to +
          {bootstrap.relative_mae_improvement_ci_upper_percent.toFixed(1)}%
        </strong>
        <span>
          The interval crosses zero, so the predeclared uncertainty gate did not pass.
        </span>
      </div>
    </div>
  );
}

function App() {
  const [bundle, setBundle] = useState<SiteBundle | null>(null);
  const [error, setError] = useState("");
  const [model, setModel] = useState<ModelId>("M2");
  const [selectedDate, setSelectedDate] = useState("");
  const [selectedTract, setSelectedTract] = useState<number | null>(null);
  const [hoveredTract, setHoveredTract] = useState<number | null>(null);
  const [mobileMap, setMobileMap] = useState<MapMode>("observed");
  const [scatterScope, setScatterScope] = useState<Scope>("date");

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetch(`${ASSET_BASE_PATH}/data/tracts.json`).then((response) => response.json()),
      fetch(`${ASSET_BASE_PATH}/data/evaluation-2025.json`).then((response) =>
        response.json(),
      ),
      fetch(`${ASSET_BASE_PATH}/data/metrics.json`).then((response) => response.json()),
      fetch(`${ASSET_BASE_PATH}/data/display-manifest.json`).then((response) =>
        response.json(),
      ),
    ])
      .then(([tracts, evaluation, metrics, manifest]) => {
        if (cancelled) return;
        const nextBundle = { tracts, evaluation, metrics, manifest } as SiteBundle;
        setBundle(nextBundle);
        setSelectedDate(evaluation.defaultDate);
        const defaultDay = (evaluation as EvaluationPayload).dates.find(
          (date) => date.date === evaluation.defaultDate,
        );
        if (defaultDay?.records.length) {
          setSelectedTract(defaultDay.records[Math.floor(defaultDay.records.length / 2)][0]);
        }
      })
      .catch(() => {
        if (!cancelled) setError("The authenticated display data could not be loaded.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const currentDate = bundle?.evaluation.dates.find((date) => date.date === selectedDate);
  const recordMap = useMemo(
    () => new Map(currentDate?.records.map((record) => [record[0], record]) ?? []),
    [currentDate],
  );
  const heroRecordMap = useMemo(() => {
    const defaultDay = bundle?.evaluation.dates.find((date) => date.date === HERO_DATE);
    return new Map(defaultDay?.records.map((record) => [record[0], record]) ?? []);
  }, [bundle]);
  const activeTract = hoveredTract ?? selectedTract;
  const activeRecord = activeTract === null ? undefined : recordMap.get(activeTract);
  const activeTractData =
    activeTract === null || !bundle ? null : bundle.tracts.tracts[activeTract];

  if (error) {
    return (
      <main className="loading-screen">
        <span>LA Surface Heat Atlas</span>
        <h1>{error}</h1>
      </main>
    );
  }

  if (!bundle || !currentDate) {
    return (
      <main className="loading-screen">
        <span>LA Surface Heat Atlas</span>
        <div className="loading-rule" />
        <p>Loading the verified 2025 evaluation…</p>
      </main>
    );
  }

  const metricsByModel = new Map(bundle.metrics.modelMetrics.map((row) => [row.model_id, row]));
  const b1 = metricsByModel.get("B1")!;
  const m2 = metricsByModel.get("M2")!;
  const currentMetrics = bundle.metrics.perDateMetrics.filter(
    (row) => row.target_date === currentDate.date,
  );
  const hotspot = new Map(bundle.metrics.hotspotSummary.map((row) => [row.model_id, row]));
  const protocolPassed = bundle.metrics.protocolGates
    .filter((gate) => gate.required_for_protocol_success)
    .every((gate) => gate.passed);
  const mapProps = {
    model,
    tracts: bundle.tracts.tracts,
    viewBox: bundle.tracts.viewBox,
    records: recordMap,
    activeTract,
    onHover: setHoveredTract,
    onSelect: setSelectedTract,
  };

  return (
    <main>
      <header className="site-header">
        <a className="wordmark" href="#top">
          <span>LA</span> Surface Heat Atlas
        </a>
        <nav aria-label="Primary navigation">
          <a href="#explore">Explore</a>
          <a href="#tract-detail">Tract detail</a>
          <a href="#performance">Performance</a>
          <a href="#method">Method</a>
          <Link href="/cities">Four cities</Link>
        </nav>
        <Link className="header-tag" href="/cities">Four-city preview</Link>
      </header>

      <section className="hero" id="top">
        <div className="hero-copy">
          <span className="eyebrow">Neighborhood-scale historical hindcast · Los Angeles</span>
          <h1>
            Can public data predict
            <br />
            <em>where the city runs hot?</em>
          </h1>
          <p>
            A frozen model combines weather, land use, geography, and lagged satellite
            features to estimate daytime land-surface temperature at census-tract scale.
          </p>
          <div className="hero-actions">
            <a className="primary-action" href="#explore">
              Explore the evaluation <span>↓</span>
            </a>
            <span>15 independent dates · 71 spatial blocks</span>
          </div>
        </div>
        <HeroPixelMap
          date={HERO_DATE}
          pixelGrid={bundle.tracts.pixelGrid}
          records={heroRecordMap}
          tracts={bundle.tracts.tracts}
        />
      </section>

      <section className="result-band" aria-label="Primary held-out result">
        <div className="result-band-copy">
          <span className="eyebrow">What the held-out year says</span>
          <h2>A strong point estimate, with uncertainty still unresolved.</h2>
          <p>
            The mosaic above is a fixed display of M2 predictions. The formal result below
            still comes from the complete prespecified evaluation—not from the pixels.
          </p>
        </div>
        <aside className="result-card">
          <div className="result-card-top">
            <span>Primary held-out result</span>
            <span className={protocolPassed ? "status pass" : "status caution"}>
              {protocolPassed ? "Confirmed" : "Not protocol-confirmed"}
            </span>
          </div>
          <div className="result-number">
            <strong>30.5%</strong>
            <span>lower equal-date MAE<br />than the legal baseline</span>
          </div>
          <div className="result-comparison">
            <div>
              <span>M2</span>
              <strong>{m2.equal_date_weighted_mae_c.toFixed(3)}°C</strong>
            </div>
            <i />
            <div>
              <span>B1</span>
              <strong>{b1.equal_date_weighted_mae_c.toFixed(3)}°C</strong>
            </div>
          </div>
          <p>
            Promising point estimate. The 95% uncertainty interval crosses zero, so the
            predeclared confirmation gate was not met.
          </p>
        </aside>
      </section>

      <section className="metric-strip" aria-label="Evaluation summary">
        <div>
          <span>Formal rows</span>
          <strong>{bundle.evaluation.evaluationRowCount.toLocaleString()}</strong>
          <small>tract × date observations</small>
        </div>
        <div>
          <span>M2 RMSE</span>
          <strong>{m2.pooled_rmse_c.toFixed(3)}°C</strong>
          <small>vs {b1.pooled_rmse_c.toFixed(3)}°C baseline</small>
        </div>
        <div>
          <span>M2 OOS R²</span>
          <strong>{m2.pooled_oos_r2.toFixed(3)}</strong>
          <small>held-out variance explained</small>
        </div>
        <div>
          <span>Median rank ρ</span>
          <strong>{m2.median_per_date_spearman.toFixed(3)}</strong>
          <small>vs {b1.median_per_date_spearman.toFixed(3)} baseline</small>
        </div>
      </section>

      <section className="explorer section-shell" id="explore">
        <div className="section-heading split">
          <div>
            <span className="eyebrow">01 · Spatial explorer</span>
            <h2>Observed, predicted, and missed.</h2>
          </div>
          <p>
            All three maps use frozen scales. Select a date, switch models, and inspect any
            census tract without changing the underlying evaluation.
          </p>
        </div>

        <div className="explorer-toolbar">
          <div className="date-control">
            <button
              aria-label="Previous evaluation date"
              onClick={() => {
                const index = bundle.evaluation.dates.findIndex(
                  (date) => date.date === selectedDate,
                );
                const next = Math.max(0, index - 1);
                setSelectedDate(bundle.evaluation.dates[next].date);
              }}
              type="button"
            >
              ←
            </button>
            <label>
              <span>Physical overpass date</span>
              <select
                aria-label="Physical overpass date"
                onChange={(event) => setSelectedDate(event.target.value)}
                value={selectedDate}
              >
                {bundle.evaluation.dates.map((date) => (
                  <option key={date.date} value={date.date}>
                    {formatDate(date.date)} · {formatSensor(date.sensor)}
                  </option>
                ))}
              </select>
            </label>
            <button
              aria-label="Next evaluation date"
              onClick={() => {
                const index = bundle.evaluation.dates.findIndex(
                  (date) => date.date === selectedDate,
                );
                const next = Math.min(bundle.evaluation.dates.length - 1, index + 1);
                setSelectedDate(bundle.evaluation.dates[next].date);
              }}
              type="button"
            >
              →
            </button>
          </div>
          <div className="toolbar-meta">
            <span>{currentDate.rowCount.toLocaleString()} evaluated tracts</span>
            <ModelToggle onChange={setModel} value={model} />
          </div>
        </div>

        <div className="mobile-map-toggle">
          {(["observed", "prediction", "residual"] as MapMode[]).map((mode) => (
            <button
              className={mobileMap === mode ? "active" : ""}
              key={mode}
              onClick={() => setMobileMap(mode)}
              type="button"
            >
              {mode === "prediction" ? model : mode}
            </button>
          ))}
        </div>

        <div className="map-grid">
          <MapPanel
            {...mapProps}
            className={mobileMap === "observed" ? "mobile-visible" : ""}
            kicker="Reference"
            mode="observed"
            title="Observed Landsat LST"
          />
          <MapPanel
            {...mapProps}
            className={mobileMap === "prediction" ? "mobile-visible" : ""}
            kicker={MODEL_LABELS[model]}
            mode="prediction"
            title="Predicted LST"
          />
          <MapPanel
            {...mapProps}
            className={mobileMap === "residual" ? "mobile-visible" : ""}
            kicker="Prediction − observed"
            mode="residual"
            title="Residual"
          />
        </div>

        <TractReadout
          date={currentDate}
          model={model}
          record={activeRecord}
          tract={activeTractData}
        />
      </section>

      <TractDetailExplorer
        evaluation={bundle.evaluation}
        model={model}
        onSelectDate={setSelectedDate}
        onSelectTract={setSelectedTract}
        selectedDate={selectedDate}
        selectedTract={selectedTract}
        tracts={bundle.tracts.tracts}
        viewBox={bundle.tracts.viewBox}
      />

      <section className="section-shell performance" id="performance">
        <div className="section-heading split">
          <div>
            <span className="eyebrow">02 · Predictive performance</span>
            <h2>Better overall. Uneven by date.</h2>
          </div>
          <p>
            M2 has lower absolute MAE on 12 of 15 dates and better within-date rank
            agreement on every evaluated date. A few dates remain clear failure cases.
          </p>
        </div>

        <div className="analysis-grid">
          <article className="analysis-card wide">
            <div className="analysis-card-header">
              <div>
                <span>Across independent dates</span>
                <h3>Error and rank agreement</h3>
              </div>
              <span className="card-index">A</span>
            </div>
            <PerformanceChart
              metrics={bundle.metrics.perDateMetrics}
              onSelectDate={(date) => {
                setSelectedDate(date);
                document.querySelector("#explore")?.scrollIntoView({ behavior: "smooth" });
              }}
              selectedDate={selectedDate}
            />
          </article>

          <article className="analysis-card">
            <div className="analysis-card-header">
              <div>
                <span>Tract-level agreement</span>
                <h3>Observed vs predicted</h3>
              </div>
              <span className="card-index">B</span>
            </div>
            <div className="chart-toolbar">
              <ModelToggle onChange={setModel} value={model} />
              <div className="segmented-control small" aria-label="Scatter plot scope">
                <button
                  className={scatterScope === "date" ? "active" : ""}
                  onClick={() => setScatterScope("date")}
                  type="button"
                >
                  This date
                </button>
                <button
                  className={scatterScope === "all" ? "active" : ""}
                  onClick={() => setScatterScope("all")}
                  type="button"
                >
                  All dates
                </button>
              </div>
            </div>
            <ScatterPlot
              currentDate={currentDate}
              evaluation={bundle.evaluation}
              model={model}
              scope={scatterScope}
            />
            <div className="current-date-metrics">
              {currentMetrics.map((row) => (
                <div className={row.model_id.toLowerCase()} key={row.model_id}>
                  <span>{row.model_id} · {compactDate(row.target_date)}</span>
                  <strong>{row.mae_c.toFixed(2)}° MAE</strong>
                  <small>ρ {row.spearman_rho.toFixed(2)}</small>
                </div>
              ))}
            </div>
          </article>

          <article className="analysis-card">
            <div className="analysis-card-header">
              <div>
                <span>Crossed bootstrap · 5,000 replicates</span>
                <h3>What uncertainty changes</h3>
              </div>
              <span className="card-index">C</span>
            </div>
            <ConfidenceInterval bootstrap={bundle.metrics.bootstrap} />
            <div className="uncertainty-copy">
              <p>
                The point estimate strongly favors M2, and 92.2% of bootstrap draws show
                some improvement.
              </p>
              <p>
                The predeclared rule was stricter: the entire 95% interval had to remain
                above zero. It did not.
              </p>
            </div>
          </article>
        </div>
      </section>

      <section className="hotspot-section">
        <div className="section-shell hotspot-inner">
          <div>
            <span className="eyebrow light">03 · Relative hotspots</span>
            <h2>Finding the hottest fifth of neighborhoods.</h2>
            <p>
              On the 10 dates that passed the frozen spatial-coverage gate, M2 improved
              both ranking quality and top-k retrieval.
            </p>
          </div>
          <div className="hotspot-comparison">
            {(["B1", "M2"] as ModelId[]).map((modelId) => {
              const row = hotspot.get(modelId)!;
              return (
                <div className={modelId.toLowerCase()} key={modelId}>
                  <span>{MODEL_LABELS[modelId]}</span>
                  <strong>{row.mean_per_date_average_precision.toFixed(3)}</strong>
                  <small>mean average precision</small>
                  <div className="hotspot-bar">
                    <i style={{ width: `${row.mean_per_date_average_precision * 100}%` }} />
                  </div>
                  <p>{(row.mean_per_date_precision_at_k * 100).toFixed(1)}% precision @ k</p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      <section className="section-shell method" id="method">
        <div className="section-heading split">
          <div>
            <span className="eyebrow">04 · Study design</span>
            <h2>Built to resist easy answers.</h2>
          </div>
          <p>
            The final year was opened once, only after the models, predictors, thresholds,
            output schema, and uncertainty procedure had been frozen.
          </p>
        </div>
        <div className="method-flow">
          <div>
            <span>Inputs</span>
            <strong>46 legal predictors</strong>
            <p>Land use, geography, calendar, lagged Daymet weather, and lagged Sentinel-2.</p>
          </div>
          <i>→</i>
          <div>
            <span>Model</span>
            <strong>Frozen before 2025</strong>
            <p>No thermal bands, same-scene optical data, future observations, or tract IDs.</p>
          </div>
          <i>→</i>
          <div>
            <span>Reference</span>
            <strong>QA-filtered Landsat LST</strong>
            <p>15 usable physical overpass dates from 23 discovered dates.</p>
          </div>
          <i>→</i>
          <div>
            <span>Inference</span>
            <strong>Dates × spatial blocks</strong>
            <p>Grouped evaluation and crossed bootstrap—never a random row split.</p>
          </div>
        </div>

        <div className="limits-grid">
          <article>
            <span>Interpretation</span>
            <h3>Surface heat, not human exposure.</h3>
            <p>
              Land-surface temperature is a hazard proxy. It is not air temperature, a
              health outcome, or a measure of an individual person’s heat exposure.
            </p>
          </article>
          <article>
            <span>Prediction frame</span>
            <h3>Historical hindcast, not live forecast.</h3>
            <p>
              Dynamic observed predictors end on the day before each target date. The
              analysis does not claim an operational weather-forecast capability.
            </p>
          </article>
          <article>
            <span>Claim strength</span>
            <h3>Promising, not confirmed.</h3>
            <p>
              Strong point performance and spatial ranking coexist with an uncertainty
              interval that includes no improvement.
            </p>
          </article>
        </div>

        <div className="evidence-panel">
          <div>
            <span className="eyebrow">Read-only evidence</span>
            <h3>Every value on this site is tied to the completed evaluation claim.</h3>
          </div>
          <dl>
            <div>
              <dt>Display state</dt>
              <dd>{bundle.manifest.state}</dd>
            </div>
            <div>
              <dt>Evidence ZIP SHA-256</dt>
              <dd>{bundle.manifest.scientificIdentity.evidenceZipSha256}</dd>
            </div>
            <div>
              <dt>Claim ID</dt>
              <dd>{bundle.manifest.scientificIdentity.claimId}</dd>
            </div>
          </dl>
        </div>
      </section>

      <footer>
        <div>
          <strong>LA Surface Heat Atlas</strong>
          <span>Public-data prediction of neighborhood-scale urban surface heat.</span>
          <span>
            Neighborhood labels:{" "}
            <a href="https://github.com/datadesk/mapping-la-data">
              Los Angeles Times Mapping L.A.
            </a>
          </span>
        </div>
        <p>
          2025 held-out evaluation · {bundle.tracts.tractCount.toLocaleString()} Los Angeles
          census tracts · {bundle.evaluation.independentDateCount} usable dates
        </p>
      </footer>
    </main>
  );
}

export default App;
