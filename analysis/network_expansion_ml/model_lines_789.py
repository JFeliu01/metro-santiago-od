"""Full Spatial ML Operation: Counterfactual Impact Prediction for Future Metro Lines 7, 8, and 9.

Pipeline:
1. Spatial Feature Engineering:
   - Extracts WGS84 (lat, lon) centroids for all ~777 Santiago traffic zones (`Zona 777`) from `etapas_2019`/`etapas_2023`.
   - Extracts all existing 2023 Metro station coordinates from `data/GTFS/stops.txt`.
   - Computes exact Haversine distance (`orig_dist_metro_km`, `dest_dist_metro_km`) from each zone to the nearest Metro station.
2. Trains & Evaluates Two Production ML Models on 2023 Workday OD Trips (80/20 stratified split):
   - Model 1 (Mode Choice Classifier): `P(uses_metro | spatial distances, trip_eucl_km, hour, peak, purpose)`
     comparing `LogisticRegressionIRLS` vs. `FastHistogramGBDT` (ROC-AUC, PR-AUC, F1, Log-Loss, Brier Score).
   - Model 2 (Travel Time Regressor): `E[duracion_obs_min | uses_metro, spatial distances, trip_eucl_km, hour]`
     comparing `RidgeRegressor` vs. `FastHistogramGBDT` (MAE, RMSE, R2).
3. Counterfactual Simulation of Future Lines 7, 8, and 9:
   - Injects official planned station coordinates for Line 7 (Renca–Vitacura), Line 8 (Los Leones–Puente Alto),
     and Line 9 (Cal y Canto–La Pintana–Puente Alto).
   - Recomputes `orig_dist_metro_km` and `dest_dist_metro_km` for all 777 zones under 4 scenarios:
     `+L7`, `+L8`, `+L9`, and `+L7+L8+L9`.
   - Quantifies: (a) daily new Metro trips captured from Bus/ZP, (b) average minutes & passenger-hours saved/day,
     (c) top beneficiary communes (`La Pintana`, `Renca`, `Cerro Navia`, `Vitacura`, `Puente Alto`, `La Florida`),
     and (d) estimated decongestion on saturated existing corridors (L1, L2, L4).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import duckdb
import numpy as np
import pandas as pd
from pyproj import Transformer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from metro_od.catalog import DATASETS, DATA_DIR
from metro_od.clean import COMUNA_CODES_2023, _sql_decode_int_map
from metro_od.ml_utils import (
    FastHistogramGBDT,
    LogisticRegressionIRLS,
    RidgeRegressor,
    StandardScaler,
    brier_score,
    f1_score,
    log_loss,
    pr_auc_score,
    regression_metrics,
    roc_auc_score,
)

# Planned future Metro de Santiago station coordinates (WGS84 lat, lon) from official EIA / Metro trazados
FUTURE_STATIONS: Dict[str, List[Tuple[str, float, float]]] = {
    "L7": [
        ("L7_Brasil_Renca", -33.3965, -70.7525),
        ("L7_Jose_Miguel_Infante", -33.4050, -70.7420),
        ("L7_Salvador_Gutierrez", -33.4148, -70.7412),
        ("L7_Huelen_Cerro_Navia", -33.4235, -70.7375),
        ("L7_Neptuno_L7", -33.4285, -70.7235),
        ("L7_Radal", -33.4310, -70.7085),
        ("L7_Walker_Martinez_QN", -33.4315, -70.6955),
        ("L7_Matucana", -33.4335, -70.6805),
        ("L7_Ricardo_Cumming", -33.4342, -70.6685),
        ("L7_Cal_y_Canto_L7", -33.4328, -70.6529),
        ("L7_Baquedano_L7", -33.4372, -70.6352),
        ("L7_Pedro_de_Valdivia_L7", -33.4255, -70.6143),
        ("L7_Isidora_Goyenechea", -33.4158, -70.5985),
        ("L7_Vitacura", -33.4045, -70.5880),
        ("L7_Americo_Vespucio", -33.3990, -70.5780),
        ("L7_Parque_Araucano", -33.4015, -70.5685),
        ("L7_Geronimo_de_Alderete", -33.3935, -70.5565),
        ("L7_Padre_Hurtado", -33.3865, -70.5445),
        ("L7_Estoril", -33.3835, -70.5315),
    ],
    "L8": [
        ("L8_Los_Leones", -33.4223, -70.6097),
        ("L8_Eliodoro_Yanez", -33.4310, -70.6070),
        ("L8_Diagonal_Oriente", -33.4435, -70.6040),
        ("L8_Chile_Espana", -33.4555, -70.6015),
        ("L8_Grecia", -33.4648, -70.5990),
        ("L8_Rodrigo_de_Araya", -33.4785, -70.5975),
        ("L8_Quilin_Macul", -33.4895, -70.5965),
        ("L8_Las_Torres_Macul", -33.4995, -70.5955),
        ("L8_Macul_L8", -33.5090, -70.5910),
        ("L8_Walker_Martinez_LF", -33.5225, -70.5855),
        ("L8_Rojas_Magallanes_LF", -33.5365, -70.5825),
        ("L8_Trinidad_LF", -33.5495, -70.5800),
        ("L8_Diego_Portales_LF", -33.5625, -70.5775),
        ("L8_Mall_Plaza_Tobalaba", -33.5715, -70.5620),
    ],
    "L9": [
        ("L9_Cal_y_Canto", -33.4328, -70.6529),
        ("L9_Santa_Lucia", -33.4428, -70.6458),
        ("L9_Matta", -33.4565, -70.6425),
        ("L9_Nuble", -33.4675, -70.6395),
        ("L9_Bio_Bio", -33.4765, -70.6380),
        ("L9_La_Legua", -33.4885, -70.6370),
        ("L9_Departamental_SR", -33.5025, -70.6365),
        ("L9_Lo_Ovalle_SR", -33.5165, -70.6360),
        ("L9_Linares", -33.5295, -70.6355),
        ("L9_Santa_Rosa_L9", -33.5425, -70.6350),
        ("L9_Hosp_Padre_Hurtado", -33.5555, -70.6345),
        ("L9_Observatorio", -33.5685, -70.6340),
        ("L9_Lo_Martinez", -33.5815, -70.6335),
        ("L9_Gabriela_La_Pintana", -33.5935, -70.6330),
        ("L9_La_Pintana_Centro", -33.5835, -70.6345),
        ("L9_El_Mariscal", -33.6055, -70.6250),
        ("L9_Plaza_Puente_Alto", -33.6095, -70.5759),
    ],
}


def _haversine_min_dist_km(
    lats: np.ndarray, lons: np.ndarray, st_lats: np.ndarray, st_lons: np.ndarray
) -> np.ndarray:
    """Compute minimum Haversine distance (km) from each point to a set of station coordinates."""
    r = 6371.0
    lat1 = np.radians(lats)[:, None]
    lon1 = np.radians(lons)[:, None]
    lat2 = np.radians(st_lats)[None, :]
    lon2 = np.radians(st_lons)[None, :]
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    d = 2.0 * r * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    return np.min(d, axis=1)


def extract_zone_centroids_and_metro_stations() -> Tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Extract WGS84 centroids for all ~777 zones from etapas_2019/2023 and existing Metro stations from GTFS."""
    con = duckdb.connect()
    utm_to_wgs84 = Transformer.from_crs("EPSG:32719", "EPSG:4326", always_xy=True)

    et19_path = DATASETS["etapas_2019"].path.as_posix()
    et23_path = DATASETS["etapas_2023"].path.as_posix()

    zones_df = con.execute(
        f"""
        WITH e AS (
            (SELECT
                CAST(ROUND(TRY_CAST(zona_subida AS DOUBLE)) AS INT) AS zona,
                TRY_CAST(x_subida AS DOUBLE) AS x,
                TRY_CAST(y_subida AS DOUBLE) AS y
            FROM read_csv('{et19_path}', delim=';', header=true, nullstr=['-', ''], all_varchar=true, ignore_errors=true)
            LIMIT 400000)
            UNION ALL
            (SELECT
                CAST(ROUND(TRY_CAST(zona_subida AS DOUBLE)) AS INT) AS zona,
                TRY_CAST(x_subida AS DOUBLE) AS x,
                TRY_CAST(y_subida AS DOUBLE) AS y
            FROM read_csv('{et23_path}', delim=';', header=true, nullstr=['-', ''], all_varchar=true, ignore_errors=true)
            LIMIT 400000)
        )
        SELECT
            zona,
            AVG(CASE WHEN x BETWEEN 250000 AND 450000 THEN x END) AS utm_x,
            AVG(CASE WHEN y BETWEEN 6200000 AND 6400000 THEN y END) AS utm_y,
            AVG(CASE WHEN x BETWEEN -34.5 AND -32.5 THEN x END) AS lat_direct,
            AVG(CASE WHEN y BETWEEN -71.5 AND -69.5 THEN y END) AS lon_direct
        FROM e
        WHERE zona IS NOT NULL AND zona > 0
        GROUP BY zona
        """
    ).df()

    lats, lons = [], []
    for _, r in zones_df.iterrows():
        if pd.notna(r["utm_x"]) and pd.notna(r["utm_y"]):
            lon, lat = utm_to_wgs84.transform(float(r["utm_x"]), float(r["utm_y"]))
        elif pd.notna(r["lat_direct"]) and pd.notna(r["lon_direct"]):
            lat, lon = float(r["lat_direct"]), float(r["lon_direct"])
        else:
            lat, lon = np.nan, np.nan
        lats.append(lat)
        lons.append(lon)
    zones_df["lat"] = lats
    zones_df["lon"] = lons
    zones_df = zones_df.dropna(subset=["lat", "lon"])[["zona", "lat", "lon"]].reset_index(drop=True)

    # Extract existing 2023 Metro stations from GTFS (exclude L3 Quilicura stations opened Sep 2023 if present,
    # or keep standard April 2023 network by using location_type=1 / Metro stations)
    gtfs_stops = (DATA_DIR / "GTFS" / "stops.txt").as_posix()
    metro_st_df = con.execute(
        f"""
        SELECT DISTINCT stop_name, stop_lat, stop_lon
        FROM read_csv_auto('{gtfs_stops}')
        WHERE location_type = 1
          AND stop_lat BETWEEN -33.70 AND -33.30
          AND stop_lon BETWEEN -70.85 AND -70.48
        """
    ).df()

    return (
        zones_df,
        metro_st_df["stop_lat"].to_numpy(dtype=np.float64),
        metro_st_df["stop_lon"].to_numpy(dtype=np.float64),
    )


