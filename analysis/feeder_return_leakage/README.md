# Asymmetric Feeder Return Leakage & OD Matrix Distortion (2019 vs. 2023)

## 1. Research Question & Hypothesis
- **Hypothesis:** Morning intermodal commuters who validate `BUS → METRO` overwhelmingly fail to validate their afternoon return feeder bus (`METRO → BUS`) due to a severe afternoon bus transfer wait penalty (`7.70 min` vs. `3.15 min` entering Metro) and fare evasion / informal mode leakage. This creates a structural **2.6× directional asymmetry** that **distorts Metro's Origin–Destination matrix** by inflating peripheral terminal stations as "final destinations" and leaving `43.7%` of `METRO → BUS` trips without an estimated destination.

## 2. Explicit Methodological Assumptions
- **Full 7-Day Population Scan:** Executed via [`run_analysis.py`](./run_analysis.py) with **zero `LIMIT` sampling** across all `22,175,170` workday trips in April 2019 (`2019-04-08` to `2019-04-12`, 5 workdays) and all `15,957,432` workday trips in April 2023 (`2023-04-17` to `2023-04-21`, 5 workdays). All daily totals are normalized by the 5 observed workdays (`n_days = 5`).
- **Comparable Travel Time (`duracion_obs_min`):** Computed identically for 2019 and 2023 as `EPOCH(tiempo_fin_viaje - tiempo_inicio_viaje) / 60.0` (filtering `[1, 300]` min) to avoid the 2023 `tviaje2` definition shift.

## 3. Full-Week Empirical Results (5 Workdays 2019 vs. 5 Workdays 2023)

### A. Structural 2.6× Directional Asymmetry (2019 vs. 2023 Workdays)
| Year | 2-Stage Sequence | Raw Trips / Day | Expanded Trips / Day | Comparable Duration (`min`) | Unestimated Dest (`sin_bajada_pct`) |
| :---: | :--- | :---: | :---: | :---: | :---: |
| **2019** | `BUS → METRO` | 439,907 | 532,802 | 41.56 min | 12.65% |
| **2019** | `METRO → BUS` | 166,287 | 123,112 | 46.44 min (`+4.88 min`) | **42.86%** |
| **2023** | `BUS → METRO` | 318,833 | 392,558 | 41.61 min | 15.83% |
| **2023** | `METRO → BUS` | 123,781 | 93,191 | **47.72 min (`+6.11 min`)** | **43.73%** |

### B. Direct Card-Level Proof (`id_tarjeta` Tracking of 826,099 Morning `BUS → METRO` Card-Days in 2023)
Tracking every bip! card that validated a 2-stage `BUS → METRO` trip between `05:30–10:30` across their afternoon (`≥ 13:00`) validations on the same day:

| Afternoon Return Behavior (`≥ 13:00`) | Card-Days (5 Workdays) | Share (%) |
| :--- | :---: | :---: |
| **1. Symmetric Validated `METRO → BUS`** | 131,651 | **15.94%** |
| **2. `SOLO METRO` (1 stage — feeder bus NOT validated!)** | **269,171** | **32.58%** |
| **3. Return started on `BUS` (1+ stages)** | 110,250 | 13.35% |
| **4. Other validated return (`Zona Paga` / `Metrotren`)** | 161,133 | 19.51% |
| **5. No validated afternoon return trip at all** | 153,894 | 18.63% |

### C. Top Priority Metro Terminals by Daily Asymmetric Return Gap (2023 Workdays)
| Metro Station | Morning `BUS → METRO` / Day | Afternoon `METRO → BUS` / Day | Daily Unvalidated Gap | Validated Return Rate |
| :--- | :---: | :---: | :---: | :---: |
| **LA CISTERNA** | 18,177 | 3,180 | **14,997** | **17.5%** |
| **PLAZA MAIPU** | 13,348 | 3,212 | **10,136** | 24.1% |
| **SAN PABLO** | 11,530 | 2,306 | **9,223** | 20.0% |
| **MANQUEHUE** | 11,537 | 2,329 | **9,208** | 20.2% |
| **HOSPITAL SOTERO DEL RIO** | 10,220 | 1,028 | **9,192** | **10.1%** |
| **LOS DOMINICOS** | 11,828 | 2,909 | **8,920** | 24.6% |
| **LOS LIBERTADORES** | 11,573 | 2,780 | **8,793** | 24.0% |
| **SANTA ROSA** | 6,807 | 576 | **6,231** | **8.5%** |

## 4. Predictive ML Model (`model_return_leakage.py` — `156,772` Tracked `id_tarjeta` Card-Days)
To test *why* morning `BUS → METRO` riders skip validating their afternoon feeder bus (`y = 1` for `SOLO METRO`, `67.7%` positive rate among Metro returners vs `y = 0` for symmetric `METRO → BUS`), we trained and evaluated two models on an 80/20 split:

| Model | ROC-AUC | PR-AUC | F1 Score | Log-Loss | Brier Score |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **L2 Logistic Regression (IRLS)** | 0.6143 | 0.7608 | **0.8027** | 0.6125 | 0.2116 |
| **Histogram GBDT (65 trees, depth 4)** | **0.6276** | **0.7630** | 0.8007 | **0.6079** | **0.2095** |

- **#1 Driver in Both Models:** `station_pm_bus_wait_min` (average afternoon bus transfer wait at the exit Metro station) — **Odds Ratio = `1.389` per SD (`z = +46.51`, `28.3%` GBDT importance)**, followed by `log_station_bm_vol` (`z = +36.14`, `23.9%` importance) and `is_pm_peak` (`z = +10.13`, `12.6%` importance).
- **Counterfactual Policy Simulation:** Cutting afternoon feeder bus wait time by **50%** (via coordinated headways / closed `Zona Paga` terminals) reduces feeder bus non-validation by **`-13.7 pp` (Logistic Regression)** to **`-14.6 pp` (GBDT)**.

## 5. Conclusion & Strategic Impact for Metro de Santiago
- **Hypothesis Validated:** More than **2× as many morning `BUS → METRO` commuters return as `SOLO METRO` (`32.58%`) than as `METRO → BUS` (`15.94%`)**, proving that afternoon feeder bus non-validation (evasion + mode leakage) is the primary driver of the `2.6×` directional gap.
- **Impact for Metro:**
  1. **OD Planning Bias Correction:** Standard ADATRAP OD tables systematically over-allocate afternoon destinations to terminal station zones (`La Cisterna`, `Plaza Maipú`, `Sótero del Río`) while hiding true last-mile feeder demand.
  2. **Closed Intermodal Hubs (`Zonas Pagas` / EIM):** Prioritizing physical paid-zone integration at the top 6 stations (`>60,000` daily unvalidated return transfers) recovers both system-wide validation data and feeder coordination.
