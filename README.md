# Metro de Santiago Origin–Destination Analysis (2019 vs. 2023)

Out-of-core **DuckDB + Polars** data cataloging, cleaning, schema harmonization, and exploratory analysis codebase for Santiago's public transport Origin–Destination (`viajes`), validation stages (`etapas`), half-hourly boarding/alighting matrices, and GTFS feeds (April 2019 vs. April 2023).

## Repository Structure

```text
├── data/
│   ├── 2019/                 # April 2019 schema docs + raw etapas, viajes & OD matrices (ignored in git)
│   ├── 2023/                 # April 2023 schema docs + raw etapas, viajes & .xlsb matrices (ignored in git)
│   ├── GTFS/                 # Transit network GTFS feed tables (ignored in git)
│   └── processed/            # Cleaned & ZSTD-compressed Apache Parquet outputs
├── notebooks/
│   ├── 01_describe_tables.ipynb      # Data catalog, schema comparison & per-column anomaly profiles
│   └── 02_clean_and_harmonize.ipynb  # Harmonized 2019 vs 2023 SUMMARIZE, modal split & Parquet export
├── src/metro_od/
│   ├── __init__.py
│   ├── catalog.py            # Dataset registry, file paths, delimiters & known data quirks
│   ├── describe.py           # Zero-dependency streaming CSV profiler & schema comparator
│   └── clean.py              # Out-of-core DuckDB & Polars cleaning, code mappings & Parquet exporter
└── requirements.txt
```

## Quick Start

1. **Install dependencies:**
   ```bash
   python -m pip install -r requirements.txt
   ```

2. **Run dataset catalog & streaming table descriptions:**
   ```bash
   PYTHONPATH=src python -m metro_od.describe
   ```

3. **Run DuckDB cleaning & 2019 vs. 2023 schema harmonization verification:**
   ```bash
   PYTHONPATH=src python -m metro_od.clean
   ```
