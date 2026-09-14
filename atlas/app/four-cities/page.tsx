"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

type MapMode = "observed" | "m3" | "b1" | "error";
type RecordRow = [number, number, number, number, number, number, number];
type AtlasTract = { id: string; name: string; path: string };
type AtlasDate = { date: string; records: RecordRow[] };
type AtlasCity = {
  id: string;
  name: string;
  code: string;
  viewBox: [number, number, number, number];
  tracts: AtlasTract[];
  dates: AtlasDate[];
  defaultDate: string;
  metrics: {
    dates: number;
    rows: number;
    blocks: number;
    b1MaeC: number;
    m3MaeC: number;
    coveragePercent: number;
    medianSpearman: number;
  };
};
type AtlasPayload = { state: string; cities: AtlasCity[] };

const ASSET_BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
const MODES: Array<{ id: MapMode; label: string; short: string }> = [
  { id: "observed", label: "Observed Landsat", short: "Observed" },
  { id: "m3", label: "M3 prediction", short: "M3" },
  { id: "b1", label: "B1 baseline", short: "B1" },
  { id: "error", label: "M3 error", short: "Error" },
];
const HEAT_STOPS: Array<[number, string]> = [
  [10, "#17324d"], [22, "#356b7d"], [30, "#e8d9ae"],
  [40, "#ef8f56"], [55, "#8e2137"],
];
const ERROR_STOPS: Array<[number, string]> = [
  [-16, "#173f63"], [-8, "#5591ac"], [0, "#eee8d7"],
  [8, "#dc7258"], [16, "#7f1d35"],
];

function mixHex(start: string, end: string, amount: number) {
  const from = start.match(/\w\w/g)?.map((part) => Number.parseInt(part, 16)) ?? [];
  const to = end.match(/\w\w/g)?.map((part) => Number.parseInt(part, 16)) ?? [];
  return `#${from.map((channel, index) =>
    Math.round(channel + (to[index] - channel) * amount).toString(16).padStart(2, "0"),
  ).join("")}`;
}

function colorFromStops(value: number, stops: Array<[number, string]>) {
  if (value <= stops[0][0]) return stops[0][1];
  if (value >= stops.at(-1)![0]) return stops.at(-1)![1];
  for (let index = 1; index < stops.length; index += 1) {
    const [rightValue, rightColor] = stops[index];
    const [leftValue, leftColor] = stops[index - 1];
    if (value <= rightValue) {
      return mixHex(leftColor, rightColor, (value - leftValue) / (rightValue - leftValue));
    }
  }
  return stops.at(-1)![1];
}

function valueForMode(record: RecordRow, mode: MapMode) {
  if (mode === "observed") return record[1];
  if (mode === "b1") return record[2];
  if (mode === "m3") return record[3];
  return record[4];
}

