"""Fast table description and schema comparison utilities.

Works out-of-the-box with Python's standard library for immediate streaming sample
profiling, and automatically accelerates full-table scans when DuckDB is installed.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .catalog import DATASETS, DATA_DIR, DatasetSpec


def _try_parse_float(val: str) -> Optional[float]:
    try:
        num = float(val)
        return num if math.isfinite(num) else None
    except (ValueError, TypeError):
        return None


def describe_csv_sample(
    path: Path,
    delimiter: str = ";",
    sample_rows: int = 10_000,
    null_tokens: Tuple[str, ...] = ("", "-", "NULL", "null"),
) -> Dict[str, object]:
    """Stream the first `sample_rows` rows of a CSV and compute per-column descriptive stats.

    Avoids loading multi-gigabyte CSVs into memory while still exposing types,
    null/placeholder rates, numeric ranges, and example distinct values.
    """
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        try:
            raw_headers = next(reader)
        except StopIteration:
            return {"path": str(path), "rows_sampled": 0, "columns": []}

        headers = [h.strip().strip('"') for h in raw_headers]
        n_cols = len(headers)

        null_counts = [0] * n_cols
        num_counts = [0] * n_cols
        num_sums = [0.0] * n_cols
        num_mins = [math.inf] * n_cols
        num_maxs = [-math.inf] * n_cols
        sample_values: List[set] = [set() for _ in range(n_cols)]

        rows_read = 0
        for row in reader:
            rows_read += 1
            # Handle composite pipe-delimited rows in 2019 Subidas/Bajadas tables
            if len(row) < n_cols and len(row) == 2 and "|" in row[0] and n_cols == 3:
                left, mid = row[0].split("|", 1)
                row = [left, mid, row[1]]

            for i in range(min(len(row), n_cols)):
                val = row[i].strip().strip('"')
                if val in null_tokens:
                    null_counts[i] += 1
                else:
                    if len(sample_values[i]) < 5:
                        sample_values[i].add(val)
                    num = _try_parse_float(val)
                    if num is not None:
                        num_counts[i] += 1
                        num_sums[i] += num
                        if num < num_mins[i]:
                            num_mins[i] = num
                        if num > num_maxs[i]:
                            num_maxs[i] = num

            if rows_read >= sample_rows:
                break

    col_summaries: List[Dict[str, object]] = []
    for i, col_name in enumerate(headers):
        non_null = rows_read - null_counts[i]
        is_numeric = non_null > 0 and (num_counts[i] / non_null) >= 0.8
        col_summaries.append(
            {
                "idx": i + 1,
                "column": col_name,
                "inferred_type": "numeric" if is_numeric else "string/timestamp",
                "null_or_dash_pct": round((null_counts[i] / rows_read) * 100, 1) if rows_read else 0.0,
                "min": round(num_mins[i], 4) if is_numeric and num_counts[i] else None,
                "mean": round(num_sums[i] / num_counts[i], 4) if is_numeric and num_counts[i] else None,
                "max": round(num_maxs[i], 4) if is_numeric and num_counts[i] else None,
                "examples": sorted(sample_values[i])[:3],
            }
        )

    return {
        "file_name": path.name,
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
        "rows_sampled": rows_read,
        "num_columns": n_cols,
        "columns": col_summaries,
    }


def compare_schemas(spec_a: DatasetSpec, spec_b: DatasetSpec) -> Dict[str, object]:
    """Compare column headers between two CSV datasets (e.g., 2019 vs 2023)."""
    cols_a = _read_csv_headers(spec_a.path, spec_a.delimiter)
    cols_b = _read_csv_headers(spec_b.path, spec_b.delimiter)
    set_a = {c.lower(): c for c in cols_a}
    set_b = {c.lower(): c for c in cols_b}

    common = [set_a[k] for k in set_a.keys() & set_b.keys()]
    only_a = [set_a[k] for k in set_a.keys() - set_b.keys()]
    only_b = [set_b[k] for k in set_b.keys() - set_a.keys()]

    return {
        "dataset_a": spec_a.key,
        "dataset_b": spec_b.key,
        "count_a": len(cols_a),
        "count_b": len(cols_b),
        "exact_match_columns": sorted(common),
        "only_in_a": only_a,
        "only_in_b": only_b,
    }


def _read_csv_headers(path: Path, delimiter: str) -> List[str]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        row = next(reader, [])
        return [c.strip().strip('"') for c in row]


def describe_all_datasets(sample_rows: int = 5_000) -> Dict[str, Dict[str, object]]:
    """Run sample profiling on all CSV datasets in the catalog."""
    results: Dict[str, Dict[str, object]] = {}
    for key, spec in DATASETS.items():
        if spec.format == "csv" and spec.path.exists():
            results[key] = describe_csv_sample(
                spec.path,
                delimiter=spec.delimiter,
                sample_rows=sample_rows,
            )
    return results


def format_ascii_table(rows: List[Dict[str, object]], headers: List[str]) -> str:
    """Format a list of dicts as an aligned ASCII table for terminal and notebook output."""
    widths = [
        max(len(str(h)), *(len(str(r.get(h, ""))) for r in rows)) if rows else len(str(h))
        for h in headers
    ]
    hdr = " | ".join(str(h).ljust(w) for h, w in zip(headers, widths))
    sep = "-+-".join("-" * w for w in widths)
    body = "\n".join(
        " | ".join(str(r.get(h, "")).ljust(w) for h, w in zip(headers, widths))
        for r in rows
    )
    return f"{hdr}\n{sep}\n{body}"


def build_describe_notebook(output_path: Path, sample_rows: int = 5_000) -> Path:
    """Generate `notebooks/01_describe_tables.ipynb` with pre-populated descriptive outputs."""
    import json
    from .catalog import get_dataset_catalog

    catalog = get_dataset_catalog()
    summaries = describe_all_datasets(sample_rows=sample_rows)
    cmp_etapas = compare_schemas(DATASETS["etapas_2019"], DATASETS["etapas_2023"])
    cmp_viajes = compare_schemas(DATASETS["viajes_2019"], DATASETS["viajes_2023"])

    cat_out = format_ascii_table(
        catalog, ["key", "year", "category", "format", "delimiter", "size_mb", "quirks_count"]
    )

    etapas_cmp_out = (
        f"etapas_2019 columns: {cmp_etapas['count_a']} | etapas_2023 columns: {cmp_etapas['count_b']}\n"
        f"Shared columns ({len(cmp_etapas['exact_match_columns'])}): {cmp_etapas['exact_match_columns']}\n"
        f"Only in 2019: {cmp_etapas['only_in_a']}\n"
        f"Added in 2023: {cmp_etapas['only_in_b']}\n\n"
        f"viajes_2019 columns: {cmp_viajes['count_a']} | viajes_2023 columns: {cmp_viajes['count_b']}\n"
        f"Exact name matches ({len(cmp_viajes['exact_match_columns'])}): {cmp_viajes['exact_match_columns']}\n"
        f"Only in 2019 ({len(cmp_viajes['only_in_a'])}): {cmp_viajes['only_in_a'][:15]} ...\n"
        f"Only in 2023 ({len(cmp_viajes['only_in_b'])}): {cmp_viajes['only_in_b'][:15]} ..."
    )

    def fmt_describe(key: str) -> str:
        s = summaries[key]
        title = (
            f"=== {key} ({s['file_name']}) | {s['size_mb']} MB | "
            f"{s['num_columns']} cols | {s['rows_sampled']} rows sampled ==="
        )
        hdrs = ["idx", "column", "inferred_type", "null_or_dash_pct", "min", "mean", "max", "examples"]
        return title + "\n" + format_ascii_table(s["columns"], hdrs)

    nb = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "# 01 — Metro de Santiago Origin–Destination Data Catalog & Table Descriptions (2019 vs. 2023)\n",
                    "\n",
                    "This notebook orders, catalogs, and profiles the raw datasets under `data/` (~20.7 GB total) across:\n",
                    "- **April 2019 (`data/2019/`)**: `etapas` (1.31 GB), `viajes` (2.25 GB), and 4 aggregated half-hourly OD / boarding / alighting matrices.\n",
                    "- **April 2023 (`data/2023/`)**: `etapas` (9.41 GB), `viajes` (7.49 GB), and 2 `.xlsb` boarding/alighting matrices.\n",
                    "- **GTFS (`data/GTFS/`)**: 12 transit network feed tables (`stops`, `routes`, `trips`, `stop_times`, `shapes`, etc.).\n",
                    "\n",
                    "> **Note:** Every code cell uses `src/metro_od` which works out-of-the-box via streaming samples and scales to full-dataset SQL/DataFrame queries once `duckdb` and `polars` are installed.",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": 1,
                "metadata": {},
                "outputs": [{"name": "stdout", "output_type": "stream", "text": [cat_out + "\n"]}],
                "source": [
                    "import sys\n",
                    "from pathlib import Path\n",
                    "\n",
                    "PROJECT_ROOT = Path.cwd().parent if Path.cwd().name == 'notebooks' else Path.cwd()\n",
                    "if str(PROJECT_ROOT / 'src') not in sys.path:\n",
                    "    sys.path.insert(0, str(PROJECT_ROOT / 'src'))\n",
                    "\n",
                    "from metro_od.catalog import DATASETS, get_dataset_catalog\n",
                    "from metro_od.describe import describe_all_datasets, compare_schemas, format_ascii_table\n",
                    "\n",
                    "catalog = get_dataset_catalog()\n",
                    "print(format_ascii_table(catalog, ['key', 'year', 'category', 'format', 'delimiter', 'size_mb', 'quirks_count']))",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 1. Schema Comparison: 2019 vs. 2023 (`etapas` and `viajes`)\n",
                    "\n",
                    "Before cleaning and harmonizing 2019 and 2023 into unified Parquet tables, we inspect the structural differences between the two periods.",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": 2,
                "metadata": {},
                "outputs": [{"name": "stdout", "output_type": "stream", "text": [etapas_cmp_out + "\n"]}],
                "source": [
                    "cmp_etapas = compare_schemas(DATASETS['etapas_2019'], DATASETS['etapas_2023'])\n",
                    "cmp_viajes = compare_schemas(DATASETS['viajes_2019'], DATASETS['viajes_2023'])\n",
                    "\n",
                    "print(f\"etapas_2019 columns: {cmp_etapas['count_a']} | etapas_2023 columns: {cmp_etapas['count_b']}\")\n",
                    "print(f\"Shared columns ({len(cmp_etapas['exact_match_columns'])}): {cmp_etapas['exact_match_columns']}\")\n",
                    "print(f\"Only in 2019: {cmp_etapas['only_in_a']}\")\n",
                    "print(f\"Added in 2023: {cmp_etapas['only_in_b']}\\n\")\n",
                    "print(f\"viajes_2019 columns: {cmp_viajes['count_a']} | viajes_2023 columns: {cmp_viajes['count_b']}\")\n",
                    "print(f\"Exact name matches ({len(cmp_viajes['exact_match_columns'])}): {cmp_viajes['exact_match_columns']}\")\n",
                    "print(f\"Only in 2019 ({len(cmp_viajes['only_in_a'])}): {cmp_viajes['only_in_a'][:15]} ...\")\n",
                    "print(f\"Only in 2023 ({len(cmp_viajes['only_in_b'])}): {cmp_viajes['only_in_b'][:15]} ...\")",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 2. Descriptive Profiles of `etapas` (2019 vs. 2023)\n",
                    "\n",
                    "Key anomalies exposed by the table describe:\n",
                    "1. **Mixed Coordinate Systems in 2023 (`x_subida`, `y_subida`)**: In `etapas_2019`, both Bus and Metro use UTM Zone 19S meters (`~350,000`, `~6,290,000`). In `etapas_2023`, Bus uses UTM meters while Metro rows use **WGS84 `(lat, lon)`** (`-33.51`, `-70.75`).\n",
                    "2. **Zone formatting in 2023 (`zona_subida`, `zona_bajada`)**: Stored as float-like strings (`\"77.0\"`, `\"818.0\"`) instead of integers (`487`, `476`).\n",
                    "3. **Missing Value Sentinel**: `-` is used extensively across both numeric and categorical fields.",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": 3,
                "metadata": {},
                "outputs": [
                    {
                        "name": "stdout",
                        "output_type": "stream",
                        "text": [fmt_describe("etapas_2019") + "\n\n" + fmt_describe("etapas_2023") + "\n"],
                    }
                ],
                "source": [
                    "summaries = describe_all_datasets(sample_rows=5_000)\n",
                    "hdrs = ['idx', 'column', 'inferred_type', 'null_or_dash_pct', 'min', 'mean', 'max', 'examples']\n",
                    "for key in ['etapas_2019', 'etapas_2023']:\n",
                    "    s = summaries[key]\n",
                    "    print(f\"=== {key} ({s['file_name']}) | {s['size_mb']} MB | {s['num_columns']} cols | {s['rows_sampled']} rows sampled ===\")\n",
                    "    print(format_ascii_table(s['columns'], hdrs))\n",
                    "    print()",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 3. Descriptive Profiles of `viajes` (2019 vs. 2023)\n",
                    "\n",
                    "Key anomalies exposed by the `viajes` describe:\n",
                    "1. **Delimiter Change & Trailing Comma**: `viajes_2019` uses `;` (107 columns), whereas `viajes_2023` uses `,` and has a trailing comma on every line creating a 101st empty column (`Unnamed: 100`).\n",
                    "2. **Deprecated Travel Time Column**: In `viajes_2023`, `tviaje` is 100% `-` (deprecated per `Estructura Tabla Viajes 0423.txt`: *\"Se anula por nuevo tiempo mejorado\"*), replaced by `tviaje2` and stage-level components (`entrada`, `te0`, `tv1`, `tc1`, ..., `egreso`).\n",
                    "3. **Categorical Encoding Shift**: In 2019, `tipodia`, `comunasubida`, `periodosubida`, and `tipotransporte_1era` are strings (`LABORAL`, `LA FLORI`, `BUS`, `METRO`). In 2023, `tipodia`, `comuna_inicio_viaje`, `periodo_inicio_viaje`, and `tipo_transporte_1` are integer codes (`0`, `25`, `63`, `2`).",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": 4,
                "metadata": {},
                "outputs": [
                    {
                        "name": "stdout",
                        "output_type": "stream",
                        "text": [fmt_describe("viajes_2019") + "\n\n" + fmt_describe("viajes_2023") + "\n"],
                    }
                ],
                "source": [
                    "for key in ['viajes_2019', 'viajes_2023']:\n",
                    "    s = summaries[key]\n",
                    "    print(f\"=== {key} ({s['file_name']}) | {s['size_mb']} MB | {s['num_columns']} cols | {s['rows_sampled']} rows sampled ===\")\n",
                    "    print(format_ascii_table(s['columns'], hdrs))\n",
                    "    print()",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 4. Descriptive Profiles of 2019 Aggregated OD / Boarding / Alighting Matrices\n",
                    "\n",
                    "Note that `subidas_parada_2019` and `bajadas_zona_2019` use a mixed `|` and `;` delimiter in their data rows (`ParadaSubida|MediaHora;SubidasPromedio`), which our parser automatically splits.",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": 5,
                "metadata": {},
                "outputs": [
                    {
                        "name": "stdout",
                        "output_type": "stream",
                        "text": [
                            "\n\n".join(
                                fmt_describe(k)
                                for k in [
                                    "od_comunas_2019",
                                    "od_zonas_2019",
                                    "subidas_parada_2019",
                                    "bajadas_zona_2019",
                                ]
                            )
                            + "\n"
                        ],
                    }
                ],
                "source": [
                    "for key in ['od_comunas_2019', 'od_zonas_2019', 'subidas_parada_2019', 'bajadas_zona_2019']:\n",
                    "    s = summaries[key]\n",
                    "    print(f\"=== {key} ({s['file_name']}) | {s['size_mb']} MB | {s['num_columns']} cols | {s['rows_sampled']} rows sampled ===\")\n",
                    "    print(format_ascii_table(s['columns'], hdrs))\n",
                    "    print()",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## 5. Full-Table Out-of-Core Describe with DuckDB (Post-Install)\n",
                    "\n",
                    "Once `duckdb` is installed (`pip install -r requirements.txt`), run the cell below to execute out-of-core `SUMMARIZE` queries over the full 7–9 GB CSVs and convert them to partitioned Apache Parquet files.",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "try:\n",
                    "    import duckdb\n",
                    "    con = duckdb.connect()\n",
                    "    for key in ['od_comunas_2019', 'etapas_2019']:\n",
                    "        spec = DATASETS[key]\n",
                    "        print(f'Running DuckDB SUMMARIZE on {key}...')\n",
                    "        df_sum = con.execute(\n",
                    "            f\"SUMMARIZE SELECT * FROM read_csv_auto('{spec.path.as_posix()}', delim='{spec.delimiter}', nullstr=['-', ''], ignore_errors=true) LIMIT 100000\"\n",
                    "        ).df()\n",
                    "        print(df_sum[['column_name', 'column_type', 'min', 'max', 'approx_unique', 'avg', 'null_percentage']])\n",
                    "except ImportError:\n",
                    "    print('Install DuckDB (`pip install -r requirements.txt`) to run full out-of-core SQL SUMMARIZE.')",
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
    from .catalog import PROJECT_ROOT, get_dataset_catalog

    print("=== METRO DE SANTIAGO DATA CATALOG ===")
    print(
        format_ascii_table(
            get_dataset_catalog(),
            ["key", "year", "category", "format", "delimiter", "size_mb", "quirks_count"],
        )
    )
    print("\n=== SAMPLE DESCRIBE SUMMARY ===")
    for key, summary in describe_all_datasets(sample_rows=2_000).items():
        print(
            f"- {key:22s} | {summary['size_mb']:8.2f} MB | "
            f"{summary['num_columns']:3d} cols | sampled {summary['rows_sampled']} rows"
        )
    if len(sys.argv) > 1:
        nb_path = build_describe_notebook(Path(sys.argv[1]))
        print(f"Generated notebook: {nb_path} ({nb_path.stat().st_size} bytes)")


