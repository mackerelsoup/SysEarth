#!/usr/bin/env python3
"""Clip GeoTIFFs to a square area around the vertices of a GeoJSON file.

The area is built in two steps:
  1. take the bounding box of every polygon vertex and grow its shorter side
     so the box is square (centred on the original box);
  2. grow all four sides by --buffer-km (default 5 km).
So the outermost vertices are at least 5 km from the clip edge (more along
the shorter original dimension).

Usage:
    pip install rasterio
    python scripts/clip_rasters.py \
        --geojson tests/data/17plots-valid.geojson \
        --input path/to/hansen_tiles/ other.tif \
        --out-dir data/clipped

Rasters are read one window at a time, so the full tile is never loaded.
Inputs can be .tif files or directories (searched for *.tif).
Outputs are written as <name>_clip.tif with the source dtype, nodata, CRS and
band descriptions preserved. A clip_bbox.json describing the area is written
to --out-dir.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import rasterio
from rasterio.errors import WindowError
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

KM_PER_DEG_LAT = 2 * math.pi * 6371.0088 / 360  # ~111.195 km on a spherical earth


def iter_coords(obj):
    """Yield (lon, lat) for every position in a GeoJSON geometry/feature tree."""
    if isinstance(obj, dict):
        t = obj.get("type")
        if t == "FeatureCollection":
            for f in obj["features"]:
                yield from iter_coords(f)
        elif t == "Feature":
            yield from iter_coords(obj["geometry"])
        elif t == "GeometryCollection":
            for g in obj["geometries"]:
                yield from iter_coords(g)
        else:
            yield from iter_coords(obj["coordinates"])
    elif obj and isinstance(obj[0], (int, float)):
        yield obj[0], obj[1]
    else:
        for item in obj:
            yield from iter_coords(item)


def clip_bounds(geojson_path, buffer_km, square=True):
    """Return (west, south, east, north) in degrees."""
    with open(geojson_path) as fh:
        pts = list(iter_coords(json.load(fh)))
    if not pts:
        sys.exit(f"no coordinates found in {geojson_path}")
    lons, lats = zip(*pts)
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)

    lat_c = (south + north) / 2
    km_per_deg_lon = KM_PER_DEG_LAT * math.cos(math.radians(lat_c))
    width_km = (east - west) * km_per_deg_lon
    height_km = (north - south) * KM_PER_DEG_LAT

    if square:
        side_km = max(width_km, height_km)
        lon_c = (west + east) / 2
        half_lon = side_km / km_per_deg_lon / 2
        half_lat = side_km / KM_PER_DEG_LAT / 2
        west, east = lon_c - half_lon, lon_c + half_lon
        south, north = lat_c - half_lat, lat_c + half_lat

    # Buffer in degrees. Use the latitude furthest from the equator for the
    # longitude conversion so the leeway is never below buffer_km.
    d_lat = buffer_km / KM_PER_DEG_LAT
    worst_lat = max(abs(south - d_lat), abs(north + d_lat))
    d_lon = buffer_km / (KM_PER_DEG_LAT * math.cos(math.radians(worst_lat)))
    return west - d_lon, south - d_lat, east + d_lon, north + d_lat


def find_tifs(inputs):
    found = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            found += sorted(p.glob("*.tif")) + sorted(p.glob("*.tiff"))
        elif p.is_file():
            found.append(p)
        else:
            print(f"warning: {item} not found, skipping", file=sys.stderr)
    return found


def clip_one(src_path, bbox_ll, out_dir):
    with rasterio.open(src_path) as src:
        if src.crs is None:
            print(f"skip {src_path.name}: no CRS", file=sys.stderr)
            return None
        bounds = bbox_ll
        if src.crs.to_epsg() != 4326:
            bounds = transform_bounds("EPSG:4326", src.crs, *bbox_ll, densify_pts=21)

        win = from_bounds(*bounds, transform=src.transform)
        # round outwards to whole pixels so the target area is always covered
        col0, row0 = math.floor(win.col_off), math.floor(win.row_off)
        col1 = math.ceil(win.col_off + win.width)
        row1 = math.ceil(win.row_off + win.height)
        full = Window(col0, row0, col1 - col0, row1 - row0)
        try:
            win = full.intersection(Window(0, 0, src.width, src.height))
        except WindowError:
            print(f"skip {src_path.name}: area is outside the raster extent", file=sys.stderr)
            return None
        partial = (win.width, win.height) != (full.width, full.height)

        profile = src.profile.copy()
        profile.update(
            driver="GTiff",
            width=int(win.width),
            height=int(win.height),
            transform=src.window_transform(win),
            compress="deflate",
            tiled=True,
            blockxsize=256,
            blockysize=256,
        )
        out_path = out_dir / f"{src_path.stem}_clip.tif"
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(src.read(window=win))
            for i, desc in enumerate(src.descriptions, start=1):
                if desc:
                    dst.set_band_description(i, desc)
            dst.update_tags(**src.tags())
    return out_path, partial


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--geojson", required=True, help="GeoJSON whose vertices define the area (lon/lat, WGS84)")
    ap.add_argument("--input", required=True, nargs="+", help="GeoTIFF files and/or directories containing them")
    ap.add_argument("--out-dir", required=True, help="where clipped files are written")
    ap.add_argument("--buffer-km", type=float, default=5.0, help="leeway added on every side (default 5)")
    ap.add_argument("--no-square", action="store_true", help="plain bounding box + buffer instead of a square")
    args = ap.parse_args()

    bbox = clip_bounds(args.geojson, args.buffer_km, square=not args.no_square)
    west, south, east, north = bbox
    lat_c = (south + north) / 2
    print(
        f"clip area (EPSG:4326): W {west:.5f}  S {south:.5f}  E {east:.5f}  N {north:.5f}\n"
        f"  size ~ {(east - west) * KM_PER_DEG_LAT * math.cos(math.radians(lat_c)):.1f} km x "
        f"{(north - south) * KM_PER_DEG_LAT:.1f} km"
    )

    tifs = find_tifs(args.input)
    if not tifs:
        sys.exit("no .tif files found")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for tif in tifs:
        res = clip_one(tif, bbox, out_dir)
        if res is None:
            continue
        out_path, partial = res
        note = "  (WARNING: raster only partly covers the area)" if partial else ""
        print(f"{tif.name} -> {out_path} [{out_path.stat().st_size / 1e6:.2f} MB]{note}")
        written.append(out_path.name)

    (out_dir / "clip_bbox.json").write_text(
        json.dumps(
            {
                "crs": "EPSG:4326",
                "west": west,
                "south": south,
                "east": east,
                "north": north,
                "buffer_km": args.buffer_km,
                "square": not args.no_square,
                "source_geojson": str(args.geojson),
                "files": written,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
