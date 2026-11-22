"""Full-week (100% population, zero LIMIT) DuckDB deep-dive analysis for Hypotheses 1, 2, and 4.

Uses strictly comparable travel time `EPOCH(tiempo_fin - tiempo_inicio) / 60.0` across
April 2019 and April 2023 to avoid the `tviaje` vs `tviaje2` definition shift, and runs
statistical tests (Difference-in-Differences, card-level return symmetry, and temporal shifts).
"""

from __future__ import annotations

import math
from typing import Dict

import duckdb
import pandas as pd

from .catalog import DATASETS
from .clean import (
    COMUNA_CODES_2023,
    DAY_TYPE_CODES_2023,
    TRANSPORT_MODE_CODES_2023,
    _sql_clean_commune,
    _sql_decode_int_map,
)


def _full_viajes_views_sql() -> str:
    """Create lightweight, strictly comparable DuckDB views over all 7 days of 2019 and 2023 viajes CSVs."""
    p19_glob = (DATASETS["viajes_2019"].path.parent / "viajes201904*_transparencia.csv").as_posix()
    p23 = DATASETS["viajes_2023"].path.as_posix()

    return f"""
    CREATE OR REPLACE VIEW v19_full AS
    SELECT
        '2019' AS anio,
        CAST(NULL AS VARCHAR) AS id_tarjeta,
        CAST(NULL AS INTEGER) AS id_viaje,
        UPPER(TRIM(tipodia)) AS tipo_dia,
        TRY_CAST(factorexpansion AS DOUBLE) AS factor_expansion,
        TRY_CAST(netapa AS INTEGER) AS n_etapas,
        TRY_CAST(ultimaetapaconbajada AS INTEGER) AS ultima_bajada,
        TRY_CAST(tiemposubida AS TIMESTAMP) AS t_inicio,
        TRY_CAST(tiempobajada AS TIMESTAMP) AS t_fin,
        TRY_CAST(mediahora AS TIME) AS mh_inicio,
        {_sql_clean_commune('comunasubida')} AS comuna_origen,
        {_sql_clean_commune('comunabajada')} AS comuna_destino,
        NULLIF(TRIM(paraderosubida), '') AS paradero_origen,
        NULLIF(TRIM(paraderobajada), '') AS paradero_destino,
        NULLIF(TRIM(paraderobajada_1era), '') AS paradero_bajada_1,
        NULLIF(TRIM(paraderosubida_2da), '') AS paradero_subida_2,
        CASE UPPER(TRIM(tipotransporte_1era)) WHEN 'ZP' THEN 'ZONA PAGA' ELSE UPPER(TRIM(tipotransporte_1era)) END AS modo_1,
        CASE UPPER(TRIM(tipotransporte_2da)) WHEN 'ZP' THEN 'ZONA PAGA' ELSE UPPER(TRIM(tipotransporte_2da)) END AS modo_2,
        UPPER(TRIM(proposito)) AS proposito,
        CASE
            WHEN TRY_CAST(tiemposubida AS TIMESTAMP) IS NOT NULL AND TRY_CAST(tiempobajada AS TIMESTAMP) IS NOT NULL
                 AND EPOCH(TRY_CAST(tiempobajada AS TIMESTAMP) - TRY_CAST(tiemposubida AS TIMESTAMP)) BETWEEN 60 AND 18000
            THEN EPOCH(TRY_CAST(tiempobajada AS TIMESTAMP) - TRY_CAST(tiemposubida AS TIMESTAMP)) / 60.0
            ELSE NULL
        END AS duracion_obs_min
    FROM read_csv('{p19_glob}', delim=';', header=true, nullstr=['-', '', 'NULL'], all_varchar=true, ignore_errors=true);

    CREATE OR REPLACE VIEW v23_full AS
    SELECT
        '2023' AS anio,
        NULLIF(TRIM(id_tarjeta), '') AS id_tarjeta,
        TRY_CAST(id_viaje AS INTEGER) AS id_viaje,
        {_sql_decode_int_map('tipodia', DAY_TYPE_CODES_2023)} AS tipo_dia,
        TRY_CAST(factor_expansion AS DOUBLE) AS factor_expansion,
        TRY_CAST(n_etapas AS INTEGER) AS n_etapas,
        TRY_CAST(ultimaetapaconbajada AS INTEGER) AS ultima_bajada,
        TRY_CAST(tiempo_inicio_viaje AS TIMESTAMP) AS t_inicio,
        TRY_CAST(tiempo_fin_viaje AS TIMESTAMP) AS t_fin,
        TRY_CAST(mediahora_inicio_viaje_hora AS TIME) AS mh_inicio,
        {_sql_decode_int_map('comuna_inicio_viaje', COMUNA_CODES_2023)} AS comuna_origen,
        {_sql_decode_int_map('comuna_fin_viaje', COMUNA_CODES_2023)} AS comuna_destino,
        NULLIF(TRIM(paradero_inicio_viaje), '') AS paradero_origen,
        NULLIF(TRIM(paradero_fin_viaje), '') AS paradero_destino,
        NULLIF(TRIM(paradero_bajada_1), '') AS paradero_bajada_1,
        NULLIF(TRIM(paradero_subida_2), '') AS paradero_subida_2,
        {_sql_decode_int_map('tipo_transporte_1', TRANSPORT_MODE_CODES_2023)} AS modo_1,
        {_sql_decode_int_map('tipo_transporte_2', TRANSPORT_MODE_CODES_2023)} AS modo_2,
        UPPER(TRIM(proposito)) AS proposito,
        CASE
            WHEN TRY_CAST(tiempo_inicio_viaje AS TIMESTAMP) IS NOT NULL AND TRY_CAST(tiempo_fin_viaje AS TIMESTAMP) IS NOT NULL
                 AND EPOCH(TRY_CAST(tiempo_fin_viaje AS TIMESTAMP) - TRY_CAST(tiempo_inicio_viaje AS TIMESTAMP)) BETWEEN 60 AND 18000
            THEN EPOCH(TRY_CAST(tiempo_fin_viaje AS TIMESTAMP) - TRY_CAST(tiempo_inicio_viaje AS TIMESTAMP)) / 60.0
            ELSE NULL
        END AS duracion_obs_min
    FROM read_csv('{p23}', delim=',', header=true, nullstr=['-', '', 'NULL'], all_varchar=true, ignore_errors=true);
    """


