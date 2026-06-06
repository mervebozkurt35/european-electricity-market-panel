# -*- coding: utf-8 -*-
"""
ember_data_process.py
=====================
Processes Ember's Europe Yearly Electricity Data for use in a panel dataset
analysing capacity auction mechanisms and their impact on investment signals
in EU electricity markets (Master's thesis, Barcelona School of Economics, 2026).

Inputs:
    - Ember Yearly Electricity Data (.xlsx)
    - Existing panel dataset (.xlsx)

Outputs:
    - ember_net_imports_panel.csv       : Net imports by country-year (TWh)
    - ember_generation_panel.csv        : Generation by country-year-fuel (TWh)
    - ember_generation_by_fueltype.csv  : Generation aggregated to 7 fuel categories
    - Updated panel dataset (.xlsx)     : Panel merged with generation variables
    - gen_vs_cap_POL_BEL_FRA.png        : Validation plot (generation vs capacity)

Usage:
    Update DATA_PATH, OUTPUT_DIR, and PANEL_PATH to point to your local files,
    then run: python ember_data_process.py
"""

import os
import pandas as pd
import matplotlib.pyplot as plt


# =============================================================================
# CONFIG — update these paths before running
# =============================================================================

DATA_PATH  = "data/Ember_Yearly_Data.xlsx"
OUTPUT_DIR = "output/"
PANEL_PATH = "data/panel_long_export.xlsx"
NEW_PANEL_OUTPUT = "output/panel_long_updated.xlsx"

os.makedirs(OUTPUT_DIR, exist_ok=True)


# =============================================================================
# COUNTRY CODE MAPPING
# 21 European countries included in the study (ISO2 <-> ISO3)
# =============================================================================

CODE_MAP = {
    'AT': 'AUT', 'BE': 'BEL', 'CZ': 'CZE', 'DK': 'DNK', 'FI': 'FIN',
    'FR': 'FRA', 'DE': 'DEU', 'GR': 'GRC', 'HU': 'HUN', 'IE': 'IRL',
    'IT': 'ITA', 'NL': 'NLD', 'NO': 'NOR', 'PL': 'POL', 'PT': 'PRT',
    'RO': 'ROU', 'SK': 'SVK', 'SI': 'SVN', 'ES': 'ESP', 'SE': 'SWE',
    'GB': 'GBR'
}
ISO3_TO_CODE = {v: k for k, v in CODE_MAP.items()}
our_iso3 = list(CODE_MAP.values())

# Country name to ISO2 mapping (used when merging with panel)
NAME_TO_CODE = {
    'Austria': 'AT', 'Belgium': 'BE', 'Czech Republic': 'CZ', 'Denmark': 'DK',
    'Finland': 'FI', 'France': 'FR', 'Germany': 'DE', 'Greece': 'GR',
    'Hungary': 'HU', 'Ireland': 'IE', 'Italy': 'IT', 'Netherlands': 'NL',
    'Norway': 'NO', 'Poland': 'PL', 'Portugal': 'PT', 'Romania': 'RO',
    'Slovakia': 'SK', 'Slovenia': 'SI', 'Spain': 'ES', 'Sweden': 'SE',
    'United Kingdom': 'GB'
}


# =============================================================================
# LOAD DATA
# =============================================================================

print("Loading Ember data...")
ember = pd.read_excel(DATA_PATH)

# Quick structure check — useful for verifying available variables on new data versions
print("\nAvailable variables:")
print(ember[['Category', 'Subcategory', 'Variable', 'Unit']].drop_duplicates().sort_values('Category').to_string())


# =============================================================================
# SECTION 1: NET IMPORTS
# Extract net electricity imports by country-year (TWh), filtered to 21 countries
# and 2010-2024 study period.
# =============================================================================

print("\n--- Extracting Net Imports ---")

net_imports = ember[
    (ember["Category"] == "Electricity imports") &
    (ember["Variable"] == "Net imports") &
    (ember["Unit"] == "TWh") &
    (ember["ISO 3 code"].isin(our_iso3))
][["ISO 3 code", "Year", "Value"]].copy()

net_imports["CountryCode"] = net_imports["ISO 3 code"].map(ISO3_TO_CODE)
net_imports = (
    net_imports[["CountryCode", "Year", "Value"]]
    .rename(columns={"Value": "NetImports_TWh"})
    .sort_values(["CountryCode", "Year"])
    .reset_index(drop=True)
)