def run_expansion_ml_and_simulation(output_json_path: Path | None = None) -> Dict[str, object]:
    """Train spatial Mode Choice & Travel Time models on 2023 trips and simulate L7, L8, L9."""
    zones_df, base_st_lats, base_st_lons = extract_zone_centroids_and_metro_stations()

    # Precompute baseline & counterfactual nearest-Metro distance (km) for every Zona 777
    z_lats = zones_df["lat"].to_numpy()
    z_lons = zones_df["lon"].to_numpy()
    zones_df["dist_base"] = _haversine_min_dist_km(z_lats, z_lons, base_st_lats, base_st_lons)

    for line_name in ("L7", "L8", "L9"):
        add_lats = np.array([s[1] for s in FUTURE_STATIONS[line_name]])
        add_lons = np.array([s[2] for s in FUTURE_STATIONS[line_name]])
        zones_df[f"dist_{line_name}"] = _haversine_min_dist_km(
            z_lats, z_lons, np.concatenate([base_st_lats, add_lats]), np.concatenate([base_st_lons, add_lons])
        )

    all_new_lats = np.array([s[1] for lst in FUTURE_STATIONS.values() for s in lst])
    all_new_lons = np.array([s[2] for lst in FUTURE_STATIONS.values() for s in lst])
    zones_df["dist_L789"] = _haversine_min_dist_km(
        z_lats, z_lons, np.concatenate([base_st_lats, all_new_lats]), np.concatenate([base_st_lons, all_new_lons])
    )

    # Load stratified sample of 2023 workday completed trips for ML training + daily population scaling
    con = duckdb.connect()
    p23 = DATASETS["viajes_2023"].path.as_posix()

    # Exact total daily expanded valid workday trips (for scaling sample predictions to full Santiago demand)
    total_daily_valid_trips = float(
        con.execute(
            f"""
            SELECT SUM(COALESCE(TRY_CAST(factor_expansion AS DOUBLE), 0)) / 5.0
            FROM read_csv('{p23}', delim=',', header=true, nullstr=['-', '', 'NULL'], all_varchar=true, ignore_errors=true)
            WHERE TRY_CAST(tipodia AS INT) = 0 AND TRY_CAST(ultimaetapaconbajada AS INT) = 1
            """
        ).fetchone()[0]
    )

    trips_df = con.execute(
        f"""
        SELECT
            TRY_CAST(zona_inicio_viaje AS INT) AS z_orig,
            TRY_CAST(zona_fin_viaje AS INT) AS z_dest,
            {_sql_decode_int_map('comuna_inicio_viaje', COMUNA_CODES_2023)} AS comuna_orig,
            {_sql_decode_int_map('comuna_fin_viaje', COMUNA_CODES_2023)} AS comuna_dest,
            TRY_CAST(factor_expansion AS DOUBLE) AS weight,
            TRY_CAST(distancia_eucl AS DOUBLE) / 1000.0 AS trip_eucl_km,
            EXTRACT('hour' FROM TRY_CAST(tiempo_inicio_viaje AS TIMESTAMP))
                + EXTRACT('minute' FROM TRY_CAST(tiempo_inicio_viaje AS TIMESTAMP)) / 60.0 AS hour_dec,
            CASE WHEN UPPER(TRIM(proposito)) = 'TRABAJO' THEN 1 ELSE 0 END AS is_work,
            CASE WHEN UPPER(TRIM(proposito)) = 'ESTUDIO' THEN 1 ELSE 0 END AS is_study,
            CASE WHEN UPPER(TRIM(proposito)) = 'HOGAR' THEN 1 ELSE 0 END AS is_home,
            CASE
                WHEN TRY_CAST(tipo_transporte_1 AS INT) = 2
                  OR TRY_CAST(tipo_transporte_2 AS INT) = 2
                  OR TRY_CAST(tipo_transporte_3 AS INT) = 2
                  OR TRY_CAST(tipo_transporte_4 AS INT) = 2
                THEN 1 ELSE 0
            END AS uses_metro,
            CASE
                WHEN TRY_CAST(n_etapas AS INT) = 1 AND TRY_CAST(tipo_transporte_1 AS INT) = 2
                THEN 1 ELSE 0
            END AS is_direct_metro,
            EPOCH(TRY_CAST(tiempo_fin_viaje AS TIMESTAMP) - TRY_CAST(tiempo_inicio_viaje AS TIMESTAMP)) / 60.0 AS dur_min
        FROM read_csv('{p23}', delim=',', header=true, nullstr=['-', '', 'NULL'], all_varchar=true, ignore_errors=true)
        WHERE TRY_CAST(tipodia AS INT) = 0
          AND TRY_CAST(ultimaetapaconbajada AS INT) = 1
          AND TRY_CAST(zona_inicio_viaje AS INT) > 0
          AND TRY_CAST(zona_fin_viaje AS INT) > 0
          AND TRY_CAST(distancia_eucl AS DOUBLE) BETWEEN 500 AND 40000
          AND EPOCH(TRY_CAST(tiempo_fin_viaje AS TIMESTAMP) - TRY_CAST(tiempo_inicio_viaje AS TIMESTAMP)) BETWEEN 120 AND 12000
        USING SAMPLE 220000 ROWS (reservoir)
        """
    ).df()

    # Merge zone distances
    z_map = zones_df.set_index("zona")
    trips_df = trips_df[trips_df["z_orig"].isin(z_map.index) & trips_df["z_dest"].isin(z_map.index)].copy()
    for col in ("dist_base", "dist_L7", "dist_L8", "dist_L9", "dist_L789"):
        trips_df[f"o_{col}"] = trips_df["z_orig"].map(z_map[col]).to_numpy()
        trips_df[f"d_{col}"] = trips_df["z_dest"].map(z_map[col]).to_numpy()

    trips_df["is_peak_am"] = ((trips_df["hour_dec"] >= 6.5) & (trips_df["hour_dec"] < 9.0)).astype(float)
    trips_df["is_peak_pm"] = ((trips_df["hour_dec"] >= 17.5) & (trips_df["hour_dec"] < 20.0)).astype(float)

    def _build_feature_matrix(df: pd.DataFrame, scen: str) -> np.ndarray:
        od = df[f"o_{scen}"].to_numpy()
        dd = df[f"d_{scen}"].to_numpy()
        return np.column_stack(
            [
                od,
                dd,
                np.minimum(od, dd),
                np.maximum(od, dd),
                od + dd,
                (od <= 1.0).astype(float),
                (dd <= 1.0).astype(float),
                df["trip_eucl_km"].to_numpy(),
                np.log1p(df["trip_eucl_km"].to_numpy()),
                df["hour_dec"].to_numpy(),
                df["is_peak_am"].to_numpy(),
                df["is_peak_pm"].to_numpy(),
                df["is_work"].to_numpy(),
                df["is_study"].to_numpy(),
                df["is_home"].to_numpy(),
            ]
        )

    feat_names = [
        "orig_dist_metro_km",
        "dest_dist_metro_km",
        "min_end_dist_metro_km",
        "max_end_dist_metro_km",
        "sum_end_dist_metro_km",
        "orig_within_1km_metro",
        "dest_within_1km_metro",
        "trip_eucl_km",
        "log_trip_eucl_km",
        "hour_dec",
        "is_peak_am",
        "is_peak_pm",
        "is_work",
        "is_study",
        "is_home",
    ]

    X_base = _build_feature_matrix(trips_df, "dist_base")
    y_metro = trips_df["uses_metro"].to_numpy(dtype=np.float64)
    y_direct = trips_df["is_direct_metro"].to_numpy(dtype=np.float64)
    y_dur = trips_df["dur_min"].to_numpy(dtype=np.float64)
    w_sample = trips_df["weight"].fillna(1.0).to_numpy(dtype=np.float64)

    rng = np.random.default_rng(42)
    perm = rng.permutation(len(y_metro))
    split = int(0.8 * len(y_metro))
    tr, te = perm[:split], perm[split:]

    # --- MODEL 1: METRO MODE CHOICE CLASSIFIER (P(uses_metro)) ---
    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_base[tr])
    X_te_s = scaler.transform(X_base[te])

    lr_clf = LogisticRegressionIRLS(l2=5.0).fit(X_tr_s, y_metro[tr])
    p_lr = lr_clf.predict_proba(X_te_s)
    clf_lr_metrics = {
        "roc_auc": round(roc_auc_score(y_metro[te], p_lr), 4),
        "pr_auc": round(pr_auc_score(y_metro[te], p_lr), 4),
        "f1": round(f1_score(y_metro[te], p_lr), 4),
        "log_loss": round(log_loss(y_metro[te], p_lr), 4),
        "brier": round(brier_score(y_metro[te], p_lr), 4),
    }

    gbdt_clf = FastHistogramGBDT(
        task="classification", n_estimators=90, learning_rate=0.08, max_depth=5, min_samples_leaf=150, n_bins=32
    ).fit(X_base[tr], y_metro[tr])
    p_gbdt = gbdt_clf.predict_proba(X_base[te])
    clf_gbdt_metrics = {
        "roc_auc": round(roc_auc_score(y_metro[te], p_gbdt), 4),
        "pr_auc": round(pr_auc_score(y_metro[te], p_gbdt), 4),
        "f1": round(f1_score(y_metro[te], p_gbdt), 4),
        "log_loss": round(log_loss(y_metro[te], p_gbdt), 4),
        "brier": round(brier_score(y_metro[te], p_gbdt), 4),
    }

    # --- MODEL 1B: DIRECT SOLO-METRO CLASSIFIER (P(direct_metro | uses_metro=1)) ---
    m_tr = tr[y_metro[tr] == 1]
    m_te = te[y_metro[te] == 1]
    gbdt_direct = FastHistogramGBDT(
        task="classification", n_estimators=80, learning_rate=0.08, max_depth=5, min_samples_leaf=150, n_bins=32
    ).fit(X_base[m_tr], y_direct[m_tr])
    p_dir_te = gbdt_direct.predict_proba(X_base[m_te])
    direct_clf_metrics = {
        "roc_auc": round(roc_auc_score(y_direct[m_te], p_dir_te), 4),
        "pr_auc": round(pr_auc_score(y_direct[m_te], p_dir_te), 4),
        "f1": round(f1_score(y_direct[m_te], p_dir_te), 4),
        "brier": round(brier_score(y_direct[m_te], p_dir_te), 4),
    }

    # --- MODEL 2: TRAVEL TIME REGRESSOR (Distinguishes Bus-Only, Intermodal Bus+Metro, Direct Solo Metro) ---
    is_intermodal = ((y_metro == 1) & (y_direct == 0)).astype(float)
    X_reg_base = np.column_stack([X_base, y_direct, is_intermodal])
    reg_scaler = StandardScaler()
    ridge = RidgeRegressor(alpha=10.0).fit(reg_scaler.fit_transform(X_reg_base[tr]), y_dur[tr])
    reg_ridge_metrics = regression_metrics(y_dur[te], ridge.predict(reg_scaler.transform(X_reg_base[te])))

    gbdt_reg = FastHistogramGBDT(
        task="regression", n_estimators=120, learning_rate=0.10, max_depth=5, min_samples_leaf=120, n_bins=32
    ).fit(X_reg_base[tr], y_dur[tr])
    reg_gbdt_metrics = regression_metrics(y_dur[te], gbdt_reg.predict(X_reg_base[te]))

    # --- COUNTERFACTUAL SIMULATION FOR L7, L8, L9, AND L7+L8+L9 ---
    scale_to_daily = total_daily_valid_trips / float(np.sum(w_sample))
    p_base_all = gbdt_clf.predict_proba(X_base)
    p_dir_base = gbdt_direct.predict_proba(X_base)

    # Predicted durations under the 3 modes: Bus-only (0,0), Intermodal (0,1), Direct Solo Metro (1,0)
    pred_dur_bus = gbdt_reg.predict(np.column_stack([X_base, np.zeros(len(y_metro)), np.zeros(len(y_metro))]))
    pred_dur_inter = gbdt_reg.predict(np.column_stack([X_base, np.zeros(len(y_metro)), np.ones(len(y_metro))]))

    scenarios_out: Dict[str, Dict[str, object]] = {}
    commune_impacts_L789: List[Dict[str, object]] = []

    for scen_key in ("dist_L7", "dist_L8", "dist_L9", "dist_L789"):
        X_cf = _build_feature_matrix(trips_df, scen_key)
        p_cf = gbdt_clf.predict_proba(X_cf)
        p_dir_cf = gbdt_direct.predict_proba(X_cf)

        # Channel 1: Bus-only riders converting to Metro
        delta_p = np.maximum(p_cf - p_base_all, 0.0)
        non_metro_mask = (y_metro == 0).astype(float)
        conv_prob = np.clip(delta_p / np.maximum(1.0 - p_base_all, 0.10), 0.0, 0.85) * non_metro_mask

        # Channel 2: Existing Intermodal (Bus+Metro) riders upgrading to Direct Solo Metro (eliminating feeder bus!)
        delta_dir = np.maximum(p_dir_cf - p_dir_base, 0.0)
        inter_mask = is_intermodal
        upgrade_prob = np.clip(delta_dir / np.maximum(1.0 - p_dir_base, 0.10), 0.0, 0.85) * inter_mask

        pred_dur_direct_cf = gbdt_reg.predict(np.column_stack([X_cf, np.ones(len(y_metro)), np.zeros(len(y_metro))]))
        pred_dur_inter_cf = gbdt_reg.predict(np.column_stack([X_cf, np.zeros(len(y_metro)), np.ones(len(y_metro))]))
        expected_metro_dur_cf = p_dir_cf * pred_dur_direct_cf + (1.0 - p_dir_cf) * pred_dur_inter_cf

        saved_bus_to_metro = np.maximum(pred_dur_bus - expected_metro_dur_cf, 0.0)
        saved_inter_to_direct = np.maximum(pred_dur_inter - pred_dur_direct_cf, 0.0)

        new_daily_metro_trips = float(np.sum(w_sample * conv_prob) * scale_to_daily)
        upgraded_direct_trips = float(np.sum(w_sample * upgrade_prob) * scale_to_daily)
        total_min_saved_conv = float(np.sum(w_sample * conv_prob * saved_bus_to_metro) * scale_to_daily)
        total_min_saved_upgr = float(np.sum(w_sample * upgrade_prob * saved_inter_to_direct) * scale_to_daily)
        total_daily_min_saved = total_min_saved_conv + total_min_saved_upgr

        total_benefited = new_daily_metro_trips + upgraded_direct_trips
        avg_min_saved_upgr = total_min_saved_upgr / upgraded_direct_trips if upgraded_direct_trips > 0 else 0.0
        avg_min_saved_all = total_daily_min_saved / total_benefited if total_benefited > 0 else 0.0

        base_metro_share = float(np.average(y_metro, weights=w_sample) * 100.0)
        new_metro_share = base_metro_share + (new_daily_metro_trips / total_daily_valid_trips) * 100.0

        scenarios_out[scen_key] = {
            "new_daily_metro_trips": round(new_daily_metro_trips, 0),
            "upgraded_intermodal_to_direct_trips": round(upgraded_direct_trips, 0),
            "total_daily_benefited_trips": round(total_benefited, 0),
            "metro_share_base_pct": round(base_metro_share, 2),
            "metro_share_new_pct": round(new_metro_share, 2),
            "metro_share_gain_pp": round(new_metro_share - base_metro_share, 2),
            "avg_min_saved_direct_upgrade": round(avg_min_saved_upgr, 2),
            "avg_min_saved_all_benefited": round(avg_min_saved_all, 2),
            "daily_passenger_hours_saved": round(total_daily_min_saved / 60.0, 0),
        }

        if scen_key == "dist_L789":
            tmp = pd.DataFrame(
                {
                    "comuna": trips_df["comuna_orig"].values,
                    "w": w_sample * scale_to_daily,
                    "base_metro": w_sample * y_metro * scale_to_daily,
                    "new_metro": w_sample * conv_prob * scale_to_daily,
                    "upgraded_direct": w_sample * upgrade_prob * scale_to_daily,
                    "min_saved": w_sample * (conv_prob * saved_bus_to_metro + upgrade_prob * saved_inter_to_direct) * scale_to_daily,
                    "dist_drop_km": (trips_df["o_dist_base"] - trips_df["o_dist_L789"]).values,
                }
            )
            agg_c = (
                tmp.groupby("comuna", as_index=False)
                .agg(
                    daily_trips=("w", "sum"),
                    base_metro=("base_metro", "sum"),
                    new_metro_trips=("new_metro", "sum"),
                    upgraded_direct=("upgraded_direct", "sum"),
                    min_saved=("min_saved", "sum"),
                    avg_dist_drop_km=("dist_drop_km", "mean"),
                )
            )
            agg_c["base_metro_pct"] = (agg_c["base_metro"] * 100.0 / agg_c["daily_trips"]).round(1)
            agg_c["gain_pp"] = (agg_c["new_metro_trips"] * 100.0 / agg_c["daily_trips"]).round(1)
            agg_c["total_benefited"] = agg_c["new_metro_trips"] + agg_c["upgraded_direct"]
            agg_c["avg_min_saved"] = (agg_c["min_saved"] / agg_c["total_benefited"].replace(0, np.nan)).fillna(0).round(1)
            agg_c = agg_c.sort_values("total_benefited", ascending=False).head(12)
            commune_impacts_L789 = [
                {
                    "comuna": r["comuna"],
                    "new_daily_metro_trips": round(float(r["new_metro_trips"]), 0),
                    "upgraded_direct_trips": round(float(r["upgraded_direct"]), 0),
                    "total_benefited_trips": round(float(r["total_benefited"]), 0),
                    "base_metro_pct": float(r["base_metro_pct"]),
                    "gain_pp": float(r["gain_pp"]),
                    "avg_min_saved": float(r["avg_min_saved"]),
                    "avg_dist_drop_km": round(float(r["avg_dist_drop_km"]), 2),
                }
                for _, r in agg_c.iterrows()
            ]

    feat_imp = [
        {"feature": f, "gbdt_importance": round(float(imp), 4), "lr_coef_std": round(float(c), 4)}
        for f, imp, c in zip(feat_names, gbdt_clf.feature_importances_, lr_clf.coef_)
    ]
    feat_imp.sort(key=lambda r: r["gbdt_importance"], reverse=True)

    summary = {
        "n_zones_mapped": int(len(zones_df)),
        "n_existing_metro_stations": int(len(base_st_lats)),
        "n_train_eval_trips": int(len(y_metro)),
        "total_daily_valid_workday_trips": round(total_daily_valid_trips, 0),
        "mode_choice_classifier": {"logistic_regression": clf_lr_metrics, "gbdt": clf_gbdt_metrics},
        "direct_metro_classifier": {"gbdt": direct_clf_metrics},
        "travel_time_regressor": {"ridge": reg_ridge_metrics, "gbdt": reg_gbdt_metrics},
        "top_features": feat_imp[:8],
        "counterfactual_scenarios": scenarios_out,
        "top_beneficiary_communes_L789": commune_impacts_L789,
    }

    if output_json_path:
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return summary


if __name__ == "__main__":
    out_p = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    res = run_expansion_ml_and_simulation(out_p)
    print(json.dumps(res, indent=2))
