"""Plot-level queries: against the original layers and against the cube.

Both paths share plot_mask(), so the polygon-to-pixel rule is identical:
pixel centres inside the polygon ('centre', GDAL default) or every touched
pixel ('all_touched'). A plot too small to contain any pixel centre falls back
to all_touched so it is never silently empty.

The original path reads every source raster window and evaluates the rule on
raw values; protected areas are tested exactly against the vector polygons.
The cube path reads one uint16 window (optionally after a block-overview
check) and uses the vector polygons only to confirm a raster PA hit.
"""
import time
from dataclasses import dataclass, field

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import from_bounds, Window
from shapely.geometry import shape
from shapely.strtree import STRtree

from . import encoding as E


def plot_window(src, geom, pad=1):
    w = from_bounds(*geom.bounds, transform=src.transform)
    c0, r0 = int(np.floor(w.col_off)) - pad, int(np.floor(w.row_off)) - pad
    c1 = int(np.ceil(w.col_off + w.width)) + pad
    r1 = int(np.ceil(w.row_off + w.height)) + pad
    c0, r0 = max(c0, 0), max(r0, 0)
    c1, r1 = min(c1, src.width), min(r1, src.height)
    return Window(c0, r0, c1 - c0, r1 - r0)


def plot_mask(geom, window, transform, pixel_rule):
    t = rasterio.windows.transform(window, transform)
    shp = (int(window.height), int(window.width))
    m = rasterize([(geom, 1)], out_shape=shp, transform=t, all_touched=(pixel_rule == "all_touched"))
    if not m.any():
        m = rasterize([(geom, 1)], out_shape=shp, transform=t, all_touched=True)
    return m.astype(bool)


class VectorIndex:
    """Exact polygon-overlap test for protected / restricted areas."""

    def __init__(self, features):
        self.geoms = [shape(f["geometry"]) for f in features]
        self.tree = STRtree(self.geoms) if self.geoms else None

    def intersects(self, geom):
        if self.tree is None:
            return False
        for i in self.tree.query(geom, predicate="intersects"):
            if self.geoms[int(i)].intersection(geom).area > 0:
                return True
        return False


@dataclass
class Stats:
    reads: int = 0          # raster window reads
    px_read: int = 0        # pixels decoded
    skipped: int = 0        # plots answered from the overview alone
    seconds: float = 0.0
    extra: dict = field(default_factory=dict)


class OriginalLayers:
    """Original per-source rasters on the Hansen grid, evaluated on raw values."""

    def __init__(self, paths, strict_pa, restricted):
        # paths: dict with keys lossyear, treecover2000, datamask, and optional
        # forest2020 (+ forest2020_values), height (metres), plantation (+ plantation_values)
        self.paths = paths
        self.src = {k: rasterio.open(v) for k, v in paths.items() if k.endswith(("year", "2000", "mask", "2020", "height", "plantation"))}
        self.forest_values = paths.get("forest2020_values")
        self.plant_values = paths.get("plantation_values")
        self.strict = strict_pa
        self.restricted = restricted
        self.stats = Stats()

    def close(self):
        for s in self.src.values():
            s.close()

    def _read(self, key, win):
        self.stats.reads += 1
        a = self.src[key].read(1, window=win)
        self.stats.px_read += a.size
        return a

    def query(self, rule, geom):
        t0 = time.perf_counter()
        ref = self.src["lossyear"]
        win = plot_window(ref, geom)
        m = plot_mask(geom, win, ref.transform, rule.pixel_rule)
        ly = self._read("lossyear", win)[m]
        land = self._read("datamask", win)[m] == 1
        after = (ly >= rule.loss_lo) & (ly <= rule.loss_hi)
        if rule.require_forest:
            tc = self._read("treecover2000", win)[m]
            forest = (tc > rule.canopy_threshold) & ((ly == 0) | (ly >= rule.loss_lo)) & land
            # a layer the deployment lacks behaves like an all-zero flag, as in the cube
            if rule.use_baseline:
                forest &= ("forest2020" in self.src) and np.isin(self._read("forest2020", win)[m], self.forest_values)
            if rule.use_height:
                forest &= ("height" in self.src) and self._read("height", win)[m] >= 5
            if rule.exclude_plantation and "plantation" in self.src:
                forest &= ~np.isin(self._read("plantation", win)[m], self.plant_values)
        else:
            forest = land
        n_def = int((forest & after).sum())
        out = {
            "n_px": int(m.sum()),
            "deforested_px": n_def,
            "c2_indication": n_def >= rule.min_px,
            "d1_strict_pa": self.strict.intersects(geom),
            "d2_restricted": self.restricted.intersects(geom),
        }
        self.stats.seconds += time.perf_counter() - t0
        return out


class CubeLayers:
    """The single-band uint16 cube (+ optional block overview) plus PA vectors."""

    def __init__(self, cube_path, strict_pa, restricted, overview_path=None, refine_pa=True):
        self.src = rasterio.open(cube_path)
        self.ov = None
        if overview_path:
            with rasterio.open(overview_path) as o:
                self.ov = o.read(1)       # tiny: 1/256 of the cube, kept in memory
                self.block = int(round(o.transform.a / self.src.transform.a))
        self.strict = strict_pa
        self.restricted = restricted
        self.refine_pa = refine_pa
        self.stats = Stats()

    def close(self):
        self.src.close()

    def query(self, rule, geom):
        t0 = time.perf_counter()
        win = plot_window(self.src, geom)
        if self.ov is not None:
            b = self.block
            r0, c0 = int(win.row_off) // b, int(win.col_off) // b
            r1, c1 = -(-int(win.row_off + win.height) // b), -(-int(win.col_off + win.width) // b)
            ov = self.ov[r0:r1, c0:c1]
            if not (rule.overview_maybe_deforested(ov).any()
                    or E.flag(ov, "strict_pa").any() or E.flag(ov, "restricted").any()):
                self.stats.skipped += 1
                self.stats.seconds += time.perf_counter() - t0
                return {"n_px": None, "deforested_px": 0, "c2_indication": False,
                        "d1_strict_pa": False, "d2_restricted": False}
        m = plot_mask(geom, win, self.src.transform, rule.pixel_rule)
        cube = self.src.read(1, window=win)
        self.stats.reads += 1
        self.stats.px_read += cube.size
        px = cube[m]
        n_def = int(rule.deforested(px).sum())
        # raster PA bits are burned all_touched, so a miss is a guaranteed miss;
        # a hit is confirmed against the vector (exact) unless refine_pa is off.
        hit_s = hit_r = False
        if (cube & (E.STRICT_PA | E.RESTRICTED)).any():   # rasterize the touch mask only if needed
            touched = rasterio_touch(cube, geom, win, self.src.transform)
            hit_s = bool(E.flag(touched, "strict_pa").any())
            hit_r = bool(E.flag(touched, "restricted").any())
        if self.refine_pa:
            hit_s = hit_s and self.strict.intersects(geom)
            hit_r = hit_r and self.restricted.intersects(geom)
        out = {"n_px": int(m.sum()), "deforested_px": n_def, "c2_indication": n_def >= rule.min_px,
               "d1_strict_pa": hit_s, "d2_restricted": hit_r}
        self.stats.seconds += time.perf_counter() - t0
        return out


def rasterio_touch(cube, geom, win, transform):
    """Cube values of every pixel the plot touches (for the PA prefilter)."""
    return cube[plot_mask(geom, win, transform, "all_touched")]
