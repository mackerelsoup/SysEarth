# SysEarth EUDR cube: compact plot-compliance encoding

**Question.** Can multiple geospatial layers across time and space be encoded compactly and
queried quickly, while preserving every plot-level compliance decision?

**Answer, on a 44 × 44 km clip of Surat Thani, Thailand (2.52 M pixels at 30 m):**

| | Original layers (COG) | SysEarth cube | Single-regime decision byte |
|---|---|---|---|
| Storage (needed layers) | 3.53 MB, 6 rasters + 2 vector files | **1.17 MB** (1.13 MB raster + 7 KB overview + 36 KB vectors), **3.0× smaller** | **0.19 MB, 18.5× smaller** |
| Uncompressed | 17.6 MB | 5.0 MB | 2.5 MB |
| Decision fidelity, 1,093 plots × 5 regimes (`c2`, `d1`, `d2` all match) | reference | **100 %** | 100 % (EUDR only) |
| Pixel fidelity | reference | 100 % on every field used in decisions; canopy % mean abs. error 10.3 pts | EUDR flags only |
| HTTP query, 17 real plots, 40 ms latency | 45 requests, 885 KB, **58.7 ms / plot** | with overview: 8 requests, 138 KB, **5.6 ms / plot** (10.5×) | – |
| Regimes answerable | one script per regime | any cut-off year, canopy ∈ {0,10,15,20,25,30,50} %, baseline / height / plantation on or off, minimum cleared-patch size | EUDR only |

**The 2020 forest baseline is the EU's own map, JRC GFC2020 V4.** It matters far more than
anything in the encoding. In this plantation landscape it removes almost all false alarms:

| Rule | Forest at cut-off | Deforested after 2020 | Plots with `c2` (of 1,093) | Real plots flagged (of 17) |
|---|---|---|---|---|
| Legacy: any loss 2021–23 | – | 6.8 % | 459 | 3 (P-0001, P-0016, P-0018) |
| EUDR, Hansen only | 53.6 % | 10.2 % | 632 | 7 |
| EUDR, GLAD GLCLU baseline + plantation mask (previous build) | 14.7 % | 0.92 % | (237 of the earlier 1,005) | 3 (P-0001: 10 px, P-0014: 2 px, P-0016: 1 px) |
| **EUDR, JRC GFC2020 baseline + plantation mask** | **8.3 %** | **0.027 %** | **82** | **1 (P-0001, 1 px)** |

Reproduce with:

```bash
python scripts/fetch_layers.py                 # ancillary layers (GLAD GCS, ESA S3, Overture S3)
python scripts/fetch_wdpa.py --gdb WDPA_WDOECM_Oct2026_Public_THA.gdb --out data/layers/wdpa_oct2026_aoi.geojson
# JRC GFC2020 V4 tile N10_E90 is user-supplied, clipped with scripts/clip_rasters.py
python scripts/align_to_hansen.py --input data/layers/JRC_GFC2020_V4_N10_E90_clip.tif --values 1 \
       --out data/layers/jrc_gfc2020_v4_pct_hansengrid.tif
python scripts/build_cube.py                   # -> data/cube/
python scripts/make_synthetic_plots.py --pa data/layers/wdpa_oct2026_aoi.geojson \
       tests/data/synthetic_restricted_areas.geojson --n-pa 250 --cube data/cube/cube.tif --n-defor 150
python scripts/evaluate.py --plots tests/data/17plots-valid.geojson tests/data/synthetic_plots.geojson
python scripts/bench_http.py --plots tests/data/17plots-valid.geojson --latency-ms 40
python scripts/plot_rules.py
```

Dependencies: `rasterio shapely pyproj numpy matplotlib` (plus `pyarrow` for the fetch script).

---

## 1. Layer analysis

See [01_layer_analysis.md](01_layer_analysis.md). In short:

