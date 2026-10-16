#!/usr/bin/env python3
"""Aggregate a finer categorical raster onto the Hansen 30 m grid as a class percentage.

For rasters whose pixels nest exactly inside Hansen pixels (e.g. JRC GFC2020 or
ESA WorldCover at 1/12000 deg = 3 x 3 per Hansen pixel), every output pixel is
the share (0-100 %) of covered sub-pixels whose value is in --values. Hansen
pixels only partly covered by the input (clip edges) use the covered part;
pixels with no coverage are 255 (nodata).

Keeping the share instead of a 0/1 call lets the rule choose the threshold:
config "values": [50..100] means majority, [1..100] means any forest sub-pixel.

    python scripts/align_to_hansen.py --input data/layers/JRC_GFC2020_V4_N10_E90_clip.tif \
        --values 1 --out data/layers/jrc_gfc2020_v4_pct_hansengrid.tif
"""
import argparse

import numpy as np
import rasterio

REF = "data/clipped/Hansen_GFC-2025-v1.13_lossyear_10N_090E_clip.tif"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--values", type=int, nargs="+", required=True, help="class values to count")
    ap.add_argument("--ref", default=REF, help="raster defining the Hansen grid")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    with rasterio.open(a.ref) as r, rasterio.open(a.input) as s:
        if s.crs != r.crs:
            raise SystemExit("input CRS differs from the Hansen grid")
        f = r.res[0] / s.res[0]
        k = int(round(f))
        if abs(f - k) > 1e-6:
            raise SystemExit(f"input resolution is not an integer fraction of Hansen ({f:.4f})")
        # sub-pixel offset of the input origin inside the Hansen grid
        dx = (s.bounds.left - r.bounds.left) / s.res[0]
        dy = (r.bounds.top - s.bounds.top) / s.res[1]
        if abs(dx - round(dx)) > 1e-6 or abs(dy - round(dy)) > 1e-6:
            raise SystemExit("input grid is not aligned with Hansen sub-pixels")
        dx, dy = int(round(dx)), int(round(dy))
        src = s.read(1)
        nodata = s.nodata
        H, W = r.height, r.width
        big = np.zeros((H * k, W * k), np.uint8)       # 1 = in class
        cov = np.zeros((H * k, W * k), np.uint8)       # 1 = covered by input
        r0, c0 = max(dy, 0), max(dx, 0)
        sr0, sc0 = max(-dy, 0), max(-dx, 0)
        h = min(H * k - r0, src.shape[0] - sr0)
        w = min(W * k - c0, src.shape[1] - sc0)
        win = src[sr0:sr0 + h, sc0:sc0 + w]
        valid = np.ones_like(win, bool) if nodata is None else win != nodata
        big[r0:r0 + h, c0:c0 + w] = np.isin(win, a.values) & valid
        cov[r0:r0 + h, c0:c0 + w] = valid
        n_in = big.reshape(H, k, W, k).sum(axis=(1, 3), dtype=np.uint16)
        n_cov = cov.reshape(H, k, W, k).sum(axis=(1, 3), dtype=np.uint16)
        pct = np.full((H, W), 255, np.uint8)
        ok = n_cov > 0
        pct[ok] = np.round(100.0 * n_in[ok] / n_cov[ok]).astype(np.uint8)
        prof = dict(driver="GTiff", height=H, width=W, count=1, dtype="uint8", crs=r.crs,
                    transform=r.transform, nodata=255, tiled=True, blockxsize=256, blockysize=256,
                    compress="deflate", zlevel=9)
        with rasterio.open(a.out, "w", **prof) as d:
            d.write(pct, 1)
            d.update_tags(source=a.input, counted_values=",".join(map(str, a.values)),
                          meaning="percent of covered sub-pixels in class; 255 = no coverage")
    print(f"{a.out}: factor {k}, offset ({dx},{dy}) sub-px, uncovered px {int((~ok).sum())}, "
          f"share >=50%: {100 * float((pct[ok] >= 50).mean()):.2f}%, any: {100 * float((pct[ok] > 0).mean()):.2f}%")


if __name__ == "__main__":
    main()
