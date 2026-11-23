# AI Usage, Prompts & Decision Log (`prompts.md`)

This document records the key prompts used during the development of the **Metro de Santiago Data Scientist Challenge**, the AI tooling employed, and how engineering, modeling, and validation decisions were directed and reviewed.

- **AI Tool:** Google Antigravity CLI (`agy`)
- **Models:** Gemini (Argon & Carbon)
- **Live Dashboard:** https://metro-santiago-od-jf-18a74.web.app
- **Public Repository:** https://github.com/JFeliu01/metro-santiago-od

---

## Phase 1 — Data Architecture & Harmonization

### 1. Selecting the stack and structuring the codebase
- **Prompt:**
  > *"We have origin–destination data for Metro de Santiago for one week in April 2019 and one week in April 2023. Let's organize the codebase to profile and clean the tables first — what tools are best fitted for multi-gigabyte CSV/XLSB processing? Start with a reproducible profiling notebook."*
- **Review & Decisions:**
  - Selected an **out-of-core DuckDB + Polars** pipeline (`src/metro_od/`) to process ~20.7 GB of raw CSV/XLSB/GTFS data (`44.43M` total trips) without exhausting RAM.
  - Built reproducible profiling and cleaning notebooks (`01_describe_tables.ipynb`, `02_clean_and_harmonize.ipynb`) resolving schema divergence across years, mixed coordinate reference systems (UTM 19S for Bus vs. WGS84 for Metro), integer commune/period decoding via `etapas_2023`, and strict travel-time comparability (`EPOCH(t_fin - t_inicio) / 60`).

---

## Phase 2 — Exploratory Data Analysis & Interactive Prototyping

### 2. Visual exploration workbench
- **Prompt:**
  > *"To explore demand patterns across Metro, Bus, Zona Paga, and Metrotren, let's build an interactive HTML view with a map and time-of-day filters so I can inspect corridors and station profiles directly."*
- **Decision:** Built a local multi-modal HTML workbench combining commune-level OD corridors, half-hourly demand curves, and station boarding profiles.

### 3. Structured 5-angle EDA & modular analysis layout
- **Prompt:**
  > *"Run a structured exploratory analysis covering temporal shifts (2019 vs. 2023), stage-level travel-time friction, unestimated destination rates (`SINBAJADA`), card-level trip chaining (`id_tarjeta`), and spatial network effects around Lines 3 and 6. Separate each study into its own subfolder under `analysis/`."*
- **Review & Decisions:**
  - Executed the 5-angle DuckDB EDA suite (`src/metro_od/eda.py`) and organized three independent analytical modules under `analysis/`:
    - `network_expansion_ml/` (L3/L6 ex-post DiD and L7–L9 spatial ML)
    - `feeder_return_leakage/` (AM→PM card-level intermodal return leakage)
    - `peak_compression_night_drop/` (night contraction and Friday hybrid-work inversion)

---

## Phase 3 — Spatial ML Modeling & Methodological Audit

### 4. Designing and iterating the Spatial ML pipeline for Lines 7, 8 & 9
- **Turn 1 — Framing & Ex-Post Baseline:**
  > *"Anchor the expansion analysis with a full-week Difference-in-Differences on Lines 3 and 6 (2019 vs. 2023), then build a spatial ML pipeline on 2023 OD trips using Zona 777 centroids and GTFS station distances to project the counterfactual impact of future Lines 7, 8, and 9."*
- **Turn 2 — Model Selection & Benchmark Design:**
  > *"Compare L2 Logistic Regression against Histogram GBDT for binary Metro mode choice (`P(uses_metro)`), and benchmark Ridge Regression against Histogram GBDT for continuous travel-time prediction (`duracion_obs_min`) on a stratified 80/20 holdout. Report ROC-AUC, PR-AUC, F1, Log-Loss, Brier, MAE, RMSE, and R²."*
- **Turn 3 — Architecture Iteration (`v1 → v2`):**
  > *"v1 only captures `Bus-only → Metro` mode shift and ignores existing `Bus → Metro` riders who gain direct walking access to a new station. Add a second classifier `P(direct_metro | uses_metro = 1)` and update the duration regressor to distinguish Bus-only, Intermodal, and Direct Solo Metro so the counterfactual quantifies both channels."*
- **Review & Outcomes:**
  - **Ex-Post DiD (L3/L6, `16.71M` workday trips):** `+7.09 pp` net Metro mode share (`z = -16.8`, `-0.039` stages/trip), explicitly noting that 2019→2023 captures L3 ramp-up/maturation.
  - **Model Selection (`131,400` trips, 80/20 holdout):** Selected **Histogram GBDT** for non-linear spatial classification (`P(uses_metro)` `ROC-AUC = 0.8817`; `P(direct_metro | uses_metro = 1)` `ROC-AUC = 0.9617`) and **Ridge Regression** for continuous distance-to-duration scaling (`MAE = 7.44 min`, `R² = 0.6401`, outperforming shallow-tree GBDT `R² = 0.5553`).
  - **Counterfactual Result (`3.19M` workday trips/day):** Combined L7+L8+L9 yield **`+72,184` new Metro trips/day** + **`65,263` Direct Metro upgrades/day** = **`137,447` benefited trips/day** and **`2,445` passenger-hours saved/day**.
  - Complementary card-level ML (`156,772` `bip!` cards) confirmed PM feeder-bus wait as the top return-leakage driver (`OR = 1.389`, `z = +46.5`, `-14.6 pp` wait counterfactual), and operational decomposition quantified the `-43.65%` night drop and `-26.14%` Friday hybrid-work inversion.

### 5. Stress-testing against the ADATRAP specification
- **Prompt:**
  > *"Cross-reference the hypothesis against the official ADATRAP specification [weekly Bus GPS + bip! supply-prioritization software] to identify potential flaws, observability gaps, or biases it may introduce."*
- **Review & Decision:** Documented two structural boundaries directly in the methodology: (i) because ADATRAP only observes `bip!` validations and bus GPS, the L7–L9 forecast is a **conservative lower bound** excluding private-car modal shift; and (ii) afternoon feeder-bus non-validation introduces a systematic undercounting bias in ADATRAP at terminal stations.

---

## Phase 4 — Production Web Dashboard & Repository Hygiene

- **Map & Chart Verification:** Spotted floating OD arcs in Google Maps view (missing commune centroid overlays), added toggleable GTFS `L1–L6` and proposed `L7–L9` polylines, fixed station-profile dictionary parsing, raised half-hourly y-axis headroom, and upgraded the station chart to a **2019 vs. 2023** comparison across all 15 stations.
- **UI Redesign, Security & Deployment:** Rebuilt `docs/` from scratch with clean HTML/CSS/JS separation (`index.html`, `styles.css`, `app.js`, `dashboard_data.js`), removed API-key inputs from the frontend UI, restricted the Google Maps key to Firebase HTTP referrers via gitignored `config.js`, and deployed to **https://metro-santiago-od-jf-18a74.web.app**.
- **Codebase Cleanup:** Refactored folder/variable names to clean domain identifiers, rewrote `README.md` around code reproducibility, added a Git `commit-msg` hook enforcing clean sole authorship, and published the repository at **https://github.com/JFeliu01/metro-santiago-od**.
