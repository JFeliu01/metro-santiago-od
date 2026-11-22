# Causal Impact of Lines 3 & 6 (2019–2023) & Predictive Counterfactual for Future Lines 7, 8 & 9

## 1. Research Question & Hypothesis
- **Retrospective Hypothesis (Ex-Post DiD, 2019 vs. 2023):** Between April 2019 (3 months after Line 3 opened in Jan 2019) and April 2023, the operational maturation of **Metro Lines 3 & 6** produced a statistically significant modal shift toward Metro (`% METRO`), fewer transfer stages (`n_etapas`), and lower door-to-door travel time (`duracion_obs_min`) in L3/L6 communes relative to control communes.
  - *Critical Domain Caveat:* Line 6 opened Nov 2017, Line 3 opened Jan 2019, and the L3 Quilicura extension opened Sep 2023 (after April 2023). Thus, 2019→2023 captures **L3 ramp-up & network maturity** rather than pre-opening baseline.
- **Prospective ML Objective (Ex-Ante Prediction for Lines 7, 8 & 9):** Train spatial Machine Learning models on **2023 OD trips (`3.19M` valid workday trips across `792` Zona 777 centroids and `126` GTFS Metro stations)** to predict **both**:
  1. **Channel 1 — Mode Shift (`Bus-only → Metro`)** via `P(uses_metro | origin, destination, period, purpose)`, and
  2. **Channel 2 — Intermodal Upgrade (`Bus+Metro → Direct Solo Metro`)** via `P(direct_metro | uses_metro=1, ...)` and travel-time savings when proposed **Line 7, Line 8, and Line 9** stations are added to the network.

---

## 2. Explicit Methodological Assumptions
- **Full 7-Day Population Scan (DiD):** [`run_analysis.py`](./run_analysis.py) processes **16,708,060** completed workday trips (`tipo_dia = 'LABORAL'`, `ultima_bajada = 1`) across all 5 workdays of April 2019 (`2019-04-08`..`12`) and April 2023 (`2023-04-17`..`21`).
- **Strict Travel Time Comparability:** Uses `EPOCH(tiempo_fin_viaje - tiempo_inicio_viaje) / 60.0` identically in both years (avoiding the `tviaje_min` vs. `tviaje2` definition mismatch).
- **Spatial ML Architecture ([`model_lines_789.py`](./model_lines_789.py)):**
  - Maps all `792` DTPM `Zona 777` centroids (from `etapas_2023` bus GPS coordinates) to exact Haversine distances to the `126` active GTFS Metro stations (`stops.txt`).
  - Evaluates counterfactual station networks for **Line 7** (19 stations: Renca–Cerro Navia–Quinta Normal–Santiago–Providencia–Las Condes–Vitacura), **Line 8** (14 stations: Providencia–Ñuñoa–Macul–La Florida–Puente Alto), and **Line 9** (19 stations: Recoleta–Santiago–San Joaquín–San Miguel–La Granja–San Ramón–La Pintana–Puente Alto).
  - **Self-Correction (v1 → v2):** v1 only predicted `Bus-only → Metro` mode shift and missed time savings for existing `BUS → METRO` riders who gain direct station access. v2 added **Model 1B (`P(direct_metro | uses_metro=1)`)** and a 3-mode travel-time regressor (`Bus-only`, `Intermodal`, `Direct Solo Metro`), revealing **65,263 additional daily riders** who eliminate a feeder bus leg.
  - **Scope Boundary:** Predictions measure modal shift and time savings **within existing public-transit (`bip!`) demand** (`3.19M` workday trips); induced demand from private automobiles represents additional upside.

---

## 3. Part A — Retrospective Difference-in-Differences (`16.71M` Workday Trips)

