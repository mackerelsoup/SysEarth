# data/layers provenance

Fetched 2026-10-14 by `scripts/fetch_layers.py` (re-run it to reproduce; about 1 minute). Machine-readable version: `layers.json`, which also holds per-layer value counts.

## Reference grid and AOI
- AOI: `data/clipped/clip_bbox.json`, EPSG:4326, lon 98.72694–99.12578, lat 8.30933–8.70374 (Surat Thani/Krabi border, southern Thailand).
- Reference grid: `data/clipped/Hansen_GFC-2025-v1.13_lossyear_10N_090E_clip.tif`. Origin (98.72675, 8.70375), 0.00025° pixels, 1597 x 1578 px, so N = 2,520,066 px.
- All rasters are written as deflate-compressed GeoTIFFs with 256x256 tiles, in EPSG:4326.
- Only the AOI window is read remotely, through GDAL `/vsicurl/` range requests. The GLAD files use strip layout and WorldCover files are COGs, so no full tiles are downloaded.

## Rasters

| File stem | Source URL | Native res | `_hansengrid` method |
|---|---|---|---|
| `glad_glclu2020_v2` | https://storage.googleapis.com/earthenginepartners-hansen/GLCLU2000-2020/v2/2020/10N_090E.tif | 0.00025° | nearest (identity) |
| `glad_glclu_change2000_2020_v2` | …/GLCLU2000-2020/v2/2000-2020change/10N_090E.tif | 0.00025° | nearest (identity) |
| `glad_tch2020` | …/TCH_2015_2024/TCH_filter_2020/10N_090E.tif | 0.00025° | nearest (identity) |
| `glad_commodities2015_asia` | …/PantropicalLandUse/GLAD_Commodities_2015_ASIA.tif | 0.00025° | nearest (identity) |
| `glad_commodities2024_asia` | …/PantropicalLandUse/GLAD_Commodities_2024_ASIA.tif | 0.00025° | nearest (identity) |
| `glad_commoditygain_asia` | …/PantropicalLandUse/GLAD_CommodityGain_ASIA.tif | 0.00025° | nearest (identity) |
| `glad_landuse2024_asia` | …/PantropicalLandUse/GLAD_LandUse_2024_ASIA.tif | 0.00025° | nearest (identity) |
| `esa_worldcover2020_v100` | https://esa-worldcover.s3.amazonaws.com/v100/2020/map/ESA_WorldCover_10m_2020_v100_N06E096_Map.tif + `..._N06E099_Map.tif` (mosaicked) | 1/12000° (~10 m), 4791 x 4734 px | majority of 3x3, plus `_treecount9_hansengrid` |

Resampling notes:
- **GLAD 30 m products** use the same 0.00025° lattice as Hansen GFC, so for these files `_clip.tif` and `_hansengrid.tif` are byte-identical in values. The script checks this on each run (`hansengrid_identical_to_clip: true`). Both are kept so that downstream code can rely on the naming.
- **WorldCover**: the 10 m pixels nest exactly 3x3 inside each Hansen pixel (98.72675 x 12000 is an integer), so no interpolation is needed.
  - `_hansengrid.tif`: majority class of the 9 sub-pixels. On ties the lowest class code wins, so tree cover (10) wins ties.
  - `_treecount9_hansengrid.tif`: number of tree-cover (class 10) sub-pixels, 0–9; tree fraction = value/9. Nodata is 255.

Versions and citations:
- **GLAD GLCLU2000-2020 v2:** Potapov et al. 2022, *Front. Remote Sens.* 3:856903. Legend: `GLCLU2000-2020/v2/legend.xlsx`.
- **PantropicalLandUse (preliminary 2015/2024, Moore Foundation):** legend from `PantropicalLandUse/download.html`.
- **TCH_2015_2024:** there is no download page in the prefix. Values are read as canopy height in m.
- **WorldCover 2020 v100:** Zanaga et al. 2021, doi:10.5281/zenodo.5571936, CC-BY 4.0. GLAD layers are CC-BY 4.0 ("Source: Hansen/UMD/Google/USGS/NASA").

## Legend codes for downstream use

**GLCLU 2020 (annual map, v2)**

