# european-electricity-market-panel
Part of data pipeline for analysing Capacity Remuneration Mechanisms and investment signals in EU electricity markets. Combines ENTSO-E, Ember, NESO and Eurostat data for 21 countries (2010-2024). Python/pandas.
# EU Electricity Market Panel Dataset — Master's Thesis Pipeline

This repository contains the data processing pipeline developed for my Master's thesis:

**"Empirical Analysis of Capacity Remuneration Mechanisms and Investment Signals in EU Electricity Markets"**
Barcelona School of Economics, 2026

The pipeline builds a panel dataset covering 21 European countries over 2010–2024, combining multiple electricity market data sources to analyse how capacity auction mechanisms affect investment signals and system stress outcomes.

---

## Research Context

Capacity Remuneration Mechanisms (CRMs) — including capacity auctions in Great Britain, Poland, Ireland, Italy, and Belgium — are designed to ensure long-term generation adequacy. This thesis uses a staggered difference-in-differences (DiD) design to estimate the causal effect of CRM adoption on:

- Installed capacity investment signals
- System stress events (high-tightness hours)
- Generation mix by fuel type

---

## Data Sources

| Source | Coverage | Variables |
|--------|----------|-----------|
| **Ember** Europe Yearly Electricity Data | 21 countries, 2009–2024 | Net imports, generation by fuel type (TWh) |
| **ENTSO-E** Monthly Hourly Load Values | 20 countries, 2006–2024 | Hourly demand (MW), three source formats |
| **NESO** (National Grid ESO) | Great Britain, 2009–2024 | Half-hourly demand (TSD, MW) |
| **Eurostat** | 21 countries, 2010–2024 | Installed net electrical capacity (MW) |

---

## Repository Structure

```
├── ember_data_process.py     # Processes Ember generation and imports data
├── european_demand_process.py      # Builds demand, peak load, and stress event panel
├── data/                     # Raw data files (not tracked — see notes below)
├── output/                   # Generated CSVs and plots
└── README.md
```

---

## Scripts

### `ember_data_process.py`

Processes Ember's Europe Yearly Electricity Data to extract:
- **Net imports** by country-year (TWh)
- **Electricity generation** by fuel type, aggregated to 7 categories: Fossil Fuels, Hydro, Nuclear, Solar, Wind, Other Renewables, Other Fuels
- **Year-on-year change in generation** per fuel type (`gen_change_TWh`) — used as a regression variable

Key steps:
1. Filter to 21 study countries using ISO2/ISO3 code mapping
2. Aggregate granular Ember fuel variables to panel fuel categories
3. Merge generation data into existing panel dataset
4. Validate with cross-plots of generation vs. installed capacity

### `european_demand_process.py`

Builds a complete annual demand and system stress panel from three ENTSO-E source formats plus a separate GB dataset:

| Format | Years | Source |
|--------|-------|--------|
| Wide xlsx (24-hour columns per row) | 2006–2015 | ENTSO-E |
| Long xlsx | 2016–2018 | ENTSO-E |
| CSV | 2019–2024 | ENTSO-E |
| Half-hourly CSV (API) | 2009–2024 | National Grid ESO |

Key steps:
1. Harmonise three ENTSO-E formats into a single annual panel
2. Download GB half-hourly demand from NESO API; convert to hourly via max()
3. Scale demand proportionally for incomplete country-years; flag severe gaps
4. Cross-check demand data across sources and document discrepancy reasons
5. Compute system stress metrics at three tightness thresholds (p90, p95, p97.5):
   - **Stress events**: consecutive hours above threshold (count + average duration)
   - **Scarcity hours**: total hours above threshold
6. Interpolate 2018 (ENTSO-E data gap) and IE 2022 using adjacent-year averages; imputation flags added
7. Merge with Eurostat capacity data to compute peak/capacity and demand/capacity ratios

---

## Outputs

| File | Description |
|------|-------------|
| `annual_panel_2010_2024.csv` | Annual demand and peak load, 21 countries |
| `stress_events_panel_2010_2024.csv` | Stress events and duration at p90/p95/p97.5 |
| `stress_percentiles_2010_2024.csv` | Scarcity hours at p90/p95/p97.5 |
| `system_stress_panel_2010_2024.csv` | Final merged panel (all variables) |
| `ember_net_imports_panel.csv` | Net imports by country-year |
| `ember_generation_by_fueltype.csv` | Generation by country-year-fuel category |
| `demand_peak_2010_2024.png` | Validation plot: demand and peak by country |
| `stress_events_p95_all_countries.png` | Treated vs control countries comparison |
| `peak_capacity_ratio.png` | Peak/capacity ratio over time |

---

## Key Methodological Notes

- **GB is not available in the ENTSO-E dataset** for the study period; sourced separately from National Grid ESO (NESO) API instead
- **Peak load is not scaled** for incomplete years — peak is an observed maximum, not a sum
- **2018 imputation**: ENTSO-E changed data format in 2018, causing widespread gaps; filled by averaging 2017 and 2019 values
- **Completeness threshold**: country-years below 80% data completeness are flagged as NaN in stress metrics
- **Generation starts from 2009** (not 2010) to enable first-difference calculation from 2010 onwards

---

## Requirements

```
pandas
numpy
matplotlib
openpyxl
requests
```

Install with:
```bash
pip install pandas numpy matplotlib openpyxl requests
```

---

## Data Access

Raw data files are not included in this repository. They can be downloaded from:

- **Ember** Europe Yearly Electricity Data: https://ember-energy.org/data/european-electricity-review/
- **ENTSO-E** Monthly Hourly Load Values: https://www.entsoe.eu/data/power-stats/
- **NESO** (National Grid ESO) GB demand data: https://api.neso.energy/ (URLs included in script)
- **Eurostat** Net maximum electrical capacity: https://ec.europa.eu/eurostat/web/energy/data/database

Place downloaded files in the `data/` directory as described in the CONFIG section of each script.

## Usage

1. Place raw data files in `data/demand/` and `data/` as described in the CONFIG section of each script
2. Run scripts in order:
```bash
python src/ember_data_process.py
python src/european_demand_process.py
```

---

## Author

**Merve Bozkurt**
MSc Economics of Energy, Climate Change and Sustainability
Barcelona School of Economics, 2026
[linkedin.com/in/merve-bozkurt](https://www.linkedin.com/in/merve-bozkurt)
