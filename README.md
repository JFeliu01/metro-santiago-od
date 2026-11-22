# Metro de Santiago — Origin–Destination Analytics & Spatial ML

Out-of-core **DuckDB**, **Polars**, and pure-**NumPy** analytics pipeline and static web dashboard for Santiago's DTPM/ADATRAP public-transport Origin–Destination (`viajes`), validation stage (`etapas`), half-hourly station boarding/alighting matrices (`.xlsb` / `.csv`), and **GTFS** feeds (April 8–14, 2019 and April 17–23, 2023; `44.43M` total trip records).

- **Live Dashboard:** [https://metro-santiago-od-jf-18a74.web.app](https://metro-santiago-od-jf-18a74.web.app)

---

## Repository Structure

```text
├── src/metro_od/
│   ├── catalog.py              # Dataset registry, file paths, delimiters, and known schema quirks
│   ├── describe.py             # Streaming CSV profiler and 2019 vs. 2023 schema comparator
│   ├── clean.py                # DuckDB/Polars cleaning, code decoding, CRS separation, Parquet export
│   ├── eda.py                  # Five-angle exploratory SQL/DuckDB summary suite
│   ├── deep_dive.py            # Full 7-day out-of-core DuckDB aggregations (44.43M trips)
│   ├── ml_utils.py             # Pure-NumPy ML primitives (StandardScaler, IRLS Logistic, Ridge, Hist-GBDT, metrics)
│   └── dashboard.py            # Data extraction pipeline generating docs/dashboard_data.js
├── analysis/
│   ├── feeder_return_leakage/
│   │   ├── run_analysis.py             # Card-level AM→PM chaining and station gap aggregations
│   │   └── model_return_leakage.py     # L2 Logistic & Hist-GBDT classifier + PM wait counterfactual
│   ├── peak_compression_night_drop/
│   │   ├── run_analysis.py             # Half-hourly & macro-block 2019 vs. 2023 volume comparison
│   │   └── operational_analysis.py     # Day-of-week and trip-purpose temporal decomposition
│   └── network_expansion_ml/
│       ├── run_analysis.py             # Full-week Difference-in-Differences (L3/L6 vs. control communes)
│       └── model_lines_789.py          # Spatial ML mode-choice, direct-Metro & duration models + L7/L8/L9 simulation
├── docs/                       # Static web dashboard (deployed to Firebase Hosting)
│   ├── index.html              # Semantic HTML5 layout (5 views)
│   ├── styles.css              # Design tokens, responsive grid, tabular-nums styling
│   ├── app.js                  # Interactive Google Maps / Leaflet GIS + Plotly visualizations
│   ├── dashboard_data.js       # Pre-aggregated JSON payload consumed by app.js
│   └── config.js               # Gitignored runtime Google Maps key loader (generated from .env)
├── notebooks/
│   ├── 01_describe_tables.ipynb
│   ├── 02_clean_and_harmonize.ipynb
│   └── 03_eda_and_hypotheses.ipynb
├── firebase.json               # Firebase Hosting config (public: docs/)
├── .firebaserc                 # Firebase project alias
├── .env.example                # Environment variable template (GOOGLE_MAPS_API_KEY)
└── requirements.txt
```

> Raw datasets under `data/` (`*.csv`, `*.xlsb`, `*.zip`, `*.rar`, `*.parquet`, `data/GTFS/*.txt`) and local secrets (`.env`, `docs/config.js`) are excluded via `.gitignore`.

---

## Core Codebase Modules

### 1. Data Ingestion & Harmonization (`src/metro_od/clean.py`, `catalog.py`, `describe.py`)
- **Out-of-Core Execution:** Queries multi-gigabyte CSVs directly on disk via DuckDB (`read_csv_auto`) without loading full tables into RAM.
- **Schema Alignment (2019 ↔ 2023):** Maps divergent column names across 107-column (2019 `;`-delimited) and 101-column (2023 `,`-delimited) `viajes` files into a canonical 32-column projection.
- **Code & CRS Normalization:** Decodes 34 integer commune codes and 12 period codes in `viajes_2023` via `etapas_2023`; separates mixed coordinate systems in `etapas_2023` (UTM Zone 19S `EPSG:32719` for Bus vs. WGS84 `EPSG:4326` for Metro) using `pyproj`; filters invalid distances (`< 0` or `> 100 km`); loads binary `.xlsb` matrices via `fastexcel` with `schema_sample_rows=10_000`.
- **Duration Comparability:** Computes `EPOCH(tiempo_fin_viaje - tiempo_inicio_viaje) / 60.0` identically across both years to avoid the `tviaje_min` (2019) vs. `tviaje2` (2023) definition mismatch.

### 2. Machine Learning & Econometric Suite (`src/metro_od/ml_utils.py`, `analysis/`)
- **`ml_utils.py`:** Self-contained NumPy implementations of `StandardScaler`, `LogisticRegressionIRLS` (with analytical standard errors and z-scores), `RidgeRegressor`, `FastHistogramGBDT` (binary classification & regression), and evaluation metrics (`roc_auc_score`, `pr_auc_score`, `log_loss`, `brier_score`, `f1_score`, `regression_metrics`).
- **Spatial Expansion Pipeline (`analysis/network_expansion_ml/model_lines_789.py`):** Builds WGS84 centroids for `792` DTPM `Zona 777` zones, computes Haversine distances to `126` active GTFS Metro stations, trains stratified 80/20 holdout models for `P(uses_metro)`, `P(direct_metro | uses_metro=1)`, and `duracion_obs_min`, and evaluates counterfactual station geometries for Lines 7, 8, and 9.
- **Intermodal Return Pipeline (`analysis/feeder_return_leakage/model_return_leakage.py`):** Chains morning `BUS → METRO` and afternoon trips per `id_tarjeta` in 2023 to train Logistic/GBDT classifiers and run PM bus-wait counterfactual simulations.
- **Temporal & DiD Scripts (`analysis/peak_compression_night_drop/`, `analysis/network_expansion_ml/run_analysis.py`):** Full 7-day DuckDB aggregations for half-hourly/day-of-week demand shifts and Difference-in-Differences estimation.

### 3. Static Web Application (`docs/`)
- Pure static HTML/CSS/JS frontend reading pre-aggregated JSON from `docs/dashboard_data.js` (`window.METRO_OD_DATA`).
- Renders GTFS Metro polylines (`L1–L6`), proposed `L7–L9` alignments, commune centroids, and OD desire arcs over Google Maps JS API (with automatic Carto Positron / Leaflet fallback via `gm_authFailure`) alongside Plotly charts.

---

## Running Locally

1. **Install dependencies:**
   ```bash
   python -m pip install -r requirements.txt
   ```

2. **Verify data catalog & cleaning queries:**
   ```bash
   PYTHONPATH=src python -m metro_od.describe
   PYTHONPATH=src python -m metro_od.clean
   ```

3. **Run analysis & ML pipelines:**
   ```bash
   PYTHONPATH=src python analysis/feeder_return_leakage/model_return_leakage.py
   PYTHONPATH=src python analysis/peak_compression_night_drop/operational_analysis.py
   PYTHONPATH=src python analysis/network_expansion_ml/model_lines_789.py
   ```

4. **Rebuild dashboard data & deploy to Firebase Hosting:**
   ```powershell
   # Optional: copy .env.example to .env and set GOOGLE_MAPS_API_KEY
   $env:PYTHONPATH = "src"
   python -m metro_od.dashboard docs/index.html 150000
   npx --yes firebase-tools deploy --only hosting
   ```
