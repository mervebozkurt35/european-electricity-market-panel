# -*- coding: utf-8 -*-
"""
eu_demand_process.py
====================
Builds a complete annual electricity demand and system stress panel
for 21 European countries (2010-2024), combining three ENTSO-E source
formats and a separate GB dataset from National Grid ESO (NESO).

Pipeline overview:
  1. Load and harmonise ENTSO-E demand data (3 formats: wide xlsx, long xlsx, CSV)
  2. Download and process GB half-hourly demand data from NESO API
  3. Merge all sources into a single annual panel (Demand_MWh, Peak_MW)
  4. Compute system stress metrics: stress events, stress duration, scarcity hours
     at three tightness thresholds (p90, p95, p97.5)
  5. Interpolate missing years (2018 for all countries, IE 2022) using
     adjacent-year averages
  6. Merge demand panel with installed capacity data (Eurostat) to compute
     peak/capacity and demand/capacity ratios
  7. Produce validation plots

Inputs (update paths in CONFIG section below):
  - ENTSO-E wide xlsx    : Monthly-hourly-load-values_2006-2015.xlsx
  - ENTSO-E long xlsx    : MHLV_data-2015-2019.xlsx
  - ENTSO-E CSVs         : monthly_hourly_load_values_20XX.csv (2019-2024)
  - NESO GB data         : downloaded via API or local CSV files
  - Capacity data        : Net maximum electrical capacity (Eurostat)

Outputs:
  - annual_panel_2006_2025.csv         : Full ENTSO-E panel (pre-filter)
  - gb_annual_panel_2009_2025.csv      : GB panel from NESO
  - annual_panel_final.csv             : All sources merged (2006-2025)
  - annual_panel_2010_2024.csv         : Study period filtered (21 countries)
  - stress_events_panel_2010_2024.csv  : Stress events and duration metrics
  - stress_percentiles_2010_2024.csv   : Scarcity hours at p90/p95/p97.5
  - stress_95_2010_2024.csv            : Scarcity hours at p95 only
  - system_stress_panel_2010_2024.csv  : Final merged panel (all variables)
  - demand_peak_selected.png           : Validation plot (selected countries)
  - demand_peak_2010_2024.png          : Study period validation plot
  - stress_events_all_countries.png    : Stress events by country
  - stress_duration_all_countries.png  : Stress duration by country
  - stress_events_p95_all_countries.png: Treated vs controls comparison
  - peak_capacity_ratio.png            : Peak/capacity ratio over time

Usage:
    Update paths in the CONFIG section, then run:
    python eu_demand_process.py
"""

import os
import glob
import calendar
import time
import io

import numpy as np
import pandas as pd
import requests
import matplotlib.pyplot as plt


# =============================================================================
# CONFIG — update these paths before running
# =============================================================================

DEMAND_DIR  = "data/demand/"
UK_DIR      = "data/demand/UK/"
OUTPUT_DIR  = "output/"

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(UK_DIR, exist_ok=True)

# Derived output paths
ANNUAL_PANEL_PATH   = os.path.join(OUTPUT_DIR, "annual_panel_2006_2025.csv")
GB_PANEL_PATH       = os.path.join(OUTPUT_DIR, "gb_annual_panel_2009_2025.csv")
FINAL_PANEL_PATH    = os.path.join(OUTPUT_DIR, "annual_panel_final.csv")
STUDY_PANEL_PATH    = os.path.join(OUTPUT_DIR, "annual_panel_2010_2024.csv")
STRESS_PANEL_PATH   = os.path.join(OUTPUT_DIR, "stress_events_panel_2010_2024.csv")
SCARCITY_PATH       = os.path.join(OUTPUT_DIR, "stress_percentiles_2010_2024.csv")
SCARCITY_95_PATH    = os.path.join(OUTPUT_DIR, "stress_95_2010_2024.csv")
SYSTEM_STRESS_PATH  = os.path.join(OUTPUT_DIR, "system_stress_panel_2010_2024.csv")


# =============================================================================
# CONSTANTS
# =============================================================================

# 21 European countries in the study
TARGET_21 = [
    'AT', 'BE', 'CZ', 'DE', 'DK', 'ES', 'FI', 'FR', 'GB', 'GR', 'HU',
    'IE', 'IT', 'NL', 'NO', 'PL', 'PT', 'RO', 'SE', 'SI', 'SK'
]

TARGET_YEARS = list(range(2010, 2025))

# Tightness thresholds for stress/scarcity metrics
# Tightness = hourly_load_MW / annual_peak_MW[country, year]
THRESHOLDS = {"p90": 0.90, "p95": 0.95, "p975": 0.975}

# Country-years with known severe data gaps — flagged as NaN in final panel
SEVERE_GAPS = {
    ("UA", 2022), ("XK", 2021), ("AL", 2021),
    ("GB", 2021), ("GB", 2023),
    ("BA", 2022),
}

# Countries excluded from ENTSO-E panel (unreliable data — added from separate sources)
EXCLUDE_COUNTRIES = {"GB", "CY"}

# Treatment years for capacity remuneration mechanism (CRM) adoption
# Used for event-study visualisations
TREATMENT_YEARS = {"GB": 2014, "PL": 2016, "IE": 2017, "IT": 2019, "BE": 2021}

TREATED_COLORS = {
    "GB": "steelblue", "PL": "darkorange",
    "IE": "green", "IT": "red", "BE": "purple"
}
CONTROLS = [c for c in TARGET_21 if c not in TREATMENT_YEARS]