# Validation checks
print(f"Shape: {net_imports.shape}")
print(f"Countries: {net_imports['CountryCode'].nunique()}")
print(f"Year range: {net_imports['Year'].min()} – {net_imports['Year'].max()}")
print(f"Missing countries: {set(CODE_MAP.keys()) - set(net_imports['CountryCode'].unique())}")
print(f"NaN check:\n{net_imports.isnull().sum()}")
print(f"\nSample (GB):")
print(net_imports[net_imports["CountryCode"] == "GB"].tail(10))

# Filter to study period
net_imports = net_imports[(net_imports["Year"] >= 2010) & (net_imports["Year"] <= 2024)]

# Final validation
print(f"\nAfter time filter — Shape: {net_imports.shape}, Years: {net_imports['Year'].min()}–{net_imports['Year'].max()}")
print(f"Missing countries: {set(CODE_MAP.keys()) - set(net_imports['CountryCode'].unique())}")

# Save
output_path = os.path.join(OUTPUT_DIR, "ember_net_imports_panel.csv")
net_imports.to_csv(output_path, index=False)
print(f"Saved → {output_path}")


# =============================================================================
# SECTION 2: ELECTRICITY GENERATION BY FUEL TYPE
# Extract generation by country-year-fuel (TWh).
# Note: starts from 2009 (not 2010) to allow first-difference calculation
# (gen_change_TWh) from 2010 onwards in the panel.
# =============================================================================

print("\n--- Extracting Generation by Fuel ---")

generation = ember[
    (ember["Category"] == "Electricity generation") &
    (ember["Subcategory"] == "Fuel") &
    (ember["Unit"] == "TWh") &
    (ember["ISO 3 code"].isin(our_iso3))
][["ISO 3 code", "Variable", "Year", "Value"]].copy()

generation["CountryCode"] = generation["ISO 3 code"].map(ISO3_TO_CODE)
generation = (
    generation[["CountryCode", "Variable", "Year", "Value"]]
    .rename(columns={"Value": "generation_TWh"})
    .sort_values(["CountryCode", "Year"])
    .reset_index(drop=True)
)

# Validation
print(f"Shape: {generation.shape}")
print(f"Countries: {generation['CountryCode'].nunique()}")
print(f"Year range: {generation['Year'].min()} – {generation['Year'].max()}")
print(f"Missing countries: {set(CODE_MAP.keys()) - set(generation['CountryCode'].unique())}")
print(f"NaN check:\n{generation.isnull().sum()}")

# Start from 2009 to allow first-difference calculation from 2010 onwards
generation = generation[(generation["Year"] >= 2009) & (generation["Year"] <= 2024)]

# Final validation
print(f"\nAfter time filter — Shape: {generation.shape}, Years: {generation['Year'].min()}–{generation['Year'].max()}")

# Save
output_path = os.path.join(OUTPUT_DIR, "ember_generation_panel.csv")
generation.to_csv(output_path, index=False)
print(f"Saved → {output_path}")


# =============================================================================
# SECTION 3: AGGREGATE GENERATION TO 7 FUEL CATEGORIES
# Maps Ember's granular fuel variables to 7 panel fuel_type categories.
# "Other Fuels" has no Ember equivalent and will be NaN after merge.
# =============================================================================

print("\n--- Aggregating to Fuel Type Categories ---")

FUEL_TYPE_MAP = {
    "Fossil Fuels": ["Gas", "Lignite", "Hard coal", "Other fossil"],
    "Hydro":        ["Hydro"],
    "Nuclear":      ["Nuclear"],
    "Solar":        ["Solar"],
    "Wind":         ["Onshore wind", "Offshore wind"],
    "Other Res":    ["Other renewables", "Bioenergy"],
    "Other Fuels":  []  # no Ember equivalent — will be NaN after merge
}

# Sanity check: verify all expected Ember variables are present in data
available_vars = generation["Variable"].unique()
for fuel_type, var_list in FUEL_TYPE_MAP.items():
    missing = [v for v in var_list if v not in available_vars]
    if missing:
        print(f"⚠️  {fuel_type} — not found in data: {missing}")
    else:
        print(f"✅ {fuel_type}: OK")


def aggregate_generation(df, var_list, label):
    """Aggregate generation TWh across a list of Ember variables into one fuel_type label."""
    if not var_list:
        return pd.DataFrame(columns=["CountryCode", "Year", "fuel_type", "gen_TWh"])
    return (
        df[df["Variable"].isin(var_list)]
        .groupby(["CountryCode", "Year"], as_index=False)["generation_TWh"]
        .sum()
        .assign(fuel_type=label)
        .rename(columns={"generation_TWh": "gen_TWh"})
    )


