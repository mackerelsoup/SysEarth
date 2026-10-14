# 1. Which layers decide an EUDR plot outcome

## 1.1 What the regulation actually asks

Regulation (EU) 2023/1115 (EUDR), as amended by Reg. (EU) 2024/3234 and Reg. (EU) 2025/2650,
applies from **30 December 2026** for large and medium operators and from **30 June 2027** for
micro and small operators (those already under the EU Timber Regulation follow the December 2026
date). The Commission's simplification review of May 2026 confirmed these dates and did not
reopen the core definitions used below.

| Requirement (Art. 2, 3, 9) | What it means for the data |
|---|---|
| **Cut-off date 31 Dec 2020** (Art. 2(13)) | Only the *state at 2020* and *change after 2020* matter. Any annual history before 2020 is only useful to establish the 2020 state. |
| **Forest** = land > 0.5 ha, trees > 5 m, canopy cover > 10 %, or trees able to reach those thresholds in situ; **excluding land predominantly under agricultural use** (Art. 2(4), FAO FRA definition) | Needs a 2020 tree-cover state, a canopy threshold (10 %), a height criterion (5 m) and a way to remove **agricultural tree plantations** (oil palm, rubber, orchards are agricultural use, not forest). |
| **Deforestation** = conversion of forest to agricultural use after the cut-off (Art. 2(3)); **forest degradation** applies to wood products only | For rubber and palm, what counts is forest at 2020 *lost after 2020*. Rotation felling of a 2020 rubber plantation is **not** deforestation. |
| **Geolocation** of every plot; polygons for plots > 4 ha (Art. 9(1)(d)) | Queries are plot polygons (or points ≤ 4 ha). Decisions are per plot, so the encoding must answer polygon-zonal questions exactly at the pixel scale. |
| **Legality** (Art. 2(40)): land-use rights, environmental protection law, etc. | Protected and restricted-use areas are a spatial proxy for legality → CSV `d1`, `d2`. They are categorical overlays, best kept as exact vectors. |

Other regimes differ only in parameters: RSPO uses a November 2005 cut-off, the Amazon Soy
Moratorium 2008, and Rainforest Alliance 2014. Canopy thresholds also vary (10 % FAO, 30 % is
the GFW default). The encoding should therefore keep the **loss year** and the **canopy level at
the thresholds in use**, rather than baking in one regime.

## 1.2 Which report outputs are spatial

Of the 35 columns in `tests/data/17plots-valid.csv`, only three are reproducible from layers:

| Column | Output | Spatial rule |
|---|---|---|
| `c2_deforestation_spatial_analysis_result` | indication yes/no | ≥ `min_px` pixels in the plot that are forest at 2020 **and** lost in 2021+ |
| `d1_strict_protected_area_spatial_analysis_result` | overlap yes/no | plot polygon ∩ strict PA polygon has area > 0 |
| `d2_restricted_use_area_spatial_analysis_result` | overlap yes/no | plot polygon ∩ restricted-use polygon has area > 0 |

`c3`–`c5` are field-verification results, `d3`–`j5` are tenure, labour, tax and quantity fields.
None of them can be derived from earth-observation layers, so they are out of scope.

**The existing report's `c2` is not reproducible.** `c1` says "Hansen Tree Cover Loss 2021-2023".
With every pixel rule tried (pixel centre, all-touched) and every window (2021–23, 2021–25), the
flagged set does not match. P-0008 and P-0010 are flagged with **zero** post-2020 loss pixels,
while P-0001 has 38 pixels of 2022 loss and is not flagged. Together with templated reference
links (`hyperlink-RG1-P-0002` for P-0003), quantities that step by fixed amounts, and identical
dates, this suggests the CSV is a mock-up. **Decision fidelity is therefore measured against an
explicit rule evaluated on the original layers**, which is what "against the original layers"
asks for. The CSV comparison is reported separately for information.

## 1.3 Layer-by-layer verdict

Sizes are for the 1597 × 1578 px clip (2.52 M pixels at 0.00025°, about 27.5 m at 8.5° N).

