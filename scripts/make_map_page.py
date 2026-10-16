#!/usr/bin/env python3
"""Build the interactive cube explorer page (docs/map/cube_explorer.html).

Embeds the cube itself (raw uint16, zlib, base64; the page decodes it with the
browser's DecompressionStream), the PA polygons, the 17 real plots with their
answers from the cube query path under every regime, the CSV answers, and the
regime parameters. The page applies the rules to the cube pixels in JS.

    python scripts/make_map_page.py [--template docs/map/template.html] [--out docs/map/cube_explorer.html]
"""
import argparse
import base64
import csv
import json
import sys
import zlib
from dataclasses import asdict
from pathlib import Path

import numpy as np
import rasterio
from shapely.geometry import mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eudr import rules as R  # noqa: E402
from eudr.query import CubeLayers, VectorIndex  # noqa: E402

CD = Path("data/cube")


def rounded(geom, nd=5):
    g = mapping(shape(geom).simplify(0.00002))
    def rr(c):
        return [rr(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], nd), round(c[1], nd)]
    return {"type": g["type"], "coordinates": rr(g["coordinates"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default="docs/map/template.html")
    ap.add_argument("--out", default="docs/map/cube_explorer.html")
    a = ap.parse_args()

    with rasterio.open(CD / "cube.tif") as s:
        cube = s.read(1)
        b = s.bounds
    raw = cube.astype("<u2").tobytes()
    blob = base64.b64encode(zlib.compress(raw, 9)).decode()

    strict_f = json.load(open(CD / "strict_pa.geojson"))["features"]
    restr_f = json.load(open(CD / "restricted.geojson"))["features"]
    strict, restr = VectorIndex(strict_f), VectorIndex(restr_f)
    feats = json.load(open("tests/data/17plots-valid.geojson"))["features"]
    rows = {r["a3_plot_id"]: r for r in csv.DictReader(open("tests/data/17plots-valid.csv", encoding="utf-8-sig"))}
    ev = json.load(open(CD / "evaluation.json"))

    plots = []
    for f in feats:
        pid = f["properties"]["ProductionPlace"]
        r = rows.get(pid, {})
        plots.append({"id": pid, "geom": rounded(f["geometry"]),
                      "area_ha": round(float(f["properties"].get("Area") or 0), 2),
                      "csv": {"c2": r.get("c2_deforestation_spatial_analysis_result", "").startswith("b."),
                              "d1": r.get("d1_strict_protected_area_spatial_analysis_result", "").startswith("b."),
                              "d2": r.get("d2_restricted_use_area_spatial_analysis_result", "").startswith("b.")},
                      "ans": {}})
    regimes = {}
    for rn, rule in R.REGIMES.items():
        eng = CubeLayers(CD / "cube.tif", strict, restr)
        for p, f in zip(plots, feats):
            x = eng.query(rule, shape(f["geometry"]))
            p["ans"][rn] = {"c2": bool(x["c2_indication"]), "px": x["deforested_px"],
                            "d1": bool(x["d1_strict_pa"]), "d2": bool(x["d2_restricted"])}
        eng.close()
        d = ev["decisions"][rn]
        regimes[rn] = {**{k: v for k, v in asdict(rule).items()},
                       "c2_of_1093": d["positives_original"]["c2_indication"],
                       "match_pct": d["cube+overview"]["all_outputs_match_pct"]}

    def pa(fs, label):
        return [{"name": " ".join(x for x in (f["properties"].get("NAME_ENG"), f["properties"].get("DESIG_ENG")) if x) or label,
                 "iucn": f["properties"].get("IUCN_CAT", ""), "geom": rounded(f["geometry"])} for f in fs]

    st = ev["storage"]
    data = {"w": int(cube.shape[1]), "h": int(cube.shape[0]),
            "bounds": [b.left, b.bottom, b.right, b.top], "cube": blob,
            "strict": pa(strict_f, "Strict protected area"), "restricted": pa(restr_f, "Restricted-use area (synthetic)"),
            "plots": plots, "regimes": regimes,
            "storage": {"original": st["original_needed_bytes"], "cube": st["cube_bytes"] + st["overview_bytes"]
                        + st["cube_vectors_bytes"], "dbyte": st["decision_byte_bytes"] + st["cube_vectors_bytes"]}}
    html = Path(a.template).read_text()
    marker = "/*__DATA__*/null"
    assert html.count(marker) == 1
    Path(a.out).write_text(html.replace(marker, json.dumps(data, separators=(",", ":"))))
    print(a.out, Path(a.out).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
