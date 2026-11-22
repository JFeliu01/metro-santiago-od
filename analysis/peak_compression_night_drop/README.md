# Post-Pandemic Peak Compression & Nighttime Contraction (2019 vs. 2023)

## 1. Research Question & Hypothesis
- **Hypothesis:** Between April 2019 and April 2023, Metro de Santiago's post-pandemic ridership recovery was structurally uneven across the 24-hour day: **late-evening demand (`20:00–24:00`) suffered a massive `-43.65%` collapse (`-106,037` daily trips)**, while **morning peak (`06:00–09:00`) fell only `-14.46%`**, reshaping the optimal distribution of train-kilometers, energy consumption, and maintenance windows.

## 2. Explicit Methodological Assumptions
- **Full 7-Day Population Scan:** Executed via [`run_analysis.py`](./run_analysis.py) across all `22,175,170` workday trips in April 2019 (`2019-04-08` to `2019-04-12`, 5 workdays) and all `15,957,432` workday trips in April 2023 (`2023-04-17` to `2023-04-21`, 5 workdays).
- **Daily Normalization & Expansion:** Both years contain exact 5-workday parity (`Mon–Fri`); volumes are normalized to **average expanded trips per workday** (`SUM(factor_expansion) / 5`).

## 3. Full-Week Empirical Results (5 Workdays `LABORAL`, Metro-Initiated Trips)

| Time Block (`mh_inicio`) | Daily Expanded Trips (2019) | Daily Expanded Trips (2023) | Volume Change (`%`) | Share 2019 (`%`) | Share 2023 (`%`) | Share Shift (`Δ pp`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **0. Madrugada (`00:00–06:00`)** | 7,984 | 4,965 | **`-37.81%`** | 0.35% | 0.29% | `-0.06 pp` |
| **1. Punta Mañana (`06:00–09:00`)** | 469,244 | 401,383 | **`-14.46%`** | 20.71% | **23.22%** | **`+2.51 pp`** |
| **2. Valle Día (`09:00–17:00`)** | 914,744 | 709,980 | `-22.38%` | 40.38% | **41.08%** | **`+0.70 pp`** |
| **3. Punta Tarde (`17:00–20:00`)** | 630,461 | 475,163 | `-24.63%` | 27.83% | 27.49% | `-0.34 pp` |
| **4. Noche (`20:00–24:00`)** | 242,915 | 136,878 | **`-43.65%`** | 10.72% | **7.92%** | **`-2.80 pp`** |
| **TOTAL METRO WORKDAY** | **2,265,348** | **1,728,369** | **`-23.70%`** | **100.0%** | **100.0%** | — |

## 4. Full 7-Day Day-of-Week & Purpose Decomposition (`operational_analysis.py`)
Running across all **7 days of 2019 (`25.84M` trips) vs. 7 days of 2023 (`18.60M` trips)** uncovers two distinct behavioral signatures:
1. **The "Friday Hybrid-Work Inversion":** In 2019, Friday was the **busiest Metro day of the week** (`2,308,977` trips). In 2023, Friday fell **`-26.14%`** (`1,705,475` trips), becoming the **lowest workday of the week** (vs. `-22.7%` to `-23.6%` Mon–Thu).
2. **Universal Nighttime Collapse (`20:00–24:00`):** Every workday night fell **`-41.9%` to `-45.4%`**, driven by both earlier return-home (`HOGAR`: `-41.2%`, `-85.9k` trips/day) and a **`-64.1%` collapse in `OTROS`** (evening leisure/commerce). Weekend nights also fell `-28.3%` (Sat) and `-30.4%` (Sun), even though total Sunday Metro ridership recovered best (`-16.2%`).

## 5. Conclusion & Strategic Impact for Metro de Santiago
- **Hypothesis Strongly Validated:** Across the full 5-workday week (including Thursday/Friday nights), **nighttime Metro ridership (`20:00–24:00`) collapsed `-43.65%` (`-106,037` daily trips)** — 3× steeper than **Punta Mañana (`-14.46%`)**, which gained **`+2.51 pp`** of daily share.
- **Impact for Metro:**
  1. **Dynamic Fleet & Headway Reallocation:** Adjusting post-20:00 train headways (or transitioning earlier to shorter train formations) unlocks significant traction-energy savings and expands preventive maintenance windows without degrading Level of Service.
  2. **Targeted Evening Recovery:** Understanding why evening riders left Metro (safety perception, hybrid schedules, or earlier commercial closing hours) informs targeted fare/security initiatives after 20:00.
