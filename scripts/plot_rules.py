#!/usr/bin/env python3
"""Figure: the same cube answering three rules (docs/img/rules_compared.png)."""
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
import rasterio
from shapely.geometry import shape

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eudr import encoding as E, rules as R  # noqa: E402

s = rasterio.open("data/cube/cube.tif")
c = s.read(1)
b = s.bounds
ext = [b.left, b.right, b.bottom, b.top]
panels = [("Legacy: any Hansen loss 2021-2023", R.LEGACY.lost_after_cutoff(c) & ~E.flag(c, "not_land"), None),
          ("EUDR, Hansen only: canopy 2000 >10%, lost 2021-2025", R.EUDR_HANSEN_ONLY.deforested(c), R.EUDR_HANSEN_ONLY.forest_mask(c)),
          ("EUDR: + GLCLU 2020 forest, - 2015 plantations", R.EUDR.deforested(c), R.EUDR.forest_mask(c))]
plots = [shape(f["geometry"]) for f in json.load(open("tests/data/17plots-valid.geojson"))["features"]]
pas = [shape(f["geometry"]) for f in json.load(open("data/cube/strict_pa.geojson"))["features"]]
fig, ax = plt.subplots(1, 3, figsize=(16, 5.8), sharey=True)
for a, (t, d, f) in zip(ax, panels):
    img = np.ones(c.shape + (3,)) * 0.93
    if f is not None:
        img[f] = (0.55, 0.78, 0.55)
    img[E.flag(c, "not_land")] = (0.6, 0.75, 0.9)
    img[d] = (0.85, 0.15, 0.1)
    a.imshow(img, extent=ext, interpolation="nearest")
    for g in pas:
        for gg in getattr(g, "geoms", [g]):
            a.plot(*gg.exterior.xy, color="purple", lw=1)
    for g in plots:
        a.plot(*g.exterior.xy, color="black", lw=1.2)
    a.set_title(f"{t}\nflagged: {100 * d.mean():.1f}% of pixels", fontsize=10)
    a.set_xlim(ext[0], ext[1])
    a.set_ylim(ext[2], ext[3])
fig.text(0.5, 0.01, "red = loss / deforestation after 31 Dec 2020 | green = forest at cut-off under the rule | "
         "purple = strict protected areas (OSM) | black = the 17 plots", ha="center")
plt.tight_layout(rect=[0, 0.03, 1, 1])
plt.savefig("docs/img/rules_compared.png", dpi=110)