| Value | Meaning |
|---|---|
| 0–24 | Terra firma short vegetation, by cover % |
| 25–48 | Terra firma tree cover. Height = value − 22 m: 25 = 3 m, 27 = 5 m, …, 47 = 25 m, 48 = >25 m |
| 100–124 | Wetland short vegetation |
| 125–148 | Wetland tree cover. Height = value − 122 m |
| 200–207 | Open water, % of year |
| 241 | Snow/ice |
| 244 | Cropland |
| 250 | Built-up |
| 254 | Ocean |
| 255 | No data |

- **EUDR/FAO "trees >5 m" = 27–48 ∪ 127–148.** This treats the "5 m trees" class as qualifying. Use 28+/128+ if you need strictly more than 5 m.
- GLCLU's own tree definition is ≥3 m, which is 25–48 ∪ 125–148.

**GLCLU 2000–2020 change (v2)**

| Value | Meaning |
|---|---|
| 25–48 | Stable trees |
| 49–72 | Trees with previous disturbance (2020 height) |
| 73–96 | Tree height gain |
| +100 | Wetland versions of the three ranges above |
| 208–211 | Water dynamics |
| 240 | Short vegetation after tree loss |
| 244–249 | Cropland stable, gain or loss (245 = cropland gain from trees) |
| 250–253 | Built-up stable or gain (251 = built-up gain from trees) |

**ESA WorldCover**

| Value | Meaning |
|---|---|
| 10 | Tree cover (no height threshold) |
| 20 | Shrubland |
| 30 | Grassland |
| 40 | Cropland |
| 50 | Built-up |
| 60 | Bare |
| 70 | Snow/ice |
| 80 | Water |
| 90 | Herbaceous wetland |
| 95 | Mangroves |
| 100 | Moss/lichen |
| 0 | No data |

**TCH 2020**: canopy height in m. 0 means no trees, or height below about 5 m: no values 1–4 occur in the AOI. Values in the AOI range from 5 to 41 m. No 255 values are present.

**GLAD Commodities 2015/2024**

| Value | Meaning |
|---|---|
| 0 | None |
| 1 | Pasture |
| 2 | Plantation, other |
| 3 | Timber plantation |
| 4 | **Palm plantation (oil palm)** |
| 5 | Mining |
| 7 | Soy |
| 8 | Maize |
| 9 | Sugarcane |
| 10 | Rice |
| 11 | Soy + maize |
| 12 | Soy + sugarcane |

- There is **no separate rubber class**. In this region rubber almost certainly falls in 2 "Plantation, other".
- `GLAD_LandUse_2024_ASIA` uses the same codes plus 6 = Cropland, other. Its legend is assumed from `TropicalLandUse/download.html`, because the Pantropical page does not list it.
- CommodityGain codes: 8 = pasture gain, 9 = plantation-other gain, 10 = timber gain, 11 = palm gain, 1–5 = crop gains.
- `TropicalLandUse/2015` and `TropicalLandUse/2024` cover only South America (GLAD_TropicalLandUse_2015_SA and 10N_xxxW tiles), so they were not used.

## Key pixel counts on the Hansen grid (N = 2,520,066)

**Forest 2020, by source**

| Measure | Pixels | Share of AOI |
|---|---|---|
| GLCLU trees ≥5 m | 2,081,644 | 82.6% |
| …of which wetland trees | 26,426 | |
| GLCLU trees ≥3 m | 2,198,452 | 87.2% |
| TCH2020 ≥5 m | 2,369,452 | 94.0% |
| WorldCover majority tree | 2,410,009 | 95.6% |
| WorldCover treecount 9/9 | 2,293,923 | |

- GLCLU and TCH (≥5 m) agree on 86.5% of pixels.
- GLCLU 250 (built-up) covers 235,916 px (9.4%). Of those pixels, about 88% are WorldCover tree cover, and their mean TCH is 12.5 m, so GLCLU's built-up class here includes tree-covered rural settlement land.

**Plantations**

| Class | 2015 pixels | 2015 share | 2024 pixels | 2024 share |
|---|---|---|---|---|
| Palm (4) | 1,180,591 | 46.8% | 1,279,958 | 50.8% |
| Plantation-other (2) | 677,643 | 26.9% | 773,566 | 30.7% |
| Timber (3) | 61 | | 64 | |
| Mining (5) | 0 | | 1,184 | |

