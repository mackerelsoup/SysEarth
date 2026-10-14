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

## Open items / next steps
1. Check the `lossyear` year range and tile version, then fix the loss window.
2. Get JRC 2020 forest map, WDPA Thailand and a plantation layer, clipped to `data/clipped/clip_bbox.json`. Test sandbox downloads; otherwise the user uploads them.
3. Confirm with the user:
   - the exact `c2` rule
   - the commodity (rubber or palm)
   - whether to generate synthetic plots, including protected-area overlaps and post-2020 loss
4. Write the EUDR layer-by-layer analysis table (keep / simplify / merge / drop, with regulatory reason).
5. Build the encoder (bit-packed COG or Zarr), the original-layer query path and the compressed query path, with the same polygon-to-pixel rule for both.
6. Benchmark storage, query time and extra storage. Measure decision fidelity on `c2`, `d1`, `d2`, and pixel fidelity.
7. Bonus cost model: extrapolate from the clipped region to the full tile and to a country.
