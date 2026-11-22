"""Quasi-Experimental Difference-in-Differences Impact of Lines 3 & 6 (2019 vs. 2023).

Runs a 100% full-week DuckDB analysis (zero LIMIT) using the strictly comparable travel time metric
`EPOCH(tiempo_fin_viaje - tiempo_inicio_viaje) / 60.0` (avoiding the 2023 `tviaje2` definition shift)
to evaluate travel time, transfer stages, and Metro share across L3/L6 expansion communes vs control communes.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

import duckdb
from metro_od.deep_dive import _full_viajes_views_sql


def run_expansion():
    con = duckdb.connect()
    con.execute(_full_viajes_views_sql())

    print("=== 1. FULL-WEEK WORKDAY COMMUNE-LEVEL COMPARISON (L3/L6 TREATED vs CONTROL) ===")
    df_comm = con.execute(
        """
        WITH base AS (
            SELECT
                anio,
                comuna_origen AS comuna,
                CASE
                    WHEN comuna_origen IN ('QUILICURA', 'CONCHALI', 'INDEPENDENCIA', 'NUNOA', 'CERRILLOS', 'PEDRO AGUIRRE CERDA')
                    THEN 'L3_L6_Treated'
                    ELSE 'Control'
                END AS grupo,
                duracion_obs_min,
                n_etapas,
                CASE WHEN modo_1 = 'METRO' THEN 100.0 ELSE 0.0 END AS is_metro
            FROM (SELECT * FROM v19_full UNION ALL SELECT * FROM v23_full)
            WHERE tipo_dia = 'LABORAL'
              AND ultima_bajada = 1
              AND duracion_obs_min IS NOT NULL
              AND comuna_origen IN (
                  'QUILICURA', 'CONCHALI', 'INDEPENDENCIA', 'NUNOA', 'CERRILLOS', 'PEDRO AGUIRRE CERDA',
                  'MAIPU', 'PUENTE ALTO', 'LA FLORIDA', 'LAS CONDES', 'SANTIAGO', 'PUDAHUEL', 'LA CISTERNA', 'SAN BERNARDO'
              )
        )
        SELECT
            grupo,
            comuna,
            ROUND(AVG(CASE WHEN anio = '2019' THEN duracion_obs_min END), 2) AS min_2019,
            ROUND(AVG(CASE WHEN anio = '2023' THEN duracion_obs_min END), 2) AS min_2023,
            ROUND(AVG(CASE WHEN anio = '2023' THEN duracion_obs_min END) - AVG(CASE WHEN anio = '2019' THEN duracion_obs_min END), 2) AS delta_min,
            ROUND(AVG(CASE WHEN anio = '2019' THEN n_etapas END), 3) AS etapas_2019,
            ROUND(AVG(CASE WHEN anio = '2023' THEN n_etapas END), 3) AS etapas_2023,
            ROUND(AVG(CASE WHEN anio = '2019' THEN is_metro END), 1) AS metro_pct_2019,
            ROUND(AVG(CASE WHEN anio = '2023' THEN is_metro END), 1) AS metro_pct_2023
        FROM base
        GROUP BY 1, 2
        ORDER BY 1 DESC, delta_min ASC
        """
    ).df()
    print(df_comm.to_string(index=False))

    print("\n=== 2. AGGREGATE DIFFERENCE-IN-DIFFERENCES (DiD) ESTIMATOR ===")
    did = con.execute(
        """
        WITH base AS (
            SELECT
                anio,
                CASE
                    WHEN comuna_origen IN ('QUILICURA', 'CONCHALI', 'INDEPENDENCIA', 'NUNOA', 'CERRILLOS', 'PEDRO AGUIRRE CERDA')
                    THEN 'L3_L6_Treated'
                    ELSE 'Control'
                END AS grupo,
                duracion_obs_min,
                n_etapas,
                CASE WHEN modo_1 = 'METRO' THEN 100.0 ELSE 0.0 END AS is_metro
            FROM (SELECT * FROM v19_full UNION ALL SELECT * FROM v23_full)
            WHERE tipo_dia = 'LABORAL'
              AND ultima_bajada = 1
              AND duracion_obs_min IS NOT NULL
              AND comuna_origen IN (
                  'QUILICURA', 'CONCHALI', 'INDEPENDENCIA', 'NUNOA', 'CERRILLOS', 'PEDRO AGUIRRE CERDA',
                  'MAIPU', 'PUENTE ALTO', 'LA FLORIDA', 'LAS CONDES', 'SANTIAGO', 'PUDAHUEL', 'LA CISTERNA', 'SAN BERNARDO'
              )
        )
        SELECT
            grupo,
            anio,
            COUNT(*) AS n_trips,
            ROUND(AVG(duracion_obs_min), 3) AS mean_min,
            ROUND(STDDEV_SAMP(duracion_obs_min), 3) AS sd_min,
            ROUND(AVG(n_etapas), 3) AS mean_etapas,
            ROUND(AVG(is_metro), 2) AS metro_share_pct
        FROM base
        GROUP BY 1, 2
        ORDER BY 1 DESC, 2
        """
    ).df()
    print(did.to_string(index=False))

    # Compute DiD point estimate and standard error
    vals = {(r["grupo"], r["anio"]): r for _, r in did.iterrows()}
    t19, t23 = vals[("L3_L6_Treated", "2019")], vals[("L3_L6_Treated", "2023")]
    c19, c23 = vals[("Control", "2019")], vals[("Control", "2023")]
    did_min = (t23["mean_min"] - t19["mean_min"]) - (c23["mean_min"] - c19["mean_min"])
    se_did = math.sqrt(
        (t23["sd_min"] ** 2) / t23["n_trips"]
        + (t19["sd_min"] ** 2) / t19["n_trips"]
        + (c23["sd_min"] ** 2) / c23["n_trips"]
        + (c19["sd_min"] ** 2) / c19["n_trips"]
    )
    z_stat = did_min / se_did if se_did > 0 else 0.0
    print(
        f"\nDiD Estimate (Travel Time): {did_min:+.3f} min (SE = {se_did:.4f}, z = {z_stat:.2f})"
    )


if __name__ == "__main__":
    run_expansion()