def run_full_deep_dive() -> Dict[str, pd.DataFrame]:
    """Run 100% full-file DuckDB analysis across 2019 and 2023 for Hypotheses 1, 2, and 4."""
    con = duckdb.connect()
    con.execute(_full_viajes_views_sql())

    results: Dict[str, pd.DataFrame] = {}

    # 0. Full Dataset Coverage (Dates & Day Types)
    results["0_coverage"] = con.execute(
        """
        SELECT anio, tipo_dia,
            COUNT(*) AS total_rows,
            COUNT(DISTINCT CAST(t_inicio AS DATE)) AS n_days,
            MIN(CAST(t_inicio AS DATE)) AS min_date,
            MAX(CAST(t_inicio AS DATE)) AS max_date,
            ROUND(SUM(COALESCE(factor_expansion, 0)) / NULLIF(COUNT(DISTINCT CAST(t_inicio AS DATE)), 0), 0) AS avg_daily_expanded_trips,
            ROUND(AVG(CASE WHEN ultima_bajada = 0 THEN 100.0 ELSE 0.0 END), 2) AS sin_bajada_pct
        FROM (SELECT * FROM v19_full UNION ALL SELECT * FROM v23_full)
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).df()

    # 1A. Hypothesis 1 — Full-Week Asymmetric Transfer Ratio (BUS->METRO vs METRO->BUS, 2019 vs 2023, LABORAL)
    results["h1_asymmetry_2019_vs_2023"] = con.execute(
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

    # 1B. Hypothesis 1 — 2023 Card-Level Return Trip Proof (Morning BUS->METRO riders' afternoon return behavior!)
    results["h1_card_return_proof_2023"] = con.execute(
        """
        WITH morning_bm AS (
            SELECT DISTINCT id_tarjeta, CAST(t_inicio AS DATE) AS dia, paradero_subida_2 AS estacion_entrada_metro
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

    # 1C. Hypothesis 1 — Top 10 Metro Stations with Largest Morning BUS->METRO vs Afternoon METRO->BUS Gap (2023)
    results["h1_top_asymmetric_stations_2023"] = con.execute(
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

    # 2. Hypothesis 2 — Full-Week LABORAL Temporal Shift & Nighttime Contraction (2019 vs 2023 Metro)
    results["h2_temporal_full_laboral"] = con.execute(
        """
        WITH m AS (
            SELECT
                anio,
                CAST(t_inicio AS DATE) AS dia,
                CASE
                    WHEN mh_inicio >= TIME '06:00:00' AND mh_inicio < TIME '09:00:00' THEN '1_Punta_Manana (06-09)'
                    WHEN mh_inicio >= TIME '09:00:00' AND mh_inicio < TIME '17:00:00' THEN '2_Valle_Dia (09-17)'
                    WHEN mh_inicio >= TIME '17:00:00' AND mh_inicio < TIME '20:00:00' THEN '3_Punta_Tarde (17-20)'
                    WHEN mh_inicio >= TIME '20:00:00' THEN '4_Noche (20-24)'
                    ELSE '0_Madrugada (00-06)'
                END AS bloque,
                COALESCE(factor_expansion, 0) AS w
            FROM (SELECT * FROM v19_full UNION ALL SELECT * FROM v23_full)
            WHERE tipo_dia = 'LABORAL' AND modo_1 = 'METRO' AND mh_inicio IS NOT NULL
        ),
        daily AS (
            SELECT anio, bloque, SUM(w) / COUNT(DISTINCT dia) AS daily_trips
            FROM m
            GROUP BY 1, 2
        )
        SELECT
            bloque,
            ROUND(MAX(CASE WHEN anio='2019' THEN daily_trips END), 0) AS trips_dia_2019,
            ROUND(MAX(CASE WHEN anio='2023' THEN daily_trips END), 0) AS trips_dia_2023,
            ROUND(
                (MAX(CASE WHEN anio='2023' THEN daily_trips END) - MAX(CASE WHEN anio='2019' THEN daily_trips END)) * 100.0
                / NULLIF(MAX(CASE WHEN anio='2019' THEN daily_trips END), 0), 2
            ) AS var_pct,
            ROUND(MAX(CASE WHEN anio='2019' THEN daily_trips END) * 100.0 / SUM(MAX(CASE WHEN anio='2019' THEN daily_trips END)) OVER (), 2) AS share_2019_pct,
            ROUND(MAX(CASE WHEN anio='2023' THEN daily_trips END) * 100.0 / SUM(MAX(CASE WHEN anio='2023' THEN daily_trips END)) OVER (), 2) AS share_2023_pct
        FROM daily
        GROUP BY bloque
        ORDER BY bloque
        """
    ).df()

    # 3. Hypothesis 4 — Full-Week LABORAL Difference-in-Differences (L3/L6 Expansion vs Control Communes)
    results["h4_did_l3l6_full"] = con.execute(
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

    # Aggregate DiD summary with Welch standard error
    did_agg = con.execute(
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
    results["h4_did_summary_stats"] = did_agg

    return results


if __name__ == "__main__":
    out = run_full_deep_dive()
    for name, df in out.items():
        print(f"\n================ {name.upper()} ================")
        print(df.to_string(index=False))
