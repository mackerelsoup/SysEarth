#!/usr/bin/env python3
"""Fetch WDPA protected-area polygons for the clip area.

Protected Planet (the live WDPA download) is not reachable from the build
sandbox. UNEP-WCMC's public archive bucket s3://wdpa (eu-west-1) is, and it
holds full monthly releases up to 2016. The newest readable one is
WDPA_Mar2016_Public (the Oct 2016 object is access-denied). All protected
areas in this clip were designated 1980-1998, so the boundaries are expected
to be stable, but swap in a current release when one can be downloaded.

Downloads the ~1 GB zip into --work (its own directory), extracts only the
file geodatabase, reads polygons and points inside clip_bbox.json, adds an
eudr_category (IUCN Ia/Ib/II -> strict, other designated sites -> restricted,
'Proposed' dropped) and writes data/layers/wdpa_mar2016_aoi.geojson.

    pip install pyogrio geopandas
    python scripts/fetch_wdpa.py --work /tmp/wdpa_dl
"""
import argparse
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pyogrio

URL = "https://wdpa.s3.eu-west-1.amazonaws.com/2016/WDPA_Mar2016_Public.zip"
GDB = "WDPA_Mar2016_Public/WDPA_Mar2016_Public.gdb"
COLS = ["WDPAID", "NAME", "ORIG_NAME", "DESIG_ENG", "IUCN_CAT", "STATUS", "STATUS_YR",
        "REP_AREA", "GIS_AREA", "MARINE"]
STRICT_IUCN = {"Ia", "Ib", "II"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", default="data/clipped/clip_bbox.json")
    ap.add_argument("--work", default="/tmp/wdpa_dl")
    ap.add_argument("--out", default="data/layers/wdpa_mar2016_aoi.geojson")
    ap.add_argument("--keep", action="store_true", help="keep the extracted geodatabase")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    zpath = work / "wdpa.zip"
    if not (work / GDB).exists():
        subprocess.run(["curl", "-sSf", "-o", str(zpath), URL], check=True)
        with zipfile.ZipFile(zpath) as z:
            names = [n for n in z.namelist() if n.startswith(GDB + "/") and not n.endswith("/")]
            if any(".." in n or n.startswith("/") for n in names):
                raise SystemExit("unexpected path in archive")
            z.extractall(work, members=names)
        zpath.unlink()

    bb = json.load(open(a.bbox))
    box = (bb["west"], bb["south"], bb["east"], bb["north"])
    poly = pyogrio.read_dataframe(str(work / GDB), layer="WDPA_poly_Mar2016", bbox=box, columns=COLS)
    pts = pyogrio.read_dataframe(str(work / GDB), layer="WDPA_point_Mar2016", bbox=box, columns=COLS)
    if len(pts):
        # point-only sites have no boundary: approximate as a circle of the reported area
        import math
        r_deg = (pts["REP_AREA"].clip(lower=0.01) * 1e6 / math.pi) ** 0.5 / 111195.0
        pts["geometry"] = [g.buffer(r) for g, r in zip(pts.geometry, r_deg)]
        pts["approx_boundary"] = True
        poly = poly.assign(approx_boundary=False)
        poly = type(poly)(__import__("pandas").concat([poly, pts], ignore_index=True), crs=poly.crs)
    poly = poly[poly["STATUS"] != "Proposed"]
    poly["eudr_category"] = ["strict" if c in STRICT_IUCN else "restricted" for c in poly["IUCN_CAT"]]
    poly["source"] = "WDPA Mar 2016 public release (UNEP-WCMC archive bucket s3://wdpa)"
    poly.to_file(a.out, driver="GeoJSON")
    print(poly.drop(columns="geometry").to_string())
    if not a.keep:
        shutil.rmtree(work / GDB.split("/")[0], ignore_errors=True)


if __name__ == "__main__":
    main()
