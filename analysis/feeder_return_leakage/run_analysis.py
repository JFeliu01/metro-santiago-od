"""Asymmetric Feeder Return Leakage & OD Distortion (2019 vs. 2023).

Runs a 100% full-week DuckDB analysis (zero LIMIT) across April 2019 and April 2023 to:
1. Measure the 2-stage BUS -> METRO vs METRO -> BUS volume & travel time asymmetry (2019 vs 2023).
2. Track individual bip! cards (`id_tarjeta` in 2023) that board BUS -> METRO in the morning
   to see what % validate METRO -> BUS vs SOLO METRO vs no validation on their afternoon return.
3. Rank the top Metro stations by daily asymmetric return gap (`bus_a_metro_dia - metro_a_bus_dia`).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

import duckdb
from metro_od.deep_dive import _full_viajes_views_sql


def run_intermodal():
    con = duckdb.connect()
    con.execute(_full_viajes_views_sql())

    print("=== 1. FULL-WEEK WORKDAY ASYMMETRY: BUS -> METRO vs METRO -> BUS (2019 vs 2023) ===")
    df_asym = con.execute(
        """
        SELECT anio,
            modo_1 || ' -> ' || modo_2 AS secuencia,
            COUNT(*) AS raw_trips,
            ROUND(COUNT(*) * 1.0 / NULLIF(COUNT(DISTINCT CAST(t_inicio AS DATE)), 0), 0) AS raw_trips_per_day,
            ROUND(SUM(COALESCE(factor_expansion, 0)) / NULLIF(COUNT(DISTINCT CAST(t_inicio AS DATE)), 0), 0) AS expanded_per_day,
            ROUND(AVG(duracion_obs_min), 2) AS avg_obs_min,
            ROUND(AVG(CASE WHEN ultima_bajada = 0 THEN 100.0 ELSE 0.0 END), 2) AS sin_bajada_pct
        FROM (SELECT * FROM v19_full UNION ALL SELECT * FROM v23_full)
        WHERE tipo_dia = 'LABORAL' AND n_etapas = 2
          AND ((modo_1 = 'BUS' AND modo_2 = 'METRO') OR (modo_1 = 'METRO' AND modo_2 = 'BUS'))
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).df()
    print(df_asym.to_string(index=False))

    print("\n=== 2. 2023 CARD-LEVEL PROOF: AFTERNOON RETURN OF MORNING BUS -> METRO RIDERS ===")
    df_cards = con.execute(
        """
        WITH morning_bm AS (
            SELECT DISTINCT id_tarjeta, CAST(t_inicio AS DATE) AS dia
            FROM v23_full
            WHERE tipo_dia = 'LABORAL' AND n_etapas = 2
              AND modo_1 = 'BUS' AND modo_2 = 'METRO'
              AND mh_inicio >= TIME '05:30:00' AND mh_inicio < TIME '10:30:00'
              AND id_tarjeta IS NOT NULL
        ),
        afternoon_trips AS (
            SELECT
                id_tarjeta,
                CAST(t_inicio AS DATE) AS dia,
                MAX(CASE WHEN modo_1 = 'METRO' AND modo_2 = 'BUS' THEN 1 ELSE 0 END) AS has_metro_to_bus,
                MAX(CASE WHEN modo_1 = 'METRO' AND n_etapas = 1 THEN 1 ELSE 0 END) AS has_metro_only,
                MAX(CASE WHEN modo_1 = 'BUS' THEN 1 ELSE 0 END) AS has_bus_start,
                COUNT(*) AS pm_trip_count
            FROM v23_full
            WHERE tipo_dia = 'LABORAL' AND mh_inicio >= TIME '13:00:00' AND id_tarjeta IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT
            CASE
                WHEN a.has_metro_to_bus = 1 THEN '1_Retorno Validado METRO -> BUS (Simétrico)'
                WHEN a.has_metro_only = 1 THEN '2_Retorno SOLO METRO (Sin validar Bus alimentador)'
                WHEN a.has_bus_start = 1 THEN '3_Retorno iniciado en BUS (Otro modo)'
                WHEN a.pm_trip_count > 0 THEN '4_Otro retorno validado (ZP/Metrotren)'
                ELSE '5_Sin retorno validado en la tarde'
            END AS comportamiento_retorno,
            COUNT(*) AS n_tarjetas_dia,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS pct
        FROM morning_bm m
        LEFT JOIN afternoon_trips a USING (id_tarjeta, dia)
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    print(df_cards.to_string(index=False))

    print("\n=== 3. TOP 12 METRO STATIONS BY DAILY ASYMMETRIC RETURN GAP (2023 WORKDAYS) ===")
    df_stations = con.execute(
        """
        WITH bm AS (
            SELECT paradero_subida_2 AS estacion, COUNT(*) / 5.0 AS bm_diario
            FROM v23_full
            WHERE tipo_dia = 'LABORAL' AND n_etapas = 2 AND modo_1 = 'BUS' AND modo_2 = 'METRO'
              AND paradero_subida_2 IS NOT NULL
            GROUP BY 1
        ),
        mb AS (
            SELECT paradero_bajada_1 AS estacion, COUNT(*) / 5.0 AS mb_diario
            FROM v23_full
            WHERE tipo_dia = 'LABORAL' AND n_etapas = 2 AND modo_1 = 'METRO' AND modo_2 = 'BUS'
              AND paradero_bajada_1 IS NOT NULL
            GROUP BY 1
        )
        SELECT
            bm.estacion,
            ROUND(bm.bm_diario, 0) AS bus_a_metro_dia,
            ROUND(COALESCE(mb.mb_diario, 0), 0) AS metro_a_bus_dia,
            ROUND(bm.bm_diario - COALESCE(mb.mb_diario, 0), 0) AS brecha_diaria,
            ROUND(COALESCE(mb.mb_diario, 0) * 100.0 / NULLIF(bm.bm_diario, 0), 1) AS tasa_retorno_validado_pct
        FROM bm
        LEFT JOIN mb USING (estacion)
        WHERE bm.bm_diario >= 2000
        ORDER BY brecha_diaria DESC
        LIMIT 12
        """
    ).df()
    print(df_stations.to_string(index=False))


if __name__ == "__main__":
    run_intermodal()