- Only `c2`, `d1` and `d2` of the 35 report columns are spatial.
- **Kept:** loss year (lossless), plus canopy-in-2000 reduced to the thresholds in use.
- **Merged:** the 2020 forest baseline (JRC GFC2020 V4, majority of its 10 m pixels), height ≥ 5 m and the pre-cut-off plantation mask each become one bit.
- **Kept as exact vectors:** protected and restricted areas, with a conservative raster bit as an index.
- **Dropped:** `gain`, `first`/`last`, and the values of `datamask` beyond one bit.

The existing CSV `c2` column cannot be reproduced from any Hansen rule and is probably mocked, so fidelity is measured against the original layers.

![Three rules from one cube](img/rules_compared.png)

## 2. Encoding strategy

### 2.1 Collapse time first

Hansen's 25 annual loss maps are already one band, because a pixel's first stand-replacing loss can
only happen once. The time axis therefore reduces to **one 5-bit loss year**. Every other layer is a
**state at a date** (2000 canopy, 2020 forest, 2015 plantations, current PAs). Each regime question
has the same shape:

> *was the pixel forest at the cut-off (state + no loss before it), and was it lost after?*

So the cube stores the minimum needed to evaluate that shape for any parameter set.

### 2.2 Bit layout: one uint16 per 30 m pixel (`eudr/encoding.py`)

| Bits | Field | Source | Fidelity |
|---|---|---|---|
| 0–4 | loss year 0–25 (2001–2025) | Hansen `lossyear` | lossless |
| 5–7 | canopy class, edges 0/10/15/20/25/30/50/100 | Hansen `treecover2000` | `canopy > T` exact for T ∈ {0,10,15,20,25,30,50} |
| 8 | forest 2020 | JRC GFC2020 V4 (10 m, aggregated to % forest per 30 m pixel, forest = ≥ 50 %); GLAD GLCLU 2020 as fallback | lossless vs the aligned layer |
| 9 | tree height ≥ 5 m | GLAD TCH 2020 | lossless |
| 10 | tree-crop plantation, **pre cut-off** | GLAD Commodities 2015 | lossless |
| 11 | strict PA (IUCN Ia/Ib/II), burned *all-touched* | WDPA/WD-OECM Oct 2026 (OSM/Overture as cross-check) | superset, never misses |
| 12 | restricted-use area, *all-touched* | synthetic here | superset, never misses |
| 13 | not land | Hansen `datamask` | lossless |
| 14–15 | spare (e.g. post-2025 GLAD/DIST alert flag) | – | – |

Stored as a single-band, 256 × 256 tiled, deflate Cloud-Optimized GeoTIFF on the Hansen grid.
Zstd saves another 3 %, and two uint8 bands save 12 %. Deflate was kept for compatibility.

### 2.3 Rules are parameters, not data (`eudr/rules.py`)

```python
EUDR  = Rule(cutoff_year=2020, canopy_threshold=10, use_baseline=True, exclude_plantation=True)
RSPO  = replace(EUDR, cutoff_year=2005, canopy_threshold=30, use_baseline=False, exclude_plantation=False)
LEGACY = Rule(loss_end_year=2023, require_forest=False, ...)   # "any Hansen loss 2021-2023"
```

A pixel is forest at the cut-off when all of these hold:

- its canopy class is above the threshold;
- its loss year is 0 or later than the cut-off;
- it is land;
- the optional conditions hold: in the baseline, at least 5 m tall, not plantation.

It is deforested when it is forest at the cut-off and lost after it, up to `loss_end_year`.

A plot gets a `c2` indication when at least `min_px` of its pixels are deforested.

**Optional minimum patch size (`min_patch_px`).** EUDR sets no minimum size for a clearing, so the
default counts every deforested pixel. A stricter reading counts only 8-connected deforested patches
of at least `min_patch_px` pixels. The `eudr_patch05ha` regime uses 7 px, which is 0.5 ha at this
latitude (a Hansen pixel is ~0.077 ha). A patch is sized over its full extent, not only the part
inside the plot, so a clearing that straddles the plot boundary counts at full size. Both query
paths pad the read window by `min_patch_px` pixels. Any patch that touches the plot and still
reaches the edge of that window therefore already has `min_patch_px` pixels, so sizing from the
window is exact: it gives the same counts as labelling the whole landscape (checked on all 1,093
plots). The cube needs no new data; the patch filter runs on the decoded window.