| Layer | Source | Decides? | Verdict | Encoded as | Regulatory reason |
|---|---|---|---|---|---|
| `lossyear` (0–25) | Hansen GFC v1.13 | **Yes** | **Keep, losslessly** (5 bits) | bits 0–4 | Gives "lost after cut-off". Keeping the year instead of a single after-2020 bit costs 4 bits and lets any cut-off (2005, 2008, 2014, 2020) or end year be queried. |
| `treecover2000` (0–100 %) | Hansen | **Yes** (canopy > 10 %) | **Simplify** to 8 classes at the thresholds 0/10/15/20/25/30/50 | bits 5–7 | Only "> threshold" is used, and every GFW/FAO threshold stays exact. The exact percentage (mean abs. error 10 points) is not needed for any decision. |
| `datamask` (0/1/2) | Hansen | Masking only | **Simplify** to 1 bit "not land" | bit 13 | Water is never forest and never a plot. |
| `gain` 2000–2012 | Hansen | No | **Drop** | – | Gain predates the cut-off and is a static 2000–12 flag. A 2020 baseline captures regrowth better. |
| `first` / `last` composites | Hansen | No | **Drop** (never downloaded) | – | Reflectance imagery, not a decision input. 4 bands × 2 epochs would dominate storage. |
| 2020 forest baseline | GLAD GLCLU 2020 v2, classes 27–48 / 127–148 (tree cover ≥ 5 m). JRC GFC2020 is the EU reference but its host is blocked from this sandbox, and it drops in unchanged. | **Yes** | **Merge** into 1 bit | bit 8 | Replaces "Hansen 2000 canopy minus loss until 2020", which ignores 2000–2020 regrowth and plantations. JRC GFC2020 is the EU's reference map. |
| Tree height ≥ 5 m (2020) | GLAD TCH 2020 (canopy height in metres) | Yes (forest definition) | **Merge** into 1 bit | bit 9 | The 5 m criterion. Optional in the default rule because the baseline already applies it. |
| Tree-crop plantation | GLAD Commodities **2015**, class 2 (other plantation, incl. rubber) + class 4 (oil palm) | **Yes** | **Merge** into 1 bit | bit 10 | "Agricultural use" is excluded from forest. **Must be a pre-2020 map**: a post-2020 plantation map would hide exactly the forest-to-plantation conversion EUDR targets. |
| Strict protected areas | OpenStreetMap via Overture Maps 2026-09-23.1 (WDPA is blocked here): Khlong Phraya WS (Ia), Than Bok Khorani NP and Khao Phanom Bencha NP (II) | **Yes** (`d1`) | **Keep as vector** + 1 conservative bit | bit 11 + GeoJSON | The decision is a polygon intersection. The bit is burned *all-touched*, so it never misses an overlap, and hits are confirmed on the vector. |
| Restricted-use areas | none mapped in OSM for the clip area → 6 **synthetic** polygons (`tests/data/synthetic_restricted_areas.geojson`) only to test the `d2` path | **Yes** (`d2`) | as above | bit 12 + GeoJSON | as above |
| GLAD / DIST alerts | GLAD | Post-2025 monitoring | **Optional extension** | spare bits 14–15 | Annual Hansen ends at 2025. Alerts would extend the window weekly. |

## 1.4 The false positives in the sample

Five of the 17 plots were flagged and later cleared on site. In this landscape (Surat Thani:
rubber and oil palm), **46 % of all pixels in the clip show Hansen loss at some point in
2001–2025**. This is the rotation cycle of tree crops, not deforestation. A rule that uses
only Hansen loss (`legacy`) or Hansen canopy in 2000 (`eudr_hansen_only`) flags plantation
replanting. The two extra bits (forest baseline at 2020, pre-cut-off plantation mask) separate
the cases:

| Rule | Pixels counted as forest at the cut-off | Pixels deforested after 2020 | Synthetic + real plots with a `c2` indication (of 1,005) | Real plots flagged (of 17) |
|---|---|---|---|---|
| Legacy: any loss 2021–2023 | – | 6.8 % | 407 | 3 (P-0001, P-0016, P-0018) |
| EUDR, Hansen only | 53.6 % | 10.2 % | 548 | 7 |
| **EUDR with 2020 baseline and 2015 plantation mask** | **14.7 %** | **0.92 %** | **238** | **3 (P-0001: 10 px, P-0014: 2 px, P-0016: 1 px)** |

In the clip area, 73.7 % of pixels are tree-crop plantation in 2015, which is why the
plantation bit decides most outcomes. All five plots flagged in the CSV (and later cleared on
site) are clean under the full EUDR rule, matching the field verification. The three remaining
indications are 1–10 pixels (0.08–0.8 ha), which is what a `min_px` / minimum-area parameter is for.

**Unexpected legality finding.** In the OSM boundary data, P-0005 and P-0006 lie **entirely inside
Khlong Phraya Wildlife Sanctuary** (IUCN Ia), but the CSV reports `d1 = No overlap`. The OSM
boundary is tagged `fixme=approximately`, and the CSV probably used WDPA, so this has to be checked
against WDPA or the DNP boundary before any conclusion. It is consistent with the map, though: 22 % of
strict-PA pixels are tree-crop plantation already in 2015 (encroachment), and the western edge of
the sanctuary is where Hansen-only rules flag most loss.
