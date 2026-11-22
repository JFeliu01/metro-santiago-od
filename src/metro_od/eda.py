"""Exploratory Data Analysis (EDA) & Hypothesis Testing queries for Metro de Santiago (2019 vs 2023).

Implements 5 core analytical angles:
1. Temporal demand structure & post-pandemic peak compression / night drop (2019 vs 2023).
2. Stage-level travel time decomposition (Access, Wait, In-Vehicle, Transfer Walk, Egress)
   and the asymmetric Bus->Metro vs Metro->Bus transfer penalty.
3. Unestimated destination dropoff rates (`ultima_etapa_con_bajada = 0`) across modes and years.
4. Daily trip-chaining & 1-trip card asymmetry (`id_tarjeta` in 2023).
5. Internal Metro station access/egress & platform wait friction.
"""

from __future__ import annotations

from typing import Dict

import duckdb
import pandas as pd

from .catalog import DATASETS
from .clean import build_clean_viajes_query


def run_eda_suite(sample_rows: int = 150_000, full_2023_scan: bool = False) -> Dict[str, pd.DataFrame]:
    """Execute the 5-angle EDA suite and return a dictionary of summary DataFrames."""
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE v19 AS " + build_clean_viajes_query("2019", limit=sample_rows))
    con.execute("CREATE TEMP TABLE v23 AS " + build_clean_viajes_query("2023", limit=sample_rows))

    # 1. Temporal block share shift (2019 vs 2023 Metro)
    temporal_shift_df = con.execute(
        """
        WITH m AS (
            SELECT
                anio,
                CASE
                    WHEN mediahora_inicio_viaje >= TIME '06:00:00' AND mediahora_inicio_viaje < TIME '09:00:00' THEN '1_Punta_Manana (06-09)'
                    WHEN mediahora_inicio_viaje >= TIME '09:00:00' AND mediahora_inicio_viaje < TIME '17:00:00' THEN '2_Valle_Dia (09-17)'
                    WHEN mediahora_inicio_viaje >= TIME '17:00:00' AND mediahora_inicio_viaje < TIME '20:00:00' THEN '3_Punta_Tarde (17-20)'
                    WHEN mediahora_inicio_viaje >= TIME '20:00:00' THEN '4_Noche (20-24)'
                    ELSE '0_Madrugada (00-06)'
                END AS bloque,
                COALESCE(factor_expansion, 1.0) AS w
            FROM (SELECT * FROM v19 UNION ALL SELECT * FROM v23)
            WHERE tipo_transporte_1 = 'METRO'
        )
        SELECT
            bloque,
            ROUND(SUM(CASE WHEN anio='2019' THEN w ELSE 0 END) * 100.0 / SUM(SUM(CASE WHEN anio='2019' THEN w ELSE 0 END)) OVER (), 2) AS share_2019_pct,
            ROUND(SUM(CASE WHEN anio='2023' THEN w ELSE 0 END) * 100.0 / SUM(SUM(CASE WHEN anio='2023' THEN w ELSE 0 END)) OVER (), 2) AS share_2023_pct,
            ROUND(
                (SUM(CASE WHEN anio='2023' THEN w ELSE 0 END) * 100.0 / SUM(SUM(CASE WHEN anio='2023' THEN w ELSE 0 END)) OVER ())
                - (SUM(CASE WHEN anio='2019' THEN w ELSE 0 END) * 100.0 / SUM(SUM(CASE WHEN anio='2019' THEN w ELSE 0 END)) OVER ()),
                2
            ) AS delta_pp
        FROM m
        GROUP BY bloque
        ORDER BY bloque
        """
    ).df()

    # 2. 2023 Travel Time Decomposition (Access, Wait, In-Vehicle, Transfer Walk, Egress)
    v23_path = DATASETS["viajes_2023"].path.as_posix()
    limit_clause = "" if full_2023_scan else f"LIMIT {int(sample_rows)}"
    time_decomp_df = con.execute(
        f"""
        WITH raw_v23 AS (
            SELECT *
            FROM read_csv('{v23_path}', delim=',', header=true, nullstr=['-', ''], all_varchar=true, ignore_errors=true)
            {limit_clause}
        )
        SELECT
            CASE TRY_CAST(tipo_transporte_1 AS INT)
                WHEN 1 THEN 'BUS' WHEN 2 THEN 'METRO' WHEN 3 THEN 'ZONA PAGA' WHEN 4 THEN 'METROTREN'
            END AS modo_1,
            CASE TRY_CAST(tipo_transporte_2 AS INT)
                WHEN 1 THEN 'BUS' WHEN 2 THEN 'METRO' WHEN 3 THEN 'ZONA PAGA' WHEN 4 THEN 'METROTREN'
                ELSE '-'
            END AS modo_2,
            TRY_CAST(n_etapas AS INT) AS etapas,
            COUNT(*) AS n_viajes,
            ROUND(AVG(TRY_CAST(entrada AS DOUBLE)) / 60.0, 2) AS avg_entrada_min,
            ROUND(AVG(TRY_CAST(te0 AS DOUBLE)) / 60.0, 2) AS avg_espera1_min,
            ROUND(AVG(TRY_CAST(tv1 AS DOUBLE)) / 60.0, 2) AS avg_vehiculo1_min,
            ROUND(AVG(TRY_CAST(tc1 AS DOUBLE)) / 60.0, 2) AS avg_caminata_trasb_min,
            ROUND(AVG(TRY_CAST(te1 AS DOUBLE)) / 60.0, 2) AS avg_espera2_min,
            ROUND(AVG(TRY_CAST(tv2 AS DOUBLE)) / 60.0, 2) AS avg_vehiculo2_min,
            ROUND(AVG(TRY_CAST(egreso AS DOUBLE)) / 60.0, 2) AS avg_egreso_min,
            ROUND(AVG(TRY_CAST(tviaje2 AS DOUBLE)) / 60.0, 2) AS avg_total_min
        FROM raw_v23
        WHERE TRY_CAST(n_etapas AS INT) IN (1, 2) AND TRY_CAST(tviaje2 AS DOUBLE) > 0
        GROUP BY 1, 2, 3
        HAVING COUNT(*) >= 50
        ORDER BY etapas, n_viajes DESC
        """
    ).df()

    # 3. Unestimated Destinations (Alighting Dropoff Rate by Mode & Year)
    dropoff_df = con.execute(
        """
        SELECT
            anio,
            tipo_transporte_1 AS modo,
            COUNT(*) AS total_viajes,
            ROUND(AVG(CASE WHEN ultima_etapa_con_bajada = 0 THEN 100.0 ELSE 0.0 END), 2) AS sin_bajada_pct,
            ROUND(AVG(CASE WHEN factor_expansion = 0 OR factor_expansion IS NULL THEN 100.0 ELSE 0.0 END), 2) AS factor_exp_cero_pct
        FROM (SELECT * FROM v19 UNION ALL SELECT * FROM v23)
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).df()

    # 4. 2023 Card Trip Chaining & One-Way Asymmetry (id_tarjeta)
    chaining_df = con.execute(
        """
        WITH card_days AS (
            SELECT
                id_tarjeta,
                DATE_TRUNC('day', tiempo_inicio_viaje) AS dia,
                COUNT(*) AS viajes_dia,
                SUM(CASE WHEN tipo_transporte_1 = 'METRO' THEN 1 ELSE 0 END) AS inicios_metro,
                SUM(CASE WHEN tipo_transporte_1 = 'BUS' THEN 1 ELSE 0 END) AS inicios_bus
            FROM v23
            WHERE id_tarjeta IS NOT NULL AND tiempo_inicio_viaje IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT
            viajes_dia,
            COUNT(*) AS n_tarjetas_dia,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS pct_tarjetas,
            ROUND(AVG(inicios_metro * 100.0 / viajes_dia), 1) AS pct_inicios_metro
        FROM card_days
        WHERE viajes_dia <= 6
        GROUP BY 1
        ORDER BY 1
        """
    ).df()

    # 5. Metro Station Access, Platform Wait & Egress Friction
    station_friction_df = con.execute(
        f"""
        WITH raw_v23 AS (
            SELECT *
            FROM read_csv('{v23_path}', delim=',', header=true, nullstr=['-', ''], all_varchar=true, ignore_errors=true)
            {limit_clause}
        )
        SELECT
            paradero_inicio_viaje AS estacion_metro,
            COUNT(*) AS n_obs,
            ROUND(AVG(TRY_CAST(entrada AS DOUBLE)), 1) AS entrada_seg,
            ROUND(AVG(TRY_CAST(te0 AS DOUBLE)), 1) AS espera_anden_seg,
            ROUND(AVG(TRY_CAST(egreso AS DOUBLE)), 1) AS egreso_destino_seg
        FROM raw_v23
        WHERE TRY_CAST(tipo_transporte_1 AS INT) = 2 AND TRY_CAST(entrada AS DOUBLE) > 0
        GROUP BY 1
        HAVING COUNT(*) >= 100
        ORDER BY entrada_seg DESC
        LIMIT 15
        """
    ).df()

    return {
        "temporal_shift": temporal_shift_df,
        "time_decomposition": time_decomp_df,
        "alighting_dropoff": dropoff_df,
        "card_trip_chaining": chaining_df,
        "station_friction": station_friction_df,
    }


if __name__ == "__main__":
    results = run_eda_suite(sample_rows=150_000)
    for name, df in results.items():
        print(f"\n=== {name.upper()} ===")
        print(df.to_string(index=False))
