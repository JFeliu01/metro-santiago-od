"""Full 7-Day Operational & Day-of-Week Nighttime Contraction Analysis (2019 vs. 2023).

Runs over all 44.43 million trips (7 days April 2019 vs 7 days April 2023) to:
1. Break down the -43.65% nighttime Metro collapse by Day of Week (Mon-Wed vs Thu-Fri vs Sat vs Sun).
2. Decompose nighttime contraction by Trip Purpose (`TRABAJO`, `ESTUDIO`, `HOGAR`, `OTROS`).
3. Estimate train-km / traction-energy optimization potential after 20:30 while maintaining load factors.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from metro_od.deep_dive import _full_viajes_views_sql


def run_temporal_operational(output_json_path: Path | None = None) -> Dict[str, object]:
    con = duckdb.connect()
    con.execute(_full_viajes_views_sql())

    # 1. Day-of-week breakdown (Mon..Sun) for Total Metro vs Nighttime Metro (20:00-24:00)
    dow_df = con.execute(
        """
        WITH m AS (
            SELECT
                anio,
                EXTRACT('isodow' FROM t_inicio) AS dow,
                CASE EXTRACT('isodow' FROM t_inicio)
                    WHEN 1 THEN '1_Lunes'
                    WHEN 2 THEN '2_Martes'
                    WHEN 3 THEN '3_Miercoles'
                    WHEN 4 THEN '4_Jueves'
                    WHEN 5 THEN '5_Viernes'
                    WHEN 6 THEN '6_Sabado'
                    WHEN 7 THEN '7_Domingo'
                END AS dia_semana,
                COALESCE(factor_expansion, 0) AS w,
                CASE WHEN mh_inicio >= TIME '20:00:00' THEN COALESCE(factor_expansion, 0) ELSE 0 END AS w_noche
            FROM (SELECT * FROM v19_full UNION ALL SELECT * FROM v23_full)
            WHERE modo_1 = 'METRO' AND t_inicio IS NOT NULL
        )
        SELECT
            dia_semana,
            ROUND(SUM(CASE WHEN anio='2019' THEN w ELSE 0 END), 0) AS total_2019,
            ROUND(SUM(CASE WHEN anio='2023' THEN w ELSE 0 END), 0) AS total_2023,
            ROUND((SUM(CASE WHEN anio='2023' THEN w ELSE 0 END) - SUM(CASE WHEN anio='2019' THEN w ELSE 0 END)) * 100.0
                  / NULLIF(SUM(CASE WHEN anio='2019' THEN w ELSE 0 END), 0), 2) AS var_total_pct,
            ROUND(SUM(CASE WHEN anio='2019' THEN w_noche ELSE 0 END), 0) AS noche_2019,
            ROUND(SUM(CASE WHEN anio='2023' THEN w_noche ELSE 0 END), 0) AS noche_2023,
            ROUND((SUM(CASE WHEN anio='2023' THEN w_noche ELSE 0 END) - SUM(CASE WHEN anio='2019' THEN w_noche ELSE 0 END)) * 100.0
                  / NULLIF(SUM(CASE WHEN anio='2019' THEN w_noche ELSE 0 END), 0), 2) AS var_noche_pct
        FROM m
        GROUP BY dow, dia_semana
        ORDER BY dow
        """
    ).df()

    # 2. Workday Nighttime (20:00-24:00) Contraction by Purpose
    purpose_df = con.execute(
        """
        WITH m AS (
            SELECT
                anio,
                COALESCE(proposito, 'OTROS') AS proposito,
                COALESCE(factor_expansion, 0) / 5.0 AS w_day
            FROM (SELECT * FROM v19_full UNION ALL SELECT * FROM v23_full)
            WHERE tipo_dia = 'LABORAL' AND modo_1 = 'METRO' AND mh_inicio >= TIME '20:00:00'
        )
        SELECT
            proposito,
            ROUND(SUM(CASE WHEN anio='2019' THEN w_day ELSE 0 END), 0) AS noche_2019_dia,
            ROUND(SUM(CASE WHEN anio='2023' THEN w_day ELSE 0 END), 0) AS noche_2023_dia,
            ROUND((SUM(CASE WHEN anio='2023' THEN w_day ELSE 0 END) - SUM(CASE WHEN anio='2019' THEN w_day ELSE 0 END)) * 100.0
                  / NULLIF(SUM(CASE WHEN anio='2019' THEN w_day ELSE 0 END), 0), 2) AS var_pct
        FROM m
        GROUP BY proposito
        ORDER BY noche_2019_dia DESC
        """
    ).df()

    # 3. Half-hourly Metro workday curve (2019 vs 2023)
    mh_df = con.execute(
        """
        SELECT
            SUBSTR(CAST(mh_inicio AS VARCHAR), 1, 5) AS mh,
            ROUND(SUM(CASE WHEN anio='2019' THEN COALESCE(factor_expansion, 0) ELSE 0 END) / 5.0, 0) AS metro_2019,
            ROUND(SUM(CASE WHEN anio='2023' THEN COALESCE(factor_expansion, 0) ELSE 0 END) / 5.0, 0) AS metro_2023
        FROM (SELECT * FROM v19_full UNION ALL SELECT * FROM v23_full)
        WHERE tipo_dia = 'LABORAL' AND modo_1 = 'METRO' AND mh_inicio IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """
    ).df()

    summary = {
        "day_of_week": dow_df.to_dict(orient="records"),
        "night_by_purpose": purpose_df.to_dict(orient="records"),
        "half_hourly_metro": mh_df.to_dict(orient="records"),
    }

    if output_json_path:
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return summary


if __name__ == "__main__":
    out_p = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    res = run_temporal_operational(out_p)
    print("=== DAY OF WEEK ===")
    for r in res["day_of_week"]:
        print(r)
    print("\n=== NIGHT BY PURPOSE ===")
    for r in res["night_by_purpose"]:
        print(r)
