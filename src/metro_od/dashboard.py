"""Interactive HTML Analytics Dashboard generator for Metro de Santiago OD data (2019 vs 2023).

Extracts real multi-modal data (`METRO`, `BUS`, `ZONA PAGA`, `METROTREN`) from the cleaned
2019 & 2023 `viajes`, `etapas`, `.xlsb` matrices, and GTFS feeds using DuckDB, Polars, and PyProj,
and builds a self-contained interactive HTML application (`dashboard/metro_od_analytics.html`)
with Leaflet + optional Google Maps JS API integration, OD flow arcs, half-hourly scrubbers,
and comparative charts.
"""

from __future__ import annotations

import json
import os
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

    # 1b. Extract one representative GTFS shape polyline per Metro line (L1-L6)
    gtfs_trips_path = (DATA_DIR / "GTFS" / "trips.txt").as_posix()
    gtfs_shapes_path = (DATA_DIR / "GTFS" / "shapes.txt").as_posix()
    metro_lines_gtfs: List[Dict[str, object]] = []
    try:
        shape_df = con.execute(
            f"""
            WITH route_shapes AS (
                SELECT
                    r.route_short_name AS line_name,
                    COALESCE(NULLIF(TRIM(r.route_color), ''), 'E11D48') AS color,
                    t.shape_id,
                    COUNT(*) AS n_trips,
                    ROW_NUMBER() OVER (PARTITION BY r.route_short_name ORDER BY COUNT(*) DESC) AS rn
                FROM read_csv_auto('{gtfs_routes_path}') r
                JOIN read_csv_auto('{gtfs_trips_path}') t ON r.route_id = t.route_id
                WHERE r.route_type = 1 AND t.shape_id IS NOT NULL
                GROUP BY r.route_short_name, r.route_color, t.shape_id
            )
            SELECT
                rs.line_name,
                rs.color,
                s.shape_pt_lat AS lat,
                s.shape_pt_lon AS lon,
                s.shape_pt_sequence AS seq
            FROM route_shapes rs
            JOIN read_csv_auto('{gtfs_shapes_path}') s ON rs.shape_id = s.shape_id
            WHERE rs.rn = 1
            ORDER BY rs.line_name, s.shape_pt_sequence
            """
        ).df()
        for lname, grp in shape_df.groupby("line_name", sort=True):
            pts = [[round(float(r.lat), 5), round(float(r.lon), 5)] for r in grp.itertuples()]
            color_hex = str(grp["color"].iloc[0]).lstrip("#")
            metro_lines_gtfs.append({"name": str(lname), "color": f"#{color_hex}", "points": pts})
    except Exception:
        metro_lines_gtfs = []

    future_lines = [
        {
            "name": "L7 (Futura · Renca–Vitacura)",
            "color": "#06b6d4",
            "stations": [
                ["Brasil (Renca)", -33.3965, -70.7525], ["J. M. Infante", -33.4050, -70.7420],
                ["Salvador Gutiérrez", -33.4148, -70.7412], ["Huelén (Cerro Navia)", -33.4235, -70.7375],
                ["Neptuno L7", -33.4285, -70.7235], ["Radal", -33.4310, -70.7085],
                ["Walker Martínez (QN)", -33.4315, -70.6955], ["Matucana", -33.4335, -70.6805],
                ["Ricardo Cumming", -33.4342, -70.6685], ["Cal y Canto L7", -33.4328, -70.6529],
                ["Baquedano L7", -33.4372, -70.6352], ["Pedro de Valdivia L7", -33.4255, -70.6143],
                ["Isidora Goyenechea", -33.4158, -70.5985], ["Vitacura", -33.4045, -70.5880],
                ["Américo Vespucio", -33.3990, -70.5780], ["Parque Araucano", -33.4015, -70.5685],
                ["Gerónimo de Alderete", -33.3935, -70.5565], ["Padre Hurtado", -33.3865, -70.5445],
                ["Estoril", -33.3835, -70.5315],
            ],
        },
        {
            "name": "L8 (Futura · Los Leones–Puente Alto)",
            "color": "#f97316",
            "stations": [
                ["Los Leones L8", -33.4223, -70.6097], ["Eliodoro Yáñez", -33.4310, -70.6070],
                ["Diagonal Oriente", -33.4435, -70.6040], ["Chile España L8", -33.4555, -70.6015],
                ["Grecia", -33.4648, -70.5990], ["Rodrigo de Araya", -33.4785, -70.5975],
                ["Quilín (Macul)", -33.4895, -70.5965], ["Las Torres (Macul)", -33.4995, -70.5955],
                ["Macul L8", -33.5090, -70.5910], ["Walker Martínez (LF)", -33.5225, -70.5855],
                ["Rojas Magallanes (LF)", -33.5365, -70.5825], ["Trinidad (LF)", -33.5495, -70.5800],
                ["Diego Portales (LF)", -33.5625, -70.5775], ["Mall Plaza Tobalaba", -33.5715, -70.5620],
            ],
        },
        {
            "name": "L9 (Futura · Cal y Canto–La Pintana–P. Alto)",
            "color": "#ec4899",
            "stations": [
                ["Cal y Canto L9", -33.4328, -70.6529], ["Santa Lucía L9", -33.4428, -70.6458],
                ["Matta L9", -33.4565, -70.6425], ["Ñuble L9", -33.4675, -70.6395],
                ["Bío Bío L9", -33.4765, -70.6380], ["La Legua", -33.4885, -70.6370],
                ["Departamental (SR)", -33.5025, -70.6365], ["Lo Ovalle (SR)", -33.5165, -70.6360],
                ["Linares", -33.5295, -70.6355], ["Santa Rosa L9", -33.5425, -70.6350],
                ["Hosp. Padre Hurtado", -33.5555, -70.6345], ["Observatorio", -33.5685, -70.6340],
                ["Lo Martínez", -33.5815, -70.6335], ["La Pintana Centro", -33.5835, -70.6345],
                ["Gabriela (La Pintana)", -33.5935, -70.6330], ["El Mariscal", -33.6055, -70.6250],
                ["Plaza Puente Alto L9", -33.6095, -70.5759],
            ],
        },
    ]

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

    # 7. Extract EDA & 4 Strategic Hypotheses Data (from vj_combined + viajes_2023)
    h1_dropoff_df = con.execute(
        """
        SELECT
            anio AS year,
            tipo_transporte_1 AS mode,
            COUNT(*) AS total_viajes,
            ROUND(AVG(CASE WHEN ultima_etapa_con_bajada = 0 THEN 100.0 ELSE 0.0 END), 2) AS sin_bajada_pct
        FROM vj_combined
        WHERE tipo_transporte_1 IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).df()

    h1_chaining_df = con.execute(
        """
        WITH card_days AS (
            SELECT id_tarjeta, DATE_TRUNC('day', tiempo_inicio_viaje) AS dia, COUNT(*) AS viajes_dia
            FROM vj_combined
            WHERE anio = '2023' AND id_tarjeta IS NOT NULL AND tiempo_inicio_viaje IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT
            CASE WHEN viajes_dia >= 4 THEN '4+ viajes/día' ELSE CAST(viajes_dia AS VARCHAR) || ' viaje(s)/día' END AS bucket,
            COUNT(*) AS cards,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct
        FROM card_days
        GROUP BY 1
        ORDER BY 1
        """
    ).df()

    h2_temporal_df = con.execute(
        """
        WITH m AS (
            SELECT
                anio,
                CASE
                    WHEN mediahora_inicio_viaje >= TIME '06:00:00' AND mediahora_inicio_viaje < TIME '09:00:00' THEN 'Punta Mañana (06–09)'
                    WHEN mediahora_inicio_viaje >= TIME '09:00:00' AND mediahora_inicio_viaje < TIME '17:00:00' THEN 'Valle Día (09–17)'
                    WHEN mediahora_inicio_viaje >= TIME '17:00:00' AND mediahora_inicio_viaje < TIME '20:00:00' THEN 'Punta Tarde (17–20)'
                    WHEN mediahora_inicio_viaje >= TIME '20:00:00' THEN 'Noche (20–24)'
                    ELSE 'Madrugada (00–06)'
                END AS bloque,
                CASE
                    WHEN mediahora_inicio_viaje >= TIME '06:00:00' AND mediahora_inicio_viaje < TIME '09:00:00' THEN 1
                    WHEN mediahora_inicio_viaje >= TIME '09:00:00' AND mediahora_inicio_viaje < TIME '17:00:00' THEN 2
                    WHEN mediahora_inicio_viaje >= TIME '17:00:00' AND mediahora_inicio_viaje < TIME '20:00:00' THEN 3
                    WHEN mediahora_inicio_viaje >= TIME '20:00:00' THEN 4
                    ELSE 0
                END AS ord,
                COALESCE(factor_expansion, 1.0) AS w
            FROM vj_combined
            WHERE tipo_transporte_1 = 'METRO' AND mediahora_inicio_viaje IS NOT NULL
        )
        SELECT
            bloque,
            ord,
            ROUND(SUM(CASE WHEN anio='2019' THEN w ELSE 0 END) * 100.0 / NULLIF(SUM(SUM(CASE WHEN anio='2019' THEN w ELSE 0 END)) OVER (), 0), 2) AS share_2019,
            ROUND(SUM(CASE WHEN anio='2023' THEN w ELSE 0 END) * 100.0 / NULLIF(SUM(SUM(CASE WHEN anio='2023' THEN w ELSE 0 END)) OVER (), 0), 2) AS share_2023
        FROM m
        GROUP BY bloque, ord
        ORDER BY ord
        """
    ).df()

    h4_l3l6_df = con.execute(
        """
        WITH base AS (
            SELECT
                anio,
                comuna_inicio_viaje AS comuna,
                CASE
                    WHEN comuna_inicio_viaje IN ('QUILICURA', 'CONCHALI', 'INDEPENDENCIA', 'NUNOA', 'CERRILLOS', 'PEDRO AGUIRRE CERDA')
                    THEN 'L3/L6 Expansión'
                    ELSE 'Control (Sin L3/L6)'
                END AS grupo,
                tviaje_min,
                n_etapas,
                CASE WHEN tipo_transporte_1 = 'METRO' THEN 100.0 ELSE 0.0 END AS is_metro
            FROM vj_combined
            WHERE comuna_inicio_viaje IN (
                'QUILICURA', 'CONCHALI', 'INDEPENDENCIA', 'NUNOA', 'CERRILLOS', 'PEDRO AGUIRRE CERDA',
                'MAIPU', 'PUENTE ALTO', 'LA FLORIDA', 'LAS CONDES', 'SANTIAGO', 'PUDAHUEL'
            ) AND tviaje_min IS NOT NULL
        )
        SELECT
            comuna,
            grupo,
            ROUND(AVG(CASE WHEN anio = '2019' THEN tviaje_min END), 1) AS min_2019,
            ROUND(AVG(CASE WHEN anio = '2023' THEN tviaje_min END), 1) AS min_2023,
            ROUND(AVG(CASE WHEN anio = '2023' THEN tviaje_min END) - AVG(CASE WHEN anio = '2019' THEN tviaje_min END), 1) AS delta_min,
            ROUND(AVG(CASE WHEN anio = '2019' THEN is_metro END), 1) AS metro_pct_2019,
            ROUND(AVG(CASE WHEN anio = '2023' THEN is_metro END), 1) AS metro_pct_2023
        FROM base
        GROUP BY comuna, grupo
        ORDER BY grupo DESC, delta_min ASC
        """
    ).df()

    # Full-dataset 2023 decomposition & station friction metrics (verified over 10.75M trips in eda.py)
    h1_time_decomp = [
        {"label": "Metro (1 etapa)", "trips": 4972348, "entrada": 0.77, "espera1": 1.50, "vehiculo1": 17.48, "caminata": 0.0, "espera2": 0.0, "vehiculo2": 0.0, "egreso": 1.51, "total": 21.26},
        {"label": "Bus (1 etapa)", "trips": 2619518, "entrada": 0.0, "espera1": 4.22, "vehiculo1": 19.11, "caminata": 0.0, "espera2": 0.0, "vehiculo2": 0.0, "egreso": 0.0, "total": 23.33},
        {"label": "Metrotren → Metro (2 et.)", "trips": 86041, "entrada": 0.0, "espera1": 0.0, "vehiculo1": 14.53, "caminata": 10.96, "espera2": 3.23, "vehiculo2": 2.76, "egreso": 1.36, "total": 32.84},
        {"label": "Bus → Metro (2 et.)", "trips": 1853466, "entrada": 0.0, "espera1": 4.22, "vehiculo1": 14.51, "caminata": 3.22, "espera2": 3.15, "vehiculo2": 14.63, "egreso": 1.54, "total": 41.27},
        {"label": "Metro → Bus (2 et.)", "trips": 708562, "entrada": 0.77, "espera1": 1.49, "vehiculo1": 20.02, "caminata": 2.96, "espera2": 7.70, "vehiculo2": 16.28, "egreso": 0.0, "total": 49.22},
    ]

    h3_station_friction = [
        {"station": "LAGUNA SUR (L5)", "entrada": 102.0, "espera": 126.4, "egreso": 102.4},
        {"station": "MONTE TABOR (L5)", "entrada": 102.0, "espera": 135.8, "egreso": 105.8},
        {"station": "LAS PARCELAS (L5)", "entrada": 102.0, "espera": 127.9, "egreso": 102.7},
        {"station": "SANTA JULIA (L4A)", "entrada": 95.0, "espera": 151.1, "egreso": 101.5},
        {"station": "SAN RAMON (L4A)", "entrada": 95.0, "espera": 149.9, "egreso": 97.5},
        {"station": "LA GRANJA (L4A)", "entrada": 95.0, "espera": 149.3, "egreso": 99.8},
        {"station": "SANTIAGO BUERAS (L5)", "entrada": 94.0, "espera": 131.9, "egreso": 99.2},
        {"station": "BARRANCAS (L5)", "entrada": 94.0, "espera": 130.3, "egreso": 104.5},
        {"station": "LO PRADO (L5)", "entrada": 94.0, "espera": 129.7, "egreso": 101.8},
        {"station": "PUDAHUEL (L5)", "entrada": 94.0, "espera": 125.0, "egreso": 101.9},
        {"station": "PARQUE OHIGGINS (L2)", "entrada": 73.0, "espera": 126.6, "egreso": 90.6},
        {"station": "HOSP. SOTERO DEL RIO (L4)", "entrada": 68.0, "espera": 97.6, "egreso": 96.8},
        {"station": "TOBALABA (L1/L4 Ref.)", "entrada": 66.0, "espera": 73.5, "egreso": 89.6},
    ]

    # 100% Full 7-Day Verified Results (zero LIMIT, 44.43M trips across 2019-04-08..14 & 2023-04-17..23)
    full_week_metrics = {
        "coverage": [
            {"label": "2019 Laboral (08–12 Abr)", "rows": 22175170, "days": 5, "daily_trips": 4435039, "sin_bajada_pct": 24.77},
            {"label": "2023 Laboral (17–21 Abr)", "rows": 15957432, "days": 5, "daily_trips": 3191485, "sin_bajada_pct": 27.60},
            {"label": "2019 Sábado (13 Abr)", "rows": 2356851, "days": 1, "daily_trips": 2356852, "sin_bajada_pct": 32.45},
            {"label": "2023 Sábado (22 Abr)", "rows": 1690625, "days": 1, "daily_trips": 1690627, "sin_bajada_pct": 35.19},
            {"label": "2019 Domingo (14 Abr)", "rows": 1305725, "days": 1, "daily_trips": 1305725, "sin_bajada_pct": 38.17},
            {"label": "2023 Domingo (23 Abr)", "rows": 949353, "days": 1, "daily_trips": 949354, "sin_bajada_pct": 40.84},
        ],
        "intermodal_asym": [
            {"year": "2019", "seq": "BUS → METRO", "raw_day": 439907, "exp_day": 532802, "obs_min": 41.56, "sin_bajada": 12.65},
            {"year": "2019", "seq": "METRO → BUS", "raw_day": 166287, "exp_day": 123112, "obs_min": 46.44, "sin_bajada": 42.86},
            {"year": "2023", "seq": "BUS → METRO", "raw_day": 318833, "exp_day": 392558, "obs_min": 41.61, "sin_bajada": 15.83},
            {"year": "2023", "seq": "METRO → BUS", "raw_day": 123781, "exp_day": 93191, "obs_min": 47.72, "sin_bajada": 43.73},
        ],
        "intermodal_cards": [
            {"label": "Solo Metro (Sin validar Bus)", "cards": 269171, "pct": 32.58, "color": "#ef4444"},
            {"label": "Otro retorno (ZP / Metrotren)", "cards": 161133, "pct": 19.51, "color": "#f59e0b"},
            {"label": "Sin retorno validado tarde", "cards": 153894, "pct": 18.63, "color": "#64748b"},
            {"label": "Simétrico METRO → BUS", "cards": 131651, "pct": 15.94, "color": "#10b981"},
            {"label": "Retorno iniciado en BUS", "cards": 110250, "pct": 13.35, "color": "#38bdf8"},
        ],
        "intermodal_stations": [
            {"station": "LA CISTERNA", "bm": 18177, "mb": 3180, "gap": 14997, "ret_pct": 17.5},
            {"station": "PLAZA MAIPU", "bm": 13348, "mb": 3212, "gap": 10136, "ret_pct": 24.1},
            {"station": "SAN PABLO", "bm": 11530, "mb": 2306, "gap": 9223, "ret_pct": 20.0},
            {"station": "MANQUEHUE", "bm": 11537, "mb": 2329, "gap": 9208, "ret_pct": 20.2},
            {"station": "HOSP. SOTERO DEL RIO", "bm": 10220, "mb": 1028, "gap": 9192, "ret_pct": 10.1},
            {"station": "LOS DOMINICOS", "bm": 11828, "mb": 2909, "gap": 8920, "ret_pct": 24.6},
            {"station": "LOS LIBERTADORES", "bm": 11573, "mb": 2780, "gap": 8793, "ret_pct": 24.0},
            {"station": "PUDAHUEL", "bm": 10166, "mb": 1688, "gap": 8478, "ret_pct": 16.6},
            {"station": "LAS REJAS", "bm": 11282, "mb": 3370, "gap": 7912, "ret_pct": 29.9},
            {"station": "SANTA ROSA", "bm": 6807, "mb": 576, "gap": 6231, "ret_pct": 8.5},
            {"station": "ESCUELA MILITAR", "bm": 8773, "mb": 3630, "gap": 5143, "ret_pct": 41.4},
        ],
        "temporal_blocks": [
            {"bloque": "Madrugada (00–06)", "t19": 7984, "t23": 4965, "var_pct": -37.81, "s19": 0.35, "s23": 0.29},
            {"bloque": "Punta Mañana (06–09)", "t19": 469244, "t23": 401383, "var_pct": -14.46, "s19": 20.71, "s23": 23.22},
            {"bloque": "Valle Día (09–17)", "t19": 914744, "t23": 709980, "var_pct": -22.38, "s19": 40.38, "s23": 41.08},
            {"bloque": "Punta Tarde (17–20)", "t19": 630461, "t23": 475163, "var_pct": -24.63, "s19": 27.83, "s23": 27.49},
            {"bloque": "Noche (20–24)", "t19": 242915, "t23": 136878, "var_pct": -43.65, "s19": 10.72, "s23": 7.92},
        ],
        "expansion_communes": [
            {"comuna": "INDEPENDENCIA", "group": "L3/L6", "min19": 25.30, "min23": 25.29, "dmin": -0.01, "m19": 50.8, "m23": 67.0, "dm": 16.2},
            {"comuna": "CONCHALI", "group": "L3/L6", "min19": 24.63, "min23": 25.04, "dmin": 0.41, "m19": 35.2, "m23": 49.5, "dm": 14.3},
            {"comuna": "QUILICURA", "group": "L3/L6", "min19": 38.28, "min23": 37.75, "dmin": -0.53, "m19": 16.1, "m23": 29.9, "dm": 13.8},
            {"comuna": "CERRILLOS", "group": "L3/L6", "min19": 31.04, "min23": 30.98, "dmin": -0.05, "m19": 27.4, "m23": 34.7, "dm": 7.3},
            {"comuna": "NUNOA", "group": "L3/L6", "min19": 24.04, "min23": 23.19, "dmin": -0.85, "m19": 53.6, "m23": 57.4, "dm": 3.8},
            {"comuna": "PEDRO AGUIRRE CERDA", "group": "L3/L6", "min19": 26.45, "min23": 25.44, "dmin": -1.01, "m19": 41.5, "m23": 43.6, "dm": 2.1},
            {"comuna": "PUENTE ALTO", "group": "Control", "min19": 37.93, "min23": 39.24, "dmin": 1.31, "m19": 39.8, "m23": 53.4, "dm": 13.6},
            {"comuna": "LA CISTERNA", "group": "Control", "min19": 27.03, "min23": 27.49, "dmin": 0.46, "m19": 61.0, "m23": 74.3, "dm": 13.3},
            {"comuna": "SANTIAGO", "group": "Control", "min19": 23.53, "min23": 22.63, "dmin": -0.90, "m19": 72.2, "m23": 74.5, "dm": 2.3},
            {"comuna": "LA FLORIDA", "group": "Control", "min19": 28.29, "min23": 27.74, "dmin": -0.55, "m19": 52.0, "m23": 52.4, "dm": 0.4},
            {"comuna": "PUDAHUEL", "group": "Control", "min19": 32.61, "min23": 30.69, "dmin": -1.92, "m19": 31.2, "m23": 31.0, "dm": -0.2},
            {"comuna": "MAIPU", "group": "Control", "min19": 34.53, "min23": 34.11, "dmin": -0.42, "m19": 23.9, "m23": 22.7, "dm": -1.2},
            {"comuna": "LAS CONDES", "group": "Control", "min19": 28.87, "min23": 30.30, "dmin": 1.43, "m19": 61.2, "m23": 59.0, "dm": -2.2},
        ],
        "expansion_ml_scenarios": [
            {"name": "Línea 7 (19 est.)", "new_trips": 40178, "upgraded": 32452, "total": 72630, "share_new": 71.66, "gain_pp": 1.26, "hrs_saved": 1527},
            {"name": "Línea 8 (14 est.)", "new_trips": 20775, "upgraded": 20258, "total": 41033, "share_new": 71.05, "gain_pp": 0.65, "hrs_saved": 500},
            {"name": "Línea 9 (19 est.)", "new_trips": 11375, "upgraded": 12284, "total": 23659, "share_new": 70.76, "gain_pp": 0.36, "hrs_saved": 352},
            {"name": "Combinado L7+L8+L9", "new_trips": 72184, "upgraded": 65263, "total": 137447, "share_new": 72.67, "gain_pp": 2.26, "hrs_saved": 2445},
        ],
        "expansion_ml_communes": [
            {"comuna": "VITACURA", "gain_pp": 14.3, "benefited": 12249, "dist_drop": 1.67},
            {"comuna": "CERRO NAVIA", "gain_pp": 12.6, "benefited": 8458, "dist_drop": 1.65},
            {"comuna": "QUINTA NORMAL", "gain_pp": 9.1, "benefited": 6954, "dist_drop": 0.40},
            {"comuna": "LA PINTANA", "gain_pp": 8.5, "benefited": 3870, "dist_drop": 1.55},
            {"comuna": "SAN JOAQUIN", "gain_pp": 5.3, "benefited": 4288, "dist_drop": 0.26},
            {"comuna": "MACUL", "gain_pp": 4.9, "benefited": 9439, "dist_drop": 0.19},
            {"comuna": "NUNOA", "gain_pp": 4.6, "benefited": 13899, "dist_drop": 0.07},
            {"comuna": "SANTIAGO", "gain_pp": 2.7, "benefited": 23818, "dist_drop": 0.01},
            {"comuna": "PROVIDENCIA", "gain_pp": 1.8, "benefited": 10401, "dist_drop": 0.04},
            {"comuna": "LA FLORIDA", "gain_pp": 1.8, "benefited": 5154, "dist_drop": 0.12},
        ],
    }

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
        "metro_lines_gtfs": metro_lines_gtfs,
        "future_lines": future_lines,
        "stop_hubs": stop_hubs,
        "half_hour_curves": _clean_records(half_hour_df),
        "od_flows": _clean_records(od_flows_df),
        "metro_2023_profiles": metro_2023_profiles,
        "eda": {
            "h1_time_decomp": h1_time_decomp,
            "h1_dropoff": _clean_records(h1_dropoff_df),
            "h1_chaining": _clean_records(h1_chaining_df),
            "h2_temporal": _clean_records(h2_temporal_df),
            "h3_station_friction": h3_station_friction,
            "h4_l3l6": _clean_records(h4_l3l6_df),
        },
        "full_week": full_week_metrics,
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

    /* EDA & 4 Hypotheses Section */
    .eda-section {
      padding: 6px 20px 36px;
      border-top: 2px solid var(--border);
      background: linear-gradient(180deg, #0b0f19 0%, #0f172a 100%);
    }
    .eda-banner {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      flex-wrap: wrap;
      gap: 10px;
      padding: 18px 0 14px;
    }
    .eda-banner h2 { font-size: 18px; font-weight: 800; color: var(--text); }
    .eda-banner p { font-size: 12px; color: var(--muted); max-width: 780px; margin-top: 3px; }
    .eda-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 16px;
    }
    @media (max-width: 1100px) { .eda-grid { grid-template-columns: 1fr; } }
    .hyp-card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .hyp-head {
      padding: 12px 16px;
      background: rgba(30, 41, 59, 0.75);
      border-bottom: 1px solid var(--border);
    }
    .hyp-tag {
      display: inline-block;
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      padding: 2px 7px;
      border-radius: 4px;
      margin-bottom: 5px;
    }
    .hyp-head h3 { font-size: 14px; font-weight: 700; color: var(--text); }
    .hyp-head p { font-size: 11.5px; color: var(--muted); margin-top: 4px; }
    .hyp-chart { width: 100%; height: 290px; padding: 4px 8px; }
    .hyp-foot {
      padding: 10px 16px;
      background: rgba(56, 189, 248, 0.06);
      border-top: 1px solid var(--border);
      font-size: 11.5px;
      color: #cbd5e1;
    }
    .hyp-foot b { color: var(--accent); }

    /* Top Navigation Tabs */
    .tab-nav {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      padding: 10px 20px;
      background: #0f172a;
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 1500;
    }
    .tab-btn {
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 700;
      background: var(--panel);
      color: var(--muted);
      border: 1px solid var(--border);
      transition: all 0.15s;
    }
    .tab-btn:hover { color: var(--text); border-color: var(--accent); }
    .tab-btn.active {
      background: linear-gradient(135deg, #0284c7 0%, #2563eb 100%);
      color: #ffffff;
      border-color: #38bdf8;
      box-shadow: 0 2px 10px rgba(56, 189, 248, 0.25);
    }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }

    .prose-wrap { padding: 18px 24px 36px; max-width: 1460px; margin: 0 auto; }
    .prose-hero {
      background: linear-gradient(135deg, rgba(30, 41, 59, 0.9) 0%, rgba(15, 23, 42, 0.95) 100%);
      border: 1px solid var(--border);
      border-left: 4px solid var(--accent);
      border-radius: 10px;
      padding: 18px 22px;
      margin-bottom: 16px;
    }
    .prose-hero h2 { font-size: 19px; font-weight: 800; margin-bottom: 6px; }
    .prose-hero p { font-size: 13px; color: #cbd5e1; max-width: 980px; }
    .findings-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
      margin-top: 16px;
    }
    .finding-box {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px 16px;
    }
    .finding-box h4 { font-size: 12.5px; color: var(--accent); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
    .finding-box ul { padding-left: 16px; color: #cbd5e1; font-size: 12.5px; display: flex; flex-direction: column; gap: 6px; }
    .callout-warn {
      background: rgba(245, 158, 11, 0.1);
      border: 1px solid rgba(245, 158, 11, 0.4);
      border-radius: 8px;
      padding: 12px 16px;
      margin-bottom: 16px;
      font-size: 12.5px;
      color: #fde68a;
    }
  </style>
</head>
<body>

<header>
  <div class="brand">
    <span class="brand-badge">METRO / RED OD</span>
    <div>
      <h1>Santiago Origin–Destination &amp; Strategic Analytics Workbench (Abril 2019 vs. Abril 2023)</h1>
      <p>Full-week ADATRAP exploration (~20.7 GB), multi-modal map (Metro, Bus, Zona Paga, Metrotren) &amp; 3 Deep-Dive Hypotheses for Metro de Santiago</p>
    </div>
  </div>
  <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
    <a href="./informe_metro_desafio.pdf" target="_blank" style="background:var(--metro); color:#fff; text-decoration:none; padding:6px 12px; border-radius:6px; font-weight:700; font-size:12px;">📄 Informe PDF (2 págs)</a>
    <button id="btn-gmap-key" title="Switch between Leaflet Dark Map and Google Maps JS API">🗺️ Google Maps API</button>
    <button id="btn-reset" style="background:#334155;">↺ Reset Map Filters</button>
  </div>
</header>

<nav class="tab-nav" id="main-tabs">
  <button class="tab-btn active" data-tab="tab-map">🗺️ Mapa Multi-Modal OD &amp; Red L1–L9</button>
  <button class="tab-btn" data-tab="tab-expansion">⭐ ML Líneas 7, 8 y 9 + DiD L3/L6</button>
  <button class="tab-btn" data-tab="tab-intermodal">🅰️ ML Fuga Alimentador PM</button>
  <button class="tab-btn" data-tab="tab-temporal">🅱️ Noche (-43.7%) &amp; Viernes Híbrido</button>
  <button class="tab-btn" data-tab="tab-dataset">📋 Dataset &amp; Metodología (44.43M viajes)</button>
</nav>

<div id="tab-map" class="tab-panel active">
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
      <div style="display:flex; gap:10px; align-items:center; font-size:11px; flex-wrap:wrap;">
        <label><input type="checkbox" id="chk-arcs" checked /> Arcos OD (Comunas)</label>
        <label><input type="checkbox" id="chk-hubs" checked /> Estaciones / Paraderos</label>
        <label><input type="checkbox" id="chk-communes" checked /> Centroides Comuna</label>
        <label><input type="checkbox" id="chk-gtfs" checked /> Red Metro L1–L6</label>
        <label><input type="checkbox" id="chk-future" checked /> Futuras L7–L9</label>
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
        <div class="legend-row"><span class="dot" style="background:#38bdf8; border:2px solid #fff;"></span> <b>Centroide Comuna</b> (Origen/Fin de Arcos OD)</div>
        <div class="legend-row"><span style="display:inline-block;width:14px;border-top:2px dashed #06b6d4;margin-right:4px;"></span> <b>Futuras L7 / L8 / L9</b> (Simulación ML)</div>
        <div style="margin-top:6px; color:var(--muted); font-size:10px;">Arcos curvos = Flujo Comuna↔Comuna | Líneas = Red Metro</div>
      </div>
    </div>
    <div class="insight-box">
      <h3>📊 Resumen Dinámico de la Selección Actual</h3>
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
</div> <!-- /#tab-map -->

<!-- ==================== TAB 2: DATASET & METODOLOGÍA ==================== -->
<div id="tab-dataset" class="tab-panel">
  <div class="prose-wrap">
    <div class="prose-hero">
      <h2>📋 Descripción del Dataset DTPM / ADATRAP &amp; Metodología de Limpieza (~20.7 GB)</h2>
      <p>Los datos provienen del algoritmo <b>ADATRAP</b> del Directorio de Transporte Público Metropolitano (DTPM), que estima destinos (bajadas) a partir de validaciones consecutivas de tarjetas <b>bip!</b> y GPS de buses. El repositorio analiza <b>26 archivos (~20.7 GB)</b> comparando abril de 2019 (pre-pandemia) y abril de 2023 (post-pandemia), enriquecidos con la red oficial <b>GTFS</b>.</p>
    </div>

    <div class="callout-warn" style="background:rgba(16,185,129,0.1); border-color:rgba(16,185,129,0.4); color:#a7f3d0;">
      <b>✅ Cobertura Completa 7 Días vs. 7 Días (44.43 Millones de Viajes sin muestreo):</b> Ambos años cubren exactamente <b>7 días completos</b>: <b>Abril 2019 (08–14 Abr, <code>25,837,746</code> viajes)</b> y <b>Abril 2023 (17–23 Abr, <code>18,597,410</code> viajes)</b>, permitiendo paridad exacta en <b>5 días laborales (<code>-28.0%</code>)</b>, <b>Sábado (<code>-28.3%</code>)</b> y <b>Domingo (<code>-27.3%</code>)</b>.
    </div>

    <div class="eda-grid">
      <div class="hyp-card">
        <div class="hyp-head">
          <span class="hyp-tag" style="background:rgba(56,189,248,0.2); color:#7dd3fc;">Cobertura &amp; Observabilidad (100% Población)</span>
          <h3>Volumen Diario Promedio y Tasa de Viajes Sin Bajada Estimada</h3>
          <p>Comparación exacta de filas diarias expandidas y porcentaje de viajes donde ADATRAP no logra estimar destino (<code>ultimaetapaconbajada = 0</code>), que sube hasta 40.8% en domingo.</p>
        </div>
        <div id="chart-ds-coverage" class="hyp-chart"></div>
      </div>

      <div class="hyp-card">
        <div class="hyp-head">
          <span class="hyp-tag" style="background:rgba(16,185,129,0.2); color:#6ee7b7;">Inventario de Tablas &amp; Arquitectura de Datos</span>
          <h3>Estructura de los 20.7 GB Procesados con DuckDB + Polars</h3>
          <p>Procesamiento <i>out-of-core</i> en disco sin saturar RAM, armonizando esquemas incompatibles entre 2019 y 2023.</p>
        </div>
        <div class="table-wrap" style="max-height:290px;">
          <table>
            <thead>
              <tr><th>Tabla / Fuente</th><th>Año</th><th>Tamaño</th><th>Cols</th><th>Observaciones de Limpieza</th></tr>
            </thead>
            <tbody>
              <tr><td><b>viajes201904*.csv (7 archivos)</b></td><td>2019</td><td>12.9 GB</td><td>107</td><td>Delimitador <code>;</code>, 7 días completos (25.84M filas), comunas truncadas a 8 chars (<code>LA FLORI</code>).</td></tr>
              <tr><td><b>etapas20190408_transparencia.csv</b></td><td>2019</td><td>1.31 GB</td><td>29</td><td>Coordenadas UTM 19S (metros) para Bus y Metro; nulos como <code>-</code>.</td></tr>
              <tr><td><b>viajes_042023_17al23.csv</b></td><td>2023</td><td>7.49 GB</td><td>101</td><td>Delimitador <code>,</code> + coma final (<code>Unnamed: 100</code>), 7 días (18.6M filas), códigos enteros + <code>id_tarjeta</code>.</td></tr>
              <tr><td><b>etapas_042023_17al23.csv</b></td><td>2023</td><td>9.41 GB</td><td>32</td><td>UTF-8 BOM, CRS mezclado: Bus en UTM (m) y Metro en WGS84 (lat/lon) en la misma columna.</td></tr>
              <tr><td><b>Matriz_sub / baj_SS_MH.xlsb</b></td><td>2023</td><td>13.6 MB</td><td>57 / 55</td><td>Libros binarios Excel; 48 columnas de media hora por parada/estación (65k–68k filas).</td></tr>
              <tr><td><b>MatrizOD 2019 (4 CSVs)</b></td><td>2019</td><td>218 MB</td><td>3–13</td><td>Delimitador mixto <code>|;</code> en tablas de subidas/bajadas corregido por parser.</td></tr>
              <tr><td><b>GTFS Red Movilidad (12 TXT)</b></td><td>GTFS</td><td>70 MB</td><td>3–10</td><td><code>stops.txt</code> (18.5k paradas), <code>routes.txt</code> (L1–L6, Metrotren), <code>pathways.txt</code> (7.4k accesos).</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="findings-grid">
      <div class="finding-box">
        <h4>🔧 1. Homologación de Esquema 2019 ↔ 2023</h4>
        <ul>
          <li>Solo 10 de ~100 columnas de <code>viajes</code> compartían nombre exacto entre 2019 y 2023; se construyó una proyección canónica de 32 columnas.</li>
          <li>Los 34 códigos numéricos de comuna y 12 de periodo en <code>viajes_2023</code> se decodificaron mediante cruce exacto con <code>etapas_2023</code> vía <code>Zona 777</code> y media hora.</li>
        </ul>
      </div>
      <div class="finding-box">
        <h4>⏱️ 2. Trampa Metodológica Resuelta: tviaje vs tviaje2</h4>
        <ul>
          <li>En 2019, <code>tviaje_min</code> mide <code>tiempobajada - tiemposubida</code>. En 2023, <code>tviaje</code> fue anulado (<code>-</code>) y reemplazado por <code>tviaje2</code>, que <b>suma</b> acceso (<code>entrada</code>), espera inicial (<code>te0</code>) y egreso.</li>
          <li>Para comparaciones causales estrictas entre 2019 y 2023, se calculó <code>EPOCH(t_fin - t_inicio) / 60.0</code> idéntico en ambos años.</li>
        </ul>
      </div>
      <div class="finding-box">
        <h4>📍 3. Corrección Geoespacial &amp; Outliers</h4>
        <ul>
          <li>Se separaron coordenadas UTM 19S (<code>EPSG:32719</code>) de WGS84 (<code>EPSG:4326</code>) mezcladas en 2023 y se proyectaron con <code>pyproj</code>.</li>
          <li>Se enmascararon distancias negativas (<code>-20,601 m</code>) y errores de <code>~6.31M m</code> originados por coordenadas faltantes <code>(0, 0)</code> en UTM.</li>
        </ul>
      </div>
    </div>
  </div>
</div>

<!-- ==================== TAB 3: INTERMODALIDAD PM ==================== -->
<div id="tab-intermodal" class="tab-panel">
  <div class="prose-wrap">
    <div class="prose-hero" style="border-left-color:var(--metro);">
      <h2>🅰️ Fuga de Retorno en Bus Alimentador &amp; Sesgo en la Matriz OD</h2>
      <p><b>Hipótesis:</b> Los usuarios intermodales que validan <code>BUS → METRO</code> en la mañana omiten masivamente validar su bus alimentador de regreso (<code>METRO → BUS</code>) en la tarde debido a la penalidad de espera (<b>7.70 min</b> vs 3.15 min al entrar a Metro), evasión en superficie o fuga a colectivos. Esto genera una <b>asimetría estructural de 2.6×</b> que distorsiona la matriz OD al sobre-asignar estaciones terminales como "destino final".</p>
    </div>

    <div class="kpi-grid" style="padding:0 0 14px;">
      <div class="kpi-card">
        <div class="kpi-title">Asimetría Diaria 2023 (Laboral)</div>
        <div class="kpi-val" style="color:var(--metro);">2.58×</div>
        <div class="kpi-sub"><span>318.8k Bus→Metro vs 123.8k Metro→Bus</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Prueba Tarjeta bip! (826k tarjetas-día)</div>
        <div class="kpi-val" style="color:var(--zp);">32.6% vs 15.9%</div>
        <div class="kpi-sub"><span>Retornan SOLO METRO vs Simétrico M→B</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Penalidad de Tiempo en Retorno</div>
        <div class="kpi-val">+6.11 min</div>
        <div class="kpi-sub"><span>47.72 min (M→B) vs 41.61 min (B→M)</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Viajes M→B Sin Bajada Estimada</div>
        <div class="kpi-val">43.7%</div>
        <div class="kpi-sub"><span>vs 15.8% en Bus→Metro (2023)</span></div>
      </div>
    </div>

    <div class="eda-grid">
      <div class="hyp-card">
        <div class="hyp-head">
          <span class="hyp-tag" style="background:rgba(239,68,68,0.2); color:#fca5a5;">Evidencia 1 · Descomposición de Tiempo (10.75M Viajes 2023)</span>
          <h3>¿Dónde se pierden los +8.0 min al volver en Metro → Bus?</h3>
          <p>Al salir del Metro hacia el bus alimentador, la espera de trasbordo (<code>te1</code>) salta de <b>3.15 min</b> a <b>7.70 min (+144%)</b>. En <code>Metrotren → Metro</code>, la caminata (<code>tc1</code>) consume <b>10.96 min</b>.</p>
        </div>
        <div id="chart-inter-decomp" class="hyp-chart"></div>
      </div>

      <div class="hyp-card">
        <div class="hyp-head">
          <span class="hyp-tag" style="background:rgba(245,158,11,0.2); color:#fcd34d;">Evidencia 2 · Seguimiento Individual por id_tarjeta (2023)</span>
          <h3>¿Cómo regresan en la tarde los 826,099 viajeros matutinos Bus → Metro?</h3>
          <p>Más del doble regresa validando <b>SOLO METRO (32.6%)</b> respecto a quienes validan el retorno simétrico <b>METRO → BUS (15.9%)</b>, confirmando no-validación del último tramo en bus.</p>
        </div>
        <div id="chart-inter-cards" class="hyp-chart"></div>
      </div>

      <div class="hyp-card" style="grid-column: 1 / -1;">
        <div class="hyp-head">
          <span class="hyp-tag" style="background:rgba(56,189,248,0.2); color:#7dd3fc;">Evidencia 3 · Estaciones Prioritarias para Metro de Santiago (2023 Laboral)</span>
          <h3>Top 11 Estaciones Terminales con Mayor Brecha Diaria (Bus → Metro vs. Metro → Bus)</h3>
          <p>En <b>La Cisterna</b> faltan casi <b>15,000 validaciones diarias</b> de bus de retorno (solo 17.5% valida el bus de salida); en <b>Hospital Sótero del Río</b> solo 10.1% y en <b>Santa Rosa</b> 8.5%.</p>
        </div>
        <div id="chart-inter-stations" class="hyp-chart" style="height:310px;"></div>
      </div>
    </div>

    <div class="findings-grid">
      <div class="finding-box">
        <h4>✅ Veredicto de la Hipótesis</h4>
        <ul>
          <li><b>Validada con prueba causal a nivel de tarjeta:</b> La brecha de 2.6× no es un error aleatorio; se mantiene idéntica en 2019 (2.61×) y 2023 (2.58×) y queda demostrada al rastrear <code>id_tarjeta</code>.</li>
        </ul>
      </div>
      <div class="finding-box">
        <h4>🚇 Impacto Directo para Metro de Santiago</h4>
        <ul>
          <li><b>Corrección de Matriz OD:</b> Sin corregir este sesgo, las zonas aledañas a terminales (<code>La Cisterna, Plaza Maipú, San Pablo</code>) aparecen artificialmente sobre-representadas como destino final vespertino.</li>
          <li><b>Inversión en EIM / Zonas Pagas Cerradas:</b> Integrar torniquetes de trasbordo cerrado en las top 6 estaciones captura &gt;60,000 trasbordos diarios hoy invisibles.</li>
        </ul>
      </div>
    </div>
  </div>
</div>

<!-- ==================== TAB 4: DEMANDA HORARIA ==================== -->
<div id="tab-temporal" class="tab-panel">
  <div class="prose-wrap">
    <div class="prose-hero" style="border-left-color:var(--accent);">
      <h2>🅱️ Compresión de Puntas &amp; Contracción Nocturna Post-Pandemia (2019 vs. 2023)</h2>
      <p><b>Hipótesis:</b> La recuperación de demanda de Metro tras la pandemia no fue uniforme durante el día: en los 5 días laborales completos, la franja <b>nocturna (20:00–24:00) sufrió un desplome estructural de -43.65%</b> (-106,037 viajes/día), 3× más severo que Punta Mañana (-14.46%), reconfigurando la programación óptima de trenes y ventanas de mantenimiento.</p>
    </div>

    <div class="kpi-grid" style="padding:0 0 14px;">
      <div class="kpi-card">
        <div class="kpi-title">Demanda Diaria Metro (5 Laborales)</div>
        <div class="kpi-val">1.73M <span style="font-size:13px; color:#f87171;">(-23.7%)</span></div>
        <div class="kpi-sub"><span>vs 2.27M viajes/día en Abril 2019</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Desplome Bloque Noche (20:00–24:00)</div>
        <div class="kpi-val" style="color:var(--metro);">-43.65%</div>
        <div class="kpi-sub"><span>-106,037 viajes/día (243k → 137k)</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Resiliencia Punta Mañana (06–09)</div>
        <div class="kpi-val" style="color:var(--accent);">-14.46%</div>
        <div class="kpi-sub"><span>Sube participación: 20.71% → 23.22%</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Participación Valle Día (09–17)</div>
        <div class="kpi-val" style="color:var(--bus);">41.08%</div>
        <div class="kpi-sub"><span>+0.70 pp respecto a 2019 (40.38%)</span></div>
      </div>
    </div>

    <div class="eda-grid">
      <div class="hyp-card">
        <div class="hyp-head">
          <span class="hyp-tag" style="background:rgba(56,189,248,0.2); color:#7dd3fc;">Evidencia 1 · Volumen Diario Expandido (100% Laboral)</span>
          <h3>Viajes Diarios Promedio Iniciados en Metro por Bloque Horario (2019 vs. 2023)</h3>
          <p>Comparación normalizada por día laboral (<code>factor_expansion / n_días</code>). La mayor contracción relativa se concentra en Madrugada (-47.3%) y Noche (-38.4%).</p>
        </div>
        <div id="chart-temp-vol" class="hyp-chart"></div>
      </div>

      <div class="hyp-card">
        <div class="hyp-head">
          <span class="hyp-tag" style="background:rgba(239,68,68,0.2); color:#fca5a5;">Evidencia 2 · Redistribución de Participación Horaria (Δ pp)</span>
          <h3>Cambio en la Estructura del Día: Ganancia en Mañana/Valle vs. Pérdida Nocturna</h3>
          <p>Punta Mañana (+1.56 pp) y Valle Día (+1.39 pp) concentran casi 3 puntos porcentuales más de la demanda total diaria que en 2019, mientras Noche cae -1.93 pp.</p>
        </div>
        <div id="chart-temp-share" class="hyp-chart"></div>
      </div>
    </div>

    <div class="findings-grid">
      <div class="finding-box">
        <h4>✅ Veredicto de la Hipótesis</h4>
        <ul>
          <li><b>Validada con matiz clave:</b> Al analizar el 100% de la semana laboral, el bloque nocturno (20:00–24:00) cayó <b>-38.4%</b> (-85.2k viajes/día), confirmando un vaciamiento estructural vespertino/nocturno.</li>
          <li>A diferencia de muestras pequeñas, en el total semanal <b>Punta Tarde (17–20)</b> cayó -25.7%, reflejando un regreso más distribuido hacia la tarde-valle (14:00–17:00).</li>
        </ul>
      </div>
      <div class="finding-box">
        <h4>⚡ Impacto Operacional para Metro de Santiago</h4>
        <ul>
          <li><b>Optimización de Oferta y Energía:</b> Ajustar intervalos o acortar formaciones de trenes desde las 20:30 permite ahorros directos en energía de tracción sin deteriorar el nivel de servicio.</li>
          <li><b>Ventanas de Mantenimiento:</b> Libera holgura operacional nocturna y focaliza campañas comerciales/de seguridad si Metro busca recuperar los 85,000 viajes nocturnos perdidos.</li>
        </ul>
      </div>
    </div>
  </div>
</div>

<!-- ==================== TAB 5: EXPANSIÓN LÍNEAS 7, 8 Y 9 + DiD L3/L6 ==================== -->
<div id="tab-expansion" class="tab-panel">
  <div class="prose-wrap">
    <div class="prose-hero" style="border-left-color:var(--bus);">
      <h2>⭐ Impacto Causal L3/L6 (DiD) &amp; Predicción ML Espacial de Líneas 7, 8 y 9</h2>
      <p><b>Hipótesis:</b> Validando primero la maduración ex-post de <b>Líneas 3 y 6</b> (<b>+7.09 pp DiD</b> sobre 16.71M viajes laborales, z = -16.8), entrenamos modelos <b>ML espaciales (GBDT + Ridge)</b> sobre <b>131,400 viajes OD</b> (792 zonas DTPM × 126 estaciones GTFS) para predecir <b>dos canales</b> en las futuras <b>Líneas 7, 8 y 9</b>: captura modal (<code>Bus → Metro</code>) y eliminación de bus alimentador (<code>Intermodal → Metro Directo</code>).</p>
    </div>

    <div class="kpi-grid" style="padding:0 0 14px;">
      <div class="kpi-card">
        <div class="kpi-title">Viajes Beneficiados L7+L8+L9</div>
        <div class="kpi-val" style="color:var(--bus);">137,447 / día</div>
        <div class="kpi-sub"><span>+72.2k nuevos Metro + 65.3k Directos</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">ROC-AUC GBDT Espacial (Holdout)</div>
        <div class="kpi-val" style="color:var(--accent);">0.882 / 0.962</div>
        <div class="kpi-sub"><span>Elección Metro / Metro Directo | Ridge R² 0.640</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Ahorro Tiempo Social (L7+L8+L9)</div>
        <div class="kpi-val" style="color:var(--zp);">2,445 h / día</div>
        <div class="kpi-sub"><span>~660,000 horas-pasajero ahorradas / año</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Validación Ex-Post DiD L3/L6</div>
        <div class="kpi-val">+7.09 pp</div>
        <div class="kpi-sub"><span>16.71M viajes · z = -16.8 · -0.039 etapas</span></div>
      </div>
    </div>

    <div class="eda-grid">
      <div class="hyp-card">
        <div class="hyp-head">
          <span class="hyp-tag" style="background:rgba(16,185,129,0.2); color:#6ee7b7;">Simulación ML Contrafactual · 3.19M Viajes Laborales/Día</span>
          <h3>Proyección Diaria por Línea: Nuevos Usuarios Metro + Upgrades a Metro Directo</h3>
          <p><b>Línea 7</b> concentra el <b>52.8%</b> del impacto total (72,630 beneficiados/día y 1,527 h-pasajero/día), seguida por <b>Línea 8</b> (41,033/día) y <b>Línea 9</b> (23,659/día).</p>
        </div>
        <div id="chart-exp-l789" class="hyp-chart" style="height:310px;"></div>
      </div>

      <div class="hyp-card">
        <div class="hyp-head">
          <span class="hyp-tag" style="background:rgba(56,189,248,0.2); color:#7dd3fc;">Equidad Territorial · Top 10 Comunas Beneficiadas (L7+L8+L9)</span>
          <h3>Salto Proyectado en Cuota Modal Metro (Δ pp) y Reducción de Distancia a Estación</h3>
          <p>Desbloqueo simultáneo de comunas sin Metro actual: <b>Vitacura (+14.3 pp, -1.67 km)</b>, <b>Cerro Navia (+12.6 pp, -1.65 km)</b>, <b>Quinta Normal (+9.1 pp)</b> y <b>La Pintana (+8.5 pp, -1.55 km)</b>.</p>
        </div>
        <div id="chart-exp-communes-ml" class="hyp-chart" style="height:310px;"></div>
      </div>

      <div class="hyp-card">
        <div class="hyp-head">
          <span class="hyp-tag" style="background:rgba(245,158,11,0.2); color:#fcd34d;">Evidencia Ex-Post · Maduración L3/L6 (2019 → 2023)</span>
          <h3>Salto en Participación de Metro (%): Comunas L3/L6 vs. Comunas Control</h3>
          <p>Las comunas del corredor norte de L3 lideraron la captura modal: <b>Independencia (+16.2 pp)</b>, <b>Conchalí (+14.3 pp)</b> y <b>Quilicura (+13.8 pp)</b>.</p>
        </div>
        <div id="chart-exp-metro" class="hyp-chart" style="height:300px;"></div>
      </div>

      <div class="hyp-card">
        <div class="hyp-head">
          <span class="hyp-tag" style="background:rgba(168,85,247,0.2); color:#d8b4fe;">Validación Ex-Post · Tiempo Comparable (EPOCH)</span>
          <h3>Duración Observada Puerta a Puerta (2019 vs. 2023) por Comuna</h3>
          <p>Usando <code>EPOCH(t_fin - t_inicio)</code> idéntico en ambos años, L3/L6 redujo el tiempo medio (-0.41 min DiD) mientras Puente Alto y Las Condes empeoraron.</p>
        </div>
        <div id="chart-exp-time" class="hyp-chart" style="height:300px;"></div>
      </div>
    </div>

    <div class="findings-grid">
      <div class="finding-box">
        <h4>🤖 Métricas Out-of-Sample (80/20 Estratificado, 131,400 viajes)</h4>
        <ul>
          <li><b>1A. Elección Metro (GBDT):</b> <code>ROC-AUC 0.8817</code> · <code>PR-AUC 0.9355</code> · <code>F1 0.8949</code> · <code>Brier 0.1136</code> (supera Reg. Logística <code>AUC 0.8595</code>).</li>
          <li><b>1B. Metro Directo | Usa Metro (GBDT):</b> <code>ROC-AUC 0.9617</code> · <code>PR-AUC 0.9707</code> · <code>F1 0.9590</code> · <code>Brier 0.0456</code>.</li>
          <li><b>2. Tiempo de Viaje (Ridge):</b> <code>MAE 7.44 min</code> · <code>RMSE 12.28 min</code> · <code>R² 0.6401</code> (supera GBDT <code>R² 0.5553</code> en extrapolación lineal).</li>
        </ul>
      </div>
      <div class="finding-box">
        <h4>📈 Impacto Estratégico para Metro de Santiago</h4>
        <ul>
          <li><b>~19.5 millones</b> de nuevos viajes Metro/año y <b>~17.6 millones</b> de viajes intermodales mejorados a Metro Directo (solo desde demanda <code>bip!</code> actual, antes de captura desde auto).</li>
          <li><b>Refuerzo de combinaciones:</b> Santiago (+23.8k/d), Ñuñoa (+13.9k/d) y Macul (+9.4k/d) requerirán dimensionamiento prioritario de andenes y accesos.</li>
        </ul>
      </div>
    </div>
  </div>
</div>

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
  showGtfs: true,
  showFuture: true,
  useGoogleMaps: false,
  gmapKey: localStorage.getItem("METRO_OD_GMAP_KEY") || "__DEFAULT_GMAP_KEY__"
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
const gtfsLayer = L.layerGroup().addTo(leafletMap);
const futureLayer = L.layerGroup().addTo(leafletMap);

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
  gtfsLayer.clearLayers();
  futureLayer.clearLayers();
  if (state.useGoogleMaps && gmap) clearGmapOverlays();

  // 2a. Real GTFS Metro Network (L1-L6)
  if (state.showGtfs && DATA.metro_lines_gtfs) {
    DATA.metro_lines_gtfs.forEach(line => {
      if (!line.points || line.points.length < 2) return;
      const lp = L.polyline(line.points, {
        color: line.color,
        weight: 3.2,
        opacity: 0.72
      }).bindTooltip(`<b>Metro ${line.name}</b> (Trazado oficial GTFS)`, { sticky: true });
      gtfsLayer.addLayer(lp);

      if (state.useGoogleMaps && gmap && window.google) {
        const gp = new google.maps.Polyline({
          path: line.points.map(p => ({ lat: p[0], lng: p[1] })),
          geodesic: true,
          strokeColor: line.color,
          strokeOpacity: 0.75,
          strokeWeight: 3.5,
          map: gmap
        });
        gmapOverlays.push(gp);
      }
    });
  }

  // 2b. Future Proposed Metro Lines (L7, L8, L9)
  if (state.showFuture && DATA.future_lines) {
    DATA.future_lines.forEach(fl => {
      const coords = fl.stations.map(s => [s[1], s[2]]);
      const fp = L.polyline(coords, {
        color: fl.color,
        weight: 3.0,
        dashArray: "7, 6",
        opacity: 0.9
      }).bindTooltip(`<b>${fl.name}</b> (Simulación Contrafactual ML)`, { sticky: true });
      futureLayer.addLayer(fp);

      fl.stations.forEach(s => {
        const stDot = L.circleMarker([s[1], s[2]], {
          radius: 3.5,
          fillColor: fl.color,
          color: "#0b0f19",
          weight: 1,
          fillOpacity: 0.95
        }).bindTooltip(`<b>${s[0]}</b><br/>${fl.name}`);
        futureLayer.addLayer(stDot);

        if (state.useGoogleMaps && gmap && window.google) {
          const gDot = new google.maps.Marker({
            position: { lat: s[1], lng: s[2] },
            map: gmap,
            title: `${s[0]} — ${fl.name}`,
            icon: {
              path: google.maps.SymbolPath.CIRCLE,
              scale: 4,
              fillColor: fl.color,
              fillOpacity: 0.95,
              strokeColor: "#0b0f19",
              strokeWeight: 1
            }
          });
          gmapOverlays.push(gDot);
        }
      });

      if (state.useGoogleMaps && gmap && window.google) {
        const dashSymbol = {
          path: "M 0,-1 0,1",
          strokeOpacity: 0.95,
          strokeWeight: 3,
          scale: 3,
          strokeColor: fl.color
        };
        const gfp = new google.maps.Polyline({
          path: coords.map(p => ({ lat: p[0], lng: p[1] })),
          strokeOpacity: 0,
          icons: [{ icon: dashSymbol, offset: "0", repeat: "14px" }],
          map: gmap
        });
        gmapOverlays.push(gfp);
      }
    });
  }

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
        radius: isSelected ? 8 : 5,
        fillColor: isSelected ? "#38bdf8" : "#cbd5e1",
        color: "#0f172a",
        weight: 1.8,
        fillOpacity: isSelected ? 1.0 : 0.85
      }).bindTooltip(`<b>Centroide Comuna: ${c.name}</b><br/>Origen/Fin de Arcos OD (Click para filtrar)`);
      cm.on("click", () => {
        state.commune = state.commune === c.name ? "ALL" : c.name;
        selCommune.value = state.commune;
        renderDashboard();
      });
      communesLayer.addLayer(cm);

      if (state.useGoogleMaps && gmap && window.google) {
        const gCentroid = new google.maps.Marker({
          position: { lat: c.lat, lng: c.lon },
          map: gmap,
          title: `Centroide Comuna: ${c.name} (Origen/Fin de Arcos OD — Click para filtrar)`,
          zIndex: isSelected ? 900 : 500,
          icon: {
            path: google.maps.SymbolPath.CIRCLE,
            scale: isSelected ? 8 : 5.5,
            fillColor: isSelected ? "#38bdf8" : "#e2e8f0",
            fillOpacity: 0.95,
            strokeColor: "#0f172a",
            strokeWeight: 2
          }
        });
        gCentroid.addListener("click", () => {
          state.commune = state.commune === c.name ? "ALL" : c.name;
          selCommune.value = state.commune;
          renderDashboard();
        });
        gmapOverlays.push(gCentroid);
      }
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
document.getElementById("chk-gtfs").addEventListener("change", e => { state.showGtfs = e.target.checked; renderDashboard(); });
document.getElementById("chk-future").addEventListener("change", e => { state.showFuture = e.target.checked; renderDashboard(); });
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

function renderEdaHypotheses() {
  if (!DATA.eda || !DATA.full_week) return;
  const fw = DATA.full_week;
  const darkLayout = {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: "#cbd5e1", size: 10.5 },
    legend: { orientation: "h", y: 1.15, x: 0, font: { size: 10 } }
  };

  // TAB 2: Dataset Coverage & Unestimated Destination Chart
  Plotly.react("chart-ds-coverage", [
    {
      x: fw.coverage.map(r => r.label),
      y: fw.coverage.map(r => r.daily_trips / 1e6),
      name: "Viajes Diarios Expandidos (Millones)",
      type: "bar",
      marker: { color: "#38bdf8" },
      text: fw.coverage.map(r => (r.daily_trips / 1e6).toFixed(2) + "M"),
      textposition: "outside",
      yaxis: "y"
    },
    {
      x: fw.coverage.map(r => r.label),
      y: fw.coverage.map(r => r.sin_bajada_pct),
      name: "% Sin Bajada Estimada",
      type: "scatter",
      mode: "lines+markers+text",
      line: { color: "#f59e0b", width: 2.5 },
      marker: { size: 8 },
      text: fw.coverage.map(r => r.sin_bajada_pct.toFixed(1) + "%"),
      textposition: "top center",
      yaxis: "y2"
    }
  ], {
    ...darkLayout,
    margin: { t: 32, r: 52, b: 46, l: 50 },
    yaxis: { title: "Millones viajes / día", range: [0, 5.4], gridcolor: "#1e293b" },
    yaxis2: { title: "% Sin Bajada", overlaying: "y", side: "right", range: [0, 50], showgrid: false }
  }, { displayModeBar: false, responsive: true });

  // TAB 3 (INTERMODAL): 1. Travel Time Decomposition
  const d1 = DATA.eda.h1_time_decomp;
  const labels1 = d1.map(r => `${r.label}<br>(${(r.trips / 1e6).toFixed(2)}M)`);
  const comps = [
    { key: "entrada", name: "Acceso", color: "#38bdf8" },
    { key: "espera1", name: "Espera 1", color: "#fbbf24" },
    { key: "vehiculo1", name: "Vehículo 1", color: "#ef4444" },
    { key: "caminata", name: "Caminata Trasb.", color: "#a855f7" },
    { key: "espera2", name: "Espera Trasb. 2", color: "#f97316" },
    { key: "vehiculo2", name: "Vehículo 2", color: "#10b981" },
    { key: "egreso", name: "Egreso", color: "#64748b" }
  ];
  Plotly.react("chart-inter-decomp", comps.map(c => ({
    y: labels1,
    x: d1.map(r => r[c.key]),
    name: c.name,
    type: "bar",
    orientation: "h",
    marker: { color: c.color }
  })), {
    ...darkLayout,
    barmode: "stack",
    margin: { t: 32, r: 20, b: 36, l: 145 },
    xaxis: { title: "Minutos promedio por viaje (tviaje2)", gridcolor: "#1e293b" },
    yaxis: { autorange: "reversed" }
  }, { displayModeBar: false, responsive: true });

  // TAB 3 (INTERMODAL): 2. Card-Level Afternoon Return Proof (826k cards)
  Plotly.react("chart-inter-cards", [{
    y: fw.intermodal_cards.map(r => r.label),
    x: fw.intermodal_cards.map(r => r.pct),
    type: "bar",
    orientation: "h",
    marker: { color: fw.intermodal_cards.map(r => r.color) },
    text: fw.intermodal_cards.map(r => `${r.pct}% (${Math.round(r.cards / 1000)}k)`),
    textposition: "outside"
  }], {
    ...darkLayout,
    margin: { t: 18, r: 78, b: 36, l: 175 },
    xaxis: { title: "% de tarjetas-día Bus→Metro matutinas", range: [0, 42], gridcolor: "#1e293b" },
    yaxis: { autorange: "reversed" }
  }, { displayModeBar: false, responsive: true });

  // TAB 3 (INTERMODAL): 3. Top 11 Asymmetric Stations
  const stA = fw.intermodal_stations;
  Plotly.react("chart-inter-stations", [
    {
      x: stA.map(r => r.station),
      y: stA.map(r => r.bm),
      name: "Mañana: BUS → METRO / día",
      type: "bar",
      marker: { color: "#38bdf8" }
    },
    {
      x: stA.map(r => r.station),
      y: stA.map(r => r.mb),
      name: "Tarde: METRO → BUS validado / día",
      type: "bar",
      marker: { color: "#ef4444" },
      text: stA.map(r => r.ret_pct + "% ret."),
      textposition: "outside"
    }
  ], {
    ...darkLayout,
    barmode: "group",
    margin: { t: 30, r: 18, b: 68, l: 55 },
    xaxis: { tickangle: -28 },
    yaxis: { title: "Viajes diarios laborales (2023)", gridcolor: "#1e293b" }
  }, { displayModeBar: false, responsive: true });

  // TAB 4 (TEMPORAL): 1. Daily Volume 2019 vs 2023
  const bB = fw.temporal_blocks;
  Plotly.react("chart-temp-vol", [
    {
      x: bB.map(r => r.bloque),
      y: bB.map(r => r.t19 / 1000),
      name: "Abril 2019 (miles / día)",
      type: "bar",
      marker: { color: "#475569" },
      text: bB.map(r => Math.round(r.t19 / 1000) + "k"),
      textposition: "outside"
    },
    {
      x: bB.map(r => r.bloque),
      y: bB.map(r => r.t23 / 1000),
      name: "Abril 2023 (miles / día)",
      type: "bar",
      marker: { color: bB.map(r => r.var_pct < -30 ? "#ef4444" : "#38bdf8") },
      text: bB.map(r => `${Math.round(r.t23 / 1000)}k (${r.var_pct.toFixed(1)}%)`),
      textposition: "outside"
    }
  ], {
    ...darkLayout,
    barmode: "group",
    margin: { t: 32, r: 18, b: 48, l: 52 },
    yaxis: { title: "Miles de viajes Metro / día laboral", range: [0, 1050], gridcolor: "#1e293b" }
  }, { displayModeBar: false, responsive: true });

  // TAB 4 (TEMPORAL): 2. Share Shift (Delta pp)
  Plotly.react("chart-temp-share", [{
    x: bB.map(r => r.bloque),
    y: bB.map(r => +(r.s23 - r.s19).toFixed(2)),
    type: "bar",
    marker: { color: bB.map(r => (r.s23 - r.s19) >= 0 ? "#10b981" : "#ef4444") },
    text: bB.map(r => {
      const d = r.s23 - r.s19;
      return `${d >= 0 ? '+' : ''}${d.toFixed(2)} pp`;
    }),
    textposition: "outside"
  }], {
    ...darkLayout,
    margin: { t: 26, r: 18, b: 48, l: 52 },
    yaxis: { title: "Cambio de participación diaria (Δ pp)", range: [-2.6, 2.3], zeroline: true, zerolinecolor: "#94a3b8", gridcolor: "#1e293b" }
  }, { displayModeBar: false, responsive: true });

  // TAB 5 (EXPANSION): 0A. ML Counterfactual L7, L8, L9 Stacked Bar
  if (fw.expansion_ml_scenarios) {
    const sc = fw.expansion_ml_scenarios;
    Plotly.react("chart-exp-l789", [
      {
        x: sc.map(r => r.name),
        y: sc.map(r => r.new_trips / 1000),
        name: "Nuevos Metro (Bus → Metro)",
        type: "bar",
        marker: { color: "#ef4444" }
      },
      {
        x: sc.map(r => r.name),
        y: sc.map(r => r.upgraded / 1000),
        name: "Upgrade a Metro Directo (Sin Bus Alim.)",
        type: "bar",
        marker: { color: "#14b8a6" },
        text: sc.map(r => `${(r.total / 1000).toFixed(1)}k/d (${r.hrs_saved.toLocaleString()} h)`),
        textposition: "outside"
      }
    ], {
      ...darkLayout,
      barmode: "stack",
      margin: { t: 32, r: 18, b: 48, l: 52 },
      yaxis: { title: "Miles de viajes laborales / día", range: [0, 165], gridcolor: "#1e293b" }
    }, { displayModeBar: false, responsive: true });
  }

  // TAB 5 (EXPANSION): 0B. Top Beneficiary Communes (L7+L8+L9)
  if (fw.expansion_ml_communes) {
    const cm = fw.expansion_ml_communes;
    Plotly.react("chart-exp-communes-ml", [{
      y: cm.map(r => r.comuna),
      x: cm.map(r => r.gain_pp),
      type: "bar",
      orientation: "h",
      marker: { color: cm.map(r => r.dist_drop >= 1.0 ? "#38bdf8" : "#10b981") },
      text: cm.map(r => `+${r.gain_pp.toFixed(1)} pp (${r.benefited.toLocaleString()}/d)`),
      textposition: "outside"
    }], {
      ...darkLayout,
      margin: { t: 16, r: 105, b: 38, l: 105 },
      xaxis: { title: "Ganancia Cuota Modal Metro (pp)", range: [0, 21], gridcolor: "#1e293b" },
      yaxis: { autorange: "reversed" }
    }, { displayModeBar: false, responsive: true });
  }

  // TAB 5 (EXPANSION): 1. Metro Mode Share Jump (L3/L6 vs Control)
  const cC = fw.expansion_communes;
  Plotly.react("chart-exp-metro", [{
    x: cC.map(r => r.comuna),
    y: cC.map(r => r.dm),
    type: "bar",
    marker: { color: cC.map(r => r.group === "L3/L6" ? "#10b981" : "#64748b") },
    text: cC.map(r => `${r.dm >= 0 ? '+' : ''}${r.dm.toFixed(1)}pp`),
    textposition: "outside"
  }], {
    ...darkLayout,
    margin: { t: 24, r: 18, b: 78, l: 50 },
    xaxis: { tickangle: -35 },
    yaxis: { title: "Cambio Participación Metro 2019→2023 (Δ pp)", range: [-4, 22], zeroline: true, zerolinecolor: "#94a3b8", gridcolor: "#1e293b" }
  }, { displayModeBar: false, responsive: true });

  // TAB 5 (EXPANSION): 2. Strictly Comparable Duration (EPOCH 2019 vs 2023)
  Plotly.react("chart-exp-time", [
    {
      x: cC.map(r => r.comuna),
      y: cC.map(r => r.min19),
      name: "Duración 2019 (min)",
      type: "bar",
      marker: { color: "#475569" }
    },
    {
      x: cC.map(r => r.comuna),
      y: cC.map(r => r.min23),
      name: "Duración 2023 (min)",
      type: "bar",
      marker: { color: cC.map(r => r.group === "L3/L6" ? "#10b981" : "#38bdf8") },
      text: cC.map(r => `${r.dmin >= 0 ? '+' : ''}${r.dmin.toFixed(1)}m`),
      textposition: "outside"
    }
  ], {
    ...darkLayout,
    barmode: "group",
    margin: { t: 30, r: 18, b: 78, l: 50 },
    xaxis: { tickangle: -35 },
    yaxis: { title: "Duración observada EPOCH (min)", range: [0, 46], gridcolor: "#1e293b" }
  }, { displayModeBar: false, responsive: true });
}

// Tab Switching Logic
document.querySelectorAll("#main-tabs .tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const targetId = btn.dataset.tab;
    document.querySelectorAll("#main-tabs .tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    const panel = document.getElementById(targetId);
    if (panel) panel.classList.add("active");

    if (targetId === "tab-map") {
      setTimeout(() => {
        leafletMap.invalidateSize();
        Plotly.Plots.resize("chart-halfhour");
        Plotly.Plots.resize("chart-station");
      }, 60);
    } else {
      setTimeout(() => {
        panel.querySelectorAll(".hyp-chart").forEach(el => {
          if (el.id) Plotly.Plots.resize(el.id);
        });
      }, 60);
    }
  });
});

// Initial Render
renderDashboard();
renderStationProfileChart();
renderEdaHypotheses();
if (state.gmapKey) {
  activateGoogleMaps(state.gmapKey);
}
</script>
</body>
</html>
"""


def _resolve_gmap_key() -> str:
    """Resolve Google Maps API key from GOOGLE_MAPS_API_KEY / GMAPS_API_KEY env vars or local .env."""
    for var in ("GOOGLE_MAPS_API_KEY", "GMAPS_API_KEY"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in ("GOOGLE_MAPS_API_KEY", "GMAPS_API_KEY"):
                return v.strip().strip('"').strip("'")
    return ""


def build_html_dashboard(output_path: Path, sample_rows: int = 60_000, gmap_key: Optional[str] = None) -> Path:
    """Extract analytics payload and render the interactive HTML dashboard + data JS."""
    payload = extract_dashboard_data(sample_rows=sample_rows)
    json_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    resolved_key = (gmap_key if gmap_key is not None else _resolve_gmap_key()).replace('"', "")
    data_js_path = output_path.parent / "dashboard_data.js"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_js_path.write_text(f"window.METRO_OD_DATA = {json_str};\n", encoding="utf-8")
    html_content = (
        HTML_TEMPLATE
        .replace("__DASHBOARD_JSON_PAYLOAD__", json_str)
        .replace("__DEFAULT_GMAP_KEY__", resolved_key)
    )
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
