# SysEarth handoff: EUDR geospatial compression challenge

Current state as of 2026-10-16 (JRC GFC2020 V4 baseline in). Branch: `claude/gracious-goldberg-68rm95`
(repo `mackerelsoup/SysEarth`), last commit: see `git log`. No PR open; do not open one unless asked.

**Start here:** the main deliverable is `docs/REPORT.md`. The regulation-based layer analysis is
`docs/01_layer_analysis.md`, and layer sources are in `data/layers/PROVENANCE.md`.

## 1. Task (from the problem prompt)
Show how SystemEarth can encode multiple geospatial layers across time and space in a compact,
query-efficient form while preserving plot-level compliance decisions.

| # | Deliverable | Status | Where |
|---|---|---|---|
| 1 | Layer analysis grounded in the regulation | Done | `docs/01_layer_analysis.md` |
| 2 | Encoding strategy | Done | `docs/REPORT.md` §2, `eudr/encoding.py` |
| 3 | Working prototype for one region | Done | `eudr/`, `scripts/` |
| 4 | Evaluation: storage, query speed and extra storage, decision fidelity, pixel fidelity | Done | `docs/REPORT.md` §4, `data/cube/evaluation.json` |
| 5 | Bonus: infrastructure and cost estimate | Done | `docs/REPORT.md` §5 |

## 2. Decisions made
- **Use case:** EUDR deforestation-free status for plots.
  - Cut-off is 31 Dec 2020.
  - Forest follows FAO: >0.5 ha, >5 m, canopy >10%, agricultural plantations excluded.
- **EUDR dates (verified):** applies from 30 Dec 2026 for large and medium operators (Reg. (EU) 2025/2650). Micro and small operators not already under the EU Timber Regulation follow from 30 Jun 2027. The May 2026 simplification review confirmed both dates.
- **Outputs in scope:** CSV columns `c2` (deforestation indication), `d1` (strict PA overlap) and `d2` (restricted-use overlap). All other columns are non-spatial.
- **Fidelity reference:** decision fidelity is measured against the *original layers* evaluated with the same explicit rule. It is not measured against the CSV, because the CSV's `c2` is not reproducible (see §5).
- **Comparison baseline:** Cloud-Optimized GeoTIFF (COG) reads, including an HTTP range-request benchmark with simulated object-storage latency.
- **Regimes, all parameters only:**
  - `eudr`: baseline and plantation mask on.
  - `eudr_hansen_only`: Hansen data only.
  - `legacy`: any loss 2021–2023, the rule the CSV claims to use.
  - `rspo`: cut-off 2005, canopy >30%, baseline and plantation mask off.
- **Plantation mask must date from before the cut-off.** A post-2020 map would hide forest converted to plantation after the cut-off.
- **Polygon-to-pixel rule:** pixels whose centre is inside the plot. If a plot contains no centre, fall back to all touched pixels. Both query paths use this rule.
- **Protected-area rule:** the PA bit is burned in all-touched, so it can over-cover but never miss. Every hit is confirmed with an exact vector intersection.
- **Codec:** deflate COG with 256 px tiles. Zstd was only 3% smaller.

## 3. Encoding (one uint16 per 30 m Hansen pixel)

| Bits | Content |
|---|---|
| 0–4 | loss year 0–25, lossless |
| 5–7 | canopy 2000 class, bin edges 0/10/15/20/25/30/50/100 (exact for those thresholds) |
| 8 | forest 2020 (JRC GFC2020 V4, ≥50% of the 10 m pixels forest; GLCLU as fallback) |
| 9 | tree height ≥5 m (TCH 2020) |
| 10 | plantation 2015 |
| 11 | strict PA |
| 12 | restricted-use area |
| 13 | not land |
| 14–15 | spare (e.g. GLAD/DIST alerts) |

Alongside the cube:
- a 16×16 block overview (OR of flags, max of loss year and canopy; +0.64% storage);
- an optional uint8 decision byte for the EUDR regime only;
- the PA vectors.

## 4. Data and configuration (`eudr/config.json`)

