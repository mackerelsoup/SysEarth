#!/usr/bin/env python3
"""Build the SysEarth EUDR cube from the original layers.

Reads eudr/config.json (layer paths and class codes), aligns everything on the
Hansen 30 m grid and writes to --out-dir:
  cube.tif            uint16 bit-packed cube (COG, 256 px tiles)
  overview.tif        uint16 16x16-block conservative summary
  decision_eudr.tif   uint8 single-regime decision byte
  strict_pa.geojson / restricted.geojson   exact vectors used to refine PA hits

    python scripts/build_cube.py --config eudr/config.json --out-dir data/cube
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eudr import encoding as E  # noqa: E402
from eudr.rules import EUDR  # noqa: E402

COG = dict(driver="GTiff", tiled=True, blockxsize=256, blockysize=256, compress="deflate",
           zlevel=9, interleave="band")


def read_on_grid(path, ref, resampling=Resampling.nearest):
    with rasterio.open(path) as s:
        if s.crs == ref.crs and s.transform == ref.transform and s.shape == ref.shape:
            return s.read(1)
        out = np.zeros(ref.shape, s.dtypes[0])
        reproject(rasterio.band(s, 1), out, dst_transform=ref.transform, dst_crs=ref.crs,
                  resampling=resampling)
        return out


def load_vectors(spec):
    if not spec:
        return []
    feats = json.load(open(spec["path"]))["features"]
    key, vals = spec.get("field"), spec.get("values")
    if key:
        feats = [f for f in feats if f["properties"].get(key) in vals]
    return feats


def burn(feats, ref):
    if not feats:
        return np.zeros(ref.shape, bool)
    return rasterize([(shape(f["geometry"]), 1) for f in feats], out_shape=ref.shape,
                     transform=ref.transform, all_touched=True).astype(bool)


def write(path, arr, ref, transform=None, **kw):
    prof = dict(COG, height=arr.shape[0], width=arr.shape[1], count=1, dtype=arr.dtype.name,
                crs=ref.crs, transform=transform or ref.transform, **kw)
    with rasterio.open(path, "w", **prof) as d:
        d.write(arr, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="eudr/config.json")
    ap.add_argument("--out-dir", default="data/cube")
    ap.add_argument("--block", type=int, default=16)
    a = ap.parse_args()
    cfg = json.load(open(a.config))
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()

    ref = rasterio.open(cfg["hansen"]["lossyear"])
    ly = ref.read(1)
    tc = read_on_grid(cfg["hansen"]["treecover2000"], ref)
    dm = read_on_grid(cfg["hansen"]["datamask"], ref)

    layers = {}
    for key in ("forest2020", "plantation"):
        spec = cfg.get(key)
        if spec:
            layers[key] = np.isin(read_on_grid(spec["path"], ref), spec["values"])
    if cfg.get("height"):
        layers["height5m"] = read_on_grid(cfg["height"]["path"], ref) >= cfg["height"].get("min", 5)

    strict = load_vectors(cfg.get("strict_pa"))
    restricted = load_vectors(cfg.get("restricted"))
    for name, feats in (("strict_pa", strict), ("restricted", restricted)):
        json.dump({"type": "FeatureCollection", "features": feats}, open(out / f"{name}.geojson", "w"))

    cube = E.encode(ly, tc, dm, strict_pa=burn(strict, ref), restricted=burn(restricted, ref), **layers)
    write(out / "cube.tif", cube, ref)

    ov = E.overview(cube, a.block)
    ov_t = ref.transform * rasterio.Affine.scale(a.block)
    write(out / "overview.tif", ov, ref, transform=ov_t)

    write(out / "decision_eudr.tif", E.decision_byte(cube, EUDR), ref)

    meta = {"config": cfg, "block": a.block, "shape": list(cube.shape),
            "layers_present": sorted(layers), "n_strict": len(strict), "n_restricted": len(restricted),
            "build_seconds": round(time.perf_counter() - t0, 2)}
    json.dump(meta, open(out / "cube_meta.json", "w"), indent=1)
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
