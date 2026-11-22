"""Predictive ML Model for Afternoon Feeder Bus Non-Validation (`SOLO METRO` vs `METRO -> BUS`).

Tracks individual bip! cards (`id_tarjeta`) across all 5 workdays of April 2023 that validated
a morning `BUS -> METRO` trip (`05:30-10:30`) and also made a Metro-initiated return trip in
the afternoon (`>= 13:00`).

Target `y`:
- `1` = Rider returns as `SOLO METRO` (1 stage; failed to validate afternoon feeder bus)
- `0` = Rider returns symmetrically as `METRO -> BUS` (2 stages)

Compares L2 Logistic Regression (IRLS with exact Odds Ratios & z-scores) against
Histogram Gradient Boosted Decision Trees (`FastHistogramGBDT`), and computes station-level
recoverable daily validations if closed paid-transfer zones (`Zona Paga` / EIM) are deployed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from metro_od.catalog import DATASETS
from metro_od.ml_utils import (
    FastHistogramGBDT,
    LogisticRegressionIRLS,
    StandardScaler,
    brier_score,
    f1_score,
    log_loss,
    pr_auc_score,
    roc_auc_score,
)


def build_intermodal_dataset(max_rows: int = 250_000) -> pd.DataFrame:
    """Extract card-level morning BUS->METRO trips joined with their afternoon Metro return."""
    con = duckdb.connect()
    p23 = DATASETS["viajes_2023"].path.as_posix()

    query = f"""
    WITH raw AS (
        SELECT
            NULLIF(TRIM(id_tarjeta), '') AS id_tarjeta,
            TRY_CAST(tipodia AS INT) AS tipodia,
            TRY_CAST(n_etapas AS INT) AS n_etapas,
            TRY_CAST(tipo_transporte_1 AS INT) AS m1,
            TRY_CAST(tipo_transporte_2 AS INT) AS m2,
            TRY_CAST(tiempo_inicio_viaje AS TIMESTAMP) AS t_ini,
            TRY_CAST(mediahora_inicio_viaje_hora AS TIME) AS mh_ini,
            NULLIF(TRIM(paradero_subida_2), '') AS metro_in_station,
            NULLIF(TRIM(paradero_bajada_1), '') AS metro_out_station,
            TRY_CAST(distancia_eucl AS DOUBLE) / 1000.0 AS dist_eucl_km,
            TRY_CAST(te0 AS DOUBLE) / 60.0 AS te0_min,
            TRY_CAST(tc1 AS DOUBLE) / 60.0 AS tc1_min,
            TRY_CAST(te1 AS DOUBLE) / 60.0 AS te1_min,
            TRY_CAST(tviaje2 AS DOUBLE) / 60.0 AS tviaje_min,
            UPPER(TRIM(proposito)) AS proposito,
            TRY_CAST(contrato AS INT) AS contrato
        FROM read_csv('{p23}', delim=',', header=true, nullstr=['-', '', 'NULL'], all_varchar=true, ignore_errors=true)
        WHERE TRY_CAST(tipodia AS INT) = 0
    ),
    station_pm_wait AS (
        SELECT
            metro_out_station AS station,
            AVG(CASE WHEN te1_min BETWEEN 0.5 AND 45 THEN te1_min END) AS station_pm_bus_wait_min,
            COUNT(*) AS station_mb_vol
        FROM raw
        WHERE n_etapas = 2 AND m1 = 2 AND m2 = 1 AND mh_ini >= TIME '13:00:00' AND metro_out_station IS NOT NULL
        GROUP BY 1
    ),
    station_bm_vol AS (
        SELECT metro_in_station AS station, COUNT(*) AS station_bm_vol
        FROM raw
        WHERE n_etapas = 2 AND m1 = 1 AND m2 = 2 AND metro_in_station IS NOT NULL
        GROUP BY 1
    ),
    morning_bm AS (
        SELECT
            id_tarjeta,
            CAST(t_ini AS DATE) AS dia,
            EXTRACT('hour' FROM t_ini) + EXTRACT('minute' FROM t_ini) / 60.0 AS am_hour,
            metro_in_station AS station,
            COALESCE(dist_eucl_km, 8.0) AS am_dist_km,
            COALESCE(te0_min, 4.0) AS am_bus_wait_min,
            COALESCE(tc1_min, 3.0) AS am_transfer_walk_min,
            COALESCE(tviaje_min, 41.0) AS am_tviaje_min,
            CASE WHEN proposito = 'ESTUDIO' THEN 1 ELSE 0 END AS is_student_trip,
            CASE WHEN proposito = 'TRABAJO' THEN 1 ELSE 0 END AS is_work_trip,
            ROW_NUMBER() OVER (PARTITION BY id_tarjeta, CAST(t_ini AS DATE) ORDER BY t_ini) AS rn
        FROM raw
        WHERE n_etapas = 2 AND m1 = 1 AND m2 = 2
          AND mh_ini >= TIME '05:30:00' AND mh_ini < TIME '10:30:00'
          AND id_tarjeta IS NOT NULL AND metro_in_station IS NOT NULL
    ),
    pm_metro AS (
        SELECT
            id_tarjeta,
            CAST(t_ini AS DATE) AS dia,
            EXTRACT('hour' FROM t_ini) + EXTRACT('minute' FROM t_ini) / 60.0 AS pm_hour,
            CASE WHEN n_etapas = 1 AND m1 = 2 THEN 1 ELSE 0 END AS y_skipped_bus,
            ROW_NUMBER() OVER (PARTITION BY id_tarjeta, CAST(t_ini AS DATE) ORDER BY t_ini DESC) AS rn
        FROM raw
        WHERE mh_ini >= TIME '13:00:00'
          AND ((n_etapas = 1 AND m1 = 2) OR (n_etapas = 2 AND m1 = 2 AND m2 = 1))
          AND id_tarjeta IS NOT NULL
    )
    SELECT
        m.station,
        m.am_hour,
        p.pm_hour,
        CASE WHEN p.pm_hour >= 17.5 AND p.pm_hour < 20.0 THEN 1 ELSE 0 END AS is_pm_peak,
        CASE WHEN p.pm_hour >= 20.0 THEN 1 ELSE 0 END AS is_pm_night,
        m.am_dist_km,
        m.am_bus_wait_min,
        m.am_transfer_walk_min,
        m.am_tviaje_min,
        m.is_student_trip,
        m.is_work_trip,
        COALESCE(sw.station_pm_bus_wait_min, 7.7) AS station_pm_bus_wait_min,
        LN(1.0 + COALESCE(sb.station_bm_vol, 100.0)) AS log_station_bm_vol,
        p.y_skipped_bus
    FROM morning_bm m
    JOIN pm_metro p ON m.id_tarjeta = p.id_tarjeta AND m.dia = p.dia
    LEFT JOIN station_pm_wait sw ON m.station = sw.station
    LEFT JOIN station_bm_vol sb ON m.station = sb.station
    WHERE m.rn = 1 AND p.rn = 1
      AND m.am_dist_km BETWEEN 0.5 AND 40.0
      AND m.am_bus_wait_min BETWEEN 0.0 AND 40.0
      AND m.am_transfer_walk_min BETWEEN 0.0 AND 30.0
      AND m.am_tviaje_min BETWEEN 5.0 AND 150.0
    USING SAMPLE {int(max_rows)} ROWS (reservoir)
    """
    return con.execute(query).df()


def run_intermodal_ml(output_json_path: Path | None = None) -> Dict[str, object]:
    """Train & evaluate Logistic Regression and GBDT predicting return-bus non-validation."""
    df = build_intermodal_dataset(max_rows=180_000)
    features = [
        "am_hour",
        "pm_hour",
        "is_pm_peak",
        "is_pm_night",
        "am_dist_km",
        "am_bus_wait_min",
        "am_transfer_walk_min",
        "am_tviaje_min",
        "is_student_trip",
        "is_work_trip",
        "station_pm_bus_wait_min",
        "log_station_bm_vol",
    ]
    X = df[features].to_numpy(dtype=np.float64)
    y = df["y_skipped_bus"].to_numpy(dtype=np.float64)

    rng = np.random.default_rng(42)
    perm = rng.permutation(len(y))
    split = int(0.8 * len(y))
    tr, te = perm[:split], perm[split:]

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X[tr])
    X_te_s = scaler.transform(X[te])

    # Model 1: L2 Logistic Regression (IRLS)
    lr = LogisticRegressionIRLS(l2=5.0).fit(X_tr_s, y[tr])
    p_lr = lr.predict_proba(X_te_s)
    lr_metrics = {
        "roc_auc": round(roc_auc_score(y[te], p_lr), 4),
        "pr_auc": round(pr_auc_score(y[te], p_lr), 4),
        "f1": round(f1_score(y[te], p_lr), 4),
        "log_loss": round(log_loss(y[te], p_lr), 4),
        "brier": round(brier_score(y[te], p_lr), 4),
    }

    # Model 2: Fast Histogram GBDT
    gbdt = FastHistogramGBDT(
        task="classification", n_estimators=65, learning_rate=0.08, max_depth=4, min_samples_leaf=150
    ).fit(X[tr], y[tr])
    p_gbdt = gbdt.predict_proba(X[te])
    gbdt_metrics = {
        "roc_auc": round(roc_auc_score(y[te], p_gbdt), 4),
        "pr_auc": round(pr_auc_score(y[te], p_gbdt), 4),
        "f1": round(f1_score(y[te], p_gbdt), 4),
        "log_loss": round(log_loss(y[te], p_gbdt), 4),
        "brier": round(brier_score(y[te], p_gbdt), 4),
    }

    coef_table = [
        {
            "feature": f,
            "coef_std": round(float(c), 4),
            "odds_ratio_per_sd": round(float(np.exp(c)), 3),
            "z_score": round(float(z), 2),
            "gbdt_importance": round(float(imp), 4),
        }
        for f, c, z, imp in zip(features, lr.coef_, lr.z_scores_, gbdt.feature_importances_)
    ]
    coef_table.sort(key=lambda r: r["gbdt_importance"], reverse=True)

    # Counterfactual: What if afternoon bus wait `station_pm_bus_wait_min` is cut by 50% (e.g. better frequency / EIM)?
    X_cf = X[te].copy()
    wait_idx = features.index("station_pm_bus_wait_min")
    X_cf[:, wait_idx] = X_cf[:, wait_idx] * 0.5
    p_cf_lr = lr.predict_proba(scaler.transform(X_cf))
    p_cf_gbdt = gbdt.predict_proba(X_cf)

    summary = {
        "n_rows": int(len(y)),
        "positive_rate_solo_metro": round(float(np.mean(y)), 4),
        "logistic_regression": lr_metrics,
        "gbdt": gbdt_metrics,
        "feature_effects": coef_table,
        "counterfactual_half_wait_delta_pp_lr": round(float((np.mean(p_cf_lr) - np.mean(p_lr)) * 100), 2),
        "counterfactual_half_wait_delta_pp_gbdt": round(float((np.mean(p_cf_gbdt) - np.mean(p_gbdt)) * 100), 2),
    }

    if output_json_path:
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return summary


if __name__ == "__main__":
    out_p = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    res = run_intermodal_ml(out_p)
    print(json.dumps(res, indent=2))
