#!/usr/bin/env python3
"""Report figures: the bit layout (docs/img/bit_layout.png) and the results
summary (docs/img/results.png). Numbers come from data/cube/evaluation.json and
data/cube/http_bench_17plots.json, so the figures follow a re-run."""
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
import rasterio

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eudr import encoding as E, rules as R  # noqa: E402

INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
ORIG, CUBE, OVER, DBYTE = "#9b9a94", "#2a78d6", "#1baf7a", "#eb6834"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2})
OUT = Path("docs/img")


# ---------------------------------------------------------------- bit layout
FIELDS = [  # (first bit, last bit, short label, colour, source layer, what is kept)
    (0, 4, "loss year", "#2a78d6", "Hansen lossyear\n2001-2025, 1 byte", "year, exact (0 = never)"),
    (5, 7, "canopy\nclass", "#6250d6", "Hansen treecover2000\n0-100 %, 1 byte", "8 bands, exact at\n10/15/20/25/30/50 %"),
    (8, 8, "forest\n2020", "#008300", "JRC GFC2020 V4\n10 m forest map", "yes if >= 50 % of\nthe 30 m square"),
    (9, 9, "trees\n>= 5 m", "#1baf7a", "GLAD tree height\n2020, metres", "yes / no"),
    (10, 10, "planta-\ntion", "#eda100", "GLAD Commodities\n2015, crop classes", "yes if oil palm\nor rubber/other"),
    (11, 11, "strict\nPA", "#e34948", "WDPA Oct 2026\npolygons", "yes if touched\n(+ exact polygons)"),
    (12, 12, "restr.\nuse", "#e87ba4", "restricted-use\npolygons", "yes if touched\n(+ exact polygons)"),
    (13, 13, "not\nland", "#52514e", "Hansen datamask\nland / water", "yes / no"),
    (14, 15, "spare", "#ffffff", None, None),
]