**Ancillary layers must not post-date the cut-off they are used for.** A 2024 plantation map would
remove forest converted to palm in 2022 from the forest mask, which hides exactly the EUDR violation.
For that reason the RSPO-2005 regime switches off the 2020 baseline and the 2015 plantation mask
instead of reusing them.

### 2.4 Polygon to pixels (one rule for both paths)

- **Which pixels belong to a plot:** those whose pixel centre falls inside the polygon (GDAL default). A plot too small to contain any centre (< ~0.08 ha) falls back to every pixel it touches, so it is never empty. `pixel_rule="all_touched"` is available as a stricter option.
- **Protected-area overlap:** the plot's touched pixels are tested against the PA bit. A PA bit burned all-touched can only over-cover, so **a miss in the raster is a guaranteed miss**. A hit is confirmed with an exact polygon intersection against the vectors shipped with the cube (36 KB).

### 2.5 Query acceleration and its extra storage

| Structure | Extra storage | What it buys |
|---|---|---|
| Single band instead of 5–6 rasters | –68 % | 1 range request per plot instead of 5–6 (one per layer) |
| 16 × 16 block overview: OR of flags, max of loss year and canopy class | **+0.64 %** (7.2 KB) | Answers "no indication, no PA" without the full-resolution read, when a plot's blocks have no possible hit |
| PA vectors (exact) | +3.2 % | Exact `d1`/`d2` with the raster bit as a spatial index |
| Decision byte (optional, one regime) | +13.7 % if kept beside the cube, or an 18.5× smaller replacement | Fastest single-regime path |

Under EUDR the overview answers **586 of 1,093 plots (54 %)** without a full-resolution read.
JRC forest is sparse (8.9 % of pixels), so most 480 m blocks cannot contain a deforested pixel.
Under the Hansen-only and legacy rules there is no baseline to prune with, and in this plantation
landscape (46 % of pixels lost at some point, 74 % plantation) the overview answers only 20 plots.

## 3. Prototype

| File | Role |
|---|---|
| `eudr/encoding.py` | bit layout, `encode`, field accessors, decision byte, block overview |
| `eudr/rules.py` | `Rule` dataclass; regimes EUDR, EUDR ≥ 0.5 ha patches, EUDR-Hansen-only, legacy, RSPO; `large_patches` |
| `eudr/query.py` | `OriginalLayers` (raw rasters + exact vectors), `CubeLayers` (cube + overview + vector refinement), shared `plot_mask` |
| `eudr/config.json` | layer paths and class codes |
| `scripts/build_cube.py` | builds `data/cube/{cube,overview,decision_eudr}.tif` and PA vectors (≈2 s) |
| `scripts/evaluate.py` | storage, decisions, pixel fidelity, query counters → `data/cube/evaluation.json` |
| `scripts/bench_http.py` | serves files over HTTP with injected latency, counts real range requests via GDAL `/vsicurl/` |
| `scripts/align_to_hansen.py` | aggregates a finer raster (JRC GFC2020, 10 m) to % of selected classes per Hansen pixel |
| `scripts/make_synthetic_plots.py` | random, post-2020-loss, PA-boundary and EUDR-deforested plots (`--cube`, `--n-defor`) |
| `scripts/fetch_layers.py` | reproducible acquisition and clipping of ancillary layers ([PROVENANCE](../data/layers/PROVENANCE.md)) |

## 4. Evaluation

**Test set.** 1,093 plots:

| Plots | Kind |
|---|---|
| 17 | real |
| 600 | random |
| 138 | centred on post-2020 loss |
| 250 | on the boundaries of protected and restricted areas |
| 88 | centred on pixels deforested under the EUDR rule (`eudr_deforested`, drawn last so the other 988 synthetic plots are unchanged) |