function colorForMode(record: RecordRow | undefined, mode: MapMode) {
  if (!record) return "#d8d5cc";
  return colorFromStops(valueForMode(record, mode), mode === "error" ? ERROR_STOPS : HEAT_STOPS);
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

export default function FourCityAtlas() {
  const [payload, setPayload] = useState<AtlasPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cityId, setCityId] = useState("seattle_wa");
  const [date, setDate] = useState("");
  const [mode, setMode] = useState<MapMode>("observed");
  const [selectedTract, setSelectedTract] = useState<number | null>(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    fetch(`${ASSET_BASE_PATH}/data/four-city-atlas.json`)
      .then((response) => {
        if (!response.ok) throw new Error("The Atlas data could not be loaded.");
        return response.json() as Promise<AtlasPayload>;
      })
      .then((nextPayload) => {
        setPayload(nextPayload);
        setCityId(nextPayload.cities[0].id);
        setDate(nextPayload.cities[0].defaultDate);
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "The Atlas could not start."),
      );
  }, []);

  const city = payload?.cities.find((item) => item.id === cityId) ?? null;
  const dateBundle = city?.dates.find((item) => item.date === date) ?? null;
  const records = new Map(dateBundle?.records.map((record) => [record[0], record]) ?? []);
  const selectedRecord = selectedTract === null ? undefined : records.get(selectedTract);
  const selected = selectedTract === null ? undefined : city?.tracts[selectedTract];
  const modeLabel = MODES.find((item) => item.id === mode)?.label ?? "Map";
  const stops = mode === "error" ? ERROR_STOPS : HEAT_STOPS;

  function chooseCity(nextCity: AtlasCity) {
    setCityId(nextCity.id);
    setDate(nextCity.defaultDate);
    setSelectedTract(null);
    setSearch("");
  }

  function submitSearch(event: React.FormEvent) {
    event.preventDefault();
    if (!city) return;
    const query = search.trim().toLowerCase();
    if (!query) return;
    const index = city.tracts.findIndex(
      (tract) => tract.id.includes(query) || tract.name.toLowerCase().includes(query),
    );
    if (index >= 0) setSelectedTract(index);
  }

  if (error) return <main className="state-screen">{error}</main>;
  if (!payload || !city || !dateBundle) {
    return <main className="state-screen"><span className="loading-mark" /> Loading the four city Atlas…</main>;
  }

  return (
    <main>
      <header className="masthead">
        <a className="wordmark" href="#top" aria-label="Four City Surface Heat Atlas">
          <span className="wordmark-dot" /> Surface Heat Atlas
        </a>
        <nav className="city-tabs" aria-label="Choose a city">
          {payload.cities.map((item) => (
            <button className={item.id === city.id ? "active" : ""} key={item.id}
              onClick={() => chooseCity(item)} type="button">
              <span>{item.code}</span>{item.name}
            </button>
          ))}
        </nav>
        <span className="edition">Opened 2025 evaluation</span>
      </header>

      <section className="atlas-shell" id="top">
        <aside className="atlas-intro">
          <p className="kicker">{city.code} · Urban surface temperature</p>
          <h1>{city.name}</h1>
          <p className="lede">
            Explore the measured and modeled daytime surface heat of every evaluated
            census tract. One city, one date, one honest comparison at a time.
          </p>

          <div className="mode-switch" role="group" aria-label="Map layer">
            {MODES.map((item) => (
              <button className={mode === item.id ? "active" : ""} key={item.id}
                onClick={() => setMode(item.id)} type="button">
                <span>{item.short}</span><small>{item.label}</small>
              </button>
            ))}
          </div>

          <label className="date-control">
            <span>Observation date</span>
            <select value={date} onChange={(event) => {
              setDate(event.target.value); setSelectedTract(null);
            }}>
              {city.dates.map((item) => (
                <option key={item.date} value={item.date}>
                  {formatDate(item.date)} · {item.records.length} tracts
                </option>
              ))}
            </select>
          </label>

          <form className="tract-search" onSubmit={submitSearch}>
            <label htmlFor="tract-search">Find a census tract</label>
            <div>
              <input id="tract-search" onChange={(event) => setSearch(event.target.value)}
                placeholder="Enter GEOID or tract number" value={search} />
              <button type="submit">Find</button>
            </div>
          </form>

          <div className="city-metrics" aria-label={`${city.name} evaluation summary`}>
            <div><span>M3 MAE</span><strong>{city.metrics.m3MaeC.toFixed(2)}°C</strong></div>
            <div><span>B1 MAE</span><strong>{city.metrics.b1MaeC.toFixed(2)}°C</strong></div>
            <div><span>Dates</span><strong>{city.metrics.dates}</strong></div>
            <div><span>Coverage</span><strong>{city.metrics.coveragePercent.toFixed(1)}%</strong></div>
          </div>
        </aside>

        <section className="map-stage" aria-label={`${modeLabel} map of ${city.name}`}>
          <div className="map-heading">
            <div><span>{formatDate(date)}</span><h2>{modeLabel}</h2></div>
            <p>{dateBundle.records.length} of {city.tracts.length} tracts scored</p>
          </div>
          <div className="map-canvas">
            <svg aria-label={`${modeLabel} by ${city.name} census tract`} role="img"
              viewBox={city.viewBox.join(" ")}>
              <g fillRule="evenodd">
                {city.tracts.map((tract, index) => {
                  const record = records.get(index);
                  return <path
                    aria-label={`${tract.name}${record ? `, ${valueForMode(record, mode).toFixed(1)} degrees Celsius` : ", not scored"}`}
                    className={selectedTract === index ? "selected" : ""}
                    d={tract.path} fill={colorForMode(record, mode)} key={tract.id}
                    onClick={() => setSelectedTract(index)} onMouseEnter={() => setSelectedTract(index)}
                    tabIndex={0} onFocus={() => setSelectedTract(index)} />;
                })}
              </g>
            </svg>
            <div className="legend">
              <span>{mode === "error" ? "Under" : "Cooler"}</span>
              <div style={{ background: `linear-gradient(90deg, ${stops.map((stop) => stop[1]).join(",")})` }} />
              <span>{mode === "error" ? "Over" : "Hotter"}</span>
            </div>
          </div>

          <aside className={`tract-card ${selected && selectedRecord ? "visible" : ""}`}>
            {selected && selectedRecord ? <>
              <button onClick={() => setSelectedTract(null)} type="button" aria-label="Close tract details">×</button>
              <p>{city.name}</p><h3>{selected.name}</h3><code>{selected.id}</code>
              <dl>
                <div><dt>Observed</dt><dd>{selectedRecord[1].toFixed(2)}°C</dd></div>
                <div><dt>M3</dt><dd>{selectedRecord[3].toFixed(2)}°C</dd></div>
                <div><dt>B1</dt><dd>{selectedRecord[2].toFixed(2)}°C</dd></div>
                <div><dt>M3 error</dt><dd>{selectedRecord[4] > 0 ? "+" : ""}{selectedRecord[4].toFixed(2)}°</dd></div>
              </dl>
              <span className="interval">M3 90% interval · {selectedRecord[5].toFixed(1)}–{selectedRecord[6].toFixed(1)}°C</span>
            </> : <p className="tract-card-placeholder">Point to any tract to inspect it.</p>}
          </aside>
        </section>
      </section>

      <footer>
        <p>Four opened stress-test cities · {city.metrics.rows.toLocaleString()} scored rows in {city.name} · Landsat land-surface temperature is not air temperature.</p>
        <div className="footer-links">
          <Link href="/">Los Angeles atlas ←</Link>
          <a href="https://github.com/CmsChase/LA-neighborhood-heat">Data & methodology ↗</a>
        </div>
      </footer>
    </main>
  );
}