| Layer | Source | Details |
|---|---|---|
| Hansen | GFC-2025-v1.13, tile 10N_090E, in `data/clipped/` (user-supplied) | `lossyear` covers 2001–2025 |
| Forest 2020 | **JRC GFC2020 V4** (EU reference map), user-supplied `data/layers/JRC_GFC2020_V4_N10_E90_clip.tif` (10 m, 118 KB, clipped with `scripts/clip_rasters.py`, same square) | value 1 = forest. `scripts/align_to_hansen.py` aggregates it to % forest per Hansen pixel (exact 3×3 nesting, offset 2 sub-pixels) → `data/layers/jrc_gfc2020_v4_pct_hansengrid.tif` (48 KB); forest = config values 50..100 (majority) |
| Forest 2020, fallback | GLAD GLCLU 2020 v2, key `forest2020_glad` (unused) | classes 27–48 and 127–148 |
| Plantation | GLAD Commodities **2015** | class 2 = other plantation (includes rubber), 4 = oil palm. Kept as a backstop: only 3.0% of JRC forest falls in it |
| Height | GLAD TCH 2020 | ≥5 m |
| Strict PA | **WDPA/WD-OECM Oct 2026, Thailand** (user-uploaded `.gdb`, clipped with `scripts/fetch_wdpa.py --gdb`) → `data/layers/wdpa_oct2026_aoi.geojson` | IUCN Ia/Ib/II = strict. Fields renamed in 2026: `WDPAID`→`SITE_ID`. The 2016 archive copy is kept as `wdpa_mar2016_aoi.geojson` |
| Restricted-use areas | **synthetic**, `tests/data/synthetic_restricted_areas.geojson` | 6 polygons, only to test `d2`; no real source exists in WDPA or OSM for this area |
| Cross-check | OSM via Overture, `data/layers/protected_areas_overture.geojson` | – |
| Unused but fetched | ESA WorldCover 2020, GLAD Commodities 2024, commodity gain, LandUse 2024, GLCLU change | – |

- **WDPA in the clip area (Oct 2026):** Khlong Phraya WS (4015, Ia) and Khao Phanom Bencha NP (5068, II). Both are polygons; there are no point-only sites or OECMs. Than Bok Khorani Marine NP (312954) is now just west of the clip edge (98.722° E).
- **Test plots:** `tests/data/synthetic_plots.geojson` holds 1,076 plots: 600 random, 138 on post-2020 loss, 250 on PA boundaries, 88 `eudr_deforested` (centred on pixels deforested under the EUDR rule; drawn last, so the first 988 are byte-identical to the earlier set). With the 17 real plots that makes 1,093.

## 5. Key findings
1. **The CSV `c2` is not reproducible from Hansen with any rule tried.** P-0008 and P-0010 are flagged with zero post-2020 loss, while P-0001 has 38 px of 2022 loss and is unflagged. Links, quantities and dates in the CSV are templated, so the CSV is probably a mock-up.
2. **The landscape is a plantation mosaic, and the baseline layer decides almost everything.** 46% of pixels show loss at some point from 2001–2025, and 74% are plantation in 2015. GLAD trees ≥5 m cover 82.6% of pixels (20.6% without 2015 plantations); JRC forest covers 8.86%. JRC agrees with (GLAD trees and not plantation) on 87.6% of pixels.

   | Rule | Forest at cut-off | Deforested after 2020 | Plots with `c2`, of 1,093 |
   |---|---|---|---|
   | Hansen only | 53.6% | 10.2% | 632 |
   | EUDR, GLAD GLCLU baseline (previous build) | 14.7% | 0.92% | (237 of the earlier 1,005) |
   | **EUDR, JRC GFC2020 baseline + plantation mask** | **8.3%** | **0.027%** | **82** |

3. **Real plots under EUDR.**
   - Only P-0001 is flagged, with 1 pixel (~0.08 ha). With GLAD it was P-0001 (10 px), P-0014 (2 px) and P-0016 (1 px).
   - The five plots flagged in the CSV (P-0003, P-0005, P-0008, P-0010, P-0013), all cleared on site, are clean. CSV `c2` agreement is 11 of 17.
   - `min_px=1` is maximally cautious; a 0.5 ha (7 px) minimum would clear all 17 plots.
   - Sensitivity: counting any JRC sub-pixel as forest gives 86 `c2` plots, 0.038% deforested, still only P-0001 (1 px).
4. **P-0005 and P-0006 overlap Khlong Phraya Wildlife Sanctuary (IUCN Ia)**, but the CSV says `d1 = no overlap`.

   | Plot | Inside WDPA Oct 2026 (= 2016) | Inside OSM 2026 |
   |---|---|---|
   | P-0005 | 32% | 100% |
   | P-0006 | 24% | 100% |

   The two boundaries differ by up to 815 m on the western edge (IoU 0.82). Confirm with the official DNP boundary. 22% of strict-PA pixels are 2015 plantation, which suggests encroachment.

## 6. Results (`data/cube/evaluation.json`, `data/cube/http_bench_17plots.json`)

