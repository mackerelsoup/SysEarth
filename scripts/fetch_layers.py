#!/usr/bin/env python
"""Fetch EUDR-relevant geospatial layers for the SysEarth sample AOI.

Reads only the AOI window from remote (Cloud-Optimized or strip) GeoTIFFs via
GDAL /vsicurl/ and writes, under data/layers/:

  <source>_clip.tif       native-resolution clip (deflate, tiled 256x256)
  <source>_hansengrid.tif same layer on the exact Hansen GFC reference grid
                          (data/clipped/*lossyear*_clip.tif, 0.00025 deg)
  protected_areas_overture.geojson   Overture Maps base/land_use protected areas
  layers.json             machine-readable provenance (also see PROVENANCE.md)

Usage:  python scripts/fetch_layers.py [--only name1,name2] [--skip-overture]

Needs: rasterio, numpy, shapely, pyarrow.  Network: storage.googleapis.com,
esa-worldcover.s3.amazonaws.com, overturemaps-us-west-2 S3 bucket.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
os.environ.setdefault("GDAL_HTTP_MULTIPLEX", "YES")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "120")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
os.environ.setdefault("VSI_CACHE", "TRUE")
_ca = "/root/.ccr/ca-bundle.crt"
if os.path.exists(_ca):
    os.environ.setdefault("CURL_CA_BUNDLE", _ca)
    os.environ.setdefault("GDAL_CURL_CA_BUNDLE", _ca)
    os.environ.setdefault("AWS_CA_BUNDLE", _ca)
    os.environ.setdefault("SSL_CERT_FILE", _ca)

import rasterio  # noqa: E402
from rasterio.transform import Affine  # noqa: E402
from rasterio.warp import Resampling, reproject  # noqa: E402
from rasterio.windows import Window  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REF = REPO / "data/clipped/Hansen_GFC-2025-v1.13_lossyear_10N_090E_clip.tif"
BBOX = REPO / "data/clipped/clip_bbox.json"
OUT = REPO / "data/layers"

GCS = "https://storage.googleapis.com/earthenginepartners-hansen"
WC = "https://esa-worldcover.s3.amazonaws.com"
OVERTURE_RELEASE = "2026-09-23.1"
OVERTURE_PREFIX = f"overturemaps-us-west-2/release/{OVERTURE_RELEASE}/theme=base/type=land_use/"

PROFILE = dict(driver="GTiff", compress="deflate", predictor=1, tiled=True,
               blockxsize=256, blockysize=256, interleave="band")

# ---------------------------------------------------------------- legends
GLCLU_LEGEND = {
    "0-24": "Terra firma short vegetation, cover 3%..100% (0-1 true desert, 2-18 semi-arid, 19-24 dense short veg)",
    "25-48": "Terra firma tree cover, canopy height 3 m (25), 4 m (26), 5 m (27) ... 25 m (47), >25 m (48); value = height_m + 22",
    "100-124": "Wetland short vegetation, cover 3%..100%",
    "125-148": "Wetland tree cover, canopy height 3 m (125) ... >25 m (148); value = height_m + 122",
    "200-207": "Open surface water, 20-29% (200) .. 90-100% (207) of year",
    "241": "Snow/ice", "244": "Cropland", "250": "Built-up", "254": "Ocean", "255": "No data",
    "49-99,149-199,208-240,242-243,245-249,251-253": "Not used",
}
GLCLU_CHANGE_LEGEND = {
    "0-24": "Terra firma short vegetation (stable)",
    "25-48": "Stable tree cover, 2020 height 3..>25 m",
    "49-72": "Tree cover with previous disturbance (2020 height 3..>25 m)",
    "73-96": "Tree height gain (2020 height 3..>25 m)",
    "100-196": "Wetland equivalents of 0-96 (+100)",
    "other": "see legend.xlsx sheet '2000-2020 change' (water, cropland, built-up transitions, 254 ocean, 255 no data)",
}
COMMODITY_LEGEND = {
    "0": "None of the mapped commodities",
    "1": "Pasture", "2": "Plantation, other (includes tree crops such as rubber, fruit; not separated)",
    "3": "Timber plantation", "4": "Palm plantation (oil palm)", "5": "Mining",
    "7": "Soy", "8": "Maize", "9": "Sugarcane", "10": "Rice",
    "11": "Soy and maize", "12": "Soy and sugarcane",
}
COMMODITY_GAIN_LEGEND = {
    "0": "No commodity gain 2015-2024",
    "1": "Soy gain", "2": "Maize gain", "3": "Soy and maize gain", "4": "Sugarcane gain",
    "5": "Soy and sugarcane gain", "8": "Pasture gain", "9": "Plantation, other gain",
    "10": "Timber plantation gain", "11": "Palm plantation gain",
}
LANDUSE_LEGEND = dict(COMMODITY_LEGEND)
LANDUSE_LEGEND["6"] = "Cropland, other"
WORLDCOVER_LEGEND = {
    "0": "No data", "10": "Tree cover", "20": "Shrubland", "30": "Grassland", "40": "Cropland",
    "50": "Built-up", "60": "Bare / sparse vegetation", "70": "Snow and ice",
    "80": "Permanent water bodies", "90": "Herbaceous wetland", "95": "Mangroves",
    "100": "Moss and lichen",
}
TCH_LEGEND = {"0-254": "Tree canopy height in metres (0 = no trees / below detection)",
              "255": "No data (assumed; none observed in AOI unless stated)"}

# ---------------------------------------------------------------- sources
# 30 m GLAD products are all on the same 0.00025 deg lattice as Hansen GFC,
# so the native clip IS on the reference grid; nearest-neighbour reprojection
# is an identity copy (verified at run time).
GLAD30 = [
    dict(name="glad_glclu2020_v2", url=f"{GCS}/GLCLU2000-2020/v2/2020/10N_090E.tif",
         product="GLAD Global Land Cover and Land Use Change 2000-2020, v2, year 2020 map",
         version="GLCLU2000-2020 v2", legend=GLCLU_LEGEND,
         legend_url=f"{GCS}/GLCLU2000-2020/v2/legend.xlsx",
         doc_url=f"{GCS}/GLCLU2000-2020/v2/download.html",
         citation="Potapov et al. 2022, Front. Remote Sens. 3:856903"),
    dict(name="glad_glclu_change2000_2020_v2", url=f"{GCS}/GLCLU2000-2020/v2/2000-2020change/10N_090E.tif",
         product="GLAD GLCLU 2000-2020 change map, v2", version="GLCLU2000-2020 v2",
         legend=GLCLU_CHANGE_LEGEND, legend_url=f"{GCS}/GLCLU2000-2020/v2/legend.xlsx",
         doc_url=f"{GCS}/GLCLU2000-2020/v2/download.html",
         citation="Potapov et al. 2022, Front. Remote Sens. 3:856903"),
    dict(name="glad_commodities2015_asia", url=f"{GCS}/PantropicalLandUse/GLAD_Commodities_2015_ASIA.tif",
         product="GLAD Pan-tropical land use (commodities) 2015, Asia", version="preliminary (PantropicalLandUse)",
         legend=COMMODITY_LEGEND, doc_url=f"{GCS}/PantropicalLandUse/download.html"),
    dict(name="glad_commodities2024_asia", url=f"{GCS}/PantropicalLandUse/GLAD_Commodities_2024_ASIA.tif",
         product="GLAD Pan-tropical land use (commodities) 2024, Asia", version="preliminary (PantropicalLandUse)",
         legend=COMMODITY_LEGEND, doc_url=f"{GCS}/PantropicalLandUse/download.html"),
    dict(name="glad_commoditygain_asia", url=f"{GCS}/PantropicalLandUse/GLAD_CommodityGain_ASIA.tif",
         product="GLAD Pan-tropical commodity gain 2015-2024, Asia", version="preliminary (PantropicalLandUse)",
         legend=COMMODITY_GAIN_LEGEND, doc_url=f"{GCS}/PantropicalLandUse/download.html"),
    dict(name="glad_landuse2024_asia", url=f"{GCS}/PantropicalLandUse/GLAD_LandUse_2024_ASIA.tif",
         product="GLAD Pan-tropical land use 2024, Asia", version="preliminary (PantropicalLandUse)",
         legend=LANDUSE_LEGEND, doc_url=f"{GCS}/TropicalLandUse/download.html",
         legend_note="Legend not on PantropicalLandUse/download.html; assumed equal to TropicalLandUse legend (adds 6 = Cropland, other)."),
    dict(name="glad_tch2020", url=f"{GCS}/TCH_2015_2024/TCH_filter_2020/10N_090E.tif",
         product="GLAD Tree Canopy Height 2020 (TCH_2015_2024, TCH_filter_2020)", version="TCH_2015_2024",
         legend=TCH_LEGEND, doc_url=None),
]
WORLDCOVER = dict(
    name="esa_worldcover2020_v100",
    tiles=["N06E096", "N06E099"],
    url_tmpl=WC + "/v100/2020/map/ESA_WorldCover_10m_2020_v100_{tile}_Map.tif",
    product="ESA WorldCover 10 m 2020 v100", version="v100 (2020)", legend=WORLDCOVER_LEGEND,
    doc_url="https://esa-worldcover.org/en/data-access",
    citation="Zanaga et al. 2021, ESA WorldCover 10 m 2020 v100, doi:10.5281/zenodo.5571936")


DOWNSTREAM_CODES = {
    "forest2020_trees_ge5m": {
        "glad_glclu2020_v2": "value in 27..48 (terra firma trees 5 m..>25 m) or 127..148 (wetland trees 5 m..>25 m)",
        "glad_glclu2020_v2_trees_ge3m": "25..48 or 125..148 (GLCLU's own tree-cover definition, height >= 3 m)",
        "esa_worldcover2020_v100": "class 10 (tree cover, no height threshold); 95 mangroves also woody",
        "esa_worldcover2020_v100_treecount9_hansengrid": "0..9 count of 10 m tree pixels; >=5 = majority tree (255 nodata)",
        "glad_tch2020": "height_m >= 5 (product stores 0 below 5 m; no values 1..4 seen in AOI)",
    },
    "plantation_commodity": {
        "glad_commodities2015_asia / 2024 / glad_landuse2024_asia": "4 = palm plantation (oil palm); 2 = plantation, other (rubber/fruit/other tree crops, not separated); 3 = timber plantation; 5 = mining; 6 = cropland other (landuse only)",
        "glad_commoditygain_asia": "11 = palm gain, 9 = plantation-other gain, 10 = timber gain (2015-2024)",
        "note": "No explicit rubber class exists in any reachable GLAD product; rubber falls under 2 'Plantation, other'.",
    },
    "glclu_change2000_2020_v2": "25..48 stable trees; 49..72 trees with previous disturbance (2020 height); 73..96 tree height gain; +100 for wetland; 240 short veg after tree loss; 245 cropland gain from trees; 251 built-up gain from trees",
}
NOT_DOWNLOADED = {
    "DIST-ALERT": dict(url=f"{GCS}/DIST-ALERT/GEN-DIST-STATUS/47.tif (also GEN-DIST-DATE/47.tif, GEN-DIST-CONF, GEN-ANOM-MAX, ...)",
                       format="one GeoTIFF per UTM zone (AOI = zone 47, EPSG:32647), 30 m, uint8 status (nodata 255) / int16 date (nodata -1), deflate, strip layout, no overviews; uploadDate.txt says updated 2026-10-03",
                       note="OPERA/GLAD DIST-ALERT (HLS-based vegetation disturbance). Not downloaded (optional). Would need reprojection UTM->EPSG:4326 for the Hansen grid."),
    "DIST-ANN": dict(url=f"{GCS}/DIST-ANN/2024/, {GCS}/DIST-ANN/2025/", format="annual disturbance composites, sub-folders per layer"),
    "GLADalert": dict(url=f"{GCS}/GLADalert/C2/2021..2025/", format="Landsat GLAD alerts, yearly folders (tile naming not checked)"),
    "S2alert": dict(url=f"{GCS}/S2alert/", format="Sentinel-2 GLAD alerts: alert, alertDate, forestMask, obsDate sub-folders"),
    "WDPA": dict(status="blocked by network policy (protectedplanet.net)"),
    "JRC GFC2020": dict(status="blocked by network policy (forobs.jrc.ec.europa.eu)"),
}


def ref_grid():
    with rasterio.open(REF) as r:
        return r.transform, r.width, r.height, r.crs, r.bounds


def write_tif(path, arr, transform, crs, nodata=None, tags=None, band_desc=None):
    prof = dict(PROFILE, height=arr.shape[-2], width=arr.shape[-1],
                count=1 if arr.ndim == 2 else arr.shape[0], dtype=arr.dtype, crs=crs,
                transform=transform, nodata=nodata)
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1) if arr.ndim == 2 else dst.write(arr)
        if tags:
            dst.update_tags(**{k: str(v) for k, v in tags.items()})
        if band_desc:
            dst.set_band_description(1, band_desc)


def counts(arr):
    v, c = np.unique(arr, return_counts=True)
    return {int(a): int(b) for a, b in zip(v, c)}


def fetch_glad30(spec, rt, rw, rh, rcrs, rb):
    url = spec["url"]
    with rasterio.open("/vsicurl/" + url) as src:
        inv = ~src.transform
        c0, r0 = inv * (rb.left, rb.top)
        col, row = int(round(c0)), int(round(r0))
        assert abs(c0 - col) < 1e-6 and abs(r0 - row) < 1e-6, "source not aligned with Hansen grid"
        win = Window(col, row, rw, rh)
        arr = src.read(1, window=win)
        wtrans = src.window_transform(win)
        nodata = src.nodata
        meta = dict(src_width=src.width, src_height=src.height, src_bounds=list(src.bounds),
                    src_res=list(src.res), src_dtype=src.dtypes[0], src_compression=str(src.compression),
                    src_nodata=nodata)
    tags = dict(source_url=url, product=spec["product"], version=spec["version"],
                fetched=dt.date.today().isoformat(), window=f"col={col},row={row},w={rw},h={rh}")
    clip = OUT / f"{spec['name']}_clip.tif"
    write_tif(clip, arr, wtrans, rcrs, nodata, tags)
    # Resample to the Hansen grid (nearest).  Identity here because aligned.
    dst = np.zeros((rh, rw), dtype=arr.dtype)
    reproject(arr, dst, src_transform=wtrans, src_crs=rcrs, dst_transform=rt, dst_crs=rcrs,
              resampling=Resampling.nearest, src_nodata=nodata, dst_nodata=nodata)
    identical = bool(np.array_equal(dst, arr))
    hg = OUT / f"{spec['name']}_hansengrid.tif"
    write_tif(hg, dst, rt, rcrs, nodata, dict(tags, resampling="nearest (identity: source on same 0.00025 deg lattice)"))
    return dict(meta, files=[clip.name, hg.name], window=tags["window"], hansengrid_identical_to_clip=identical,
                resampling="nearest; source lattice identical to Hansen grid so values are copied 1:1",
                value_counts_aoi=counts(arr))


def fetch_worldcover(spec, rt, rw, rh, rcrs, rb):
    f = 3  # 0.00025 / (1/12000)
    res = 1.0 / 12000.0
    W, H = rw * f, rh * f
    t10 = Affine(res, 0, rb.left, 0, -res, rb.top)
    mosaic = np.zeros((H, W), dtype=np.uint8)
    urls = []
    for tile in spec["tiles"]:
        url = spec["url_tmpl"].format(tile=tile)
        urls.append(url)
        with rasterio.open("/vsicurl/" + url) as src:
            assert abs(src.res[0] - res) < 1e-12
            # intersection in degrees
            left, right = max(rb.left, src.bounds.left), min(rb.right, src.bounds.right)
            bottom, top = max(rb.bottom, src.bounds.bottom), min(rb.top, src.bounds.top)
            if left >= right or bottom >= top:
                continue
            sc0 = int(round((left - src.bounds.left) / res)); sc1 = int(round((right - src.bounds.left) / res))
            sr0 = int(round((src.bounds.top - top) / res)); sr1 = int(round((src.bounds.top - bottom) / res))
            dc0 = int(round((left - rb.left) / res)); dr0 = int(round((rb.top - top) / res))
            a = src.read(1, window=Window(sc0, sr0, sc1 - sc0, sr1 - sr0))
            mosaic[dr0:dr0 + a.shape[0], dc0:dc0 + a.shape[1]] = a
    tags = dict(source_urls=" ".join(urls), product=spec["product"], version=spec["version"],
                fetched=dt.date.today().isoformat())
    clip = OUT / f"{spec['name']}_clip.tif"
    write_tif(clip, mosaic, t10, rcrs, 0, tags)

    # 3x3 block aggregation onto Hansen grid (exact nesting, no interpolation)
    blk = mosaic.reshape(rh, f, rw, f).transpose(0, 2, 1, 3).reshape(rh, rw, f * f)
    classes = np.array([10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100], dtype=np.uint8)
    cnt = np.stack([(blk == c).sum(-1) for c in classes], axis=-1)  # rh,rw,ncls
    valid = cnt.sum(-1)
    mode = classes[np.argmax(cnt, axis=-1)].astype(np.uint8)  # ties -> lowest code (tree cover wins)
    mode[valid == 0] = 0
    tree9 = (blk == 10).sum(-1).astype(np.uint8)  # 0..9 of the nine 10 m pixels
    tree9[valid == 0] = 255
    hg = OUT / f"{spec['name']}_hansengrid.tif"
    write_tif(hg, mode, rt, rcrs, 0, dict(tags, resampling="mode (majority) of the 3x3 nested 10 m pixels; ties -> lowest class code"))
    tf = OUT / f"{spec['name']}_treecount9_hansengrid.tif"
    write_tif(tf, tree9, rt, rcrs, 255, dict(tags, resampling="count of class-10 (tree cover) 10 m pixels among the 3x3 nested in each Hansen pixel; fraction = value/9"),
              band_desc="tree_cover_count_of_9")
    return dict(files=[clip.name, hg.name, tf.name], src_res=[res, res], clip_shape=[H, W],
                resampling="10 m pixels nest exactly 3x3 in each 0.00025 deg Hansen pixel: _hansengrid = majority class (ties to lowest code, so tree cover wins ties); _treecount9_hansengrid = number of tree-cover (10) pixels, 0-9 (255 nodata)",
                value_counts_aoi=counts(mosaic), value_counts_hansengrid_mode=counts(mode),
                treecount9_counts=counts(tree9))


# ---------------------------------------------------------------- Overture
PROTECT_KEYS = ("protect_class", "protection_title", "protect_id", "iucn_level", "designation")
STRICT_HINTS = ("national park", "national_park", "wildlife sanctuary", "wildlife_sanctuary",
                "nature reserve", "nature_reserve", "strict", "wilderness")


def fetch_overture(rb):
    import pyarrow.compute as pc  # noqa: F401
    import pyarrow.fs as pafs
    import pyarrow.parquet as pq
    from shapely import wkb
    from shapely.geometry import box, mapping

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    kw = dict(anonymous=True, region="us-west-2")
    if proxy:
        kw["proxy_options"] = proxy
    s3 = pafs.S3FileSystem(**kw)
    files = [f.path for f in s3.get_file_info(pafs.FileSelector(OVERTURE_PREFIX)) if f.path.endswith(".parquet")]
    aoi = box(rb.left, rb.bottom, rb.right, rb.top)
    feats, scanned_rg, hit_rg = [], 0, 0
    subtype_counts = {}
    for path in files:
        pf = pq.ParquetFile(path, filesystem=s3)
        md = pf.metadata
        names = pf.schema_arrow.names
        # locate bbox leaf columns in the flattened parquet schema
        leaf = {md.schema.column(i).path: i for i in range(md.num_columns)}
        ix = {k: leaf[f"bbox.{k}"] for k in ("xmin", "xmax", "ymin", "ymax")}
        rgs = []
        for g in range(md.num_row_groups):
            scanned_rg += 1
            rg = md.row_group(g)
            st = {k: rg.column(i).statistics for k, i in ix.items()}
            if any(s is None or not s.has_min_max for s in st.values()):
                rgs.append(g); continue
            if (st["xmin"].min <= rb.right and st["xmax"].max >= rb.left and
                    st["ymin"].min <= rb.top and st["ymax"].max >= rb.bottom):
                rgs.append(g)
        if not rgs:
            continue
        hit_rg += len(rgs)
        tbl = pf.read_row_groups(rgs, columns=[c for c in names])
        rows = tbl.to_pylist()
        for r in rows:
            b = r["bbox"]
            if b["xmin"] > rb.right or b["xmax"] < rb.left or b["ymin"] > rb.top or b["ymax"] < rb.bottom:
                continue
            g = wkb.loads(r["geometry"])
            if not g.intersects(aoi):
                continue
            subtype_counts[f"{r.get('subtype')}/{r.get('class')}"] = subtype_counts.get(f"{r.get('subtype')}/{r.get('class')}", 0) + 1
            tags = {}
            for t in (r.get("source_tags") or []):
                if isinstance(t, tuple):
                    tags[t[0]] = t[1]
                elif isinstance(t, dict) and "key" in t:
                    tags[t["key"]] = t["value"]
            if isinstance(r.get("source_tags"), dict):
                tags = r["source_tags"]
            is_prot = (r.get("subtype") == "protected" or
                       r.get("class") in ("national_park", "nature_reserve", "protected", "protected_landscape_seascape", "wilderness_area", "species_management_area", "state_park")
                       or tags.get("boundary") in ("protected_area", "national_park")
                       or tags.get("leisure") == "nature_reserve"
                       or any(k in tags for k in PROTECT_KEYS))
            if not is_prot:
                continue
            names_ = r.get("names") or {}
            primary = names_.get("primary") if isinstance(names_, dict) else None
            common = names_.get("common") if isinstance(names_, dict) else None
            if isinstance(common, list):
                common = {k: v for k, v in common}
            label = " ".join(str(x) for x in (r.get("class"), tags.get("protection_title"), tags.get("protect_class"), primary,
                                                   (common or {}).get("en") if isinstance(common, dict) else None) if x).lower()
            pclass = tags.get("protect_class")
            strict = (pclass in ("1", "1a", "1b", "2") or any(h in label for h in STRICT_HINTS)
                      or r.get("class") in ("national_park", "nature_reserve", "wilderness_area"))
            src = r.get("sources") or []
            props = dict(id=r["id"], subtype=r.get("subtype"), class_=r.get("class"), name=primary,
                         name_en=(common or {}).get("en") if isinstance(common, dict) else None,
                         wikidata=r.get("wikidata"), source_tags=tags,
                         sources=[{k: (str(v) if v is not None else None) for k, v in s.items()} for s in src],
                         overture_release=OVERTURE_RELEASE,
                         eudr_category="strict" if strict else "restricted_use",
                         area_in_aoi_km2=None, fully_inside_aoi=bool(aoi.contains(g)))
            # approx area in km2 (equirectangular at AOI latitude)
            inter = g.intersection(aoi)
            k = (111.32 ** 2) * np.cos(np.radians((rb.top + rb.bottom) / 2))
            props["area_in_aoi_km2"] = round(inter.area * k, 3)
            props["area_total_km2"] = round(g.area * k, 3)
            props["class"] = props.pop("class_")
            feats.append(dict(type="Feature", properties=props, geometry=mapping(g)))
    fc = dict(type="FeatureCollection", name="protected_areas_overture",
              crs=dict(type="name", properties=dict(name="urn:ogc:def:crs:OGC:1.3:CRS84")), features=feats)
    out = OUT / "protected_areas_overture.geojson"
    out.write_text(json.dumps(fc, default=str))
    return dict(files=[out.name], n_features=len(feats), parquet_files=len(files), row_groups_scanned=scanned_rg,
                row_groups_read=hit_rg, land_use_subtype_class_counts_in_aoi=subtype_counts,
                features=[{k: f["properties"][k] for k in ("id", "name", "name_en", "subtype", "class", "eudr_category", "area_in_aoi_km2", "area_total_km2", "fully_inside_aoi")} | {"protect_class": f["properties"]["source_tags"].get("protect_class"), "protection_title": f["properties"]["source_tags"].get("protection_title")} for f in feats])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--skip-overture", action="store_true")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    OUT.mkdir(parents=True, exist_ok=True)
    rt, rw, rh, rcrs, rb = ref_grid()
    lj = OUT / "layers.json"
    doc = json.loads(lj.read_text()) if lj.exists() else {}
    doc["reference_grid"] = dict(file=str(REF.relative_to(REPO)), crs="EPSG:4326", width=rw, height=rh,
                                 transform=list(rt)[:6], bounds=list(rb), res_deg=0.00025)
    doc["aoi_bbox"] = json.loads(BBOX.read_text())
    doc.setdefault("layers", {})
    doc["downstream_codes"] = DOWNSTREAM_CODES
    doc["not_downloaded"] = NOT_DOWNLOADED
    for spec in GLAD30:
        if only and spec["name"] not in only:
            continue
        print("fetching", spec["name"], flush=True)
        try:
            info = fetch_glad30(spec, rt, rw, rh, rcrs, rb)
            doc["layers"][spec["name"]] = {k: v for k, v in spec.items()} | info | dict(status="ok", fetched=dt.date.today().isoformat())
        except Exception as e:  # record failure, keep going
            print("  FAILED", e, file=sys.stderr)
            doc["layers"][spec["name"]] = {k: v for k, v in spec.items()} | dict(status=f"failed: {e}")
    if not only or WORLDCOVER["name"] in only:
        print("fetching", WORLDCOVER["name"], flush=True)
        try:
            info = fetch_worldcover(WORLDCOVER, rt, rw, rh, rcrs, rb)
            spec = {k: v for k, v in WORLDCOVER.items() if k != "url_tmpl"}
            spec["urls"] = [WORLDCOVER["url_tmpl"].format(tile=t) for t in WORLDCOVER["tiles"]]
            doc["layers"][WORLDCOVER["name"]] = spec | info | dict(status="ok", fetched=dt.date.today().isoformat())
        except Exception as e:
            print("  FAILED", e, file=sys.stderr)
            doc["layers"][WORLDCOVER["name"]] = dict(status=f"failed: {e}")
    if not a.skip_overture and (not only or "protected_areas_overture" in only):
        print("fetching overture protected areas", flush=True)
        try:
            info = fetch_overture(rb)
            doc["layers"]["protected_areas_overture"] = dict(
                product="Overture Maps base theme, type=land_use (OSM-derived)", version=OVERTURE_RELEASE,
                url=f"s3://{OVERTURE_PREFIX}", https_url=f"https://overturemaps-us-west-2.s3.amazonaws.com/release/{OVERTURE_RELEASE}/theme=base/type=land_use/",
                filter="intersects reference-grid bbox AND (subtype=protected OR protected class OR OSM boundary=protected_area/national_park OR leisure=nature_reserve OR protect_class/protection_title tag)",
                status="ok", fetched=dt.date.today().isoformat()) | info
        except Exception as e:
            print("  FAILED", e, file=sys.stderr)
            doc["layers"]["protected_areas_overture"] = dict(status=f"failed: {e}")
    lj.write_text(json.dumps(doc, indent=1, default=str))
    print("wrote", lj)


if __name__ == "__main__":
    main()
