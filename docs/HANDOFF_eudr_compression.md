# SysEarth handoff: EUDR geospatial compression challenge

Working notes carried between sessions. Update this file as decisions land.

## Task (from the problem prompt)
Show how SystemEarth can encode multiple geospatial layers across time and space in a compact, query-efficient form while preserving plot-level compliance decisions.

Deliverables:
1. Analysis of which layers and attributes decide the outcome, and which can be simplified, summarised or dropped. Ground it in the regulation (forest definition, cut-off date).
2. Encoding strategy combining layers and time steps.
3. Working prototype that encodes a sample for one region and queries it.
4. Evaluation against the original layers:
   - storage reduction
   - query speed and extra storage
   - decision fidelity (% of plots where every compliance-relevant output matches)
   - pixel fidelity
5. Bonus: infrastructure and processing cost estimate.

## Decisions so far
- **Use case:** EUDR deforestation-free status for farm plots. Cut-off is 31 Dec 2020. Forest per FAO: >0.5 ha, canopy >10%, trees >5 m, plantations excluded. Polygons are required above 4 ha. Check the current EUDR application dates before citing any.
- **Second regime (optional):** show the same encoding answering another regime (e.g. RSPO 2005 cut-off) by changing parameters only.
- **Decision-fidelity scope:** the outputs reproducible from layers are CSV columns `c2` (deforestation indication), `d1` (strict protected area overlap) and `d2` (restricted-use area overlap). Other CSV sections (labour, tax, FPIC, quantities) are non-spatial and out of scope.
- **Baseline for comparison:** compare against Cloud-Optimized GeoTIFF reads, not plain GeoTIFF.

## Sample data
Originally committed on `claude/intelligent-turing-5sn6lg`; also present on `claude/gracious-goldberg-68rm95`.
- `tests/data/17plots-valid.geojson`: 17 polygon plots, 80.5 ha total, WGS84. Fields: ProducerName, ProducerCountry, ProductionPlace (plot ID), Area (ha).
- `tests/data/17plots-valid.csv`: the existing due-diligence report. 17 rows, 35 columns, joins on `a3_plot_id`.
- `tests/data/clip_area_preview.geojson`: clip square plus plots, for viewing on geojson.io.
- `scripts/clip_rasters.py`: clips GeoTIFFs to a square around the GeoJSON vertices plus a 5 km buffer (tested on synthetic tiles). Needs `rasterio`; writes `clip_bbox.json` and `clip_area.geojson`.
- `data/clipped/` (added by the user in commit d919524): Hansen GFC-2025-v1.13 tile 10N_090E clipped to the area. Files: datamask, gain, lossyear, treecover2000 (~2.1 MB total). `first`/`last` not included and not needed.

## Key facts about the sample
- **Location:** southern Thailand, Surat Thani province. CSV says district "Chai Buri" (unverified). Plots span lon 98.77–99.08, lat 8.43–8.58, centre ≈ 8.51°N, 98.94°E, in four clusters.
- **Hansen tile:** all plots in **10N_090E** (named by top-left corner; lat 0–10°N, lon 90–100°E).
- **Clip area:** lon 98.727–99.126, lat 8.309–8.704, a 43.9 km square (~1,600 × 1,600 px at 30 m). Leeway 5 km E/W, ~13.6 km N/S.
- **Plots over 4 ha:** P-0001, P-0003, P-0005, P-0006.
- **Deforestation flags:** 5/17 flagged `b. Indication of deforestation` (P-0003, P-0005, P-0008, P-0010, P-0013). All five end `Deforestation-free` after on-site verification → Hansen check gave false positives. Likely cause: Hansen loss fires on harvested/replanted tree crops (rubber, oil palm) → argument for a forest baseline and a plantation mask.
- **Overlap results:** `d1` and `d2` are 17/17 "no overlap" → no positive cases; synthetic plots needed to test them.
- **CSV data quality:** partly templated.
  - Reference links point to other plots' IDs (e.g. `hyperlink-RG1-P-0002`, a plot not in the files).
  - Quantity columns step by fixed increments and don't track plot area.
  - All dates identical.
