"""Post-Pandemic Peak Compression & Nighttime Contraction (2019 vs. 2023).

Runs a 100% full-week DuckDB analysis (zero LIMIT) across April 2019 and April 2023 to:
1. Quantify the shift in daily Metro demand across all 5 time blocks (Madrugada, Punta Mañana,
   Valle Día, Punta Tarde, Noche 20-24) on workdays (`LABORAL`).
2. Compare half-hourly peak timing and nighttime contraction by trip purpose (`TRABAJO`, `ESTUDIO`, `HOGAR`).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

import duckdb
from metro_od.deep_dive import _full_viajes_views_sql


def run_temporal():
    con = duckdb.connect()
    con.execute(_full_viajes_views_sql())

    print("=== 1. FULL-WEEK WORKDAY METRO DEMAND BY TIME BLOCK (2019 vs 2023) ===")
    df_blocks = con.execute(
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
    print(df_blocks.to_string(index=False))

    print("\n=== 2. NIGHTTIME (20:00-24:00) METRO CONTRACTION BY TRIP PURPOSE ===")
    df_night = con.execute(
        """
        WITH m AS (
            SELECT
                anio,
                CAST(t_inicio AS DATE) AS dia,
                COALESCE(proposito, 'OTROS') AS proposito,
                COALESCE(factor_expansion, 0) AS w
            FROM (SELECT * FROM v19_full UNION ALL SELECT * FROM v23_full)
            WHERE tipo_dia = 'LABORAL' AND modo_1 = 'METRO' AND mh_inicio >= TIME '20:00:00'
        ),
        daily AS (
            SELECT anio, proposito, SUM(w) / COUNT(DISTINCT dia) AS daily_trips
            FROM m GROUP BY 1, 2
        )
        SELECT
            proposito,
            ROUND(MAX(CASE WHEN anio='2019' THEN daily_trips END), 0) AS noche_dia_2019,
            ROUND(MAX(CASE WHEN anio='2023' THEN daily_trips END), 0) AS noche_dia_2023,
            ROUND(
                (MAX(CASE WHEN anio='2023' THEN daily_trips END) - MAX(CASE WHEN anio='2019' THEN daily_trips END)) * 100.0
                / NULLIF(MAX(CASE WHEN anio='2019' THEN daily_trips END), 0), 2
            ) AS var_pct
        FROM daily
        GROUP BY proposito
        ORDER BY noche_dia_2019 DESC NULLS LAST
        """
    ).df()
    print(df_night.to_string(index=False))


if __name__ == "__main__":
    run_temporal()
