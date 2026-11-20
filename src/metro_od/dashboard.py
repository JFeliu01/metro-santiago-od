"""Interactive HTML Analytics Dashboard generator for Metro de Santiago OD data (2019 vs 2023).

Extracts real multi-modal data (`METRO`, `BUS`, `ZONA PAGA`, `METROTREN`) from the cleaned
2019 & 2023 `viajes`, `etapas`, `.xlsb` matrices, and GTFS feeds using DuckDB, Polars, and PyProj,
and builds a self-contained interactive HTML application (`dashboard/metro_od_analytics.html`)
with Leaflet + optional Google Maps JS API integration, OD flow arcs, half-hourly scrubbers,
and comparative charts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import duckdb
from pyproj import Transformer

from .catalog import DATASETS, DATA_DIR, PROJECT_ROOT
from .clean import (
    build_clean_etapas_query,
    build_clean_viajes_query,
    load_clean_xlsb_matrix_2023,
)


def _classify_macro_period_sql(time_col: str) -> str:
    """Classify a TIME column into 4 canonical macro-periods for interactive filtering."""
    return f"""CASE
        WHEN {time_col} >= TIME '06:00:00' AND {time_col} < TIME '09:30:00' THEN 'PUNTA_MANANA'
        WHEN {time_col} >= TIME '09:30:00' AND {time_col} < TIME '17:30:00' THEN 'FUERA_PUNTA'
        WHEN {time_col} >= TIME '17:30:00' AND {time_col} < TIME '21:00:00' THEN 'PUNTA_TARDE'
        ELSE 'NOCTURNO'
    END"""


def extract_dashboard_data(sample_rows: int = 250_000) -> Dict[str, object]:
    """Query 2019, 2023, and GTFS tables to build the structured analytics JSON payload."""
    con = duckdb.connect()
    utm_to_wgs84 = Transformer.from_crs("EPSG:32719", "EPSG:4326", always_xy=True)

    # 1. Extract GTFS Metro / Metrotren routes & shapes
    gtfs_routes_path = (DATA_DIR / "GTFS" / "routes.txt").as_posix()
    gtfs_stops_path = (DATA_DIR / "GTFS" / "stops.txt").as_posix()

    metro_routes_df = con.execute(
        f"""
        SELECT
            route_id,
            route_short_name,
            route_long_name,
            route_type,
            COALESCE(NULLIF(TRIM(route_color), ''), 'E11D48') AS route_color
        FROM read_csv_auto('{gtfs_routes_path}')
        WHERE route_type != 3
        ORDER BY route_short_name
        """
    ).df()
    metro_routes = metro_routes_df.to_dict(orient="records")

    # 2. Materialize cleaned sample tables once in DuckDB memory (avoids re-scanning 9 GB CSVs)
    q_et19 = build_clean_etapas_query("2019", limit=sample_rows)
    q_et23 = build_clean_etapas_query("2023", limit=sample_rows)
    con.execute(f"CREATE TEMP TABLE et_combined AS SELECT * FROM ({q_et19}) UNION ALL SELECT * FROM ({q_et23})")

    commune_coords_df = con.execute(
        """
        SELECT
            comuna_subida AS comuna,
            AVG(utm_x_subida) AS avg_utm_x,
            AVG(utm_y_subida) AS avg_utm_y,
            AVG(lat_subida) AS avg_lat,
            AVG(lon_subida) AS avg_lon,
            COUNT(*) AS n_obs
        FROM et_combined
        WHERE comuna_subida IS NOT NULL
        GROUP BY comuna_subida
        ORDER BY comuna_subida
        """
    ).df()

    communes: Dict[str, Dict[str, object]] = {}
    for row in commune_coords_df.to_dict(orient="records"):
        c_name = row["comuna"]
        if row["avg_utm_x"] and row["avg_utm_y"] and str(row["avg_utm_x"]) != "nan":
            lon, lat = utm_to_wgs84.transform(float(row["avg_utm_x"]), float(row["avg_utm_y"]))
        elif row["avg_lat"] and row["avg_lon"] and str(row["avg_lat"]) != "nan":
            lat, lon = float(row["avg_lat"]), float(row["avg_lon"])
        else:
            continue
        communes[c_name] = {
            "name": c_name,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
        }

    # 3. Extract Top Stop/Station Hubs per Mode (METRO, BUS, ZONA PAGA, METROTREN) for 2019 and 2023
    stops_df = con.execute(
        f"""
        WITH combined AS (
            SELECT
                anio,
                tipo_transporte AS mode,
                COALESCE(par_subida, sitio_subida) AS stop_name,
                comuna_subida AS comuna,
                servicio_subida AS service,
                {_classify_macro_period_sql('mh_subida')} AS macro_period,
                utm_x_subida AS utm_x,
                utm_y_subida AS utm_y,
                lat_subida AS lat,
                lon_subida AS lon,
                COALESCE(fexpansion_zona_periodo, f_exp_servicioperiodots, 1.0) AS weight,
                tiempo_etapa_seg / 60.0 AS stage_min
            FROM et_combined
        ),
        agg AS (
            SELECT
                anio,
                mode,
                stop_name,
                MODE(comuna) AS comuna,
                MODE(service) AS top_service,
                AVG(utm_x) AS utm_x,
                AVG(utm_y) AS utm_y,
                AVG(lat) AS lat,
                AVG(lon) AS lon,
                ROUND(SUM(weight), 1) AS boardings_total,
                ROUND(SUM(CASE WHEN macro_period = 'PUNTA_MANANA' THEN weight ELSE 0 END), 1) AS boardings_pm,
                ROUND(SUM(CASE WHEN macro_period = 'FUERA_PUNTA' THEN weight ELSE 0 END), 1) AS boardings_fp,
                ROUND(SUM(CASE WHEN macro_period = 'PUNTA_TARDE' THEN weight ELSE 0 END), 1) AS boardings_pt,
                ROUND(AVG(stage_min), 1) AS avg_stage_min,
                ROW_NUMBER() OVER (PARTITION BY anio, mode ORDER BY SUM(weight) DESC) AS rn
            FROM combined
            WHERE stop_name IS NOT NULL AND mode IS NOT NULL
            GROUP BY anio, mode, stop_name
        )
        SELECT * FROM agg WHERE rn <= 22
        ORDER BY anio, mode, boardings_total DESC
        """
    ).df()

    # Lookup GTFS station coordinates by upper name for any stop missing direct coordinates
    gtfs_station_coords = {}
    gtfs_st_df = con.execute(
        f"""
        SELECT UPPER(TRIM(stop_name)) AS sname, AVG(stop_lat) AS lat, AVG(stop_lon) AS lon
        FROM read_csv_auto('{gtfs_stops_path}')
        WHERE stop_lat IS NOT NULL AND stop_lon IS NOT NULL
        GROUP BY UPPER(TRIM(stop_name))
        """
    ).df()
    for r in gtfs_st_df.to_dict(orient="records"):
        gtfs_station_coords[r["sname"]] = (float(r["lat"]), float(r["lon"]))

    stop_hubs: List[Dict[str, object]] = []
    for r in stops_df.to_dict(orient="records"):
        lat, lon = None, None
        if r["lat"] and r["lon"] and str(r["lat"]) != "nan":
            lat, lon = float(r["lat"]), float(r["lon"])
        elif r["utm_x"] and r["utm_y"] and str(r["utm_x"]) != "nan":
            lon, lat = utm_to_wgs84.transform(float(r["utm_x"]), float(r["utm_y"]))
        else:
            sname = str(r["stop_name"]).upper().strip()
            if sname in gtfs_station_coords:
                lat, lon = gtfs_station_coords[sname]
            elif r["comuna"] in communes:
                lat = communes[r["comuna"]]["lat"]
                lon = communes[r["comuna"]]["lon"]

        if lat is None or lon is None or not (-34.2 <= lat <= -33.0 and -71.2 <= lon <= -70.2):
            continue

        svc = r["top_service"]
        if svc is None or (isinstance(svc, float) and svc != svc) or str(svc) == "nan":
            svc = r["mode"]
        cname = r["comuna"]
        if cname is None or (isinstance(cname, float) and cname != cname) or str(cname) == "nan":
            cname = "SANTIAGO"

        stop_hubs.append(
            {
                "year": r["anio"],
                "mode": r["mode"],
                "name": r["stop_name"],
                "comuna": cname,
                "service": svc,
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "total": float(r["boardings_total"] or 0),
                "pm": float(r["boardings_pm"] or 0),
                "fp": float(r["boardings_fp"] or 0),
                "pt": float(r["boardings_pt"] or 0),
                "avg_stage_min": float(r["avg_stage_min"] or 0) if str(r["avg_stage_min"]) != "nan" else 0.0,
            }
        )

    # 4. Extract Half-Hourly Demand Curves & Trip Characterizations from Viajes (2019 vs 2023)
    q_vj19 = build_clean_viajes_query("2019", limit=sample_rows)
    q_vj23 = build_clean_viajes_query("2023", limit=sample_rows)
    con.execute(f"CREATE TEMP TABLE vj_combined AS SELECT * FROM ({q_vj19}) UNION ALL SELECT * FROM ({q_vj23})")

    half_hour_df = con.execute(
        """
        SELECT
            anio AS year,
            SUBSTR(CAST(mediahora_inicio_viaje AS VARCHAR), 1, 5) AS mh,
            tipo_transporte_1 AS mode,
            COUNT(*) AS sample_trips,
            ROUND(SUM(COALESCE(factor_expansion, 1.0)), 1) AS expanded_trips,
            ROUND(AVG(tviaje_min), 2) AS avg_travel_min,
            ROUND(AVG(distancia_ruta_m) / 1000.0, 2) AS avg_dist_km,
            ROUND(AVG(n_etapas), 2) AS avg_stages
        FROM vj_combined
        WHERE mediahora_inicio_viaje IS NOT NULL AND tipo_transporte_1 IS NOT NULL
        GROUP BY anio, SUBSTR(CAST(mediahora_inicio_viaje AS VARCHAR), 1, 5), tipo_transporte_1
        ORDER BY anio, mh, mode
        """
    ).df()

    # 5. Extract Commune-to-Commune Origin-Destination Flows (by Year, Mode, Macro-Period, Purpose)
    od_flows_df = con.execute(
        f"""
        WITH combined AS (
            SELECT
                anio AS year,
                comuna_inicio_viaje AS origin,
                comuna_fin_viaje AS dest,
                tipo_transporte_1 AS mode,
                CASE
                    WHEN n_etapas > 1 AND (
                        tipo_transporte_1 != COALESCE(tipo_transporte_2, tipo_transporte_1)
                        OR tipo_transporte_1 != COALESCE(tipo_transporte_3, tipo_transporte_1)
                    ) THEN 1 ELSE 0
                END AS is_intermodal,
                {_classify_macro_period_sql('mediahora_inicio_viaje')} AS macro_period,
                COALESCE(proposito, 'OTROS') AS purpose,
                COALESCE(factor_expansion, 1.0) AS weight,
                tviaje_min,
                distancia_ruta_m / 1000.0 AS dist_km,
                n_etapas
            FROM vj_combined
            WHERE comuna_inicio_viaje IS NOT NULL AND comuna_fin_viaje IS NOT NULL
              AND tipo_transporte_1 IS NOT NULL
        ),
        grouped AS (
            SELECT
                year,
                origin,
                dest,
                mode,
                macro_period,
                purpose,
                ROUND(SUM(weight), 1) AS trips,
                ROUND(SUM(CASE WHEN is_intermodal = 1 THEN weight ELSE 0 END), 1) AS intermodal_trips,
                ROUND(AVG(tviaje_min), 1) AS avg_min,
                ROUND(AVG(dist_km), 2) AS avg_km,
                ROUND(AVG(n_etapas), 2) AS avg_stages,
                ROW_NUMBER() OVER (PARTITION BY year, mode ORDER BY SUM(weight) DESC) AS rn
            FROM combined
            GROUP BY year, origin, dest, mode, macro_period, purpose
            HAVING SUM(weight) >= 10.0
        )
        SELECT year, origin, dest, mode, macro_period, purpose, trips, intermodal_trips, avg_min, avg_km, avg_stages
        FROM grouped
        WHERE rn <= 40
        ORDER BY year, trips DESC
        """
    ).df()

    # 6. Extract 2023 XLSB Top Metro Station Half-Hourly Boardings & Alightings
    df_sub23 = load_clean_xlsb_matrix_2023("matriz_subidas_2023_xlsb")
    mh_cols = [c for c in df_sub23.columns if ":" in c]
    top_metro_sub = (
        df_sub23.filter(df_sub23["paradero"].is_null() & df_sub23["parada SIMT"].is_not_null())
        .sort("TOTAL", descending=True)
        .head(15)
    )
    metro_2023_profiles = []
    for row in top_metro_sub.to_dicts():
        metro_2023_profiles.append(
            {
                "station": row["parada SIMT"],
                "comuna": row["Comuna"],
                "total_daily": round(float(row["TOTAL"] or 0), 1),
                "half_hours": {
                    mh: round(float(row[mh] or 0), 1) for mh in mh_cols if row.get(mh) is not None
                },
            }
        )

    # Clean NaNs in pandas dicts before JSON serialization
    def _clean_records(df):
        recs = df.to_dict(orient="records")
        for r in recs:
            for k, v in list(r.items()):
                if isinstance(v, float) and (v != v):  # NaN check
                    r[k] = 0.0
        return recs

    return {
        "sample_rows_per_table": sample_rows,
        "communes": communes,
        "metro_routes": metro_routes,
        "stop_hubs": stop_hubs,
        "half_hour_curves": _clean_records(half_hour_df),
        "od_flows": _clean_records(od_flows_df),
        "metro_2023_profiles": metro_2023_profiles,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Metro de Santiago &amp; Red Movilidad — Origin-Destination Analytics (2019 vs 2023)</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {
      --bg: #0b0f19;
      --panel: #111827;
      --panel-alt: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --metro: #ef4444;
      --bus: #10b981;
      --zp: #f59e0b;
      --metrotren: #a855f7;
      --accent: #38bdf8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
      line-height: 1.45;
      overflow-x: hidden;
    }
    header {
      background: linear-gradient(90deg, #0f172a 0%, #1e1b4b 100%);
      border-bottom: 1px solid var(--border);
      padding: 12px 20px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand-badge {
      background: var(--metro);
      color: white;
      font-weight: 800;
      padding: 6px 10px;
      border-radius: 6px;
      letter-spacing: 0.5px;
      font-size: 12px;
    }
    .brand h1 { font-size: 16px; font-weight: 700; }
    .brand p { font-size: 12px; color: var(--muted); }
    .controls-bar {
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      padding: 10px 20px;
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      align-items: center;
    }
    .ctrl-group { display: flex; flex-direction: column; gap: 4px; }
    .ctrl-group label {
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: var(--muted);
      font-weight: 600;
    }
    select, input[type="text"], button {
      background: var(--panel-alt);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 6px 10px;
      font-size: 12px;
      cursor: pointer;
    }
    select:focus, input:focus { outline: 1px solid var(--accent); }
    .mode-pills { display: flex; gap: 5px; flex-wrap: wrap; }
    .pill {
      padding: 5px 10px;
      border-radius: 999px;
      font-weight: 600;
      font-size: 11px;
      border: 1px solid var(--border);
      background: var(--panel-alt);
      transition: all 0.15s;
    }
    .pill.active[data-mode="ALL"] { background: var(--accent); color: #0b0f19; border-color: var(--accent); }
    .pill.active[data-mode="METRO"] { background: var(--metro); color: white; border-color: var(--metro); }
    .pill.active[data-mode="BUS"] { background: var(--bus); color: #052e16; border-color: var(--bus); }
    .pill.active[data-mode="ZONA PAGA"] { background: var(--zp); color: #451a03; border-color: var(--zp); }
    .pill.active[data-mode="METROTREN"] { background: var(--metrotren); color: white; border-color: var(--metrotren); }

    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
      padding: 14px 20px 6px;
    }
    .kpi-card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
    }
    .kpi-title { font-size: 11px; color: var(--muted); text-transform: uppercase; font-weight: 600; }
    .kpi-val { font-size: 22px; font-weight: 800; margin: 4px 0; color: var(--text); }
    .kpi-sub { font-size: 11px; color: var(--muted); display: flex; justify-content: space-between; }
    .badge-delta { padding: 1px 6px; border-radius: 4px; font-weight: 700; font-size: 11px; }
    .delta-pos { background: rgba(16, 185, 129, 0.2); color: #34d399; }
    .delta-neg { background: rgba(239, 68, 68, 0.2); color: #f87171; }

    .main-grid {
      display: grid;
      grid-template-columns: 1.35fr 1fr;
      gap: 14px;
      padding: 10px 20px 20px;
    }
    @media (max-width: 1150px) { .main-grid { grid-template-columns: 1fr; } }

    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .card-header {
      padding: 10px 14px;
      background: rgba(30, 41, 59, 0.6);
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }
    .card-header h2 { font-size: 13px; font-weight: 700; }
    #map-container { position: relative; width: 100%; height: 590px; }
    #leaflet-map, #gmap-canvas { width: 100%; height: 100%; }
    #gmap-canvas { display: none; }
    .map-legend {
      position: absolute;
      bottom: 16px;
      left: 16px;
      z-index: 1000;
      background: rgba(15, 23, 42, 0.92);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 11px;
      pointer-events: auto;
    }
    .legend-row { display: flex; align-items: center; gap: 7px; margin-top: 4px; }
    .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }

    .insight-box {
      padding: 12px 14px;
      background: rgba(56, 189, 248, 0.08);
      border-top: 1px solid var(--border);
      font-size: 12px;
    }
    .insight-box h3 { font-size: 12px; color: var(--accent); margin-bottom: 6px; text-transform: uppercase; }
    .insight-list { list-style: none; display: flex; flex-direction: column; gap: 5px; }
    .insight-list li::before { content: "▸ "; color: var(--accent); font-weight: bold; }

    .right-col { display: flex; flex-direction: column; gap: 14px; }
    .chart-box { width: 100%; height: 245px; }

    .table-wrap { max-height: 250px; overflow-y: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { padding: 7px 10px; text-align: left; border-bottom: 1px solid rgba(51, 65, 85, 0.5); }
    th { position: sticky; top: 0; background: var(--panel-alt); color: var(--muted); font-size: 11px; text-transform: uppercase; }
    tr:hover { background: rgba(56, 189, 248, 0.08); cursor: pointer; }
    .mode-tag { padding: 2px 6px; border-radius: 4px; font-weight: 700; font-size: 10px; }

    /* Modal for Google Maps API key */
    .modal-backdrop {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.75);
      z-index: 3000;
      align-items: center;
      justify-content: center;
    }
    .modal {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 20px;
      width: 440px;
      max-width: 92vw;
    }
    .modal h3 { margin-bottom: 8px; font-size: 15px; }
    .modal p { color: var(--muted); font-size: 12px; margin-bottom: 12px; }
    .modal input { width: 100%; margin-bottom: 12px; }
    .modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
  </style>
</head>
<body>

<header>
  <div class="brand">
    <span class="brand-badge">METRO / RED OD</span>
    <div>
      <h1>Santiago Origin–Destination &amp; Multi-Modal Demand Workbench (Abril 2019 vs. Abril 2023)</h1>
      <p>Interactive exploration of validations, estimated destinations, modal share (Metro, Bus, Zona Paga, Metrotren) &amp; half-hourly station profiles</p>
    </div>
  </div>
  <div style="display:flex; gap:8px; align-items:center;">
    <button id="btn-gmap-key" title="Switch between Leaflet Dark Map and Google Maps JS API">🗺️ Google Maps API</button>
    <button id="btn-reset" style="background:#334155;">↺ Reset Filters</button>
  </div>
</header>

<section class="controls-bar">
  <div class="ctrl-group">
    <label>Año / Comparación</label>
    <select id="sel-year">
      <option value="2023" selected>2023 (17–23 Abril)</option>
      <option value="2019">2019 (08–14 Abril)</option>
    </select>
  </div>

  <div class="ctrl-group">
    <label>Modo de Transporte</label>
    <div class="mode-pills" id="mode-pills">
      <button class="pill active" data-mode="ALL">Todos</button>
      <button class="pill" data-mode="METRO">Metro</button>
      <button class="pill" data-mode="BUS">Bus</button>
      <button class="pill" data-mode="ZONA PAGA">Zona Paga</button>
      <button class="pill" data-mode="METROTREN">Metrotren</button>
    </div>
  </div>

  <div class="ctrl-group">
    <label>Periodo Horario</label>
    <select id="sel-period">
      <option value="ALL" selected>Día Completo (00:00 – 23:30)</option>
      <option value="PUNTA_MANANA">Punta Mañana (06:00 – 09:30)</option>
      <option value="FUERA_PUNTA">Fuera de Punta (09:30 – 17:30)</option>
      <option value="PUNTA_TARDE">Punta Tarde (17:30 – 21:00)</option>
      <option value="NOCTURNO">Nocturno (21:00 – 06:00)</option>
    </select>
  </div>

  <div class="ctrl-group">
    <label>Propósito Estimado</label>
    <select id="sel-purpose">
      <option value="ALL" selected>Todos los Propósitos</option>
      <option value="TRABAJO">Trabajo</option>
      <option value="ESTUDIO">Estudio</option>
      <option value="HOGAR">Hogar (Regreso)</option>
      <option value="OTROS">Otros</option>
    </select>
  </div>

  <div class="ctrl-group">
    <label>Comuna Foco (Origen o Destino)</label>
    <select id="sel-commune">
      <option value="ALL" selected>Todas las Comunas (34)</option>
    </select>
  </div>

  <div class="ctrl-group">
    <label>Dirección de Flujo</label>
    <select id="sel-direction">
      <option value="BOTH" selected>Origen ↔ Destino</option>
      <option value="ORIGIN">Solo Salidas (Origen)</option>
      <option value="DEST">Solo Llegadas (Destino)</option>
    </select>
  </div>

  <div class="ctrl-group">
    <label>Arcos OD en Mapa: <span id="lbl-topn">35</span></label>
    <input type="range" id="rng-topn" min="10" max="100" step="5" value="35" />
  </div>
</section>

<section class="kpi-grid">
  <div class="kpi-card">
    <div class="kpi-title">Viajes Expandidos (Muestra Filtrada)</div>
    <div class="kpi-val" id="kpi-trips">—</div>
    <div class="kpi-sub">
      <span id="kpi-trips-vs">vs 2019: —</span>
      <span id="kpi-intermodal" style="color:var(--accent);">Intermodal: —%</span>
    </div>
  </div>
  <div class="kpi-card">
    <div class="kpi-title">Tiempo de Viaje Promedio</div>
    <div class="kpi-val" id="kpi-time">— min</div>
    <div class="kpi-sub">
      <span id="kpi-time-vs">2019: — min</span>
      <span id="kpi-stages">Etapas prom: —</span>
    </div>
  </div>
  <div class="kpi-card">
    <div class="kpi-title">Distancia en Ruta Promedio</div>
    <div class="kpi-val" id="kpi-dist">— km</div>
    <div class="kpi-sub">
      <span id="kpi-dist-vs">2019: — km</span>
      <span id="kpi-speed">Velocidad est: — km/h</span>
    </div>
  </div>
  <div class="kpi-card">
    <div class="kpi-title">Corredor OD Principal &amp; Modo Dominante</div>
    <div class="kpi-val" id="kpi-top-od" style="font-size:15px; margin:7px 0;">—</div>
    <div class="kpi-sub">
      <span id="kpi-dom-mode">Modo: —</span>
      <span id="kpi-hub-top">Nodo top: —</span>
    </div>
  </div>
</section>

<main class="main-grid">
  <!-- Left Column: Interactive Map + Automated Insights -->
  <div class="card">
    <div class="card-header">
      <h2>Mapa Multi-Modal de Flujos Origen–Destino y Nodos (Paraderos / Estaciones)</h2>
      <div style="display:flex; gap:10px; align-items:center; font-size:11px;">
        <label><input type="checkbox" id="chk-arcs" checked /> Arcos OD</label>
        <label><input type="checkbox" id="chk-hubs" checked /> Estaciones / Paraderos</label>
        <label><input type="checkbox" id="chk-communes" checked /> Centroides Comuna</label>
      </div>
    </div>
    <div id="map-container">
      <div id="leaflet-map"></div>
      <div id="gmap-canvas"></div>
      <div class="map-legend">
        <div style="font-weight:700; margin-bottom:4px;">Modos &amp; Capas (Click para filtrar)</div>
        <div class="legend-row"><span class="dot" style="background:var(--metro);"></span> <b>Metro</b> (Estaciones &amp; Flujos)</div>
        <div class="legend-row"><span class="dot" style="background:var(--bus);"></span> <b>Bus Red</b> (Paraderos &amp; Corredores)</div>
        <div class="legend-row"><span class="dot" style="background:var(--zp);"></span> <b>Zona Paga</b> (Hubs de Trasbordo)</div>
        <div class="legend-row"><span class="dot" style="background:var(--metrotren);"></span> <b>Metrotren Nos</b> (Estaciones EFE)</div>
        <div style="margin-top:6px; color:var(--muted); font-size:10px;">Grosor de arco = Volumen OD | Círculo = Subidas</div>
      </div>
    </div>
    <div class="insight-box">
      <h3>💡 Insights Automáticos de la Selección Actual</h3>
      <ul class="insight-list" id="insights-list"></ul>
    </div>
  </div>

  <!-- Right Column: Charts & OD Corridor Table -->
  <div class="right-col">
    <div class="card">
      <div class="card-header">
        <h2>Curva Horaria de Demanda por Media Hora (2023 Sólido vs. 2019 Punteado)</h2>
        <span style="font-size:11px; color:var(--muted);">Validaciones / Viajes por Modo</span>
      </div>
      <div id="chart-halfhour" class="chart-box"></div>
    </div>

    <div class="card">
      <div class="card-header">
        <h2>Perfil Horario de Estaciones de Metro (Matriz Subidas Abril 2023)</h2>
        <select id="sel-station-profile" style="padding:3px 8px; font-size:11px;"></select>
      </div>
      <div id="chart-station" class="chart-box" style="height:205px;"></div>
    </div>

    <div class="card">
      <div class="card-header">
        <h2>Top Corredores Origen–Destino (Click en una fila para enfocar en el mapa)</h2>
        <span id="tbl-count" style="font-size:11px; color:var(--muted);"></span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Origen → Destino</th>
              <th>Modo Inicial</th>
              <th>Viajes</th>
              <th>% Intermodal</th>
              <th>T. Viaje</th>
              <th>Distancia</th>
            </tr>
          </thead>
          <tbody id="od-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>
</main>

<!-- Google Maps API Modal -->
<div class="modal-backdrop" id="gmap-modal">
  <div class="modal">
    <h3>Configurar Google Maps JavaScript API</h3>
    <p>El dashboard incluye un mapa oscuro interactivo listo para usar (Leaflet + Carto Dark), pero también soporta renderizado nativo sobre <b>Google Maps JS API</b>. Ingresa tu API Key para activar Google Maps (o déjalo vacío para volver a Leaflet):</p>
    <input type="text" id="inp-gmap-key" placeholder="AIzaSy..." />
    <div class="modal-actions">
      <button id="btn-gmap-cancel">Cancelar</button>
      <button id="btn-gmap-save" style="background:var(--accent); color:#0b0f19; font-weight:700;">Aplicar Motor de Mapa</button>
    </div>
  </div>
</div>

<script>
const DATA = __DASHBOARD_JSON_PAYLOAD__;

const MODE_COLORS = {
  "METRO": "#ef4444",
  "BUS": "#10b981",
  "ZONA PAGA": "#f59e0b",
  "METROTREN": "#a855f7",
  "ALL": "#38bdf8"
};

let state = {
  year: "2023",
  mode: "ALL",
  period: "ALL",
  purpose: "ALL",
  commune: "ALL",
  direction: "BOTH",
  topN: 35,
  showArcs: true,
  showHubs: true,
  showCommunes: true,
  useGoogleMaps: false,
  gmapKey: localStorage.getItem("METRO_OD_GMAP_KEY") || ""
};

// Populate Commune Dropdown
const selCommune = document.getElementById("sel-commune");
Object.keys(DATA.communes).sort().forEach(c => {
  const opt = document.createElement("option");
  opt.value = c;
  opt.textContent = c;
  selCommune.appendChild(opt);
});

// Populate 2023 Station Profile Dropdown
const selStation = document.getElementById("sel-station-profile");
DATA.metro_2023_profiles.forEach((st, idx) => {
  const opt = document.createElement("option");
  opt.value = idx;
  opt.textContent = `${st.station} (${st.comuna}) — ${st.total_daily.toLocaleString()} sub/día`;
  selStation.appendChild(opt);
});

// Initialize Leaflet Map
const leafletMap = L.map("leaflet-map", {
  center: [-33.455, -70.655],
  zoom: 11,
  preferCanvas: true
});

const darkTiles = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: "&copy; OpenStreetMap &copy; CARTO",
  subdomains: "abcd",
  maxZoom: 19
}).addTo(leafletMap);

const voyagerTiles = L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
  attribution: "&copy; OpenStreetMap &copy; CARTO"
});

const satelliteTiles = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
  attribution: "Tiles &copy; Esri"
});

L.control.layers({
  "Oscuro (Carto Dark)": darkTiles,
  "Claro (Carto Voyager)": voyagerTiles,
  "Satélite (Esri)": satelliteTiles
}, null, { position: "topright" }).addTo(leafletMap);

const arcsLayer = L.layerGroup().addTo(leafletMap);
const hubsLayer = L.layerGroup().addTo(leafletMap);
const communesLayer = L.layerGroup().addTo(leafletMap);

// Google Maps instance & overlays
let gmap = null;
let gmapOverlays = [];

function clearGmapOverlays() {
  gmapOverlays.forEach(o => o.setMap(null));
  gmapOverlays = [];
}

function getCurvedPoints(lat1, lon1, lat2, lon2, segments = 24) {
  const pts = [];
  const dx = lon2 - lon1;
  const dy = lat2 - lat1;
  const offset = 0.16;
  const midLat = (lat1 + lat2) / 2 - dx * offset;
  const midLon = (lon1 + lon2) / 2 + dy * offset;
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const lat = (1 - t) * (1 - t) * lat1 + 2 * (1 - t) * t * midLat + t * t * lat2;
    const lon = (1 - t) * (1 - t) * lon1 + 2 * (1 - t) * t * midLon + t * t * lon2;
    pts.push([lat, lon]);
  }
  return pts;
}

function filterFlowsForYear(yr) {
  return DATA.od_flows.filter(f => {
    if (f.year !== yr) return false;
    if (state.mode !== "ALL" && f.mode !== state.mode) return false;
    if (state.period !== "ALL" && f.macro_period !== state.period) return false;
    if (state.purpose !== "ALL" && f.purpose !== state.purpose) return false;
    if (state.commune !== "ALL") {
      if (state.direction === "ORIGIN" && f.origin !== state.commune) return false;
      if (state.direction === "DEST" && f.dest !== state.commune) return false;
      if (state.direction === "BOTH" && f.origin !== state.commune && f.dest !== state.commune) return false;
    }
    return true;
  });
}

function aggregateCorridors(rawFlows) {
  const map = new Map();
  for (const f of rawFlows) {
    const key = `${f.origin}|${f.dest}|${f.mode}`;
    if (!map.has(key)) {
      map.set(key, {
        origin: f.origin,
        dest: f.dest,
        mode: f.mode,
        trips: 0,
        intermodal_trips: 0,
        weighted_min: 0,
        weighted_km: 0,
        weighted_stages: 0
      });
    }
    const rec = map.get(key);
    rec.trips += f.trips;
    rec.intermodal_trips += f.intermodal_trips;
    rec.weighted_min += f.trips * (f.avg_min || 0);
    rec.weighted_km += f.trips * (f.avg_km || 0);
    rec.weighted_stages += f.trips * (f.avg_stages || 1);
  }
  const arr = Array.from(map.values()).map(r => ({
    ...r,
    avg_min: r.trips > 0 ? r.weighted_min / r.trips : 0,
    avg_km: r.trips > 0 ? r.weighted_km / r.trips : 0,
    avg_stages: r.trips > 0 ? r.weighted_stages / r.trips : 1,
    intermodal_pct: r.trips > 0 ? (r.intermodal_trips / r.trips) * 100 : 0
  }));
  arr.sort((a, b) => b.trips - a.trips);
  return arr;
}

function summarizeTotals(corridors) {
  let trips = 0, inter = 0, wMin = 0, wKm = 0, wStg = 0;
  const modeTotals = {};
  for (const c of corridors) {
    trips += c.trips;
    inter += c.intermodal_trips;
    wMin += c.trips * c.avg_min;
    wKm += c.trips * c.avg_km;
    wStg += c.trips * c.avg_stages;
    modeTotals[c.mode] = (modeTotals[c.mode] || 0) + c.trips;
  }
  let domMode = "—", domVal = 0;
  for (const [m, v] of Object.entries(modeTotals)) {
    if (v > domVal) { domVal = v; domMode = m; }
  }
  return {
    trips,
    intermodalPct: trips > 0 ? (inter / trips) * 100 : 0,
    avgMin: trips > 0 ? wMin / trips : 0,
    avgKm: trips > 0 ? wKm / trips : 0,
    avgStages: trips > 0 ? wStg / trips : 0,
    domMode,
    domPct: trips > 0 ? (domVal / trips) * 100 : 0,
    modeTotals
  };
}

function renderDashboard() {
  const currFlows = filterFlowsForYear(state.year);
  const otherYear = state.year === "2023" ? "2019" : "2023";
  const prevFlows = filterFlowsForYear(otherYear);

  const corridors = aggregateCorridors(currFlows);
  const prevCorridors = aggregateCorridors(prevFlows);

  const currSum = summarizeTotals(corridors);
  const prevSum = summarizeTotals(prevCorridors);

  // 1. Update KPIs
  document.getElementById("kpi-trips").textContent = Math.round(currSum.trips).toLocaleString();
  const tripDelta = prevSum.trips > 0 ? ((currSum.trips - prevSum.trips) / prevSum.trips) * 100 : 0;
  document.getElementById("kpi-trips-vs").innerHTML = `vs ${otherYear}: <span class="badge-delta ${tripDelta >= 0 ? 'delta-pos' : 'delta-neg'}">${tripDelta >= 0 ? '+' : ''}${tripDelta.toFixed(1)}%</span>`;
  document.getElementById("kpi-intermodal").textContent = `Intermodal: ${currSum.intermodalPct.toFixed(1)}%`;

  document.getElementById("kpi-time").textContent = `${currSum.avgMin.toFixed(1)} min`;
  document.getElementById("kpi-time-vs").textContent = `${otherYear}: ${prevSum.avgMin.toFixed(1)} min`;
  document.getElementById("kpi-stages").textContent = `Etapas prom: ${currSum.avgStages.toFixed(2)}`;

  document.getElementById("kpi-dist").textContent = `${currSum.avgKm.toFixed(2)} km`;
  document.getElementById("kpi-dist-vs").textContent = `${otherYear}: ${prevSum.avgKm.toFixed(2)} km`;
  const speedKmh = currSum.avgMin > 0 ? (currSum.avgKm / (currSum.avgMin / 60)) : 0;
  document.getElementById("kpi-speed").textContent = `Velocidad est: ${speedKmh.toFixed(1)} km/h`;

  const topC = corridors[0];
  document.getElementById("kpi-top-od").textContent = topC ? `${topC.origin} → ${topC.dest}` : "Sin datos en filtro";
  document.getElementById("kpi-dom-mode").textContent = `Modo líder: ${currSum.domMode} (${currSum.domPct.toFixed(0)}%)`;

  const filteredHubs = DATA.stop_hubs.filter(h =>
    h.year === state.year &&
    (state.mode === "ALL" || h.mode === state.mode) &&
    (state.commune === "ALL" || h.comuna === state.commune)
  );
  filteredHubs.sort((a, b) => b.total - a.total);
  document.getElementById("kpi-hub-top").textContent = filteredHubs[0] ? `Top: ${filteredHubs[0].name}` : "—";

  // 2. Render Map Layers (Leaflet & optional Google Maps)
  arcsLayer.clearLayers();
  hubsLayer.clearLayers();
  communesLayer.clearLayers();
  if (state.useGoogleMaps && gmap) clearGmapOverlays();

  const topCorridors = corridors.slice(0, state.topN);
  const maxTrips = topCorridors.length > 0 ? topCorridors[0].trips : 1;

  if (state.showArcs) {
    topCorridors.forEach(c => {
      const orig = DATA.communes[c.origin];
      const dest = DATA.communes[c.dest];
      if (!orig || !dest || c.origin === c.dest) return;
      const color = MODE_COLORS[c.mode] || "#38bdf8";
      const weight = Math.max(1.8, Math.min(9, 1.5 + (c.trips / maxTrips) * 7.5));
      const pts = getCurvedPoints(orig.lat, orig.lon, dest.lat, dest.lon);

      const poly = L.polyline(pts, {
        color,
        weight,
        opacity: 0.72
      }).bindTooltip(
        `<b>${c.origin} → ${c.dest}</b> (${c.mode})<br/>` +
        `Viajes: <b>${Math.round(c.trips).toLocaleString()}</b> | Intermodal: ${c.intermodal_pct.toFixed(1)}%<br/>` +
        `Tiempo prom: ${c.avg_min.toFixed(1)} min | Ruta: ${c.avg_km.toFixed(1)} km`,
        { sticky: true }
      );
      poly.on("click", () => {
        state.commune = c.origin;
        selCommune.value = c.origin;
        renderDashboard();
      });
      arcsLayer.addLayer(poly);

      if (state.useGoogleMaps && gmap && window.google) {
        const gPoly = new google.maps.Polyline({
          path: pts.map(p => ({ lat: p[0], lng: p[1] })),
          geodesic: false,
          strokeColor: color,
          strokeOpacity: 0.78,
          strokeWeight: weight,
          map: gmap
        });
        gmapOverlays.push(gPoly);
      }
    });
  }

  if (state.showHubs) {
    const maxHub = filteredHubs.length > 0 ? filteredHubs[0].total : 1;
    filteredHubs.slice(0, 120).forEach(h => {
      const color = MODE_COLORS[h.mode] || "#38bdf8";
      const radius = Math.max(4, Math.min(13, 4 + Math.sqrt(h.total / maxHub) * 9));
      const marker = L.circleMarker([h.lat, h.lon], {
        radius,
        fillColor: color,
        color: "#0b0f19",
        weight: 1.2,
        fillOpacity: 0.88
      }).bindTooltip(
        `<b>${h.name}</b> <span style="color:${color}">(${h.mode})</span><br/>` +
        `Comuna: ${h.comuna} | Servicio/Línea: <b>${h.service}</b><br/>` +
        `Subidas muestra exp: <b>${Math.round(h.total).toLocaleString()}</b> (PM: ${Math.round(h.pm)} | PT: ${Math.round(h.pt)})<br/>` +
        `Tiempo etapa prom: ${h.avg_stage_min.toFixed(1)} min`
      );
      marker.on("click", () => {
        state.commune = h.comuna;
        selCommune.value = h.comuna;
        renderDashboard();
      });
      hubsLayer.addLayer(marker);

      if (state.useGoogleMaps && gmap && window.google) {
        const gCircle = new google.maps.Marker({
          position: { lat: h.lat, lng: h.lon },
          map: gmap,
          title: `${h.name} (${h.mode}) - ${Math.round(h.total)} subidas`,
          icon: {
            path: google.maps.SymbolPath.CIRCLE,
            scale: radius,
            fillColor: color,
            fillOpacity: 0.9,
            strokeColor: "#0b0f19",
            strokeWeight: 1
          }
        });
        gmapOverlays.push(gCircle);
      }
    });
  }

  if (state.showCommunes) {
    Object.values(DATA.communes).forEach(c => {
      const isSelected = state.commune === c.name;
      const cm = L.circleMarker([c.lat, c.lon], {
        radius: isSelected ? 8 : 4,
        fillColor: isSelected ? "#38bdf8" : "#94a3b8",
        color: "#0f172a",
        weight: 1.5,
        fillOpacity: isSelected ? 1.0 : 0.65
      }).bindTooltip(`Comuna: <b>${c.name}</b> (Click para filtrar flujos OD)`);
      cm.on("click", () => {
        state.commune = state.commune === c.name ? "ALL" : c.name;
        selCommune.value = state.commune;
        renderDashboard();
      });
      communesLayer.addLayer(cm);
    });
  }

  // 3. Render Top OD Corridors Table
  const tbody = document.getElementById("od-tbody");
  tbody.innerHTML = "";
  document.getElementById("tbl-count").textContent = `${corridors.length} pares OD`;
  corridors.slice(0, 60).forEach(c => {
    const tr = document.createElement("tr");
    const color = MODE_COLORS[c.mode] || "#38bdf8";
    tr.innerHTML = `
      <td><b>${c.origin}</b> → ${c.dest}</td>
      <td><span class="mode-tag" style="background:${color}22; color:${color}; border:1px solid ${color}55;">${c.mode}</span></td>
      <td>${Math.round(c.trips).toLocaleString()}</td>
      <td>${c.intermodal_pct.toFixed(1)}%</td>
      <td>${c.avg_min.toFixed(1)} min</td>
      <td>${c.avg_km.toFixed(1)} km</td>
    `;
    tr.addEventListener("click", () => {
      state.commune = c.origin;
      selCommune.value = c.origin;
      const orig = DATA.communes[c.origin];
      if (orig) leafletMap.setView([orig.lat, orig.lon], 12);
      renderDashboard();
    });
    tbody.appendChild(tr);
  });

  // 4. Generate Automated Insights for Current View
  const insightsEl = document.getElementById("insights-list");
  const insights = [];
  if (topC) {
    insights.push(
      `<b>Corredor dominante (${state.year}):</b> <b>${topC.origin} → ${topC.dest}</b> vía <b>${topC.mode}</b> concentra ${Math.round(topC.trips).toLocaleString()} viajes expandidos con un tiempo medio de <b>${topC.avg_min.toFixed(1)} min</b> (${topC.avg_km.toFixed(1)} km).`
    );
  }
  const metroMin = corridors.filter(c => c.mode === "METRO").reduce((a, b) => a + b.trips * b.avg_min, 0) /
                   Math.max(1, corridors.filter(c => c.mode === "METRO").reduce((a, b) => a + b.trips, 0));
  const busMin = corridors.filter(c => c.mode === "BUS").reduce((a, b) => a + b.trips * b.avg_min, 0) /
                 Math.max(1, corridors.filter(c => c.mode === "BUS").reduce((a, b) => a + b.trips, 0));
  if (metroMin > 0 && busMin > 0) {
    const diff = busMin - metroMin;
    insights.push(
      `<b>Brecha modal de tiempo de viaje:</b> En la selección actual, los viajes iniciados en <b>Metro</b> promedian <b>${metroMin.toFixed(1)} min</b> frente a <b>${busMin.toFixed(1)} min</b> en <b>Bus</b> (${diff >= 0 ? 'Bus tarda +' + diff.toFixed(1) + ' min más' : 'Metro tarda +' + Math.abs(diff).toFixed(1) + ' min'}).`
    );
  }
  if (state.commune !== "ALL") {
    const outbound = DATA.od_flows.filter(f => f.year === state.year && f.origin === state.commune && (state.period === "ALL" || f.macro_period === state.period)).reduce((s, f) => s + f.trips, 0);
    const inbound = DATA.od_flows.filter(f => f.year === state.year && f.dest === state.commune && (state.period === "ALL" || f.macro_period === state.period)).reduce((s, f) => s + f.trips, 0);
    const ratio = inbound > 0 ? (outbound / inbound) : 1;
    const role = ratio > 1.25 ? "Comuna Generadora / Dormitorio (emite más viajes de los que recibe)" :
                 ratio < 0.8 ? "Comuna Atractora de Actividad (recibe más viajes de los que emite)" :
                 "Comuna de Flujo Balanceado";
    insights.push(
      `<b>Balance Neto de ${state.commune}:</b> Emite <b>${Math.round(outbound).toLocaleString()}</b> viajes y recibe <b>${Math.round(inbound).toLocaleString()}</b> (Ratio Salida/Entrada = <b>${ratio.toFixed(2)}x</b> → <i>${role}</i>).`
    );
  } else {
    insights.push(
      `<b>Comparación 2019 vs 2023:</b> El tiempo promedio de viaje pasó de <b>${prevSum.avgMin.toFixed(1)} min</b> (${otherYear}) a <b>${currSum.avgMin.toFixed(1)} min</b> (${state.year}), con un <b>${currSum.intermodalPct.toFixed(1)}%</b> de viajes intermodales (combinación Bus/Metro/Zona Paga/Metrotren).`
    );
  }
  insightsEl.innerHTML = insights.map(i => `<li>${i}</li>`).join("");

  // 5. Render Half-Hourly Demand Chart
  renderHalfHourChart();
}

function renderHalfHourChart() {
  const modes = state.mode === "ALL" ? ["METRO", "BUS", "ZONA PAGA", "METROTREN"] : [state.mode];
  const traces = [];

  modes.forEach(m => {
    const rows23 = DATA.half_hour_curves.filter(r => r.year === "2023" && r.mode === m);
    const rows19 = DATA.half_hour_curves.filter(r => r.year === "2019" && r.mode === m);

    traces.push({
      x: rows23.map(r => r.mh),
      y: rows23.map(r => r.expanded_trips),
      name: `${m} 2023`,
      type: "scatter",
      mode: "lines",
      line: { color: MODE_COLORS[m], width: 2.5 }
    });
    traces.push({
      x: rows19.map(r => r.mh),
      y: rows19.map(r => r.expanded_trips),
      name: `${m} 2019`,
      type: "scatter",
      mode: "lines",
      line: { color: MODE_COLORS[m], width: 1.5, dash: "dot" },
      opacity: 0.7
    });
  });

  Plotly.react("chart-halfhour", traces, {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: "#cbd5e1", size: 10 },
    margin: { t: 12, r: 12, b: 36, l: 46 },
    xaxis: { title: "Media Hora", gridcolor: "#1e293b", tickangle: -45 },
    yaxis: { title: "Viajes Expandidos", gridcolor: "#1e293b" },
    legend: { orientation: "h", y: 1.14, x: 0 }
  }, { displayModeBar: false, responsive: true });
}

function renderStationProfileChart() {
  const idx = parseInt(selStation.value || "0", 10);
  const st = DATA.metro_2023_profiles[idx];
  if (!st) return;
  const mhs = Object.keys(st.half_hours);
  const vals = mhs.map(k => st.half_hours[k]);

  Plotly.react("chart-station", [{
    x: mhs,
    y: vals,
    type: "bar",
    marker: { color: "#ef4444" },
    name: st.station
  }], {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: "#cbd5e1", size: 10 },
    margin: { t: 10, r: 12, b: 38, l: 46 },
    xaxis: { gridcolor: "#1e293b", tickangle: -45 },
    yaxis: { title: "Subidas Prom. Laboral", gridcolor: "#1e293b" }
  }, { displayModeBar: false, responsive: true });
}

// Event Listeners
document.getElementById("sel-year").addEventListener("change", e => { state.year = e.target.value; renderDashboard(); });
document.getElementById("sel-period").addEventListener("change", e => { state.period = e.target.value; renderDashboard(); });
document.getElementById("sel-purpose").addEventListener("change", e => { state.purpose = e.target.value; renderDashboard(); });
selCommune.addEventListener("change", e => {
  state.commune = e.target.value;
  if (state.commune !== "ALL" && DATA.communes[state.commune]) {
    const c = DATA.communes[state.commune];
    leafletMap.setView([c.lat, c.lon], 12);
  } else {
    leafletMap.setView([-33.455, -70.655], 11);
  }
  renderDashboard();
});
document.getElementById("sel-direction").addEventListener("change", e => { state.direction = e.target.value; renderDashboard(); });
document.getElementById("rng-topn").addEventListener("input", e => {
  state.topN = parseInt(e.target.value, 10);
  document.getElementById("lbl-topn").textContent = state.topN;
  renderDashboard();
});
document.getElementById("chk-arcs").addEventListener("change", e => { state.showArcs = e.target.checked; renderDashboard(); });
document.getElementById("chk-hubs").addEventListener("change", e => { state.showHubs = e.target.checked; renderDashboard(); });
document.getElementById("chk-communes").addEventListener("change", e => { state.showCommunes = e.target.checked; renderDashboard(); });
selStation.addEventListener("change", renderStationProfileChart);

document.querySelectorAll("#mode-pills .pill").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#mode-pills .pill").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    state.mode = btn.dataset.mode;
    renderDashboard();
  });
});

document.getElementById("btn-reset").addEventListener("click", () => {
  state.year = "2023";
  state.mode = "ALL";
  state.period = "ALL";
  state.purpose = "ALL";
  state.commune = "ALL";
  state.direction = "BOTH";
  state.topN = 35;
  document.getElementById("sel-year").value = "2023";
  document.getElementById("sel-period").value = "ALL";
  document.getElementById("sel-purpose").value = "ALL";
  selCommune.value = "ALL";
  document.getElementById("sel-direction").value = "BOTH";
  document.getElementById("rng-topn").value = 35;
  document.getElementById("lbl-topn").textContent = "35";
  document.querySelectorAll("#mode-pills .pill").forEach(b => b.classList.toggle("active", b.dataset.mode === "ALL"));
  leafletMap.setView([-33.455, -70.655], 11);
  renderDashboard();
});

// Google Maps API Modal Logic
const gmapModal = document.getElementById("gmap-modal");
const inpGmapKey = document.getElementById("inp-gmap-key");
document.getElementById("btn-gmap-key").addEventListener("click", () => {
  inpGmapKey.value = state.gmapKey;
  gmapModal.style.display = "flex";
});
document.getElementById("btn-gmap-cancel").addEventListener("click", () => {
  gmapModal.style.display = "none";
});
document.getElementById("btn-gmap-save").addEventListener("click", () => {
  const key = inpGmapKey.value.trim();
  state.gmapKey = key;
  localStorage.setItem("METRO_OD_GMAP_KEY", key);
  gmapModal.style.display = "none";
  if (!key) {
    state.useGoogleMaps = false;
    document.getElementById("gmap-canvas").style.display = "none";
    document.getElementById("leaflet-map").style.display = "block";
    leafletMap.invalidateSize();
    renderDashboard();
    return;
  }
  activateGoogleMaps(key);
});

function activateGoogleMaps(apiKey) {
  state.useGoogleMaps = true;
  document.getElementById("leaflet-map").style.display = "none";
  const canvas = document.getElementById("gmap-canvas");
  canvas.style.display = "block";

  window.initMetroGoogleMap = function() {
    gmap = new google.maps.Map(canvas, {
      center: { lat: -33.455, lng: -70.655 },
      zoom: 11,
      styles: [
        { elementType: "geometry", stylers: [{ color: "#1d2c4d" }] },
        { elementType: "labels.text.fill", stylers: [{ color: "#8ec3b9" }] },
        { elementType: "labels.text.stroke", stylers: [{ color: "#1a3646" }] },
        { featureType: "road", elementType: "geometry", stylers: [{ color: "#304a7d" }] },
        { featureType: "water", elementType: "geometry", stylers: [{ color: "#0e1626" }] }
      ]
    });
    const transitLayer = new google.maps.TransitLayer();
    transitLayer.setMap(gmap);
    renderDashboard();
  };

  if (window.google && window.google.maps) {
    window.initMetroGoogleMap();
  } else {
    const script = document.createElement("script");
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&callback=initMetroGoogleMap`;
    script.async = true;
    document.head.appendChild(script);
  }
}

// Initial Render
renderDashboard();
renderStationProfileChart();
</script>
</body>
</html>
"""


def build_html_dashboard(output_path: Path, sample_rows: int = 60_000) -> Path:
    """Extract analytics payload and render the interactive HTML dashboard + data JS."""
    payload = extract_dashboard_data(sample_rows=sample_rows)
    json_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data_js_path = output_path.parent / "dashboard_data.js"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_js_path.write_text(f"window.METRO_OD_DATA = {json_str};\n", encoding="utf-8")
    html_content = HTML_TEMPLATE.replace("__DASHBOARD_JSON_PAYLOAD__", json_str)
    output_path.write_text(html_content, encoding="utf-8")
    return output_path


if __name__ == "__main__":
    import sys

    out_file = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else (PROJECT_ROOT / "dashboard" / "metro_od_analytics.html")
    )
    samples = int(sys.argv[2]) if len(sys.argv) > 2 else 200_000
    built = build_html_dashboard(out_file, sample_rows=samples)
    print(f"Built HTML dashboard: {built} ({built.stat().st_size} bytes)")