| Group | Year | Valid Trips (`n`) | Mean Duration (`min`) | Std Dev (`min`) | Mean Stages (`n_etapas`) | Metro Mode Share (`%`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **L3/L6 Treated (6 communes)** | 2019 | 1,918,809 | 27.296 | 20.098 | 1.351 | 42.10% |
| **L3/L6 Treated (6 communes)** | 2023 | 1,418,109 | **26.789** (`-0.507`) | 20.985 | **1.313** (`-0.038`) | **50.27%** (**`+8.17 pp`**) |
| **Control (8 communes)** | 2019 | 7,910,239 | 28.612 | 20.878 | 1.295 | 55.38% |
| **Control (8 communes)** | 2023 | 5,460,903 | **28.519** (`-0.093`) | 21.803 | **1.296** (`+0.001`) | **56.46%** (`+1.08 pp`) |

- **DiD Net Causal Effects:** **`+7.09 pp`** Metro mode share capture, **`-0.414 min`** duration ($z = -16.8, p < 10^{-50}$), **`-0.039` stages/trip**.
- **Top L3 Maturity Gainers:** Independencia (`+16.2 pp`), Conchalí (`+14.3 pp`), Quilicura (`+13.8 pp`), Cerrillos (`+7.3 pp`).

---

## 4. Part B — Spatial ML Model Validation (`131,400` Stratified Workday Trips, 80/20 Holdout)

| Task | Model | ROC-AUC | PR-AUC | F1 | Log-Loss | Brier | MAE (`min`) | RMSE (`min`) | $R^2$ | Selected |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1A. Mode Choice (`uses_metro`)** | L2 Logistic Reg. | 0.8595 | 0.9284 | 0.8880 | 0.3977 | 0.1247 | — | — | — | Baseline |
| **1A. Mode Choice (`uses_metro`)** | **Histogram GBDT** | **0.8817** | **0.9355** | **0.8949** | **0.3707** | **0.1136** | — | — | — | **Winner** |
| **1B. Direct Metro (`direct \| metro`)** | **Histogram GBDT** | **0.9617** | **0.9707** | **0.9590** | — | **0.0456** | — | — | — | **Winner** |
| **2. Travel Time (`duracion_min`)** | **Ridge Regression** | — | — | — | — | — | **7.439** | **12.283** | **0.6401** | **Winner** |
| **2. Travel Time (`duracion_min`)** | Histogram GBDT | — | — | — | — | — | 9.210 | 13.652 | 0.5553 | Benchmark |

- **Key Model Selection Insight:** Non-linear **Histogram GBDT** dominates spatial classification (`ROC-AUC = 0.8817` for Metro choice and `0.9617` for Direct Solo Metro) because station-access thresholds are sharply non-linear (`< 1 km` walking catchment), whereas **Ridge Regression** (`R² = 0.6401`, `MAE = 7.44 min`) outperforms shallow trees on continuous linear distance-to-duration extrapolation.
- **Top Mode-Choice Predictors (GBDT Importance):** `trip_eucl_km` (`35.4%`), `max_end_dist_metro_km` (`19.0%`), `dest_dist_metro_km` (`15.0%`), `sum_end_dist_metro_km` (`8.9%`), `min_end_dist_metro_km` (`7.3%`), `is_study` (`5.8%`).

---

## 5. Part C — Counterfactual Forecast for Lines 7, 8 & 9 (`3.19M` Workday Trips/Day)

| Scenario | New Daily Metro Trips (`Bus → Metro`) | Upgraded Direct Trips (`Intermodal → Direct`) | Total Benefited Trips / Day | Network Metro Share (`%`) | Share Gain (`pp`) | Daily Passenger-Hours Saved |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Baseline (2023 Network)** | — | — | — | 70.40% | — | — |
| **+ Line 7 (Renca–Vitacura)** | **+40,178** | **32,452** | **72,630** | 71.66% | **+1.26 pp** | **1,527 hrs/day** |
| **+ Line 8 (Prov.–Puente Alto)** | **+20,775** | **20,258** | **41,033** | 71.05% | **+0.65 pp** | **500 hrs/day** |
| **+ Line 9 (Recoleta–La Pintana)** | **+11,375** | **12,284** | **23,659** | 70.76% | **+0.36 pp** | **352 hrs/day** |
| **+ Combined L7 + L8 + L9** | **+72,184** | **65,263** | **137,447** | **72.67%** | **+2.26 pp** | **2,445 hrs/day** |

### Top Beneficiary Communes under Combined L7 + L8 + L9
| Commune | New Metro Trips/Day | Upgraded Direct Trips/Day | Total Benefited/Day | Base Metro Share | Projected Share Gain | Avg Nearest Station Drop |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **SANTIAGO** | +15,124 | 8,693 | 23,818 | 75.7% | +2.7 pp | -0.01 km (dest. effect) |
| **ÑUÑOA** | +7,941 | 5,959 | 13,899 | 65.3% | +4.6 pp | -0.07 km |
| **VITACURA** | +5,849 | 6,400 | 12,249 | 52.8% | **+14.3 pp** | **-1.67 km** |
| **PROVIDENCIA** | +5,820 | 4,581 | 10,401 | 82.8% | +1.8 pp | -0.04 km |
| **MACUL** | +4,022 | 5,417 | 9,439 | 81.9% | +4.9 pp | -0.19 km |
| **LAS CONDES** | +3,891 | 4,920 | 8,811 | 74.7% | +1.7 pp | -0.12 km |
| **CERRO NAVIA** | +4,442 | 4,016 | 8,458 | 45.0% | **+12.6 pp** | **-1.65 km** |
| **QUINTA NORMAL** | +4,266 | 2,688 | 6,954 | 45.7% | **+9.1 pp** | -0.40 km |
| **LA PINTANA** | +1,892 | 1,978 | 3,870 | 58.4% | **+8.5 pp** | **-1.55 km** |

## 6. Strategic Impact for Metro de Santiago
- **Capital Prioritization & Equity:** **Line 7** delivers the largest single-line demand & time-savings impact (**`72,630` benefited trips/day**, **`1,527` passenger-hours/day**), unlocking both high-income **Vitacura (`+14.3 pp`)** and underserved **Cerro Navia (`+12.6 pp`)**. **Line 9** delivers the highest per-trip time savings in **La Pintana (`1.6 min/trip`, `+8.5 pp` share)**, directly advancing territorial equity.
- **Annualized Value:** Combined L7+L8+L9 capture **`~19.5M` new annual Metro trips** from existing transit users alone, upgrade **`~17.6M` annual intermodal trips** to Direct Solo Metro, and save **`~660,000` passenger-hours/year** (~270 workdays) — before accounting for private-car modal shift.
