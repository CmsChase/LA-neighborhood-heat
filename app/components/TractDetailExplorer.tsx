"use client";

import {
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useMemo,
  useRef,
  useState,
} from "react";
import styles from "./TractDetailExplorer.module.css";

type ModelId = "B1" | "M2";
type MapMode = "observed" | "prediction" | "error";

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

export type TractDetailExplorerProps = {
  tracts: Tract[];
  viewBox: [number, number, number, number];
  evaluation: EvaluationPayload;
  model: ModelId;
  selectedTract: number | null;
  selectedDate: string;
  onSelectTract: (tractIndex: number) => void;
  onSelectDate: (date: string) => void;
};

type ViewState = {
  x: number;
  y: number;
  width: number;
  height: number;
};

type TimelineRow = {
  date: string;
  sensor: string;
  record?: EvaluationRecord;
  observed?: number;
  prediction?: number;
  signedError?: number;
  absoluteError?: number;
};

const HEAT_STOPS: Array<[number, string]> = [
  [28, "#f5f0e6"],
  [35, "#eac78d"],
  [42, "#e98a5f"],
  [49, "#bd4d45"],
  [56, "#542436"],
];

const ERROR_STOPS: Array<[number, string]> = [
  [-8, "#244a68"],
  [-4, "#91b7ca"],
  [0, "#f5f0e6"],
  [4, "#df8a6e"],
  [8, "#8d3035"],
];

const MIN_ZOOM = 1;
const MAX_ZOOM = 9;
const DRAG_THRESHOLD_PX = 5;

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

function tractLabel(geoid: string, sourceName?: string) {
  const candidate = sourceName?.trim();
  if (candidate && /\d/.test(candidate)) return candidate;
  const code = geoid.replace(/\D/g, "").slice(-6).padStart(6, "0");
  return `Census Tract ${code.slice(0, 4)}.${code.slice(4)}`;
}

