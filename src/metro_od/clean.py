"""Out-of-core data cleaning, schema harmonization, and Parquet export pipeline using DuckDB & Polars.

Addresses all data quality issues identified during the table describe phase:
1. Delimiter normalization (';', ',', and mixed '|;' in 2019 boarding/alighting matrices)
   plus removal of the trailing empty column ('Unnamed: 100') in 2023 viajes.
2. Sentinel & null handling ('-', '', '-1' codes, and malformed '2010-01-00 -' timestamps).
3. Mixed CRS normalization in `etapas`: separates UTM Zone 19S meters (Bus & 2019 Metro)
   from WGS84 (lat, lon) stored in (x, y) on 2023 Metro rows.
4. Distance outlier masking: masks negative route distances (e.g. -20601 m) and ~6.31M meter
   Euclidean distances caused by (0, 0) UTM coordinate fallbacks.
5. Zone ID normalization: converts float-strings ('"77.0"') to integer zone IDs (77).
6. Commune & categorical harmonization across 2019 (truncated strings like 'LA FLORI')
   and 2023 (integer codes in `viajes_2023` decoded via `COMUNA_CODES_2023` & `PERIODO_CODES_2023`).
7. 2023 Excel Binary Workbook (.xlsb) extraction (`header_row=1`) for Subidas and Bajadas matrices.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .catalog import DATASETS, DATA_DIR, PROJECT_ROOT

# Canonical expansion for 8-character truncated commune names in 2019 tables
COMMUNE_NORMALIZATION: Dict[str, str] = {
    "LA FLORI": "LA FLORIDA",
    "ESTACION": "ESTACION CENTRAL",
    "ESTACIÓN CENTRAL": "ESTACION CENTRAL",
    "SAN BERN": "SAN BERNARDO",
    "PUENTE A": "PUENTE ALTO",
    "SAN MIGU": "SAN MIGUEL",
    "PROVIDEN": "PROVIDENCIA",
    "LAS COND": "LAS CONDES",
    "QUILICUR": "QUILICURA",
    "PENALOLE": "PENALOLEN",
    "PEÑALOLEN": "PENALOLEN",
    "PEÑALOLÉN": "PENALOLEN",
    "CONCHALI": "CONCHALI",
    "CONCHALÍ": "CONCHALI",
    "HUECHURA": "HUECHURABA",
    "INDEPEND": "INDEPENDENCIA",
    "LA CISTE": "LA CISTERNA",
    "LA GRANJ": "LA GRANJA",
    "LA PINTA": "LA PINTANA",
    "LO BARNE": "LO BARNECHEA",
    "LO ESPEJ": "LO ESPEJO",
    "PEDRO AG": "PEDRO AGUIRRE CERDA",
    "QUINTA N": "QUINTA NORMAL",
    "SAN JOAQ": "SAN JOAQUIN",
    "SAN JOAQUÍN": "SAN JOAQUIN",
    "SAN RAMO": "SAN RAMON",
    "SAN RAMÓN": "SAN RAMON",
    "MAIPÚ": "MAIPU",
    "ÑUÑOA": "NUNOA",
    "NUNOA": "NUNOA",
}

# 34-commune integer code lookup for `viajes_2023` (verified by cross-joining `etapas_2023` via Zona 777)
COMUNA_CODES_2023: Dict[int, str] = {
    2: "LO BARNECHEA",
    3: "LAS CONDES",
    4: "PENALOLEN",
    5: "LA FLORIDA",
    6: "PUENTE ALTO",
    8: "SAN BERNARDO",
    14: "MAIPU",
    15: "PUDAHUEL",
    16: "ESTACION CENTRAL",
    17: "LO PRADO",
    18: "CERRO NAVIA",
    19: "RENCA",
    20: "QUILICURA",
    21: "HUECHURABA",
    22: "VITACURA",
    23: "PROVIDENCIA",
    24: "LA REINA",
    25: "NUNOA",
    26: "MACUL",
    27: "SAN JOAQUIN",
    28: "LA GRANJA",
    29: "LA PINTANA",
    30: "EL BOSQUE",
    31: "LO ESPEJO",
    32: "CERRILLOS",
    33: "PEDRO AGUIRRE CERDA",
    34: "SANTIAGO",
    35: "QUINTA NORMAL",
    36: "INDEPENDENCIA",
    37: "CONCHALI",
    38: "RECOLETA",
    39: "SAN MIGUEL",
    40: "SAN RAMON",
    41: "LA CISTERNA",
}

# Time-period integer code lookup for `viajes_2023`
PERIODO_CODES_2023: Dict[int, str] = {
    59: "001 - PRE NOCTURNO MADRUGADA",
    60: "002 - NOCTURNO",
    61: "003 - TRANSICION NOCTURNO",
    62: "004 - PUNTA MANANA",
    63: "005 - TRANSICION PUNTA MANANA",
    64: "006 - FUERA PUNTA MANANA",
    65: "007 - PUNTA MEDIODIA",
    66: "008 - FUERA DE PUNTA TARDE",
    67: "009 - PUNTA TARDE 1",
    68: "010 - PUNTA TARDE 2",
    69: "011 - FUERA DE PUNTA NOCTURNO",
    70: "012 - PRE NOCTURNO NOCHE",
}

TRANSPORT_MODE_CODES_2023: Dict[int, str] = {
    1: "BUS",
    2: "METRO",
    3: "ZONA PAGA",
    4: "METROTREN",
}

DAY_TYPE_CODES_2023: Dict[int, str] = {
    0: "LABORAL",
    1: "SABADO",
    2: "DOMINGO",
}


def _sql_clean_commune(col_expr: str) -> str:
    """Return a DuckDB SQL expression that normalizes commune names (accents + 8-char truncations)."""
    base = f"UPPER(TRANSLATE(TRIM({col_expr}), 'ÁÉÍÓÚÑáéíóúñ', 'AEIOUNaeioun'))"
    cases = "\n        ".join(
        f"WHEN {base} = '{k}' THEN '{v}'"
        for k, v in COMMUNE_NORMALIZATION.items()
        if k.isascii()
    )
    return f"""CASE
        WHEN {col_expr} IS NULL OR TRIM({col_expr}) IN ('', '-') THEN NULL
        {cases}
        ELSE {base}
    END"""


def _sql_decode_int_map(col_expr: str, mapping: Dict[int, str]) -> str:
    """Return a DuckDB SQL CASE expression decoding integer codes to canonical labels."""
    cases = "\n        ".join(f"WHEN {k} THEN '{v}'" for k, v in sorted(mapping.items()))
    return f"""CASE TRY_CAST({col_expr} AS INTEGER)
        {cases}
        ELSE NULL
    END"""


def build_clean_etapas_query(year: str, limit: Optional[int] = None) -> str:
    """Return a DuckDB SQL query that cleans and harmonizes `etapas_2019` or `etapas_2023`."""
    spec = DATASETS[f"etapas_{year}"]
    csv_path = (
        (spec.path.parent / "etapas201904*_transparencia.csv").as_posix()
        if year == "2019"
        else spec.path.as_posix()
    )
    limit_clause = f"LIMIT {int(limit)}" if limit else ""

    extra_2023_cols = (
        """
        NULLIF(UPPER(TRANSLATE(TRIM(periodosubida), 'ÁÉÍÓÚÑáéíóúñ', 'AEIOUNaeioun')), '-') AS periodo_subida,
        NULLIF(UPPER(TRANSLATE(TRIM(periodobajada), 'ÁÉÍÓÚÑáéíóúñ', 'AEIOUNaeioun')), '-') AS periodo_bajada,
        TRY_CAST(NULLIF(TRIM(tiempoiniexpedicion), '-') AS TIMESTAMP) AS tiempo_ini_expedicion
        """
        if year == "2023"
        else """
        CAST(NULL AS VARCHAR) AS periodo_subida,
        CAST(NULL AS VARCHAR) AS periodo_bajada,
        CAST(NULL AS TIMESTAMP) AS tiempo_ini_expedicion
        """
    )

    return f"""
    WITH raw AS (
        SELECT *
        FROM read_csv(
            '{csv_path}',
            delim='{spec.delimiter}',
            header=true,
            nullstr=['-', '', 'NULL'],
            all_varchar=true,
            ignore_errors=true
        )
        {limit_clause}
    ),
    typed AS (
        SELECT
            '{year}' AS anio,
            TRY_CAST(op AS INTEGER) AS op,
            TRY_CAST(netapa AS INTEGER) AS netapa,
            UPPER(TRIM(tipo_dia)) AS tipo_dia,
            CASE UPPER(TRIM(tipo_transporte))
                WHEN 'ZP' THEN 'ZONA PAGA'
                ELSE UPPER(TRIM(tipo_transporte))
            END AS tipo_transporte,
            TRY_CAST(f_exp_servicioperiodots AS DOUBLE) AS f_exp_servicioperiodots,
            TRY_CAST(tiene_bajada AS INTEGER) AS tiene_bajada,
            TRY_CAST(tiempo_subida AS TIMESTAMP) AS tiempo_subida,
            TRY_CAST(tiempo_bajada AS TIMESTAMP) AS tiempo_bajada,
            TRY_CAST(tiempo_etapa AS DOUBLE) AS tiempo_etapa_seg,
            TRY_CAST(mh_subida AS TIME) AS mh_subida,
            TRY_CAST(mh_bajada AS TIME) AS mh_bajada,
            TRY_CAST(x_subida AS DOUBLE) AS raw_x_subida,
            TRY_CAST(y_subida AS DOUBLE) AS raw_y_subida,
            TRY_CAST(x_bajada AS DOUBLE) AS raw_x_bajada,
            TRY_CAST(y_bajada AS DOUBLE) AS raw_y_bajada,
            TRY_CAST(dist_ruta_paraderos AS INTEGER) AS raw_dist_ruta_m,
            TRY_CAST(dist_eucl_paraderos AS INTEGER) AS raw_dist_eucl_m,
            NULLIF(TRIM(servicio_subida), '') AS servicio_subida,
            NULLIF(TRIM(servicio_bajada), '') AS servicio_bajada,
            NULLIF(TRIM(par_subida), '') AS par_subida,
            NULLIF(TRIM(par_bajada), '') AS par_bajada,
            {_sql_clean_commune('comuna_subida')} AS comuna_subida,
            {_sql_clean_commune('comuna_bajada')} AS comuna_bajada,
            CAST(ROUND(TRY_CAST(zona_subida AS DOUBLE)) AS INTEGER) AS zona_subida,
            CAST(ROUND(TRY_CAST(zona_bajada AS DOUBLE)) AS INTEGER) AS zona_bajada,
            NULLIF(TRIM(sitio_subida), '') AS sitio_subida,
            TRY_CAST(fexpansionzonaperiodots AS DOUBLE) AS fexpansion_zona_periodo,
            TRY_CAST(tesperamediaintervalo AS DOUBLE) AS tespera_media_intervalo_min,
            {extra_2023_cols}
        FROM raw
    )
    SELECT
        anio,
        op,
        netapa,
        tipo_dia,
        tipo_transporte,
        f_exp_servicioperiodots,
        tiene_bajada,
        tiempo_subida,
        tiempo_bajada,
        CASE WHEN tiempo_etapa_seg >= 0 AND tiempo_etapa_seg <= 21600 THEN tiempo_etapa_seg ELSE NULL END AS tiempo_etapa_seg,
        mh_subida,
        mh_bajada,
        CASE WHEN raw_x_subida BETWEEN 250000 AND 450000 THEN raw_x_subida ELSE NULL END AS utm_x_subida,
        CASE WHEN raw_y_subida BETWEEN 6200000 AND 6400000 THEN raw_y_subida ELSE NULL END AS utm_y_subida,
        CASE WHEN raw_x_subida BETWEEN -34.5 AND -32.5 THEN raw_x_subida ELSE NULL END AS lat_subida,
        CASE WHEN raw_y_subida BETWEEN -71.5 AND -69.5 THEN raw_y_subida ELSE NULL END AS lon_subida,
        CASE WHEN raw_x_bajada BETWEEN 250000 AND 450000 THEN raw_x_bajada ELSE NULL END AS utm_x_bajada,
        CASE WHEN raw_y_bajada BETWEEN 6200000 AND 6400000 THEN raw_y_bajada ELSE NULL END AS utm_y_bajada,
        CASE WHEN raw_x_bajada BETWEEN -34.5 AND -32.5 THEN raw_x_bajada ELSE NULL END AS lat_bajada,
        CASE WHEN raw_y_bajada BETWEEN -71.5 AND -69.5 THEN raw_y_bajada ELSE NULL END AS lon_bajada,
        CASE WHEN raw_dist_ruta_m >= 0 AND raw_dist_ruta_m <= 100000 THEN raw_dist_ruta_m ELSE NULL END AS dist_ruta_paraderos_m,
        CASE WHEN raw_dist_eucl_m >= 0 AND raw_dist_eucl_m <= 100000 THEN raw_dist_eucl_m ELSE NULL END AS dist_eucl_paraderos_m,
        servicio_subida,
        servicio_bajada,
        par_subida,
        par_bajada,
        comuna_subida,
        comuna_bajada,
        zona_subida,
        zona_bajada,
        sitio_subida,
        fexpansion_zona_periodo,
        tespera_media_intervalo_min,
        periodo_subida,
        periodo_bajada,
        tiempo_ini_expedicion
    FROM typed
    """


def build_clean_viajes_query(year: str, limit: Optional[int] = None) -> str:
    """Return a DuckDB SQL query that cleans and harmonizes `viajes_2019` or `viajes_2023` into a unified schema."""
    spec = DATASETS[f"viajes_{year}"]
    csv_path = (
        (spec.path.parent / "viajes201904*_transparencia.csv").as_posix()
        if year == "2019"
        else spec.path.as_posix()
    )
    limit_clause = f"LIMIT {int(limit)}" if limit else ""

    if year == "2019":
        return f"""
        WITH raw AS (
            SELECT *
            FROM read_csv(
                '{csv_path}',
                delim=';',
                header=true,
                nullstr=['-', '', 'NULL'],
                all_varchar=true,
                ignore_errors=true
            )
            {limit_clause}
        )
        SELECT
            '2019' AS anio,
            CAST(NULL AS VARCHAR) AS id_tarjeta,
            CAST(NULL AS INTEGER) AS id_viaje,
            UPPER(TRIM(tipodia)) AS tipo_dia,
            TRY_CAST(factorexpansion AS DOUBLE) AS factor_expansion,
            TRY_CAST(netapa AS INTEGER) AS n_etapas,
            TRY_CAST(netapassinbajada AS INTEGER) AS n_etapas_sin_bajada,
            TRY_CAST(ultimaetapaconbajada AS INTEGER) AS ultima_etapa_con_bajada,
            CASE
                WHEN TRY_CAST(tviaje_min AS DOUBLE) BETWEEN 0 AND 360
                THEN TRY_CAST(tviaje_min AS DOUBLE)
                ELSE NULL
            END AS tviaje_min,
            CASE
                WHEN TRY_CAST(dviajeeuclidiana_mts AS DOUBLE) BETWEEN 0 AND 100000
                THEN TRY_CAST(dviajeeuclidiana_mts AS DOUBLE)
                ELSE NULL
            END AS distancia_eucl_m,
            CASE
                WHEN TRY_CAST(dviajeenruta_mts AS DOUBLE) BETWEEN 0 AND 100000
                THEN TRY_CAST(dviajeenruta_mts AS DOUBLE)
                ELSE NULL
            END AS distancia_ruta_m,
            TRY_CAST(tiemposubida AS TIMESTAMP) AS tiempo_inicio_viaje,
            TRY_CAST(tiempobajada AS TIMESTAMP) AS tiempo_fin_viaje,
            TRY_CAST(mediahora AS TIME) AS mediahora_inicio_viaje,
            NULLIF(TRIM(periodosubida), '') AS periodo_inicio_viaje,
            NULLIF(TRIM(periodobajada), '') AS periodo_fin_viaje,
            NULLIF(TRIM(paraderosubida), '') AS paradero_inicio_viaje,
            NULLIF(TRIM(paraderobajada), '') AS paradero_fin_viaje,
            {_sql_clean_commune('comunasubida')} AS comuna_inicio_viaje,
            {_sql_clean_commune('comunabajada')} AS comuna_fin_viaje,
            CAST(ROUND(TRY_CAST(diseno777subida AS DOUBLE)) AS INTEGER) AS zona_inicio_viaje,
            CAST(ROUND(TRY_CAST(diseno777bajada AS DOUBLE)) AS INTEGER) AS zona_fin_viaje,
            CASE UPPER(TRIM(tipotransporte_1era)) WHEN 'ZP' THEN 'ZONA PAGA' ELSE UPPER(TRIM(tipotransporte_1era)) END AS tipo_transporte_1,
            CASE UPPER(TRIM(tipotransporte_2da)) WHEN 'ZP' THEN 'ZONA PAGA' ELSE UPPER(TRIM(tipotransporte_2da)) END AS tipo_transporte_2,
            CASE UPPER(TRIM(tipotransporte_3era)) WHEN 'ZP' THEN 'ZONA PAGA' ELSE UPPER(TRIM(tipotransporte_3era)) END AS tipo_transporte_3,
            CASE UPPER(TRIM(tipotransporte_4ta)) WHEN 'ZP' THEN 'ZONA PAGA' ELSE UPPER(TRIM(tipotransporte_4ta)) END AS tipo_transporte_4,
            NULLIF(TRIM(serv_1era_etapa), '') AS srv_1,
            NULLIF(TRIM(serv_2da_etapa), '') AS srv_2,
            NULLIF(TRIM(serv_3era_etapa), '') AS srv_3,
            NULLIF(TRIM(serv_4ta_etapa), '') AS srv_4,
            UPPER(TRIM(proposito)) AS proposito,
            UPPER(TRIM(tipo_corte_etapa_viaje)) AS tipo_corte_etapa_viaje
        FROM raw
        """

    return f"""
    WITH raw AS (
        SELECT *
        FROM read_csv(
            '{csv_path}',
            delim=',',
            header=true,
            nullstr=['-', '', 'NULL'],
            all_varchar=true,
            ignore_errors=true
        )
        {limit_clause}
    )
    SELECT
        '2023' AS anio,
        NULLIF(TRIM(id_tarjeta), '') AS id_tarjeta,
        TRY_CAST(id_viaje AS INTEGER) AS id_viaje,
        {_sql_decode_int_map('tipodia', DAY_TYPE_CODES_2023)} AS tipo_dia,
        TRY_CAST(factor_expansion AS DOUBLE) AS factor_expansion,
        TRY_CAST(n_etapas AS INTEGER) AS n_etapas,
        TRY_CAST(netapassinbajada AS INTEGER) AS n_etapas_sin_bajada,
        TRY_CAST(ultimaetapaconbajada AS INTEGER) AS ultima_etapa_con_bajada,
        CASE
            WHEN TRY_CAST(tviaje2 AS DOUBLE) BETWEEN 0 AND 21600
            THEN ROUND(TRY_CAST(tviaje2 AS DOUBLE) / 60.0, 4)
            ELSE NULL
        END AS tviaje_min,
        CASE
            WHEN TRY_CAST(distancia_eucl AS DOUBLE) BETWEEN 0 AND 100000
            THEN TRY_CAST(distancia_eucl AS DOUBLE)
            ELSE NULL
        END AS distancia_eucl_m,
        CASE
            WHEN TRY_CAST(distancia_ruta AS DOUBLE) BETWEEN 0 AND 100000
            THEN TRY_CAST(distancia_ruta AS DOUBLE)
            ELSE NULL
        END AS distancia_ruta_m,
        TRY_CAST(tiempo_inicio_viaje AS TIMESTAMP) AS tiempo_inicio_viaje,
        TRY_CAST(tiempo_fin_viaje AS TIMESTAMP) AS tiempo_fin_viaje,
        TRY_CAST(mediahora_inicio_viaje_hora AS TIME) AS mediahora_inicio_viaje,
        {_sql_decode_int_map('periodo_inicio_viaje', PERIODO_CODES_2023)} AS periodo_inicio_viaje,
        {_sql_decode_int_map('periodo_fin_viaje', PERIODO_CODES_2023)} AS periodo_fin_viaje,
        NULLIF(TRIM(paradero_inicio_viaje), '') AS paradero_inicio_viaje,
        NULLIF(TRIM(paradero_fin_viaje), '') AS paradero_fin_viaje,
        {_sql_decode_int_map('comuna_inicio_viaje', COMUNA_CODES_2023)} AS comuna_inicio_viaje,
        {_sql_decode_int_map('comuna_fin_viaje', COMUNA_CODES_2023)} AS comuna_fin_viaje,
        CAST(ROUND(TRY_CAST(zona_inicio_viaje AS DOUBLE)) AS INTEGER) AS zona_inicio_viaje,
        CAST(ROUND(TRY_CAST(zona_fin_viaje AS DOUBLE)) AS INTEGER) AS zona_fin_viaje,
        {_sql_decode_int_map('tipo_transporte_1', TRANSPORT_MODE_CODES_2023)} AS tipo_transporte_1,
        {_sql_decode_int_map('tipo_transporte_2', TRANSPORT_MODE_CODES_2023)} AS tipo_transporte_2,
        {_sql_decode_int_map('tipo_transporte_3', TRANSPORT_MODE_CODES_2023)} AS tipo_transporte_3,
        {_sql_decode_int_map('tipo_transporte_4', TRANSPORT_MODE_CODES_2023)} AS tipo_transporte_4,
        NULLIF(TRIM(srv_1), '') AS srv_1,
        NULLIF(TRIM(srv_2), '') AS srv_2,
        NULLIF(TRIM(srv_3), '') AS srv_3,
        NULLIF(TRIM(srv_4), '') AS srv_4,
        UPPER(TRIM(proposito)) AS proposito,
        UPPER(TRIM(tipo_corte_etapa_viaje)) AS tipo_corte_etapa_viaje
    FROM raw
    """


def build_clean_pipe_matrix_2019_query(key: str) -> str:
    """Fix the mixed '|;' delimiter in `subidas_parada_2019` and `bajadas_zona_2019`."""
    spec = DATASETS[key]
    csv_path = spec.path.as_posix()
    first_col = "parada_subida" if key == "subidas_parada_2019" else "zona_777_bajada"
    val_col = "subidas_promedio" if key == "subidas_parada_2019" else "bajadas_promedio"
    cast_first = (
        "NULLIF(SPLIT_PART(col0, '|', 1), '-')"
        if key == "subidas_parada_2019"
        else "TRY_CAST(SPLIT_PART(col0, '|', 1) AS INTEGER)"
    )

    return f"""
    SELECT
        {cast_first} AS {first_col},
        TRY_CAST(SPLIT_PART(col0, '|', 2) AS TIME) AS media_hora,
        TRY_CAST(col1 AS DOUBLE) AS {val_col}
    FROM read_csv(
        '{csv_path}',
        delim=';',
        skip=1,
        header=false,
        columns={{'col0': 'VARCHAR', 'col1': 'VARCHAR'}},
        ignore_errors=true
    )
    """


def load_clean_xlsb_matrix_2023(key: str):
    """Load and clean the 2023 `.xlsb` Subidas or Bajadas matrix using fastexcel + Polars.

    Promotes row 1 (`Semana`, `Comuna`, `paradero`, `parada SIMT`, ..., `0:00:00`..`23:30:00`)
    to column headers and casts `TOTAL` and all 48 half-hour columns to Float64.
    """
    import fastexcel
    import polars as pl

    spec = DATASETS[key]
    wb = fastexcel.read_excel(spec.path)
    data_sheet_name = wb.sheet_names[1]  # 'subidas_prom_laboral' or 'bajadas_prom_laboral'
    # Use schema_sample_rows=10_000 so late-night columns (00:00-03:30) that are null
    # on the top Metro rows still sample bus rows further down without warning.
    df = wb.load_sheet_by_name(
        data_sheet_name, header_row=1, schema_sample_rows=10_000
    ).to_polars()

    meta_cols = [c for c in df.columns if ":" not in c and c != "TOTAL"]
    numeric_cols = [c for c in df.columns if c == "TOTAL" or ":" in c]

    cleaned = df.with_columns(
        [pl.col(c).cast(pl.Utf8).str.strip_chars() for c in meta_cols]
        + [pl.col(c).cast(pl.Float64, strict=False) for c in numeric_cols]
    )
    return cleaned


def inspect_xlsb_matrices() -> Dict[str, List[Dict[str, object]]]:
    """Inspect sheet names, dimensions, and cleaned column names of the 2023 `.xlsb` files."""
    import fastexcel

    report: Dict[str, List[Dict[str, object]]] = {}
    for key in ("matriz_subidas_2023_xlsb", "matriz_bajadas_2023_xlsb"):
        spec = DATASETS[key]
        if not spec.path.exists():
            continue
        wb = fastexcel.read_excel(spec.path)
        sheets_info: List[Dict[str, object]] = []
        for idx, sheet_name in enumerate(wb.sheet_names):
            header_row = 1 if idx == 1 else 0
            df = wb.load_sheet_by_name(sheet_name, header_row=header_row).to_polars()
            sheets_info.append(
                {
                    "sheet_name": sheet_name,
                    "rows": df.height,
                    "cols": df.width,
                    "column_names": df.columns[:12],
                }
            )
        report[key] = sheets_info
    return report


def export_cleaned_parquet(
    output_dir: Optional[Path] = None,
    sample_limit: Optional[int] = None,
) -> List[Dict[str, object]]:
    """Run DuckDB & Polars cleaning pipelines and export cleaned Parquet files to `output_dir`."""
    import duckdb

    out_dir = output_dir or (DATA_DIR / "processed")
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    jobs = [
        ("etapas_2019_clean.parquet", build_clean_etapas_query("2019", limit=sample_limit)),
        ("etapas_2023_clean.parquet", build_clean_etapas_query("2023", limit=sample_limit)),
        ("viajes_2019_clean.parquet", build_clean_viajes_query("2019", limit=sample_limit)),
        ("viajes_2023_clean.parquet", build_clean_viajes_query("2023", limit=sample_limit)),
        ("subidas_parada_2019_clean.parquet", build_clean_pipe_matrix_2019_query("subidas_parada_2019")),
        ("bajadas_zona_2019_clean.parquet", build_clean_pipe_matrix_2019_query("bajadas_zona_2019")),
    ]

    results: List[Dict[str, object]] = []
    for filename, query in jobs:
        target = out_dir / filename
        con.execute(
            f"COPY ({query}) TO '{target.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        row_count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{target.as_posix()}')").fetchone()[0]
        results.append(
            {
                "file": filename,
                "rows": row_count,
                "size_mb": round(target.stat().st_size / (1024 * 1024), 2),
                "path": str(target),
            }
        )

    for xlsb_key, out_name in [
        ("matriz_subidas_2023_xlsb", "subidas_parada_2023_clean.parquet"),
        ("matriz_bajadas_2023_xlsb", "bajadas_parada_2023_clean.parquet"),
    ]:
        df_xlsb = load_clean_xlsb_matrix_2023(xlsb_key)
        target = out_dir / out_name
        df_xlsb.write_parquet(target, compression="zstd")
        results.append(
            {
                "file": out_name,
                "rows": df_xlsb.height,
                "size_mb": round(target.stat().st_size / (1024 * 1024), 2),
                "path": str(target),
            }
        )

    return results


def build_clean_notebook(output_path: Path, sample_limit: int = 50_000) -> Path:
    """Generate `notebooks/02_clean_and_harmonize.ipynb` with pre-populated DuckDB & Polars outputs."""
    import json
    import duckdb

    con = duckdb.connect()
    cols = ["column_name", "column_type", "min", "max", "approx_unique", "null_percentage"]

    et_19 = con.execute(f"SUMMARIZE ({build_clean_etapas_query('2019', limit=sample_limit)})").df()
    et_23 = con.execute(f"SUMMARIZE ({build_clean_etapas_query('2023', limit=sample_limit)})").df()
    vj_19 = con.execute(f"SUMMARIZE ({build_clean_viajes_query('2019', limit=sample_limit)})").df()
    vj_23 = con.execute(f"SUMMARIZE ({build_clean_viajes_query('2023', limit=sample_limit)})").df()

    et_out = (
        f"=== Clean Etapas 2019 ({sample_limit} sample) ===\n"
        + et_19[cols].to_string(index=False)
        + f"\n\n=== Clean Etapas 2023 ({sample_limit} sample) ===\n"
        + et_23[cols].to_string(index=False)
    )
    vj_out = (
        f"=== Clean & Harmonized Viajes 2019 ({sample_limit} sample) ===\n"
        + vj_19[cols].to_string(index=False)
        + f"\n\n=== Clean & Harmonized Viajes 2023 ({sample_limit} sample) ===\n"
        + vj_23[cols].to_string(index=False)
    )

    df_sub23 = load_clean_xlsb_matrix_2023("matriz_subidas_2023_xlsb")
    df_baj23 = load_clean_xlsb_matrix_2023("matriz_bajadas_2023_xlsb")

    top_sub = (
        df_sub23.filter(df_sub23["paradero"].is_null())
        .select(["Comuna", "parada SIMT", "servicio usuario", "TOTAL", "8:00:00", "18:00:00"])
        .sort("TOTAL", descending=True)
        .head(15)
    )
    top_baj = (
        df_baj23.filter(df_baj23["parada SIMT"].is_null())
        .select(["Comuna", "paradero", "servicio usuario", "TOTAL", "8:00:00", "18:00:00"])
        .sort("TOTAL", descending=True)
        .head(15)
    )
    xlsb_out = (
        f"2023 Subidas XLSB shape: {df_sub23.shape}\n"
        f"Top 15 Metro Stations by Average Workday Boardings (April 17-21, 2023):\n{top_sub}\n\n"
        f"2023 Bajadas XLSB shape: {df_baj23.shape}\n"
        f"Top 15 Metro Stations by Average Workday Alightings (April 17-21, 2023):\n{top_baj}"
    )

    q_od_cmp = f"""
    WITH v19 AS ({build_clean_viajes_query('2019', limit=100_000)}),
         v23 AS ({build_clean_viajes_query('2023', limit=100_000)}),
         combined AS (SELECT * FROM v19 UNION ALL SELECT * FROM v23)
    SELECT
        anio,
        tipo_transporte_1 AS modo_inicial,
        COUNT(*) AS viajes_muestra,
        ROUND(SUM(factor_expansion), 1) AS viajes_expandidos,
        ROUND(AVG(tviaje_min), 2) AS tviaje_promedio_min,
        ROUND(AVG(distancia_ruta_m) / 1000.0, 2) AS dist_ruta_promedio_km,
        ROUND(AVG(n_etapas), 2) AS etapas_promedio
    FROM combined
    WHERE tipo_transporte_1 IS NOT NULL
    GROUP BY anio, tipo_transporte_1
    ORDER BY anio, viajes_expandidos DESC
    """
    od_cmp_out = con.execute(q_od_cmp).df().to_string(index=False)

    nb = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "# 02 — Data Cleaning, Schema Harmonization & Parquet Pipeline (2019 vs. 2023)\n",
                    "\n",
                    "This notebook runs the out-of-core **DuckDB + Polars** cleaning and schema harmonization pipeline in `src/metro_od/clean.py` to resolve every anomaly found in `01_describe_tables.ipynb`:\n",
                    "1. **Delimiter & Trailing Column Fixes**: Normalizes `;` (2019 & 2023 `etapas`), `,` (2023 `viajes`, dropping `Unnamed: 100`), and mixed `|;` in 2019 boarding/alighting matrices.\n",
                    "2. **Coordinate System Disentangling (`etapas`)**: Separates UTM Zone 19S meters (`utm_x_*`, `utm_y_*`) from WGS84 degrees (`lat_*`, `lon_*`) mixed into 2023 Metro rows.\n",
                    "3. **Distance & Travel Time Outlier Masking**: Masks negative sentinel route distances (`-20,601 m`) and `~6.31M m` Euclidean glitches caused by `(0, 0)` UTM fallbacks, and converts 2023 `tviaje2` (seconds) to `tviaje_min` (minutes).\n",
                    "4. **2019 vs. 2023 Categorical & Commune Harmonization**: Expands 2019 8-char truncated communes (`LA FLORI` -> `LA FLORIDA`) and decodes all 34 integer commune codes (`COMUNA_CODES_2023`), 12 period codes (`PERIODO_CODES_2023`), and transport modes in `viajes_2023`.\n",
                    "5. **2023 `.xlsb` Matrices Cleaning**: Loads `2023.04 Matriz_sub_SS_MH.xlsb` and `2023.04 Matriz_baj_SS_MH.xlsb` via `fastexcel` + `polars` with `header_row=1` and strongly-typed half-hourly columns.",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": 1,
                "metadata": {},
                "outputs": [{"name": "stdout", "output_type": "stream", "text": [et_out + "\n"]}],
                "source": [
                    "import sys\n",
                    "from pathlib import Path\n",
                    "import duckdb\n",
                    "\n",
                    "PROJECT_ROOT = Path.cwd().parent if Path.cwd().name == 'notebooks' else Path.cwd()\n",
                    "if str(PROJECT_ROOT / 'src') not in sys.path:\n",
                    "    sys.path.insert(0, str(PROJECT_ROOT / 'src'))\n",
                    "\n",
                    "from metro_od.clean import (\n",
                    "    build_clean_etapas_query,\n",
                    "    build_clean_viajes_query,\n",
                    "    load_clean_xlsb_matrix_2023,\n",
                    "    export_cleaned_parquet,\n",
                    ")\n",
                    "\n",
                    "con = duckdb.connect()\n",
                    "cols = ['column_name', 'column_type', 'min', 'max', 'approx_unique', 'null_percentage']\n",
                    "\n",
                    "for yr in ('2019', '2023'):\n",
                    "    df_et = con.execute('SUMMARIZE (' + build_clean_etapas_query(yr, limit=50_000) + ')').df()\n",
                    "    print(f'=== Clean Etapas {yr} (50k sample) ===')\n",
                    "    print(df_et[cols].to_string(index=False))\n",
                    "    print()",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 1. Harmonized `viajes` (2019 vs. 2023) — Unified Schema Describe\n",
                    "\n",
                    "Both `viajes_2019` (107 raw columns) and `viajes_2023` (101 raw columns) now project into the exact same 32-column canonical OD schema with decoded commune names, time periods, transport modes, and travel times in minutes.",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": 2,
                "metadata": {},
                "outputs": [{"name": "stdout", "output_type": "stream", "text": [vj_out + "\n"]}],
                "source": [
                    "for yr in ('2019', '2023'):\n",
                    "    df_vj = con.execute('SUMMARIZE (' + build_clean_viajes_query(yr, limit=50_000) + ')').df()\n",
                    "    print(f'=== Clean & Harmonized Viajes {yr} (50k sample) ===')\n",
                    "    print(df_vj[cols].to_string(index=False))\n",
                    "    print()",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 2. Cross-Year Demand Characterization (2019 vs. 2023 Sample Comparison)\n",
                    "\n",
                    "Because both years share the same schema, we can directly query `UNION ALL` across 2019 and 2023 to compare modal split, average travel time (`tviaje_min`), route distance (`distancia_ruta_m`), and stages per trip (`n_etapas`).",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": 3,
                "metadata": {},
                "outputs": [{"name": "stdout", "output_type": "stream", "text": [od_cmp_out + "\n"]}],
                "source": [
                    "q_od_cmp = (\n",
                    "    'WITH v19 AS (' + build_clean_viajes_query('2019', limit=100_000) + '), '\n",
                    "    'v23 AS (' + build_clean_viajes_query('2023', limit=100_000) + '), '\n",
                    "    'combined AS (SELECT * FROM v19 UNION ALL SELECT * FROM v23) '\n",
                    "    'SELECT anio, tipo_transporte_1 AS modo_inicial, COUNT(*) AS viajes_muestra, '\n",
                    "    'ROUND(SUM(factor_expansion), 1) AS viajes_expandidos, '\n",
                    "    'ROUND(AVG(tviaje_min), 2) AS tviaje_promedio_min, '\n",
                    "    'ROUND(AVG(distancia_ruta_m) / 1000.0, 2) AS dist_ruta_promedio_km, '\n",
                    "    'ROUND(AVG(n_etapas), 2) AS etapas_promedio '\n",
                    "    'FROM combined WHERE tipo_transporte_1 IS NOT NULL '\n",
                    "    'GROUP BY anio, tipo_transporte_1 ORDER BY anio, viajes_expandidos DESC'\n",
                    ")\n",
                    "print(con.execute(q_od_cmp).df().to_string(index=False))",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 3. Cleaned 2023 `.xlsb` Half-Hourly Boarding & Alighting Matrices\n",
                    "\n",
                    "Inspecting the top Metro stations by average workday boardings (`subidas_prom_laboral`) and alightings (`bajadas_prom_laboral`) during April 17–21, 2023.",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": 4,
                "metadata": {},
                "outputs": [{"name": "stdout", "output_type": "stream", "text": [xlsb_out + "\n"]}],
                "source": [
                    "df_sub23 = load_clean_xlsb_matrix_2023('matriz_subidas_2023_xlsb')\n",
                    "df_baj23 = load_clean_xlsb_matrix_2023('matriz_bajadas_2023_xlsb')\n",
                    "\n",
                    "print(f'2023 Subidas XLSB shape: {df_sub23.shape}')\n",
                    "print(\n",
                    "    df_sub23.filter(df_sub23['paradero'].is_null())\n",
                    "    .select(['Comuna', 'parada SIMT', 'servicio usuario', 'TOTAL', '8:00:00', '18:00:00'])\n",
                    "    .sort('TOTAL', descending=True)\n",
                    "    .head(15)\n",
                    ")\n",
                    "\n",
                    "print(f'\\n2023 Bajadas XLSB shape: {df_baj23.shape}')\n",
                    "print(\n",
                    "    df_baj23.filter(df_baj23['parada SIMT'].is_null())\n",
                    "    .select(['Comuna', 'paradero', 'servicio usuario', 'TOTAL', '8:00:00', '18:00:00'])\n",
                    "    .sort('TOTAL', descending=True)\n",
                    "    .head(15)\n",
                    ")",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 4. Export Cleaned Datasets to Compressed Parquet (`data/processed/`)\n",
                    "\n",
                    "Use `export_cleaned_parquet(sample_limit=None)` to convert the entire ~20 GB raw data into fast, ZSTD-compressed Parquet files (or pass `sample_limit=100_000` for a quick test export).",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "# Set sample_limit=None to export full datasets, or sample_limit=100_000 for a quick subset\n",
                    "exported = export_cleaned_parquet(sample_limit=100_000)\n",
                    "for item in exported:\n",
                    "    print(f\"{item['file']:35s} | {item['rows']:9d} rows | {item['size_mb']:6.2f} MB\")",
                ],
            },
        ],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.14.5"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
    return output_path


if __name__ == "__main__":
    import sys
    import duckdb

    if len(sys.argv) > 1:
        nb_path = build_clean_notebook(Path(sys.argv[1]))
        print(f"Generated clean notebook: {nb_path} ({nb_path.stat().st_size} bytes)")
        sys.exit(0)

    print("=== 1. 2023 XLSB MATRICES (CLEANED HEADERS & TYPES) ===")
    for key in ("matriz_subidas_2023_xlsb", "matriz_bajadas_2023_xlsb"):
        df_x = load_clean_xlsb_matrix_2023(key)
        print(f"{key}: shape={df_x.shape}")
        print(df_x.select(df_x.columns[:10]).head(4))

    print("\n=== 2. DUCKDB CLEANED & HARMONIZED SUMMARIZE (50,000 ROW SAMPLE) ===")
    con = duckdb.connect()
    for yr in ("2019", "2023"):
        q_vj = build_clean_viajes_query(yr, limit=50_000)
        df_vj = con.execute(f"SUMMARIZE ({q_vj})").df()
        print(f"\n--- Clean & Harmonized Viajes {yr} ---")
        print(df_vj[["column_name", "column_type", "min", "max", "approx_unique", "null_percentage"]].to_string(index=False))