gen_by_fueltype = pd.concat(
    [aggregate_generation(generation, vars_, ft) for ft, vars_ in FUEL_TYPE_MAP.items()],
    ignore_index=True
).sort_values(["CountryCode", "Year", "fuel_type"]).reset_index(drop=True)

# Validation
print(f"\nShape: {gen_by_fueltype.shape}")  # expect 21 countries * 15 years * 6 fuel types = 1890 (excl. Other Fuels)
print(f"\nSample (GB, 2022):")
print(gen_by_fueltype[
    (gen_by_fueltype["CountryCode"] == "GB") &
    (gen_by_fueltype["Year"] == 2022)
])

# Save
output_path = os.path.join(OUTPUT_DIR, "ember_generation_by_fueltype.csv")
gen_by_fueltype.to_csv(output_path, index=False)
print(f"Saved → {output_path}")


# =============================================================================
# SECTION 4: MERGE GENERATION INTO PANEL DATASET
# Merges gen_by_fueltype into the existing panel and computes yearly change
# in generation per fuel type (gen_change_TWh) for use as a regression variable.
# =============================================================================

print("\n--- Merging into Panel Dataset ---")

panel = pd.read_excel(PANEL_PATH)
print(f"Panel shape (before merge): {panel.shape}")

# Map country names to ISO2 codes for merge key
panel["CountryCode"] = panel["country"].map(NAME_TO_CODE)

panel = panel.merge(
    gen_by_fueltype.rename(columns={"Year": "year"}),
    on=["CountryCode", "year", "fuel_type"],
    how="left"
).drop(columns=["CountryCode"])

print(f"Panel shape (after merge): {panel.shape}")

# Fill NaN generation with 0 (fuel types not present in a country-year)
panel["gen_TWh"] = panel["gen_TWh"].fillna(0)

# Compute year-on-year change in generation by country and fuel type
panel = panel.sort_values(["country", "fuel_type", "year"]).reset_index(drop=True)
panel["gen_change_TWh"] = panel.groupby(["country", "fuel_type"])["gen_TWh"].diff().fillna(0)

# Validation
print(f"\nFinal panel shape: {panel.shape}")
print(f"NaN check:\n{panel[['gen_TWh', 'gen_change_TWh']].isnull().sum()}")
print(f"\nSample (Austria, 2022):")
print(panel[(panel["country"] == "Austria") & (panel["year"] == 2022)]
      [["country", "year", "fuel_type", "gen_TWh", "gen_change_TWh"]])

# Save updated panel
panel.to_excel(NEW_PANEL_OUTPUT, index=False)
print(f"Saved → {NEW_PANEL_OUTPUT}")


# =============================================================================
# SECTION 5: VALIDATION PLOT
# Visual cross-check: generation (TWh) vs installed capacity (GW) by fuel type
# for selected countries. Used to verify data merge and detect anomalies.
# =============================================================================

print("\n--- Generating Validation Plot ---")

countries  = ["Poland", "Belgium", "France"]
fuel_types = panel["fuel_type"].unique()

fig, axes = plt.subplots(len(countries), len(fuel_types), figsize=(28, 12))
fig.suptitle("Generation (TWh) vs Installed Capacity (GW) by Fuel Type", fontsize=14)

for i, country in enumerate(countries):
    for j, ft in enumerate(fuel_types):
        ax  = axes[i, j]
        df  = panel[(panel["country"] == country) & (panel["fuel_type"] == ft)].sort_values("year")

        ax.bar(df["year"], df["gen_TWh"], color="steelblue", alpha=0.7, label="gen_TWh")
        ax2 = ax.twinx()
        ax2.plot(df["year"], df["TOTAL_cap"] / 1000, color="tomato", linestyle="--",
                 linewidth=1.5, label="TOTAL_cap (GW)")

        ax.set_title(f"{country} — {ft}", fontsize=8)
        ax.tick_params(labelsize=7)
        ax2.tick_params(labelsize=7)
        if j == 0:
            ax.set_ylabel("TWh", fontsize=7)
        if j == len(fuel_types) - 1:
            ax2.set_ylabel("GW", fontsize=7)

plt.tight_layout()
plot_path = os.path.join(OUTPUT_DIR, "gen_vs_cap_POL_BEL_FRA.png")
plt.savefig(plot_path, dpi=150, bbox_inches="tight")
plt.show()
print(f"Saved → {plot_path}")