function formatDate(date: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${date}T00:00:00Z`));
}

function formatSensor(sensor: string) {
  return sensor.replace("landsat-", "Landsat ");
}

function clamp(value: number, low: number, high: number) {
  return Math.min(Math.max(value, low), high);
}

function clampView(view: ViewState, base: ViewState): ViewState {
  return {
    ...view,
    x: clamp(view.x, base.x, base.x + base.width - view.width),
    y: clamp(view.y, base.y, base.y + base.height - view.height),
  };
}

function recordValue(record: EvaluationRecord | undefined, mode: MapMode, model: ModelId) {
  if (!record) return null;
  const prediction = model === "M2" ? record[3] : record[2];
  if (mode === "observed") return record[1];
  if (mode === "prediction") return prediction;
  return prediction - record[1];
}

function buildLineSegments(
  rows: TimelineRow[],
  field: "observed" | "prediction",
  x: (index: number) => number,
  y: (value: number) => number,
) {
  const segments: string[] = [];
  let current: string[] = [];
  rows.forEach((row, index) => {
    const value = row[field];
    if (value === undefined) {
      if (current.length > 1) segments.push(current.join(" "));
      current = [];
      return;
    }
    current.push(`${x(index)},${y(value)}`);
  });
  if (current.length > 1) segments.push(current.join(" "));
  return segments;
}

export default function TractDetailExplorer({
  tracts,
  viewBox,
  evaluation,
  model,
  selectedTract,
  selectedDate,
  onSelectTract,
  onSelectDate,
}: TractDetailExplorerProps) {
  const baseView = useMemo<ViewState>(
    () => ({ x: viewBox[0], y: viewBox[1], width: viewBox[2], height: viewBox[3] }),
    [viewBox],
  );
  const [mapMode, setMapMode] = useState<MapMode>("prediction");
  const [mapView, setMapView] = useState<ViewState>(baseView);
  const [search, setSearch] = useState("");
  const [searchMessage, setSearchMessage] = useState("");
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<{
    pointerId: number;
    clientX: number;
    clientY: number;
    startView: ViewState;
    moved: boolean;
    pendingTractIndex: number | null;
  } | null>(null);

  const selectedDay = useMemo(
    () => evaluation.dates.find((day) => day.date === selectedDate) ?? evaluation.dates[0],
    [evaluation.dates, selectedDate],
  );
  const selectedDayRecords = useMemo(
    () => new Map(selectedDay?.records.map((record) => [record[0], record]) ?? []),
    [selectedDay],
  );
  const selectedTractData =
    selectedTract === null ? null : (tracts[selectedTract] ?? null);

  const timeline = useMemo<TimelineRow[]>(() => {
    if (selectedTract === null) {
      return evaluation.dates.map(({ date, sensor }) => ({ date, sensor }));
    }
    return evaluation.dates.map(({ date, sensor, records }) => {
      const record = records.find((candidate) => candidate[0] === selectedTract);
      if (!record) return { date, sensor };
      const prediction = model === "M2" ? record[3] : record[2];
      const signedError = prediction - record[1];
      return {
        date,
        sensor,
        record,
        observed: record[1],
        prediction,
        signedError,
        absoluteError: Math.abs(signedError),
      };
    });
  }, [evaluation.dates, model, selectedTract]);

  const zoom = baseView.width / mapView.width;
  const setZoomAround = (nextZoom: number, anchorX?: number, anchorY?: number) => {
    const boundedZoom = clamp(nextZoom, MIN_ZOOM, MAX_ZOOM);
    const nextWidth = baseView.width / boundedZoom;
    const nextHeight = baseView.height / boundedZoom;
    const centerX = anchorX ?? mapView.x + mapView.width / 2;
    const centerY = anchorY ?? mapView.y + mapView.height / 2;
    const ratioX = (centerX - mapView.x) / mapView.width;
    const ratioY = (centerY - mapView.y) / mapView.height;
    setMapView(
      clampView(
        {
          x: centerX - ratioX * nextWidth,
          y: centerY - ratioY * nextHeight,
          width: nextWidth,
          height: nextHeight,
        },
        baseView,
      ),
    );
  };

  const handlePointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    const target = (event.target as Element).closest("[data-tract-index]");
    const tractIndexValue = target?.getAttribute("data-tract-index") ?? undefined;
    const pendingTractIndex =
      tractIndexValue === undefined ? null : Number.parseInt(tractIndexValue, 10);
    dragRef.current = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      startView: mapView,
      moved: false,
      pendingTractIndex:
        pendingTractIndex !== null && Number.isInteger(pendingTractIndex)
          ? pendingTractIndex
          : null,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    const bounds = svgRef.current?.getBoundingClientRect();
    if (!drag || drag.pointerId !== event.pointerId || !bounds) return;
    const pixelDistance = Math.hypot(
      event.clientX - drag.clientX,
      event.clientY - drag.clientY,
    );
    if (!drag.moved && pixelDistance < DRAG_THRESHOLD_PX) return;
    drag.moved = true;
    const deltaX = ((event.clientX - drag.clientX) / bounds.width) * drag.startView.width;
    const deltaY = ((event.clientY - drag.clientY) / bounds.height) * drag.startView.height;
    setMapView(
      clampView(
        {
          ...drag.startView,
          x: drag.startView.x - deltaX,
          y: drag.startView.y - deltaY,
        },
        baseView,
      ),
    );
  };

  const finishPointer = (
    event: ReactPointerEvent<SVGSVGElement>,
    selectPendingTract: boolean,
  ) => {
    const drag = dragRef.current;
    if (drag?.pointerId === event.pointerId) {
      dragRef.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      if (selectPendingTract && !drag.moved && drag.pendingTractIndex !== null) {
        onSelectTract(drag.pendingTractIndex);
      }
    }
  };

  const handlePointerUp = (event: ReactPointerEvent<SVGSVGElement>) => {
    finishPointer(event, true);
  };

  const handlePointerCancel = (event: ReactPointerEvent<SVGSVGElement>) => {
    finishPointer(event, false);
  };

  const findTract = () => {
    const query = search.trim();
    const normalized = query.replace(/\D/g, "");
    const lowerQuery = query.toLocaleLowerCase("en-US");
    const bestMatchingIndex = (
      predicate: (tract: Tract) => boolean,
      score: (tract: Tract) => number,
    ) =>
      tracts.reduce((best, tract, candidate) => {
        if (!predicate(tract)) return best;
        if (best < 0 || score(tract) > score(tracts[best])) return candidate;
        return best;
      }, -1);
    let index =
      normalized.length >= 4
        ? tracts.findIndex(
            (tract) => tract.id === normalized || tract.id.endsWith(normalized),
          )
        : -1;
    if (index < 0 && lowerQuery) {
      index = bestMatchingIndex(
        (tract) =>
          tract.neighborhood.toLocaleLowerCase("en-US") === lowerQuery,
        (tract) => tract.neighborhoodShare * tract.neighborhoodCoverage,
      );
    }
    if (index < 0 && lowerQuery) {
      index = bestMatchingIndex(
        (tract) =>
          tract.neighborhoods.some(([name]) =>
            name.toLocaleLowerCase("en-US") === lowerQuery,
          ),
        (tract) =>
          (tract.neighborhoods.find(
            ([name]) => name.toLocaleLowerCase("en-US") === lowerQuery,
          )?.[1] ?? 0) * tract.neighborhoodCoverage,
      );
    }
    if (index < 0 && lowerQuery) {
      index = bestMatchingIndex(
        (tract) =>
          tract.neighborhood.toLocaleLowerCase("en-US").includes(lowerQuery),
        (tract) => tract.neighborhoodShare * tract.neighborhoodCoverage,
      );
    }
    if (index < 0 && lowerQuery) {
      index = bestMatchingIndex(
        (tract) =>
          tract.neighborhoods.some(([name]) =>
            name.toLocaleLowerCase("en-US").includes(lowerQuery),
          ),
        (tract) =>
          Math.max(
            ...tract.neighborhoods
              .filter(([name]) =>
                name.toLocaleLowerCase("en-US").includes(lowerQuery),
              )
              .map(([, share]) => share * tract.neighborhoodCoverage),
          ),
      );
    }
    if (index < 0) {
      setSearchMessage("No matching neighborhood or GEOID was found.");
      return;
    }
    onSelectTract(index);
    setSearch(tracts[index].neighborhood);
    setSearchMessage(
      `${tracts[index].neighborhood} · ${tractLabel(tracts[index].id, tracts[index].name)} selected.`,
    );
  };

  const handleTractKeyDown = (
    event: KeyboardEvent<SVGPathElement>,
    tractIndex: number,
  ) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelectTract(tractIndex);
    }
  };

  const chartWidth = 920;
  const chartHeight = 300;
  const chartPad = { left: 54, right: 22, top: 24, bottom: 50 };
  const chartValues = timeline.flatMap((row) =>
    [row.observed, row.prediction].filter((value): value is number => value !== undefined),
  );
  const rawMin = chartValues.length ? Math.min(...chartValues) : 28;
  const rawMax = chartValues.length ? Math.max(...chartValues) : 56;
  const chartMin = Math.floor(rawMin - 1);
  const chartMax = Math.ceil(rawMax + 1);
  const chartSpan = Math.max(1, chartMax - chartMin);
  const chartX = (index: number) =>
    chartPad.left +
    (index / Math.max(1, timeline.length - 1)) *
      (chartWidth - chartPad.left - chartPad.right);
  const chartY = (value: number) =>
    chartHeight -
    chartPad.bottom -
    ((value - chartMin) / chartSpan) *
      (chartHeight - chartPad.top - chartPad.bottom);
  const yTicks = Array.from({ length: 5 }, (_, index) => chartMin + (chartSpan * index) / 4);
  const observedSegments = buildLineSegments(timeline, "observed", chartX, chartY);
  const predictionSegments = buildLineSegments(timeline, "prediction", chartX, chartY);
  const selectedNeighborhoods =
    selectedTractData?.neighborhoods.filter(([, share]) => share >= 0.1) ?? [];

  return (
    <section
      className={styles.explorer}
      id="tract-detail"
      aria-label="Detailed census tract explorer"
    >
      <div className={styles.heading}>
        <div>
          <span className={styles.eyebrow}>Detailed spatial inspection</span>
          <h3>Explore every evaluated tract.</h3>
        </div>
        <p>
          Use the zoom buttons, drag to pan, search by neighborhood or GEOID, or
          select a tract to inspect its complete held-out 2025 record.
        </p>
      </div>

      <div className={styles.mapToolbar}>
        <form
          className={styles.search}
          onSubmit={(event) => {
            event.preventDefault();
            findTract();
          }}
        >
          <label htmlFor="tract-geoid-search">Find neighborhood or GEOID</label>
          <div>
            <input
              id="tract-geoid-search"
              onChange={(event) => {
                setSearch(event.target.value);
                setSearchMessage("");
              }}
              placeholder="Hollywood or 06037101110"
              value={search}
            />
            <button type="submit">Find</button>
          </div>
          <span className={styles.searchMessage} aria-live="polite">
            {searchMessage}
          </span>
        </form>

        <div className={styles.mapControls}>
          <div className={styles.segmented} aria-label="Detailed map display">
            {(["observed", "prediction", "error"] as MapMode[]).map((mode) => (
              <button
                className={mapMode === mode ? styles.active : ""}
                key={mode}
                onClick={() => setMapMode(mode)}
                type="button"
              >
                {mode === "prediction" ? `${model} prediction` : mode}
              </button>
            ))}
          </div>
          <div className={styles.zoomControls} aria-label="Map zoom controls">
            <button
              aria-label="Zoom out"
              disabled={zoom <= MIN_ZOOM}
              onClick={() => setZoomAround(zoom / 1.4)}
              type="button"
            >
              -
            </button>
            <span>{zoom.toFixed(1)}x</span>
            <button
              aria-label="Zoom in"
              disabled={zoom >= MAX_ZOOM}
              onClick={() => setZoomAround(zoom * 1.4)}
              type="button"
            >
              +
            </button>
            <button
              className={styles.resetButton}
              onClick={() => setMapView(baseView)}
              type="button"
            >
              Reset
            </button>
          </div>
        </div>
      </div>

      <div className={styles.mapFrame}>
        <svg
          aria-label={`${mapMode} land-surface temperature by Los Angeles census tract`}
          className={styles.map}
          onPointerCancel={handlePointerCancel}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          preserveAspectRatio="xMidYMid meet"
          ref={svgRef}
          role="img"
          viewBox={`${mapView.x} ${mapView.y} ${mapView.width} ${mapView.height}`}
        >
          {tracts.map((tract, index) => {
            const value = recordValue(selectedDayRecords.get(index), mapMode, model);
            const fill =
              value === null
                ? "#d9d6ce"
                : colorFromStops(value, mapMode === "error" ? ERROR_STOPS : HEAT_STOPS);
            const isSelected = index === selectedTract;
            return (
              <path
                aria-label={`${tract.neighborhood}, ${tractLabel(tract.id, tract.name)}, GEOID ${tract.id}${
                  value === null ? ", no observation" : `, ${value.toFixed(2)} degrees Celsius`
                }`}
                aria-pressed={isSelected}
                className={`${styles.tract} ${isSelected ? styles.selected : ""}`}
                data-tract-index={index}
                d={tract.path}
                fill={fill}
                fillRule="evenodd"
                key={tract.id}
                onKeyDown={(event) => handleTractKeyDown(event, index)}
                role="button"
                tabIndex={0}
                vectorEffect="non-scaling-stroke"
              >
                <title>
                  {`${tract.neighborhood} · ${tractLabel(tract.id, tract.name)} · GEOID ${tract.id}`}
                </title>
              </path>
            );
          })}
        </svg>
        <div className={`${styles.legend} ${mapMode === "error" ? styles.errorLegend : ""}`}>
          <div />
          <span>{mapMode === "error" ? "-8 under" : "28°C"}</span>
          <span>{mapMode === "error" ? "0" : "42°C"}</span>
          <span>{mapMode === "error" ? "+8 over" : "56°C"}</span>
        </div>
        <span className={styles.mapHint}>Use +/− to zoom · drag to pan · select a tract</span>
      </div>

      {selectedTractData ? (
        <div className={styles.detail}>
          <header className={styles.tractIdentity}>
            <div>
              <span className={styles.eyebrow}>Selected Mapping L.A. neighborhood</span>
              <h3>{selectedTractData.neighborhood}</h3>
              <p className={styles.tractSecondary}>
                {tractLabel(selectedTractData.id, selectedTractData.name)}
              </p>
            </div>
            <dl>
              <div>
                <dt>GEOID</dt>
                <dd>{selectedTractData.id}</dd>
              </div>
              <div>
                <dt>Spatial block</dt>
                <dd>{selectedTractData.block}</dd>
              </div>
              <div>
                <dt>Share of mapped area</dt>
                <dd>{(selectedTractData.neighborhoodShare * 100).toFixed(1)}%</dd>
              </div>
              <div>
                <dt>Mapping L.A. coverage</dt>
                <dd>{(selectedTractData.neighborhoodCoverage * 100).toFixed(1)}%</dd>
              </div>
              <div>
                <dt>Evaluated dates</dt>
                <dd>{timeline.filter((row) => row.record).length} / {timeline.length}</dd>
              </div>
            </dl>
          </header>
          <p className={styles.boundaryNote}>
            <strong>
              {selectedNeighborhoods.length > 1
                ? "Crosses neighborhoods:"
                : "Neighborhood match:"}
            </strong>{" "}
            {selectedNeighborhoods
              .map(([name, share]) => `${name} ${(share * 100).toFixed(1)}%`)
              .join(" · ")}
            . Percentages divide only the tract area covered by the Mapping L.A. layer;
            that layer covers {(selectedTractData.neighborhoodCoverage * 100).toFixed(1)}%
            of this tract. Labels are display attributes assigned by maximum overlap; the
            Census tract remains the evaluated unit.
          </p>

          <article className={styles.chartCard}>
            <div className={styles.chartHeader}>
              <div>
                <span>Held-out 2025 timeline</span>
                <h4>Observed vs {model} predicted LST</h4>
              </div>
              <div className={styles.chartKey}>
                <span><i className={styles.observedKey} />Observed</span>
                <span><i className={styles.predictionKey} />{model} prediction</span>
              </div>
            </div>
            <svg
              aria-label={`Observed and ${model} predicted land-surface temperature for ${selectedTractData.neighborhood}, ${tractLabel(selectedTractData.id, selectedTractData.name)}`}
              className={styles.timeline}
              role="img"
              viewBox={`0 0 ${chartWidth} ${chartHeight}`}
            >
              {yTicks.map((tick) => (
                <g key={tick}>
                  <line
                    className={styles.gridLine}
                    x1={chartPad.left}
                    x2={chartWidth - chartPad.right}
                    y1={chartY(tick)}
                    y2={chartY(tick)}
                  />
                  <text
                    className={styles.tickLabel}
                    textAnchor="end"
                    x={chartPad.left - 10}
                    y={chartY(tick) + 4}
                  >
                    {tick.toFixed(0)}°
                  </text>
                </g>
              ))}
              {timeline.map((row, index) => (
                <g key={row.date}>
                  {row.date === selectedDate ? (
                    <line
                      className={styles.selectedDateLine}
                      x1={chartX(index)}
                      x2={chartX(index)}
                      y1={chartPad.top}
                      y2={chartHeight - chartPad.bottom}
                    />
                  ) : null}
                  <text
                    className={styles.dateLabel}
                    textAnchor="end"
                    transform={`translate(${chartX(index) + 3} ${chartHeight - 24}) rotate(-45)`}
                  >
                    {row.date.slice(5)}
                  </text>
                </g>
              ))}
              {observedSegments.map((points) => (
                <polyline className={styles.observedLine} key={points} points={points} />
              ))}
              {predictionSegments.map((points) => (
                <polyline className={styles.predictionLine} key={points} points={points} />
              ))}
              {timeline.map((row, index) => {
                if (!row.record) return null;
                return (
                  <g
                    aria-label={`${formatDate(row.date)}. Observed ${row.observed?.toFixed(2)} degrees. ${model} predicted ${row.prediction?.toFixed(2)} degrees.`}
                    className={styles.datePoint}
                    key={row.date}
                    onClick={() => onSelectDate(row.date)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onSelectDate(row.date);
                      }
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <rect
                      className={styles.dateHitArea}
                      height={chartHeight - chartPad.top - chartPad.bottom}
                      width={30}
                      x={chartX(index) - 15}
                      y={chartPad.top}
                    />
                    <circle
                      className={styles.observedPoint}
                      cx={chartX(index)}
                      cy={chartY(row.observed!)}
                      r={row.date === selectedDate ? 5.5 : 4}
                    />
                    <circle
                      className={styles.predictionPoint}
                      cx={chartX(index)}
                      cy={chartY(row.prediction!)}
                      r={row.date === selectedDate ? 5.5 : 4}
                    />
                  </g>
                );
              })}
            </svg>
            <p className={styles.chartNote}>
              Select any available date in the chart or table to synchronize the main atlas.
              Missing dates are shown as gaps.
            </p>
          </article>

          <div className={styles.tableWrap}>
            <table>
              <caption>
                Complete evaluation record for {selectedTractData.neighborhood} ·{" "}
                {tractLabel(selectedTractData.id, selectedTractData.name)}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Date / sensor</th>
                  <th scope="col">Observed</th>
                  <th scope="col">{model} predicted</th>
                  <th scope="col">Signed error</th>
                  <th scope="col">Absolute error</th>
                  <th scope="col">Valid fraction</th>
                  <th scope="col">Median uncertainty</th>
                  <th scope="col">Sentinel-2</th>
                </tr>
              </thead>
              <tbody>
                {timeline.map((row) => (
                  <tr
                    className={row.date === selectedDate ? styles.selectedRow : ""}
                    key={row.date}
                  >
                    <th scope="row">
                      <button onClick={() => onSelectDate(row.date)} type="button">
                        {formatDate(row.date)}
                      </button>
                      <span>{formatSensor(row.sensor)}</span>
                    </th>
                    {row.record ? (
                      <>
                        <td>{row.observed!.toFixed(2)}°C</td>
                        <td>{row.prediction!.toFixed(2)}°C</td>
                        <td className={row.signedError! > 0 ? styles.warm : styles.cool}>
                          {row.signedError! > 0 ? "+" : ""}
                          {row.signedError!.toFixed(2)}°C
                        </td>
                        <td>{row.absoluteError!.toFixed(2)}°C</td>
                        <td>{(row.record[4] * 100).toFixed(1)}%</td>
                        <td>{row.record[5].toFixed(2)} K</td>
                        <td>{row.record[6] === 1 ? "Available" : "Unavailable"}</td>
                      </>
                    ) : (
                      <td className={styles.missing} colSpan={7}>
                        Missing — this tract had no evaluated Landsat observation on this date.
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className={styles.emptyState}>
          Select a tract on the map or search for a neighborhood or GEOID to open its
          2025 record.
        </div>
      )}
    </section>
  );
}