So every output has positives. Under EUDR: `c2` 82, `d1` 103, `d2` 186. The JRC baseline leaves so
few deforested pixels that the `eudr_deforested` group is needed to test `c2`; 77 of its 88 plots get an indication.

### 4.1 Storage

| Item | Bytes | Bytes / pixel |
|---|---|---|
| Hansen lossyear + treecover2000 + datamask | 2,016,468 | 0.80 |
| JRC GFC2020 % on grid (48,060) + Commodities 2015 (374,129) + TCH 2020 (1,051,611) | 1,473,800 | 0.58 |
| PA vectors (WDPA + synthetic restricted) | 38,607 | – |
| **Original, needed** | **3,528,875** | **1.40** |
| Original, plus dropped `gain` (141,758) | 3,670,633 | 1.46 |
| Original, uncompressed | 17,640,462 | 7.0 |
| **Cube (1,126,706) + overview (7,172) + vectors (35,516)** | **1,169,394** | **0.464** |
| Decision byte (154,825) + vectors | 190,341 | 0.076 |

The JRC clip at its native 10 m is 118,426 bytes.

Reductions:

- **Cube:** 3.0× against the needed original layers, and 15.1× uncompressed to compressed (17.6 MB to 1.17 MB).
- **Decision byte:** 18.5×.

The ratio fell from 3.6× with the GLAD baseline because the JRC layer itself is tiny (48 KB against
1.16 MB for GLCLU), so the original stack shrank. The cube shrank too (1.23 MB to 1.13 MB),
because the forest bit is sparser.

Going further is entropy-bound. The loss year alone is 3.1 bits per pixel in this rotation landscape.
Collapsing pre-2020 loss into the epochs that bound the supported cut-offs (≤ 2005, 2006–08,
2009–14, 2015–20) gives identical decisions pixel for pixel, but saves only another 10 %.

### 4.2 Query speed

**Local disk, median per plot, 1,093 plots:**

| Regime | Original layers | Cube | Cube + overview |
|---|---|---|---|
| EUDR | 1.19 ms | 0.84 ms | **0.21 ms** |
| EUDR, ≥ 0.5 ha patches | 1.24 ms | 1.06 ms | 0.82 ms |
| Legacy | 0.87 ms | 0.79 ms | 0.80 ms |

Without the overview both paths are bound by polygon rasterisation, not I/O. Under EUDR the
overview skips that work for 54 % of plots. The patch regime reads a window padded by 7 px, so the
overview answers fewer plots (538, 49 %) and the median plot needs the full read.

**COG tile requests across the 1,093 plots:**

| Regime | Original layers | Cube | Cube + overview |
|---|---|---|---|
| EUDR | 6,005 (5 files) | 1,201 | **557** |
| EUDR, ≥ 0.5 ha patches (7 px pad) | 6,420 (5 files) | 1,284 | 647 |
| EUDR, Hansen only / RSPO | 3,603 (3 files) | 1,201 | 1,181 / 1,200 |
| Legacy | 2,402 (2 files) | 1,201 | 1,181 |

**Object storage (HTTP range requests through GDAL `/vsicurl/`, 40 ms injected latency), EUDR, 17 real plots** (`data/cube/http_bench_17plots.json`):

| Path | Requests | Bytes | Time per plot | Speed-up |
|---|---|---|---|---|
| Original layers | 45 | 885 KB | 58.7 ms | – |
| Cube | 10 | 377 KB | 19.8 ms | 3.0× |
| **Cube + overview** | **8** | **138 KB** | **5.6 ms** | **10.5×** |

The ≥ 0.5 ha patch regime gives the same request counts on these 17 plots: 58.0 → 19.2 → 5.4 ms per plot
(`data/cube/http_bench_17plots_patch05ha.json`).

### 4.3 Decision fidelity