- **Current rule (unconfirmed):** `c1` says "Hansen Tree Cover Loss 2021-2023". Seems to be any loss in that window, no canopy threshold or forest baseline.
- **Layer version note:** clipped tiles are v1.13 (GFC-2025), not v1.11, so loss data may extend past 2023. Check which years `lossyear` covers before fixing the post-cut-off window.
- **Polygon-to-pixel rule:** sample ≈ 900 pixels total; smallest plot (0.53 ha) ≈ 6 pixels. The overlap rule (any pixel / pixel centre / minimum fraction) must be fixed once and applied identically to original and compressed paths.

## Layer analysis (draft, to finish)
- **Needed:** `treecover2000`, `lossyear`, a 2020 forest baseline (JRC GFC2020 recommended), protected areas (WDPA Thailand), ideally a plantation mask.
- **Droppable or mergeable:**
  - raw canopy %: replace with a "canopy >10%" bit.
  - year-level loss: collapse to before / after cut-off.
  - `first`, `last` imagery: drop.
  - `gain`: probably drop.
  - `datamask`: keep only to mask water.
- **Proposed encoding:** one byte per pixel of bit-packed flags (forest at 2020, lost before 2021, lost after 2020, in protected/restricted area, spare bits), stored as a single-band COG or Zarr. Keep plots and protected areas as vectors.
- **GLAD alerts:** optional extension for post-Hansen loss; coverage of Thai plantation landscapes unverified.

## Environment notes
- Sandbox outbound HTTPS goes through a proxy that blocks some hosts (Nominatim refused). Whether Hansen, WDPA and JRC downloads work from the sandbox is **untested**.
- No `rasterio` by default; install into a venv under the session scratchpad (ephemeral).
- GitHub push failed once with 403 and succeeded on retry. Do not open a PR unless asked.
- Commit trailers: use whatever the current session's harness specifies.

## Status (session 2, 2026-10-14)
All five deliverables are drafted. See **docs/REPORT.md** (main report) and docs/01_layer_analysis.md.
- `lossyear` covers 2001-2025 (v1.13). The EUDR window is 2021-2025; the legacy rule is 2021-2023.
- The CSV `c2` cannot be reproduced by any Hansen rule, so fidelity is measured against the original layers.
- Layers come from GLAD GCS, ESA S3 and Overture S3; JRC, WDPA and Zenodo are blocked. See data/layers/PROVENANCE.md and scripts/fetch_layers.py.
- Config: eudr/config.json. Forest 2020 = GLCLU 2020 classes 27-48 and 127-148. Plantation = GLAD Commodities **2015** classes 2 and 4. TCH >= 5 m. PA = OSM via Overture (3 strict). Restricted areas are synthetic.
- Results: decision fidelity 100% on 1,005 plots x 4 regimes. The cube is 3.6x smaller than the needed originals and the decision byte 16.5x. Over HTTP at 40 ms latency: 72 -> 19 ms per plot.
- Findings: P-0005 and P-0006 lie inside Khlong Phraya WS (OSM), but the CSV says no overlap. 22% of strict-PA pixels are 2015 plantation. Under EUDR, the 5 CSV-flagged plots are clean, and P-0001, P-0014 and P-0016 have 1-10 deforested px.
- The venv must be recreated per session: `pip install rasterio shapely pyproj numpy matplotlib pyarrow`.

## Open items / next steps
1. Swap in JRC GFC2020 and WDPA when they can be downloaded, and check the P-0005/P-0006 overlap against WDPA.
2. Use real restricted-use areas (Thai RFD National Reserved Forests).
3. Add a minimum-area (0.5 ha) or connectivity option for `c2`; add GLAD/DIST alerts in spare bits 14-15.
4. Use a 2019/2020 plantation map if one becomes available.
