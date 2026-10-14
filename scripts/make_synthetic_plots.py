#!/usr/bin/env python3
"""Generate synthetic farm plots inside the clip area for decision-fidelity tests.

The 17 real plots have no protected-area overlap and few post-2020 loss
pixels, so they exercise only one side of each decision. This adds:
  - random plots anywhere on land (size 0.3-30 ha, log-uniform),
  - plots centred on post-2020 Hansen loss pixels (likely c2 positives),
  - plots straddling protected / restricted area boundaries (d1/d2 positives
    and near-misses), when --pa is given.
Shapes are irregular 5-8 vertex polygons, like hand-digitised plots.

    python scripts/make_synthetic_plots.py --n 600 --pa data/layers/protected_areas_overture.geojson
"""
import argparse
import json
import math

import numpy as np
import rasterio
from shapely.geometry import Polygon, mapping, shape

M_PER_DEG = 111_195.0


def blob(rng, lon, lat, area_ha):
    k = rng.integers(5, 9)
    ang = np.sort(rng.uniform(0, 2 * np.pi, k))
    rad = rng.uniform(0.6, 1.0, k)
    aspect = rng.uniform(0.5, 1.0)
    pts = np.c_[np.cos(ang) * rad, np.sin(ang) * rad * aspect]
    poly = Polygon(pts)
    scale_m = math.sqrt(area_ha * 1e4 / poly.area)
    rot = rng.uniform(0, np.pi)
    R = np.array([[math.cos(rot), -math.sin(rot)], [math.sin(rot), math.cos(rot)]])
    xy = (pts @ R.T) * scale_m
    lons = lon + xy[:, 0] / (M_PER_DEG * math.cos(math.radians(lat)))
    lats = lat + xy[:, 1] / M_PER_DEG
    return Polygon(np.c_[lons, lats])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lossyear", default="data/clipped/Hansen_GFC-2025-v1.13_lossyear_10N_090E_clip.tif")
    ap.add_argument("--datamask", default="data/clipped/Hansen_GFC-2025-v1.13_datamask_10N_090E_clip.tif")
    ap.add_argument("--pa", nargs="*", default=[], help="GeoJSON(s) of protected / restricted areas")
    ap.add_argument("--n", type=int, default=600, help="random plots")
    ap.add_argument("--n-loss", type=int, default=150, help="plots centred on post-2020 loss")
    ap.add_argument("--n-pa", type=int, default=150, help="plots on PA boundaries (if --pa)")
    ap.add_argument("--seed", type=int, default=2020)
    ap.add_argument("--out", default="tests/data/synthetic_plots.geojson")
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)

    with rasterio.open(a.lossyear) as s:
        ly, T = s.read(1), s.transform
        bounds = s.bounds
    with rasterio.open(a.datamask) as s:
        land = s.read(1) == 1
    margin = 0.01
    feats = []

    def size():
        return float(np.exp(rng.uniform(np.log(0.3), np.log(30))))

    def add(poly, kind):
        feats.append({"type": "Feature", "geometry": mapping(poly),
                      "properties": {"ProductionPlace": f"S-{len(feats) + 1:04d}", "kind": kind,
                                     "Area": round(poly.area * M_PER_DEG ** 2 * math.cos(math.radians(poly.centroid.y)) / 1e4, 2)}})

    while sum(f["properties"]["kind"] == "random" for f in feats) < a.n:
        lon = rng.uniform(bounds.left + margin, bounds.right - margin)
        lat = rng.uniform(bounds.bottom + margin, bounds.top - margin)
        r, c = rasterio.transform.rowcol(T, lon, lat)
        if land[r, c]:
            add(blob(rng, lon, lat, size()), "random")

    rr, cc = np.nonzero(ly >= 21)
    for i in rng.choice(len(rr), a.n_loss, replace=False):
        lon, lat = rasterio.transform.xy(T, rr[i], cc[i])
        add(blob(rng, lon + rng.normal(0, 1e-4), lat + rng.normal(0, 1e-4), size()), "post2020_loss")

    pas = []
    for p in a.pa:
        pas += [shape(f["geometry"]) for f in json.load(open(p))["features"]]
    pas = [g for g in pas if g.area > 0]
    if pas:
        for _ in range(a.n_pa):
            g = pas[rng.integers(len(pas))]
            ring = g.exterior if g.geom_type == "Polygon" else max(g.geoms, key=lambda x: x.area).exterior
            pt = ring.interpolate(rng.uniform(0, ring.length))
            # offset +-150 m across the boundary: mixes overlaps, touches and near-misses
            off = rng.normal(0, 150) / M_PER_DEG
            add(blob(rng, pt.x + off, pt.y + rng.normal(0, 150) / M_PER_DEG, size()), "pa_boundary")

    json.dump({"type": "FeatureCollection", "features": feats}, open(a.out, "w"))
    kinds = {}
    for f in feats:
        kinds[f["properties"]["kind"]] = kinds.get(f["properties"]["kind"], 0) + 1
    print(a.out, kinds)


if __name__ == "__main__":
    main()