- CommodityGain: palm gain (11) = 99,488 px; plantation-other gain (9) = 96,069 px.
- GLCLU trees ≥5 m that overlap 2015 palm or plantation-other: 1,561,415 px (62%). GLCLU trees ≥5 m that are not plantation in either 2015 or 2024: 377,925 px (15%). **The "trees >5 m" layer alone therefore greatly overstates forest under the FAO/EUDR definition, which excludes plantations.** The plantation mask is decisive here.

## Protected areas: `protected_areas_overture.geojson`

**Source**
- Overture Maps release `2026-09-23.1`, base theme, `type=land_use`. The features are derived from OpenStreetMap.
- Location: `s3://overturemaps-us-west-2/release/2026-09-23.1/theme=base/type=land_use/` (64 parquet files).
- It was read with pyarrow S3 (anonymous, us-west-2) through the proxy. Row-group bbox statistics were used to skip data, so only 2 of 3,936 row groups were read.

**Filter**: a feature is kept if it intersects the reference-grid bbox and meets any of these:
- `subtype=protected`
- the OSM tag `boundary=protected_area` or `boundary=national_park`
- the OSM tag `leisure=nature_reserve`
- a `protect_class` or `protection_title` tag

**3 protected areas intersect the AOI**:

| Name | OSM | Overture class | protect_class | Area in AOI | EUDR category |
|---|---|---|---|---|---|
| Khlong Phraya Wildlife Sanctuary (เขตรักษาพันธุ์สัตว์ป่าคลองพระยา) | r12092902 | strict_nature_reserve | 1a | 149.8 km² (entirely inside AOI) | **strict** |
| Than Bok Khorani National Park | r3283184 | national_park | 2 | 7.6 of 102.6 km² | **strict** |
| Khao Phanom Bencha National Park | r12092903 | national_park | 2 | 6.4 of 53.3 km² | **strict** |

- **No "restricted use" features** were found: no OSM national reserved forest, non-hunting area or protect_class 4–6 features in the AOI. This does not mean there are none on the ground, only that OSM/Overture does not map them. Thai National Reserved Forests are usually missing from OSM.
- Khao Sok, Kaeng Krung and Tai Rom Yen national parks lie outside the bbox.
- Other land_use features in the AOI (not kept): residential 14, orchard 9, school 8, and a few others. `layers.json` has the full list.
- **Caveats**
  - The Khlong Phraya boundary carries the OSM tag `fixme=approximately`. Than Bok Khorani carries "borders are not exact".
  - Plots **P-0005 and P-0006 fall 100% inside the OSM Khlong Phraya Wildlife Sanctuary polygon**, but the reference CSV says `d1 = a. No overlap with strict PA` for both. Either the CSV used WDPA (whose boundary may differ) or the OSM polygon is too coarse. Treat this as a known source discrepancy for the decision-fidelity evaluation.
  - WDPA (protectedplanet.net) is blocked by network policy, so IUCN categories here are the OSM `protect_class` values, not WDPA's.

## Not downloaded / availability notes
- **GLAD/OPERA DIST-ALERT** (post-2020 vegetation disturbance) is available in the same bucket:
  - URLs: `…/DIST-ALERT/GEN-DIST-STATUS/47.tif`, `GEN-DIST-DATE/47.tif`, plus `GEN-DIST-CONF`, `GEN-DIST-COUNT`, `GEN-DIST-DUR`, `GEN-ANOM(-MAX)` and `GEN-LAST-DATE`.
  - Format: one file per UTM zone. The AOI is zone 47 (EPSG:32647, 30 m). Status is uint8 with nodata 255; date is int16 with nodata −1. Files use strip layout with no overviews.
  - Last updated 2026-10-03 (`uploadDate.txt`).
  - Using it would need a UTM→4326 reprojection.
- Also present but not fetched:
  - `DIST-ANN/2024`, `DIST-ANN/2025` (annual)
  - `GLADalert/C2/2021…2025` (Landsat GLAD alerts)
  - `S2alert/` (Sentinel-2 alerts: alert, alertDate, forestMask)
- The GLAD/GFW web APIs (glad.umd.edu, data-api.globalforestwatch.org) were unreachable (HTTP 000).
- **Blocked by policy:** WDPA/protectedplanet, JRC GFC2020 (forobs), zenodo, overpass, the GFW API and geofabrik. None of these were attempted beyond what the task brief recorded.
