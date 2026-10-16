#!/usr/bin/env python3
"""Clip WDPA protected areas to the clip area (polygons + point-only sites).

Two ways to run it:

1. With a WDPA file geodatabase you already have (any release). Polygon and
   point layers are found by geometry type, so layer names do not matter:

       python scripts/fetch_wdpa.py --gdb C:/data/WDPA_Oct2026_Public.gdb --out data/layers/wdpa_aoi.geojson

2. Without one: download the newest release readable from UNEP-WCMC's public
   archive bucket s3://wdpa (WDPA_Mar2016_Public, ~1 GB; Protected Planet
   itself is blocked from the build sandbox):

       python scripts/fetch_wdpa.py --work /tmp/wdpa_dl

Point-only sites have no boundary; they are approximated as a circle of their
reported area (REP_AREA) and marked approx_boundary=True. 'Proposed' sites are
dropped. eudr_category: IUCN Ia/Ib/II -> strict (d1), everything else -> restricted (d2).

    pip install pyogrio geopandas
"""
import argparse
import json
import math
import shutil
import subprocess
import zipfile
from pathlib import Path

import pandas as pd
import pyogrio

URL = "https://wdpa.s3.eu-west-1.amazonaws.com/2016/WDPA_Mar2016_Public.zip"
ARCHIVE_GDB = "WDPA_Mar2016_Public/WDPA_Mar2016_Public.gdb"
COLS = ["SITE_ID", "SITE_PID", "SITE_TYPE", "WDPAID", "WDPA_PID", "NAME", "NAME_ENG", "ORIG_NAME", "DESIG_ENG", "IUCN_CAT", "STATUS",
        "STATUS_YR", "REP_AREA", "GIS_AREA", "MARINE", "ISO3", "PA_DEF"]
STRICT_IUCN = {"Ia", "Ib", "II"}
M_PER_DEG = 111195.0


def download_archive(work):
    work.mkdir(parents=True, exist_ok=True)
    gdb = work / ARCHIVE_GDB
    if gdb.exists():
        return gdb
    zpath = work / "wdpa.zip"
    subprocess.run(["curl", "-sSf", "-o", str(zpath), URL], check=True)
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if n.startswith(ARCHIVE_GDB + "/") and not n.endswith("/")]
        if any(".." in n or n.startswith("/") for n in names):
            raise SystemExit("unexpected path in archive")
        z.extractall(work, members=names)
    zpath.unlink()
    return gdb


def read_layers(gdb, names, box):
    dfs = []
    for n in names:
        fields = set(pyogrio.read_info(str(gdb), layer=n)["fields"])
        cols = [c for c in COLS if c in fields]
        df = pyogrio.read_dataframe(str(gdb), layer=n, bbox=box, columns=cols)
        if len(df):
            dfs.append(df.assign(layer=n))
    if not dfs:
        return None
    out = pd.concat(dfs, ignore_index=True)
    return type(dfs[0])(out, geometry="geometry", crs=dfs[0].crs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gdb", help="local WDPA .gdb to clip (skips the download)")
    ap.add_argument("--bbox", default="data/clipped/clip_bbox.json")
    ap.add_argument("--work", default="/tmp/wdpa_dl", help="download folder when --gdb is not given")
    ap.add_argument("--out", default="data/layers/wdpa_mar2016_aoi.geojson")
    ap.add_argument("--keep", action="store_true", help="keep the downloaded geodatabase")
    a = ap.parse_args()

    gdb = Path(a.gdb) if a.gdb else download_archive(Path(a.work))
    bb = json.load(open(a.bbox))
    box = (bb["west"], bb["south"], bb["east"], bb["north"])

    layers = pyogrio.list_layers(str(gdb))
    poly_l = [str(n) for n, t in layers if t and "Polygon" in str(t)]
    pt_l = [str(n) for n, t in layers if t and "Point" in str(t)]
    print("polygon layers:", poly_l, "| point layers:", pt_l)

    poly = read_layers(gdb, poly_l, box)
    pts = read_layers(gdb, pt_l, box)
    if poly is None and pts is None:
        raise SystemExit("no WDPA features inside the clip area")
    parts = []
    if poly is not None:
        parts.append(poly.assign(approx_boundary=False))
    if pts is not None:
        r_deg = (pts["REP_AREA"].fillna(0).clip(lower=0.01) * 1e6 / math.pi) ** 0.5 / M_PER_DEG
        pts["geometry"] = [g.buffer(r) for g, r in zip(pts.geometry, r_deg)]
        parts.append(pts.assign(approx_boundary=True))
    out = pd.concat(parts, ignore_index=True)
    out = type(parts[0])(out, geometry="geometry", crs=parts[0].crs)
    if "STATUS" in out:
        out = out[out["STATUS"] != "Proposed"]
    out["eudr_category"] = ["strict" if c in STRICT_IUCN else "restricted" for c in out["IUCN_CAT"]]
    out["source"] = f"WDPA {gdb.name}" if a.gdb else "WDPA Mar 2016 public release (UNEP-WCMC archive bucket s3://wdpa)"
    if out.crs is not None and out.crs.to_epsg() != 4326:
        out = out.to_crs(4326)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_file(a.out, driver="GeoJSON")
    print(out.drop(columns="geometry").to_string())
    print(f"\nwrote {len(out)} features to {a.out}")
    if not a.gdb and not a.keep:
        shutil.rmtree(Path(a.work) / ARCHIVE_GDB.split("/")[0], ignore_errors=True)


if __name__ == "__main__":
    main()