# Country name mapping
COUNTRY_NAMES = {
    'AL': 'Albania', 'AT': 'Austria', 'BA': 'Bosnia and Herzegovina',
    'BE': 'Belgium', 'BG': 'Bulgaria', 'CH': 'Switzerland',
    'CZ': 'Czech Republic', 'DE': 'Germany', 'DK': 'Denmark',
    'EE': 'Estonia', 'ES': 'Spain', 'FI': 'Finland', 'FR': 'France',
    'GB': 'United Kingdom', 'GE': 'Georgia', 'GR': 'Greece',
    'HR': 'Croatia', 'HU': 'Hungary', 'IE': 'Ireland', 'IT': 'Italy',
    'LT': 'Lithuania', 'LU': 'Luxembourg', 'LV': 'Latvia',
    'MD': 'Moldova', 'ME': 'Montenegro', 'MK': 'North Macedonia',
    'NL': 'Netherlands', 'NO': 'Norway', 'PL': 'Poland',
    'PT': 'Portugal', 'RO': 'Romania', 'RS': 'Serbia', 'SE': 'Sweden',
    'SI': 'Slovenia', 'SK': 'Slovakia', 'UA': 'Ukraine', 'XK': 'Kosovo'
}

# Eurostat uses different codes for two countries — map to ISO2
EUROSTAT_MAP = {'EL': 'GR', 'UK': 'GB'}