| Measure | Result |
|---|---|
| Decision fidelity | **100%** of 1,093 plots in all 4 regimes, with and without the overview (`c2` / `d1` / `d2` positives under EUDR: 82 / 103 / 186; Hansen only 632, legacy 459, RSPO 913 `c2`) |
| Storage | 3.53 MB originals vs **1.17 MB** cube + overview + vectors (**3.0×**); decision byte + vectors 0.19 MB (**18.5×**). Down from 3.6× because the JRC layer is tiny (48 KB vs 1.16 MB for GLCLU) |
| Pixel fidelity | 100% on every field used by a rule (forest 2020 exact vs aligned JRC); canopy % MAE 10.3 pts (not used); PA bit misses 0 px and has 1.6% extra boundary pixels |
| COG tile requests (EUDR, 1,093 plots) | 6,005 → 1,201 (cube) → 557 (cube + overview) |
| HTTP query, 17 real plots at 40 ms latency | 58.7 → 19.8 → **5.6 ms** per plot (original → cube → cube + overview, 10.5×); 45 → 10 → 8 requests |
| Local-disk speed (EUDR, median) | 1.22 ms original, 0.97 ms cube, 0.23 ms cube + overview |
| Compression headroom | entropy-bound: collapsing pre-2020 loss into cut-off epochs gives identical decisions but saves only about 10% more |
| Overview | answers 586 of 1,093 plots (54%) under EUDR, because JRC forest is sparse; only 20 under Hansen-only/legacy |
| Cost, global land | cube about 74 GB (≈$1.7/month on S3); build about 37 core-hours (≈$1.50); 1 M plot checks about $0.28 (requests + Lambda) |

## 7. How to reproduce

The venv is ephemeral and must be recreated each session:

```bash
python3 -m venv /tmp/venv && /tmp/venv/bin/pip install rasterio shapely pyproj numpy matplotlib pyarrow pyogrio geopandas
PY=/tmp/venv/bin/python
$PY scripts/fetch_layers.py          # GLAD (GCS) + ESA (S3) + Overture (S3), about 1 min
$PY scripts/fetch_wdpa.py --gdb <WDPA_WDOECM_Oct2026_Public_THA.gdb> --out data/layers/wdpa_oct2026_aoi.geojson   # or --work DIR to download the 2016 archive
$PY scripts/align_to_hansen.py --input data/layers/JRC_GFC2020_V4_N10_E90_clip.tif --values 1 --out data/layers/jrc_gfc2020_v4_pct_hansengrid.tif
$PY scripts/build_cube.py            # -> data/cube/
$PY scripts/make_synthetic_plots.py --pa data/layers/wdpa_oct2026_aoi.geojson tests/data/synthetic_restricted_areas.geojson --n-pa 250 --cube data/cube/cube.tif --n-defor 150
$PY scripts/evaluate.py --plots tests/data/17plots-valid.geojson tests/data/synthetic_plots.geojson
$PY scripts/bench_http.py --plots tests/data/17plots-valid.geojson --latency-ms 40 --out data/cube/http_bench_17plots.json
$PY scripts/plot_rules.py            # docs/img/rules_compared.png
```

**Code map**

| File | Contents |
|---|---|
| `eudr/encoding.py` | bit layout, encode, overview, decision byte |
| `eudr/rules.py` | `Rule` dataclass and `REGIMES` |
| `eudr/query.py` | `OriginalLayers`, `CubeLayers`, `VectorIndex`, shared `plot_mask` |
| `scripts/clip_rasters.py` | original Hansen clipper (also used for the JRC tile) |
| `scripts/align_to_hansen.py` | aggregates a finer raster to % of given classes per Hansen pixel |

## 8. Environment notes
- **Network access:**
  - *Blocked:* JRC forobs, protectedplanet.net, Zenodo, Overpass, GFW API, glad.umd.edu, data.go.th, dnp.go.th, Hugging Face, Wikidata.
  - *Reachable:* storage.googleapis.com (`earthenginepartners-hansen`), S3 (`esa-worldcover`, `overturemaps-us-west-2`, `wdpa`), PyPI, raw.githubusercontent.com.
  - Do not try to route around blocked hosts.
- **The `s3://wdpa` bucket** (eu-west-1) is UNEP-WCMC's archive: listing is public, but some objects return 403. The newest readable full release is `2016/WDPA_Mar2016_Public.zip`; the Oct 2016 release is 403.
- **Downloads:** put them in their own new directory, and run Python that reads them with `-I`.
- **Git:** a push once failed with 403 and worked on retry. Use the current session's commit trailers.
- **Context:** keep each context under about 150k tokens; spin up subagents for large chunks of work. The user has given full autonomy and wants steps and findings reported as work proceeds.

## 9. Open items / next steps
1. Done: JRC GFC2020 V4 is the bit 8 baseline. WDPA is current (Oct 2026); only the DNP boundary could still change the P-0005/P-0006 result.
2. Add real restricted-use areas, e.g. Thai Royal Forest Department National Reserved Forests (not in WDPA).
3. Add a minimum-area (0.5 ha) or connectivity option for `c2`. With JRC, a 7 px minimum clears all 17 real plots.
4. Add GLAD/DIST alerts in spare bits 14–15 for monitoring after 2025.
5. Use a 2019/2020 plantation map if one becomes available.
6. Confirm with the user the commodity in scope and how the CSV's `c2` was produced.
7. Optional: polish or publish the report.