| Regime | Plots | All of `c2`, `d1`, `d2` match | Deforested pixel count matches | Positives `c2` / `d1` / `d2` |
|---|---|---|---|---|
| EUDR (JRC baseline + plantation mask) | 1,093 | **100 %** | 100 % | 82 / 103 / 186 |
| EUDR, only patches ≥ 0.5 ha (7 px) | 1,093 | **100 %** | 100 % | 39 / 103 / 186 |
| EUDR, Hansen only | 1,093 | **100 %** | 100 % | 632 / 103 / 186 |
| Legacy (any loss 2021–23) | 1,093 | **100 %** | 100 % | 459 / 103 / 186 |
| RSPO-style 2005, canopy > 30 % | 1,093 | **100 %** | 100 % | 913 / 103 / 186 |
| Same, answered from the overview where possible | 1,093 | **100 %** | 100 % | – |

Fidelity is 100 % by construction:

- every field used by a rule is stored losslessly, or at exact threshold boundaries;
- the PA raster can only over-cover, and its hits are refined on the exact vectors;
- the overview is a monotone over-approximation, so it can only skip plots that are guaranteed negative.

**Against the existing CSV (17 plots).** The CSV is a different, undocumented process, so this is not a fidelity
measure. `c2` agrees on 11 of 17 plots under EUDR (12 with ≥ 0.5 ha patches, 9 under legacy). Under EUDR, the five plots the CSV
flagged (all cleared on site) are clean, and the only indication is P-0001 with a single pixel (~0.08 ha). `d1` disagrees on P-0005 and P-0006, which overlap Khlong
Phraya Wildlife Sanctuary in the current WDPA (Oct 2026: 32 % and 24 % of plot area) and in OSM (see 01 §1.4).

### 4.4 Pixel fidelity (cube vs aligned sources, all 2.52 M pixels)

| Field | Exact |
|---|---|
| loss year | 100 % |
| canopy class, and `canopy > T` for all 7 thresholds | 100 % |
| canopy % (reconstructed from the class midpoint) | mean abs. error 10.3 pts, RMSE 13.3 pts (not used by any decision) |
| forest 2020 (vs aligned JRC), height ≥ 5 m, plantation, not-land | 100 % |
| strict PA bit vs pixel-centre rasterisation of the vectors | 0 pixels missed; 1.6 % extra (boundary pixels, by design) |
| restricted bit | 0 missed; 2.8 % extra |
| decision byte vs cube rule | 100 % |

Keeping exact canopy percentages would need 7 bits, which does not fit in a uint16 with the rest. If
a regime ever needs another threshold (for example 75 %), the bin edges change. That is a rebuild,
not a redesign.

**Baseline threshold sensitivity.** Counting a 30 m pixel as forest when *any* of its 10 m JRC
sub-pixels is forest, instead of the majority, gives forest at cut-off 8.65 %, deforested 0.038 %,
86 EUDR `c2` positives (vs 82), and still only P-0001 (1 px) among the real plots. Fidelity stays 100 %.

## 5. Infrastructure and cost estimate

**Scaling basis.** Bytes per pixel from this clip:

- cube 0.464;
- original needed layers 1.40;
- decision byte 0.076.

This clip is a high-entropy plantation mosaic, so these are close to worst case: intact forest,
water and cropland compress much better.

### 5.1 Data volumes

Pixels are 0.00025°, about 760 m² near the equator.

| Extent | Pixels | Cube | Original needed | Decision byte |
|---|---|---|---|---|
| This clip (1,900 km²) | 2.5 M | 1.17 MB | 3.5 MB | 0.19 MB |
| Hansen tile 10N_090E (10° × 10°, land and sea) | 1.6 G | ≤ 0.74 GB | ≤ 2.2 GB | ≤ 0.12 GB |
| Thailand (513 k km²) | 0.68 G | 0.32 GB | 0.95 GB | 0.05 GB |
| Tropics, EUDR producer belt (~50 M km²) | 66 G | 31 GB | 92 GB | 5 GB |
| Global land (~149 M km²) | 160 G | 74 GB | 224 GB | 12 GB |

### 5.2 Running cost (AWS list prices, us-east-1; GCP is similar)