# NESO API URLs (GB demand data, annual files)
NESO_URLS = {
    2009: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/ed8a37cb-65ac-4581-8dbc-a3130780da3a/download/demanddata_2009.csv",
    2010: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/b3eae4a5-8c3c-4df1-b9de-7db243ac3a09/download/demanddata_2010.csv",
    2011: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/01522076-2691-4140-bfb8-c62284752efd/download/demanddata_2011.csv",
    2012: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/4bf713a2-ea0c-44d3-a09a-63fc6a634b00/download/demanddata_2012.csv",
    2013: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/2ff7aaff-8b42-4c1b-b234-9446573a1e27/download/demanddata_2013.csv",
    2014: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/b9005225-49d3-40d1-921c-03ee2d83a2ff/download/demanddata_2014.csv",
    2015: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/cc505e45-65ae-4819-9b90-1fbb06880293/download/demanddata_2015.csv",
    2016: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/3bb75a28-ab44-4a0b-9b1c-9be9715d3c44/download/demanddata_2016.csv",
    2017: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/2f0f75b8-39c5-46ff-a914-ae38088ed022/download/demanddata_2017.csv",
    2018: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/fcb12133-0db0-4f27-a4a5-1669fd9f6d33/download/demanddata_2018.csv",
    2019: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/dd9de980-d724-415a-b344-d8ae11321432/download/demanddata_2019.csv",
    2020: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/33ba6857-2a55-479f-9308-e5c4c53d4381/download/demanddata_2020.csv",
    2021: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/18c69c42-f20d-46f0-84e9-e279045befc6/download/demanddata_2021.csv",
    2022: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/bb44a1b5-75b1-4db2-8491-257f23385006/download/demanddata_2022.csv",
    2023: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/bf5ab335-9b40-4ea4-b93a-ab4af7bce003/download/demanddata_2023.csv",
    2024: "https://api.neso.energy/dataset/8f2fe0af-871c-488d-8bad-960426f24601/resource/f6d02c0f-957b-48cb-82ee-09003f2ba759/download/demanddata_2024.csv",
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def expected_annual_hours(year: int) -> int:
    """Return 8784 for leap years, 8760 otherwise."""
    return 8784 if calendar.isleap(year) else 8760


def aggregate(df: pd.DataFrame, scale_col: str) -> pd.DataFrame:
    """
    Aggregate hourly load data to annual demand and peak per country-year.

    Returns columns: CountryCode | Year | raw_sum | peak_MW | hours_present
    """
    df["_year"] = pd.to_datetime(
        df["DateUTC"].astype(str).str[:10], dayfirst=True, errors="coerce"
    ).dt.year
    return (
        df.groupby(["CountryCode", "_year"])
        .agg(
            raw_sum      =(scale_col, "sum"),
            peak_MW      =(scale_col, "max"),
            hours_present=(scale_col, "count"),
        )
        .reset_index()
        .rename(columns={"_year": "Year"})
    )


def build_panel(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean and finalise the annual demand panel.

    - Excludes unreliable countries (GB, CY handled separately)
    - Scales demand up proportionally for incomplete years
    - Peak load is NOT scaled (peak is an observed maximum, not a sum)
    - Flags known severe data gaps and low-completeness country-years as NaN
    """
    df = df[~df["CountryCode"].isin(EXCLUDE_COUNTRIES)].copy()

    df["hours_expected"]   = df["Year"].apply(expected_annual_hours)
    df["completeness_pct"] = (df["hours_present"] / df["hours_expected"] * 100).round(1)
    df["scale_factor"]     = df["hours_expected"] / df["hours_present"].clip(lower=1)

    # Scale demand proportionally to compensate for missing hours
    df["Demand_MWh"] = df["raw_sum"] * df["scale_factor"]

    # Peak is a single observed value — no scaling applied
    df["Peak_MW"] = df["peak_MW"].round(1)

    # Flag known severe data gaps (e.g. war-related outages, reporting failures)
    mask = df.apply(lambda r: (r["CountryCode"], int(r["Year"])) in SEVERE_GAPS, axis=1)
    df.loc[mask, ["Demand_MWh", "Peak_MW"]] = float("nan")

    # Flag country-years with very low completeness (<35%) as unreliable
    low = df["completeness_pct"] < 35
    df.loc[low, ["Demand_MWh", "Peak_MW"]] = float("nan")

    return (
        df[["CountryCode", "Year", "Demand_MWh", "Peak_MW", "hours_present",
            "hours_expected", "completeness_pct", "scale_factor"]]
        .sort_values(["CountryCode", "Year"])
        .reset_index(drop=True)
    )


def compute_stress_metrics(tightness_series: pd.Series, threshold: float):
    """
    Count stress events and compute average duration for one country-year.

    A stress event is a run of consecutive hours where tightness > threshold.
    Tightness = hourly_load_MW / annual_peak_MW.

    Returns:
        n_events     : number of distinct stress events
        avg_duration : mean duration of events in hours
    """
    stress = (tightness_series > threshold).astype(int).values
    if stress.sum() == 0:
        return 0, 0.0

    events = []
    in_event, length = False, 0
    for s in stress:
        if s == 1:
            in_event = True
            length += 1
        else:
            if in_event:
                events.append(length)
                in_event, length = False, 0
    if in_event:
        events.append(length)

    return len(events), (np.mean(events) if events else 0.0)


def interpolate_missing_year(df, country, year, stress_cols):
    """
    Fill a missing country-year by averaging the adjacent years (year-1, year+1).
    Only applies if both adjacent years have non-NaN values.
    Used for 2018 (ENTSO-E data gap) and IE 2022.
    """
    mask = (df["CountryCode"] == country) & (df["Year"] == year)
    r_prev = df[(df["CountryCode"] == country) & (df["Year"] == year - 1)]
    r_next = df[(df["CountryCode"] == country) & (df["Year"] == year + 1)]
    if len(r_prev) == 0 or len(r_next) == 0:
        return
    for col in stress_cols:
        v_prev = r_prev[col].values[0]
        v_next = r_next[col].values[0]
        if pd.notna(v_prev) and pd.notna(v_next):
            df.loc[mask, col] = round((v_prev + v_next) / 2, 1)


# =============================================================================
# SECTION 1: LOAD ENTSO-E DATA (THREE SOURCE FORMATS)
# =============================================================================

# ── 1a. CSV files: 2019-2025 ─────────────────────────────────────────────────

def load_csv(filepath: str) -> pd.DataFrame:
    """Load one ENTSO-E monthly-hourly CSV file and aggregate to country-year."""
    t0 = time.time()
    print(f"  {os.path.basename(filepath)} ...", end=" ", flush=True)
    df = pd.read_csv(filepath, sep=None, engine="python")
    df.columns = df.columns.str.strip()
    scale_col = next((c for c in df.columns if c.lower() == "value_scaleto100"), None)
    if scale_col is None:
        raise KeyError(f"No Value_ScaleTO100 column.\nColumns: {list(df.columns)}")
    grp = aggregate(df, scale_col)
    print(f"done in {time.time()-t0:.1f}s")
    return grp


# ── 1b. Long-format xlsx: 2016-2018 ──────────────────────────────────────────

def load_long_xlsx(filepath: str, years: list, sheet_name=0) -> pd.DataFrame:
    """Load ENTSO-E long-format xlsx (2015-2019 consolidated file) and aggregate."""
    t0 = time.time()
    print(f"  {os.path.basename(filepath)} (years {years}) ...", end=" ", flush=True)
    df = pd.read_excel(filepath, sheet_name=sheet_name, engine="openpyxl")
    df.columns = df.columns.str.strip()
    scale_col = next((c for c in df.columns if c.lower() == "value_scaleto100"), None)
    if scale_col is None:
        raise KeyError(f"No Value_ScaleTO100 column.\nColumns: {list(df.columns)}")
    grp = aggregate(df, scale_col)
    grp = grp[grp["Year"].isin(years)]
    print(f"done in {time.time()-t0:.0f}s  |  years: {sorted(grp['Year'].unique())}")
    return grp


# ── 1c. Wide-format xlsx: 2006-2015 ──────────────────────────────────────────

def load_wide(filepath: str) -> pd.DataFrame:
    """
    Load ENTSO-E wide-format xlsx (2006-2015).

    Format: one row per country-day, 24 hour columns (0-23),
    plus a coverage % column used to scale for partially missing days.
    Returns aggregated annual demand and peak per country-year.
    """
    print(f"  {os.path.basename(filepath)} [wide] ...", end=" ", flush=True)
    t0 = time.time()
    df = pd.read_excel(filepath, header=3, engine="openpyxl")
    cols        = list(df.columns)
    country_col = cols[0]
    cov_col     = next((c for c in cols if str(c).lower().startswith("cov")), None)
    hour_cols   = [c for c in cols if str(c).strip() in [str(h) for h in range(24)]]
    if len(hour_cols) != 24:
        hour_cols = [c for c in cols if isinstance(c, (int, float)) and 0 <= c <= 23]

    df = df.dropna(subset=["Year", country_col])
    df["Year"]    = df["Year"].astype(int)
    df[cov_col]   = pd.to_numeric(df[cov_col], errors="coerce").fillna(100)
    df[hour_cols] = df[hour_cols].apply(pd.to_numeric, errors="coerce")

    # Scale daily totals and peaks for partial coverage days
    df["_daily"]       = df[hour_cols].sum(axis=1) * (100.0 / df[cov_col])
    df["_daily_peak"]  = df[hour_cols].max(axis=1) * (100.0 / df[cov_col])
    df["_valid_hours"] = df[hour_cols].notna().sum(axis=1)

    agg = (
        df.groupby([country_col, "Year"])
        .agg(
            raw_sum      =("_daily",       "sum"),
            peak_MW      =("_daily_peak",  "max"),   # max of daily peaks = annual peak
            hours_present=("_valid_hours", "sum"),
        )
        .reset_index()
        .rename(columns={country_col: "CountryCode"})
    )
    print(f"done in {time.time()-t0:.0f}s")
    return agg


print("\n=== SECTION 1: Loading ENTSO-E data ===")

# Load CSV files (2019-2025)
print("\nCSV files (2019-2025)")
csv_files = sorted(glob.glob(os.path.join(DEMAND_DIR, "monthly_hourly_load_values_20*.csv")))
if not csv_files:
    raise FileNotFoundError(f"No CSV files found in:\n  {DEMAND_DIR}")
df_csv = pd.concat([load_csv(f) for f in csv_files], ignore_index=True)
df_csv = df_csv.groupby(["CountryCode", "Year"]).agg(
    raw_sum      =("raw_sum",       "sum"),
    peak_MW      =("peak_MW",       "max"),
    hours_present=("hours_present", "sum"),
).reset_index()
print(f"  → {len(df_csv):,} country-years | years: {sorted(df_csv['Year'].unique())}")

# Load long xlsx (2016-2018)
print("\nLong xlsx (2016-2018)")
consol = os.path.join(DEMAND_DIR, "MHLV_data-2015-2019.xlsx")
if os.path.exists(consol):
    df_mid_a = load_long_xlsx(consol, years=[2016, 2017], sheet_name="2015-2017")
    df_mid_b = load_long_xlsx(consol, years=[2018],       sheet_name="2018-2019")
    df_mid   = pd.concat([df_mid_a, df_mid_b], ignore_index=True)
    print(f"  → {len(df_mid):,} country-years")
else:
    print(f"  WARNING: Not found: {consol}")
    df_mid = pd.DataFrame(columns=["CountryCode", "Year", "raw_sum", "peak_MW", "hours_present"])

# Load wide xlsx (2006-2015)
print("\nWide-format xlsx (2006-2015)")
wide_candidates = (
    sorted(glob.glob(os.path.join(DEMAND_DIR, "*2006*2015*.xlsx"))) or
    sorted(glob.glob(os.path.join(DEMAND_DIR, "*2006*15*.xlsx")))
)
if wide_candidates:
    df_wide = load_wide(wide_candidates[0])
    df_wide = df_wide[df_wide["Year"] <= 2015]
    print(f"  → {len(df_wide):,} country-years | years: {sorted(df_wide['Year'].unique())}")
else:
    print("  WARNING: Wide-format file not found.")
    df_wide = pd.DataFrame(columns=["CountryCode", "Year", "raw_sum", "peak_MW", "hours_present"])

# Combine and build ENTSO-E panel
print("\nBuilding ENTSO-E panel...")
df_all    = pd.concat([df_wide, df_mid, df_csv], ignore_index=True)
df_panel  = build_panel(df_all)

print(f"\n  Shape      : {df_panel.shape}")
print(f"  Countries  : {df_panel['CountryCode'].nunique()}")
print(f"  Year range : {df_panel['Year'].min()} - {df_panel['Year'].max()}")
print(f"  NaN Demand : {df_panel['Demand_MWh'].isna().sum()} country-years")
print(f"  NaN Peak   : {df_panel['Peak_MW'].isna().sum()} country-years")

df_panel.to_csv(ANNUAL_PANEL_PATH, index=False)
print(f"\nSaved → {ANNUAL_PANEL_PATH}")

# Sanity checks — validate against known expected ranges
for country, label in [("DE", "Germany"), ("IE", "Ireland"), ("GB", "Great Britain (excluded — should be empty)")]:
    subset = df_panel[df_panel["CountryCode"] == country].copy()
    subset["Demand_TWh"] = (subset["Demand_MWh"] / 1e6).round(1)
    print(f"\n{label} sanity check:")
    print(subset[["Year", "Demand_TWh", "Peak_MW", "completeness_pct"]].to_string(index=False))


# =============================================================================
# SECTION 2: GB DATA FROM NESO API
# National Grid ESO provides half-hourly demand (TSD column).
# Aggregated to annual Demand_MWh (sum * 0.5) and Peak_MW (max).
# =============================================================================

print("\n=== SECTION 2: Downloading GB data (NESO) ===")

results = []

for year, url in sorted(NESO_URLS.items()):
    print(f"  {year} ...", end=" ", flush=True)
    r  = requests.get(url, timeout=60)
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = df.columns.str.strip()

    # TSD is half-hourly MW; multiply by 0.5 to convert periods to MWh
    demand_mwh   = (df["TSD"] * 0.5).sum()
    peak_mw      = df["TSD"].max()
    periods      = df["TSD"].count()
    expected     = 366 * 48 if calendar.isleap(year) else 365 * 48
    completeness = round(periods / expected * 100, 1)

    results.append({
        "CountryCode"     : "GB",
        "Year"            : year,
        "Demand_MWh"      : round(demand_mwh, 0),
        "Peak_MW"         : round(peak_mw, 1),
        "completeness_pct": completeness,
        "source"          : "National Grid ESO (TSD)",
    })
    print(f"Demand={demand_mwh/1e6:.1f} TWh  Peak={peak_mw:.0f} MW  Complete={completeness}%")
    del df

# Load 2025 from local file if available
local_2025 = os.path.join(UK_DIR, "demanddata_2025.csv")
if os.path.exists(local_2025):
    print("  2025 (local file) ...", end=" ", flush=True)
    df25 = pd.read_csv(local_2025)
    df25.columns = df25.columns.str.strip()
    demand_mwh   = (df25["TSD"] * 0.5).sum()
    peak_mw      = df25["TSD"].max()
    periods      = df25["TSD"].count()
    completeness = round(periods / (365 * 48) * 100, 1)  # 2025 is not a leap year
    results.append({
        "CountryCode"     : "GB",
        "Year"            : 2025,
        "Demand_MWh"      : round(demand_mwh, 0),
        "Peak_MW"         : round(peak_mw, 1),
        "completeness_pct": completeness,
        "source"          : "National Grid ESO (TSD)",
    })
    print(f"Demand={demand_mwh/1e6:.1f} TWh  Peak={peak_mw:.0f} MW  Complete={completeness}%")

gb_panel = pd.DataFrame(results).sort_values("Year").reset_index(drop=True)
gb_panel["Demand_TWh"] = (gb_panel["Demand_MWh"] / 1e6).round(1)

print("\nGB Annual Panel:")
print(gb_panel[["Year", "Demand_TWh", "Peak_MW", "completeness_pct"]].to_string(index=False))

gb_panel.to_csv(GB_PANEL_PATH, index=False)
print(f"\nSaved → {GB_PANEL_PATH}")


# =============================================================================
# SECTION 3: MERGE ALL SOURCES INTO FINAL PANEL
# Combines ENTSO-E panel (35 countries excl. GB/CY) with NESO GB panel.
# Adds country names and filters to study period/countries.
# =============================================================================

print("\n=== SECTION 3: Merging all sources ===")

entso = pd.read_csv(ANNUAL_PANEL_PATH)
entso["Year"]   = entso["Year"].astype(int)
entso["source"] = "ENTSO-E"
entso = entso[["CountryCode", "Year", "Demand_MWh", "Peak_MW", "source"]]

gb = pd.read_csv(GB_PANEL_PATH)
gb["Year"] = gb["Year"].astype(int)
gb = gb[["CountryCode", "Year", "Demand_MWh", "Peak_MW", "source"]]

panel = pd.concat([entso, gb], ignore_index=True)
panel = panel.sort_values(["CountryCode", "Year"]).reset_index(drop=True)

print(f"  Shape      : {panel.shape}")
print(f"  Countries  : {panel['CountryCode'].nunique()} — {sorted(panel['CountryCode'].unique())}")
print(f"  Year range : {panel['Year'].min()} - {panel['Year'].max()}")
print(f"  NaN Demand : {panel['Demand_MWh'].isna().sum()}")
print(f"  NaN Peak   : {panel['Peak_MW'].isna().sum()}")

panel.to_csv(FINAL_PANEL_PATH, index=False)
print(f"\nSaved → {FINAL_PANEL_PATH}")

# Filter to study period and 21 target countries
panel = pd.read_csv(FINAL_PANEL_PATH)
panel["Year"] = panel["Year"].astype(int)
panel = panel[panel["Year"].between(2010, 2024) & panel["CountryCode"].isin(TARGET_21)].copy()

# Add country names
panel.insert(1, "CountryName", panel["CountryCode"].map(COUNTRY_NAMES))

print(f"\nStudy panel: {panel.shape}")
print(f"Countries ({panel['CountryCode'].nunique()}): {sorted(panel['CountryCode'].unique())}")
print(f"Unmapped codes: {panel[panel['CountryName'].isna()]['CountryCode'].unique()}")

panel.to_csv(STUDY_PANEL_PATH, index=False)
print(f"Saved → {STUDY_PANEL_PATH}")


# =============================================================================
# SECTION 4: VALIDATION PLOTS — DEMAND AND PEAK LOAD
# =============================================================================

print("\n=== SECTION 4: Validation plots ===")

df = pd.read_csv(STUDY_PANEL_PATH)
df["Year"]       = df["Year"].astype(int)
df["Demand_TWh"] = df["Demand_MWh"] / 1e6

PLOT_COUNTRIES = {
    "GB": "UK", "IE": "Ireland", "IT": "Italy", "FR": "France",
    "PL": "Poland", "BE": "Belgium", "DE": "Germany", "ES": "Spain",
    "NL": "Netherlands", "NO": "Norway"
}

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))

for code, name in PLOT_COUNTRIES.items():
    data = df[df["CountryCode"] == code].sort_values("Year")
    ax1.plot(data["Year"], data["Demand_TWh"], marker="o", markersize=4, linewidth=1.5, label=name)
    ax2.plot(data["Year"], data["Peak_MW"],    marker="o", markersize=4, linewidth=1.5, label=name)

for ax, title, ylabel in [
    (ax1, "Annual Electricity Demand (2010-2024)", "TWh"),
    (ax2, "Annual Peak Load (2010-2024)", "MW")
]:
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.set_ylabel(ylabel)
    ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.set_xticks(range(2010, 2025))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

ax2.set_xlabel("Year")
plt.suptitle("Annual Electricity Demand & Peak Load (2010-2024)", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "demand_peak_2010_2024.png"), dpi=150, bbox_inches="tight")
plt.show()
print(f"Saved → demand_peak_2010_2024.png")


# =============================================================================
# SECTION 5: COMPUTE SYSTEM STRESS METRICS
# Hourly data is reloaded from source files and tightness is computed as:
#   tightness = hourly_load_MW / annual_peak_MW[country, year]
# Stress events: consecutive hours above threshold → counted and timed.
# Scarcity hours: total hours above threshold (simpler metric).
# Both are computed at three thresholds (p90, p95, p97.5) for robustness.
# =============================================================================

print("\n=== SECTION 5: Computing system stress metrics ===")

# Load annual peak lookup
peak_panel  = pd.read_csv(STUDY_PANEL_PATH)
peak_panel["Year"] = peak_panel["Year"].astype(int)
peak_lookup = peak_panel.set_index(["CountryCode", "Year"])["Peak_MW"].to_dict()
print(f"Peak lookup loaded: {len(peak_lookup)} country-years")

# ── Reload all raw hourly data ────────────────────────────────────────────────
all_frames = []

# Wide format 2010-2015
print("\nLoading wide format (2010-2015)...", end=" ", flush=True)
wide_path   = glob.glob(os.path.join(DEMAND_DIR, "*2006*2015*.xlsx"))[0]
df_wide_raw = pd.read_excel(wide_path, header=3, engine="openpyxl")
cols        = list(df_wide_raw.columns)
country_col = cols[0]
cov_col     = next((c for c in cols if str(c).lower().startswith("cov")), None)
hour_cols   = [c for c in cols if str(c).strip() in [str(h) for h in range(24)]]
if len(hour_cols) != 24:
    hour_cols = [c for c in cols if isinstance(c, (int, float)) and 0 <= c <= 23]

df_wide_raw = df_wide_raw[
    df_wide_raw[country_col].isin(TARGET_21) &
    df_wide_raw["Year"].between(2010, 2015)
].dropna(subset=["Year", country_col]).copy()
df_wide_raw["Year"]    = df_wide_raw["Year"].astype(int)
df_wide_raw[cov_col]   = pd.to_numeric(df_wide_raw[cov_col], errors="coerce").fillna(100)

rows = []
for h in hour_cols:
    vals = pd.to_numeric(df_wide_raw[h], errors="coerce")
    cov  = pd.to_numeric(df_wide_raw[cov_col], errors="coerce").fillna(100)
    for idx in df_wide_raw[vals.notna()].index:
        rows.append({
            "CountryCode": df_wide_raw.at[idx, country_col],
            "Year"       : df_wide_raw.at[idx, "Year"],
            "load_MW"    : vals[idx] * (100.0 / cov[idx])
        })

df_w = pd.DataFrame(rows)
all_frames.append(df_w)
print(f"done ({len(df_w):,} rows)")

# Long xlsx 2016-2018
print("Loading long xlsx (2016-2018)...", end=" ", flush=True)
for sheet, years in [("2015-2017", [2016, 2017]), ("2018-2019", [2018])]:
    df = pd.read_excel(consol, sheet_name=sheet, engine="openpyxl")
    df.columns = df.columns.str.strip()
    sc = next(c for c in df.columns if c.lower() == "value_scaleto100")
    df["Year"] = pd.to_datetime(
        df["DateUTC"].astype(str).str[:10], dayfirst=True, errors="coerce"
    ).dt.year
    df = df[df["Year"].isin(years) & df["CountryCode"].isin(TARGET_21)]
    all_frames.append(df[["CountryCode", "Year", sc]].rename(
        columns={sc: "load_MW"}).dropna(subset=["load_MW"]))
print("done")

# CSVs 2019-2024
print("Loading CSVs (2019-2024)...")
for f in sorted(glob.glob(os.path.join(DEMAND_DIR, "monthly_hourly_load_values_20*.csv"))):
    year = int(os.path.basename(f).replace("monthly_hourly_load_values_", "").replace(".csv", ""))
    if year not in TARGET_YEARS:
        continue
    print(f"  {year}...", end=" ", flush=True)
    df = pd.read_csv(f, sep=None, engine="python")
    df.columns = df.columns.str.strip()
    sc = next(c for c in df.columns if c.lower() == "value_scaleto100")
    df["Year"] = year
    df = df[df["CountryCode"].isin(TARGET_21)]
    all_frames.append(df[["CountryCode", "Year", sc]].rename(
        columns={sc: "load_MW"}).dropna(subset=["load_MW"]))
    print("done")

# GB from NESO (half-hourly → hourly via max per settlement period pair)
print("Loading GB (NESO)...")
for year, url in sorted(NESO_URLS.items()):
    if year not in TARGET_YEARS:
        continue
    local = os.path.join(UK_DIR, f"demanddata_{year}.csv")
    print(f"  {year}...", end=" ", flush=True)
    if os.path.exists(local):
        df_gb = pd.read_csv(local)
    else:
        r     = requests.get(url, timeout=60)
        df_gb = pd.read_csv(io.StringIO(r.text))

    df_gb.columns  = df_gb.columns.str.strip()
    df_gb["_date"] = pd.to_datetime(df_gb["SETTLEMENT_DATE"], dayfirst=True, errors="coerce")
    df_gb["_year"] = df_gb["_date"].dt.year
    # Convert half-hourly to hourly: settlement periods 1-2 → hour 0, 3-4 → hour 1, etc.
    df_gb["_hour"] = (df_gb["SETTLEMENT_PERIOD"] - 1) // 2
    df_gb["_day"]  = df_gb["_date"].dt.date

    hourly_gb = (
        df_gb.groupby(["_year", "_day", "_hour"])["TSD"]
        .max().reset_index()
        .rename(columns={"TSD": "load_MW", "_year": "Year"})
    )
    hourly_gb["CountryCode"] = "GB"
    all_frames.append(hourly_gb[["CountryCode", "Year", "load_MW"]].dropna())
    print(f"done ({len(hourly_gb):,} hourly rows)")
    del df_gb

df_all_hourly = pd.concat(all_frames, ignore_index=True)
df_all_hourly = df_all_hourly[df_all_hourly["Year"].isin(TARGET_YEARS)]

# ── Completeness check ────────────────────────────────────────────────────────
comp_rows = []
for country in sorted(df_all_hourly["CountryCode"].unique()):
    for year in TARGET_YEARS:
        n        = df_all_hourly[(df_all_hourly["CountryCode"] == country) &
                                  (df_all_hourly["Year"] == year)]["load_MW"].notna().sum()
        expected = expected_annual_hours(year)
        comp_rows.append({
            "CountryCode"     : country,
            "Year"            : year,
            "completeness_pct": round(n / expected * 100, 1)
        })
df_comp    = pd.DataFrame(comp_rows)
comp_lookup = df_comp.set_index(["CountryCode", "Year"])["completeness_pct"].to_dict()

pivot_comp = df_comp.pivot(index="CountryCode", columns="Year", values="completeness_pct")
print("\nData completeness by country-year (%):")
print(pivot_comp.to_string())

low = df_comp[df_comp["completeness_pct"] < 80]
print(f"\nLow completeness (<80%) country-years: {len(low)}")
print(low[["CountryCode", "Year", "completeness_pct"]].sort_values("completeness_pct").to_string(index=False))

# ── Compute stress events ─────────────────────────────────────────────────────
print("\nComputing stress events...")
stress_results = []

for country in sorted(df_all_hourly["CountryCode"].unique()):
    df_c = df_all_hourly[df_all_hourly["CountryCode"] == country]
    for year in TARGET_YEARS:
        df_cy = df_c[df_c["Year"] == year]["load_MW"].dropna()
        if len(df_cy) < 100:
            continue
        peak = peak_lookup.get((country, year), None)
        if peak is None or pd.isna(peak) or peak == 0:
            continue

        completeness = comp_lookup.get((country, year), 0)
        tightness    = df_cy / peak
        row = {"CountryCode": country, "Year": year, "completeness_pct": completeness}

        if completeness < 80:
            # Too much missing data — do not compute metrics
            for label in THRESHOLDS:
                row[f"stress_events_{label}"]   = float("nan")
                row[f"stress_duration_{label}"] = float("nan")
        else:
            for label, threshold in THRESHOLDS.items():
                n_events, avg_dur = compute_stress_metrics(tightness, threshold)
                row[f"stress_events_{label}"]   = n_events
                row[f"stress_duration_{label}"] = round(avg_dur, 2)

        stress_results.append(row)

df_stress = pd.DataFrame(stress_results)

# Validation pivot
print("\nStress events p95 pivot:")
pd.set_option("display.max_columns", None)
pd.set_option("display.width", 250)
print(df_stress.pivot(index="CountryCode", columns="Year", values="stress_events_p95").round(0).to_string())

df_stress.to_csv(STRESS_PANEL_PATH, index=False)
print(f"\nSaved → {STRESS_PANEL_PATH}")

# ── Compute scarcity hours (simpler metric: total hours above threshold) ───────
print("\nComputing scarcity hours...")
scarcity_results = []

for country in sorted(df_all_hourly["CountryCode"].unique()):
    df_c = df_all_hourly[df_all_hourly["CountryCode"] == country]
    for year in TARGET_YEARS:
        df_cy = df_c[df_c["Year"] == year]["load_MW"].dropna()
        if len(df_cy) < 100:
            continue
        peak = peak_lookup.get((country, year), None)
        if peak is None or pd.isna(peak) or peak == 0:
            continue

        completeness = comp_lookup.get((country, year), 0)
        tightness    = df_cy / peak
        row = {"CountryCode": country, "Year": year, "completeness_pct": completeness}

        if completeness < 80:
            for label in ["p90", "p95", "p975"]:
                row[f"scarcity_hours_{label}"] = float("nan")
        else:
            row["scarcity_hours_p90"]  = int((tightness > 0.90).sum())
            row["scarcity_hours_p95"]  = int((tightness > 0.95).sum())
            row["scarcity_hours_p975"] = int((tightness > 0.975).sum())

        scarcity_results.append(row)

df_scarcity = pd.DataFrame(scarcity_results)


# =============================================================================
# SECTION 6: INTERPOLATE MISSING YEARS
# 2018 is missing for all countries due to an ENTSO-E data format change.
# IE 2022 is missing due to a reporting gap.
# Both are filled by averaging adjacent years (year-1, year+1).
# Imputation flags are added for transparency in the regression analysis.
# =============================================================================

print("\n=== SECTION 6: Interpolating missing years ===")

stress_cols   = [c for c in df_stress.columns if c.startswith("stress_")]
scarcity_cols = ["scarcity_hours_p90", "scarcity_hours_p95", "scarcity_hours_p975"]

# Interpolate 2018 for all countries
for country in df_stress["CountryCode"].unique():
    interpolate_missing_year(df_stress, country, 2018, stress_cols)
for country in df_scarcity["CountryCode"].unique():
    interpolate_missing_year(df_scarcity, country, 2018, scarcity_cols)

# Interpolate IE 2022
interpolate_missing_year(df_stress,   "IE", 2022, stress_cols)
interpolate_missing_year(df_scarcity, "IE", 2022, scarcity_cols)

# Add imputation flags
df_stress["imputed_2018"]    = (df_stress["Year"] == 2018).astype(int)
df_stress["imputed_ie_2022"] = ((df_stress["CountryCode"] == "IE") & (df_stress["Year"] == 2022)).astype(int)
df_scarcity["imputed_2018"]    = (df_scarcity["Year"] == 2018).astype(int)
df_scarcity["imputed_ie_2022"] = ((df_scarcity["CountryCode"] == "IE") & (df_scarcity["Year"] == 2022)).astype(int)

df_stress.to_csv(STRESS_PANEL_PATH, index=False)
df_scarcity.to_csv(SCARCITY_PATH, index=False)
df_scarcity[["CountryCode", "Year", "scarcity_hours_p95", "imputed_2018", "imputed_ie_2022"]].to_csv(
    SCARCITY_95_PATH, index=False)
print(f"Saved → {STRESS_PANEL_PATH}")
print(f"Saved → {SCARCITY_PATH}")
print(f"Saved → {SCARCITY_95_PATH}")


# =============================================================================
# SECTION 7: BUILD FINAL SYSTEM STRESS PANEL
# Merges annual demand panel with stress events metrics into one output file.
# =============================================================================

print("\n=== SECTION 7: Building final system stress panel ===")

panel  = pd.read_csv(STUDY_PANEL_PATH)
stress = pd.read_csv(STRESS_PANEL_PATH)

panel["Year"]  = panel["Year"].astype(int)
stress["Year"] = stress["Year"].astype(int)

panel_cols = [
    "CountryCode", "CountryName", "Year",
    "Demand_MWh", "Peak_MW",
]
stress_keep = [
    "CountryCode", "Year",
    "stress_events_p90",  "stress_duration_p90",
    "stress_events_p95",  "stress_duration_p95",
    "stress_events_p975", "stress_duration_p975",
    "imputed_2018", "imputed_ie_2022"
]
stress_keep = [c for c in stress_keep if c in stress.columns]

final = panel[[c for c in panel_cols if c in panel.columns]].merge(
    stress[stress_keep], on=["CountryCode", "Year"], how="left"
).sort_values(["CountryCode", "Year"]).reset_index(drop=True)

print(f"  Shape      : {final.shape}")
print(f"  Countries  : {final['CountryCode'].nunique()}")
print(f"\nNaN summary:")
print(final.isna().sum()[final.isna().sum() > 0])

final.to_csv(SYSTEM_STRESS_PATH, index=False)
print(f"\nSaved → {SYSTEM_STRESS_PATH}")


# =============================================================================
# SECTION 8: VISUALISATIONS — STRESS EVENTS AND TREATED vs CONTROLS
# =============================================================================

print("\n=== SECTION 8: Stress event plots ===")

df_plot = pd.read_csv(STRESS_PANEL_PATH)
df_plot["Year"] = df_plot["Year"].astype(int)

all_countries = list(TREATMENT_YEARS.keys()) + CONTROLS

# ── Per-country stress events plot ───────────────────────────────────────────
fig, axes = plt.subplots(3, 7, figsize=(24, 12))
axes = axes.flatten()

for i, country in enumerate(all_countries):
    ax   = axes[i]
    data = df_plot[df_plot["CountryCode"] == country].sort_values("Year")
    color = "steelblue" if country in TREATMENT_YEARS else "gray"

    ax.plot(data["Year"], data["stress_events_p95"],
            marker="o", markersize=3, linewidth=1.5, color=color)
    ax.fill_between(data["Year"], data["stress_events_p90"], data["stress_events_p975"],
                    alpha=0.2, color=color)
    if country in TREATMENT_YEARS:
        ax.axvline(x=TREATMENT_YEARS[country], color="red", linestyle="--", linewidth=1, alpha=0.7)

    ax.set_title(f"{'★ ' if country in TREATMENT_YEARS else ''}{country}",
                 fontsize=9,
                 fontweight="bold" if country in TREATMENT_YEARS else "normal",
                 color="steelblue" if country in TREATMENT_YEARS else "black")
    ax.set_xticks([2010, 2015, 2020, 2024])
    ax.tick_params(labelsize=7)
    ax.grid(True, alpha=0.3)

for j in range(len(all_countries), len(axes)):
    axes[j].set_visible(False)

fig.suptitle("Annual Stress Events (p95) by Country\n★ = CRM treated | Red dashed = treatment year | Shaded = p90–p97.5",
             fontsize=12, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "stress_events_all_countries.png"), dpi=150, bbox_inches="tight")
plt.show()
print("Saved → stress_events_all_countries.png")

# ── Treated vs controls comparison ───────────────────────────────────────────
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(18, 12))

for country in CONTROLS:
    data = df_plot[df_plot["CountryCode"] == country].sort_values("Year")
    ax1.plot(data["Year"], data["stress_events_p95"], color="lightgray", linewidth=0.8, alpha=0.6, zorder=1)
    ax2.plot(data["Year"], data["stress_duration_p95"], color="lightgray", linewidth=0.8, alpha=0.6, zorder=1)

for country, color in TREATED_COLORS.items():
    data = df_plot[df_plot["CountryCode"] == country].sort_values("Year")
    ax1.plot(data["Year"], data["stress_events_p95"],
             color=color, linewidth=2.5, marker="o", markersize=5, label=country, zorder=3)
    ax2.plot(data["Year"], data["stress_duration_p95"],
             color=color, linewidth=2.5, marker="o", markersize=5, label=country, zorder=3)

for country, yr in TREATMENT_YEARS.items():
    ax1.axvline(x=yr, color=TREATED_COLORS[country], linestyle="--", linewidth=1, alpha=0.6)
    ax2.axvline(x=yr, color=TREATED_COLORS[country], linestyle="--", linewidth=1, alpha=0.6)

for ax, title, ylabel in [
    (ax1, "Annual Stress Events — p95 (2010–2024)", "Number of Events"),
    (ax2, "Average Stress Duration — p95 (2010–2024)", "Hours per Event")
]:
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.set_ylabel(ylabel)
    ax.set_xticks(range(2010, 2025))
    ax.tick_params(axis="x", rotation=45)
    ax.grid(True, alpha=0.3)
    ax.legend(title="Treated", bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=9)

ax2.set_xlabel("Year")
plt.suptitle("Stress Events & Duration — Treated (colored) vs Controls (gray)\nDashed = CRM treatment year",
             fontsize=13, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "stress_events_p95_all_countries.png"), dpi=150, bbox_inches="tight")
plt.show()
print("Saved → stress_events_p95_all_countries.png")

print("\n✅ All sections complete.")