def bit_layout():
    fig, ax = plt.subplots(figsize=(14, 6.4))
    ax.set_xlim(-3.4, 16.1)
    ax.set_ylim(-4.0, 4.6)
    ax.axis("off")
    y0, h = 0, 1.0
    rows = [(3.55, "Source layers\n(6 rasters +\n2 polygon files)"), (0.5, "The cube:\none 16-bit number\nper 30 m square"),
            (-1.25, "What is kept")]
    for y, t in rows:
        ax.text(-3.3, y, t, ha="left", va="center", fontsize=9.5, weight="bold", color=INK)
    for i, (b0, b1, lab, col, src, kept) in enumerate(FIELDS):
        x0, w = b0, b1 - b0 + 1
        cx = x0 + w / 2
        spare = col == "#ffffff"
        ax.add_patch(FancyBboxPatch((x0 + 0.04, y0), w - 0.08, h, boxstyle="round,pad=0,rounding_size=0.08",
                                    fc=col, ec=GRID if spare else "white", lw=2, hatch="//" if spare else None))
        txtcol = MUTED if spare else ("#0b0b0b" if col in ("#eda100", "#e87ba4", "#1baf7a") else "white")
        ax.text(cx, y0 + h / 2, lab, ha="center", va="center", fontsize=9.5, color=txtcol, weight="bold")
        for b in range(b0, b1 + 1):
            ax.text(b + 0.5, y0 - 0.12, str(b), ha="center", va="top", fontsize=8, color=MUTED)
        if not src:
            continue
        narrow = w == 1
        ys = 3.95 if (not narrow or i % 2 == 0) else 2.85     # stagger 1-bit fields over two rows
        ax.text(cx, ys, src, ha="center", va="center", fontsize=8, color=INK2,
                bbox=dict(boxstyle="round,pad=0.3", fc="#f5f5f2", ec=GRID))
        ax.add_patch(FancyArrowPatch((cx, ys - 0.42), (cx, y0 + h + 0.06), arrowstyle="-|>", mutation_scale=9,
                                     color=MUTED, lw=1))
        yk = -0.95 if (not narrow or i % 2 == 0) else -1.75
        ax.plot([cx, cx], [-0.5, yk + 0.05], color=GRID, lw=1)
        ax.text(cx, yk, kept, ha="center", va="top", fontsize=7.8, color=INK2)

    # worked example: decode one real deforested pixel from the cube
    with rasterio.open("data/cube/cube.tif") as s:
        cube = s.read(1)
    rr, cc = np.nonzero(R.EUDR.deforested(cube))
    v = np.uint16(cube[rr[0], cc[0]])
    k = int(E.canopy(v))
    parts = [f"loss year {2000 + int(E.lossyear(v))}", f"canopy class {k} ({E.CANOPY_EDGES[k - 1]}-{E.CANOPY_EDGES[k]} %)"]
    for name, lab in (("forest2020", "forest 2020"), ("height5m", "trees >= 5 m"), ("plantation", "plantation"),
                      ("strict_pa", "strict PA"), ("restricted", "restricted"), ("not_land", "not land")):
        parts.append(f"{lab} {'yes' if E.flag(v, name) else 'no'}")
    ax.text(-3.3, -2.95, f"Example: one real square, stored value {int(v)}", fontsize=9.5, weight="bold", color=INK)
    ax.text(-3.3, -3.35, "  ·  ".join(parts), fontsize=8.8, color=INK2)
    ax.text(-3.3, -3.75, "EUDR rule at query time: forest at the end of 2020 (canopy > 10 %, EU map says forest, "
            "not a plantation) and lost after 2020  ->  deforested", fontsize=8.8, color=INK2)
    plt.tight_layout()
    plt.savefig(OUT / "bit_layout.png", dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------- results
def hbar(ax, labels, values, colors, fmt, title, note=None):
    y = np.arange(len(values))[::-1]
    ax.barh(y, values, color=colors, height=0.58, edgecolor="white", linewidth=2)
    vmax = max(values)
    for yi, v in zip(y, values):
        ax.text(v + vmax * 0.015, yi, fmt(v), va="center", fontsize=9.5, color=INK)
    ax.set_yticks(y, labels)
    ax.set_xlim(0, vmax * 1.28)
    ax.set_title(title, loc="left", fontsize=11, weight="bold", color=INK)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    if note:
        ax.text(0, -0.3, note, transform=ax.transAxes, fontsize=8.3, color=MUTED, va="top")


def results():
    ev = json.load(open("data/cube/evaluation.json"))
    hb = json.load(open("data/cube/http_bench_17plots.json"))
    st, q = ev["storage"], ev["query"]["eudr"]
    cube_all = st["cube_bytes"] + st["overview_bytes"] + st["cube_vectors_bytes"]
    dbyte = st["decision_byte_bytes"] + st["cube_vectors_bytes"]
    fig, axs = plt.subplots(2, 2, figsize=(13, 7.4))
    paths = ["Original layers", "Cube", "Cube + overview"]
    hbar(axs[0, 0], ["Original layers", "Cube + overview\n+ polygons", "EUDR decision byte\n+ polygons"],
         [st["original_needed_bytes"] / 1e6, cube_all / 1e6, dbyte / 1e6], [ORIG, CUBE, DBYTE],
         lambda v: f"{v:.2f} MB", "Storage for the 44 x 44 km area",
         f"{st['reduction_vs_original_needed']:.1f}x smaller; the decision byte answers the EUDR rule only")
    ms = [hb[k]["ms_per_plot"] for k in ("original", "cube", "cube+overview")]
    hbar(axs[0, 1], paths, ms, [ORIG, CUBE, OVER], lambda v: f"{v:.1f} ms",
         "Time per plot check over the internet",
         f"17 real plots, 40 ms simulated latency per request ({ms[0] / ms[2]:.1f}x faster with the overview)")
    req = [q["original"]["cog_range_requests"], q["cube"]["cog_range_requests"], q["cube+overview"]["cog_range_requests"]]
    hbar(axs[1, 0], paths, req, [ORIG, CUBE, OVER], lambda v: f"{v:,}",
         "File reads (tile requests) for 1,093 plots",
         f"EUDR rule; the overview alone answers {q['cube+overview']['plots_answered_from_overview']} plots")
    names = {"eudr": "EUDR", "eudr_patch05ha": "EUDR, >= 0.5 ha", "eudr_hansen_only": "Hansen only",
             "legacy": "Legacy (CSV rule)", "rspo": "RSPO 2005"}
    regs = list(ev["decisions"])
    match = [ev["decisions"][r]["cube+overview"]["all_outputs_match_pct"] for r in regs]
    ax = axs[1, 1]
    hbar(ax, [names[r] for r in regs], match, [CUBE] * len(regs), lambda v: f"{v:.0f} %",
         "Same answers as the original layers", "share of 1,093 plots where c2, d1 and d2 all match")
    ax.set_xlim(0, 125)
    ax.set_xticks([0, 25, 50, 75, 100])
    for yi, r in zip(np.arange(len(regs))[::-1], regs):
        n = ev["decisions"][r]["positives_original"]["c2_indication"]
        ax.text(3, yi, f"{n} plots flagged", va="center", fontsize=8.5, color="white")
    plt.tight_layout(h_pad=3.2, w_pad=3)
    plt.savefig(OUT / "results.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    bit_layout()
    results()