| Item | Original layers | Cube |
|---|---|---|
| S3 Standard storage, global land ($0.023/GB-month) | $5.2 / month | **$1.7 / month** |
| Five-year audit retention (EUDR Art. 12), one frozen version per year | 1.12 TB → $26 / month | 0.37 TB → **$8.5 / month** |
| GET requests per cold plot query ($0.0004 / 1,000) | ~2.6 | ~0.5 (cube + overview) |
| 1 M plot checks: requests | ~$1.06 | **~$0.19** |
| 1 M plot checks: Lambda at 1 GB, latency-bound (58.7 vs 5.6 ms) | ~$0.98 | **~$0.09** |
| Build, global (≈1.2 Mpx/s per core → 160 Gpx ≈ 37 core-hours, spot ~$0.04 per core-hour) | – | **~$1.50** |
| Ingest: Hansen and GLAD read from GCS (public; free if compute runs in GCP, otherwise ~$0.12/GB egress on ~300 GB) | – | $0–36 |
| Annual refresh (new Hansen loss year): rewrite bits 0–4 only, same build cost | – | ~$1.50 |

**Takeaway.** At any realistic EUDR volume (tens of millions of plots per year), infrastructure cost
is small either way: a few hundred dollars a year. What the cube changes:

- **Latency.** 3–10× fewer round trips per plot, and about 10× lower latency with the overview, which matters for interactive due-diligence tools.
- **Operations.** One versioned artefact per year instead of six sources with different grids, versions and legends.
- **Audit.** A frozen 74 GB global cube per year is a cheap, reproducible record of exactly what each decision was based on.
- **Adaptability.** New regimes need no new data, only a new `Rule`.

## 6. Limitations and next steps

1. **Done: the EU baseline is in.** JRC GFC2020 V4 (user-supplied, clipped to the same square) is
   bit 8; GLAD GLCLU 2020 stays in `eudr/config.json` as `forest2020_glad` (unused). JRC already
   excludes plantations: only 3.0 % of JRC forest falls in the GLAD 2015 plantation mask, which is
   kept as a backstop and for regimes without JRC. JRC and (GLAD trees ≥ 5 m minus 2015
   plantations) agree on 87.6 % of pixels, but JRC forest covers 8.86 % of pixels against 20.6 %.
   **WDPA is current**: the October 2026 Thailand WDPA/WD-OECM release (supplied as a `.gdb`,
   clipped by `scripts/fetch_wdpa.py --gdb`). Khlong Phraya's boundary is identical to the 2016
   archive release. Than Bok Khorani Marine NP's revised boundary now ends at 98.722° E, just
   outside the clip area, so two strict PAs remain.
2. **Restricted-use areas are synthetic.** National Reserved Forests (Thai RFD) are the obvious
   real source.
3. **Plantation map is from 2015.** Plantations established 2016–2020 are not in it. They are
   still caught, because they show Hansen loss before the cut-off, which removes them from the
   forest mask, and JRC excludes them anyway. A 2019/2020 commodity map would be cleaner.
4. **Loss ends in 2025.** GLAD/DIST alerts (UTM 47, weekly) could fill spare bits 14–15 for
   post-2025 monitoring.
5. **Done: optional minimum cleared-patch size.** The default (`min_px = 1`, no patch filter) stays
   maximally cautious, because EUDR sets no minimum clearing size; its 0.5 ha applies to the forest.
   The `eudr_patch05ha` regime counts only 8-connected cleared patches of ≥ 7 px (0.5 ha), sized
   exactly (§2.3), with 100 % fidelity. It clears P-0001, the only real plot flagged under EUDR
   (a single isolated pixel), and lowers `c2` positives from 82 to 39 of 1,093. Of the 680 EUDR
   deforested pixels in the clip, 53 % lie in the 25 patches ≥ 0.5 ha (the largest is 49 px, ~3.8 ha);
   the rest are 169 smaller fragments. Of the 88 plots centred on an EUDR-deforested pixel, only 38
   stay flagged. Whether to apply a patch minimum is a policy decision for the user.
6. **Open with the user:** the commodity in scope and how the CSV's `c2` was produced are still
   unconfirmed.
