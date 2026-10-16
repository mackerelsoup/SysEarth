#!/usr/bin/env python3
"""Evaluate the cube against the original layers.

Reports (JSON to --out, Markdown summary to stdout):
  storage      bytes of original layers vs cube / decision byte / overview / vectors
  query        per-plot latency, window reads and 256-px tile requests (= HTTP range
               requests against COGs in object storage), with and without overview
  decisions    per regime: % plots where c2, d1, d2 all match (+ deforested pixel counts)
  pixels       per-field pixel fidelity of the cube vs the aligned source layers
  report_csv   agreement of each regime with the existing due-diligence CSV (c2)

    python scripts/evaluate.py --config eudr/config.json --cube-dir data/cube \
        --plots tests/data/17plots-valid.geojson tests/data/synthetic_plots.geojson
"""
import argparse
import csv
import json
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eudr import encoding as E  # noqa: E402
from eudr import rules as R  # noqa: E402
from eudr.query import CubeLayers, OriginalLayers, VectorIndex, plot_window  # noqa: E402

DECISION_KEYS = ("c2_indication", "d1_strict_pa", "d2_restricted")


def size(p):
    return os.path.getsize(p) if p and os.path.exists(p) else 0


def tiles_touched(win, block=256):
    r0, c0 = int(win.row_off) // block, int(win.col_off) // block
    r1 = (int(win.row_off + win.height) - 1) // block
    c1 = (int(win.col_off + win.width) - 1) // block
    return (r1 - r0 + 1) * (c1 - c0 + 1)


def files_needed(rule, cfg):
    f = ["lossyear", "datamask"]
    if rule.require_forest:
        f.append("treecover2000")
        if rule.use_baseline and cfg.get("forest2020"):
            f.append("forest2020")
        if rule.use_height and cfg.get("height"):
            f.append("height")
        if rule.exclude_plantation and cfg.get("plantation"):
            f.append("plantation")
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="eudr/config.json")
    ap.add_argument("--cube-dir", default="data/cube")
    ap.add_argument("--plots", nargs="+", default=["tests/data/17plots-valid.geojson"])
    ap.add_argument("--csv", default="tests/data/17plots-valid.csv")
    ap.add_argument("--regimes", nargs="+", default=list(R.REGIMES))
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--out", default="data/cube/evaluation.json")
    a = ap.parse_args()
    cfg = json.load(open(a.config))
    cd = Path(a.cube_dir)
    res = {}

    # ---------------- plots ----------------
    plots = []
    for p in a.plots:
        for f in json.load(open(p))["features"]:
            plots.append((f["properties"].get("ProductionPlace"), f["properties"].get("kind", "real"),
                          shape(f["geometry"])))

    # ---------------- storage ----------------
    hansen_all = {k: size(v) for k, v in cfg["hansen"].items()}
    gain = cfg.get("hansen_unused", {})
    anc = {k: size(cfg[k]["path"]) for k in ("forest2020", "plantation", "height") if cfg.get(k)}
    anc_native = {k: size(cfg[k].get("native", cfg[k]["path"])) for k in ("forest2020", "plantation", "height") if cfg.get(k)}
    vec = {k: size(cfg[k]["path"]) for k in ("strict_pa", "restricted") if cfg.get(k)}
    vec_unique = sum(set(vec.values())) if vec else 0
    cube_v = size(cd / "strict_pa.geojson") + size(cd / "restricted.geojson")
    orig_aligned = sum(hansen_all.values()) + sum(anc.values()) + vec_unique
    with rasterio.open(cd / "cube.tif") as s:
        npx = s.width * s.height
    res["storage"] = {
        "pixels": npx,
        "hansen_used_bytes": hansen_all,
        "hansen_dropped_bytes": {k: size(v) for k, v in gain.items()},
        "ancillary_on_grid_bytes": anc,
        "ancillary_native_bytes": anc_native,
        "pa_vector_bytes": vec,
        "original_total_bytes": orig_aligned + sum(size(v) for v in gain.values()),
        "original_needed_bytes": orig_aligned,
        "original_native_total_bytes": sum(hansen_all.values()) + sum(size(v) for v in gain.values())
                                        + sum(anc_native.values()) + vec_unique,
        "original_uncompressed_bytes": npx * (len(hansen_all) + len(gain) + len(anc)),
        "cube_bytes": size(cd / "cube.tif"),
        "cube_uncompressed_bytes": npx * 2,
        "overview_bytes": size(cd / "overview.tif"),
        "decision_byte_bytes": size(cd / "decision_eudr.tif"),
        "cube_vectors_bytes": cube_v,
    }
    st = res["storage"]
    st["reduction_vs_original_needed"] = round(st["original_needed_bytes"] / (st["cube_bytes"] + cube_v), 2)
    st["reduction_vs_original_total"] = round(st["original_total_bytes"] / (st["cube_bytes"] + cube_v), 2)
    st["reduction_vs_native_total"] = round(st["original_native_total_bytes"] / (st["cube_bytes"] + cube_v), 2)
    st["overview_extra_pct"] = round(100 * st["overview_bytes"] / st["cube_bytes"], 2)

    # ---------------- decisions + query ----------------
    strict = VectorIndex(json.load(open(cd / "strict_pa.geojson"))["features"])
    restr = VectorIndex(json.load(open(cd / "restricted.geojson"))["features"])
    orig_paths = dict(cfg["hansen"])
    for k in ("forest2020", "plantation", "height"):
        if cfg.get(k):
            orig_paths[k] = cfg[k]["path"]
            if "values" in cfg[k]:
                orig_paths[k + "_values"] = cfg[k]["values"]

    res["decisions"], res["query"] = {}, {}
    csv_rows = {r["a3_plot_id"]: r for r in csv.DictReader(open(a.csv, encoding="utf-8-sig"))} if a.csv else {}
    res["report_csv"] = {}
    for rn in a.regimes:
        rule = R.REGIMES[rn]
        runs = {"original": [], "cube": [], "cube+overview": []}
        answers = {}
        for rep in range(a.repeat):
            o = OriginalLayers(orig_paths, strict, restr)
            c = CubeLayers(cd / "cube.tif", strict, restr)
            co = CubeLayers(cd / "cube.tif", strict, restr, overview_path=cd / "overview.tif")
            for name, eng in (("original", o), ("cube", c), ("cube+overview", co)):
                lat = []
                for pid, kind, g in plots:
                    t0 = time.perf_counter()
                    ans = eng.query(rule, g)
                    lat.append(time.perf_counter() - t0)
                    if rep == 0:
                        answers.setdefault(name, []).append(ans)
                runs[name].append({"lat": lat, "reads": eng.stats.reads, "px": eng.stats.px_read,
                                   "skipped": eng.stats.skipped})
                eng.close()
        n = len(plots)
        nfiles = len(files_needed(rule, cfg))
        with rasterio.open(cd / "cube.tif") as s:
            tiles = [tiles_touched(plot_window(s, g, rule.window_pad)) for _, _, g in plots]
        skipped = runs["cube+overview"][0]["skipped"]
        q = {}
        for name in runs:
            per = [statistics.median(x) for x in zip(*[r["lat"] for r in runs[name]])]
            q[name] = {"median_ms_per_plot": round(1000 * statistics.median(per), 3),
                       "mean_ms_per_plot": round(1000 * statistics.mean(per), 3),
                       "window_reads": runs[name][0]["reads"], "pixels_decoded": runs[name][0]["px"],
                       "plots_answered_from_overview": runs[name][0]["skipped"]}
        q["original"]["cog_range_requests"] = sum(tiles) * nfiles
        q["cube"]["cog_range_requests"] = sum(tiles)
        q["cube+overview"]["cog_range_requests"] = sum(
            t for t, a2 in zip(tiles, answers["cube+overview"]) if a2["n_px"] is not None)
        q["files_per_query_original"] = nfiles
        res["query"][rn] = q

        dec = {}
        for name in ("cube", "cube+overview"):
            match = [all(x[k] == y[k] for k in DECISION_KEYS) for x, y in zip(answers["original"], answers[name])]
            px_match = [x["deforested_px"] == y["deforested_px"] for x, y in zip(answers["original"], answers[name])]
            dec[name] = {"plots": n, "all_outputs_match_pct": round(100 * sum(match) / n, 3),
                         "deforested_px_match_pct": round(100 * sum(px_match) / n, 3),
                         "mismatches": [plots[i][0] for i, m in enumerate(match) if not m][:20]}
        orig = answers["original"]
        dec["positives_original"] = {k: sum(x[k] for x in orig) for k in DECISION_KEYS}
        by_kind = {}
        for (pid, kind, _), x in zip(plots, orig):
            d = by_kind.setdefault(kind, {"n": 0, **{k: 0 for k in DECISION_KEYS}})
            d["n"] += 1
            for k in DECISION_KEYS:
                d[k] += int(x[k])
        dec["positives_by_kind"] = by_kind
        res["decisions"][rn] = dec

        # vs the existing report (real plots only)
        if csv_rows:
            agree, rows = 0, []
            for (pid, kind, _), x in zip(plots, orig):
                if pid in csv_rows:
                    flagged = csv_rows[pid]["c2_deforestation_spatial_analysis_result"].startswith("b.")
                    agree += flagged == x["c2_indication"]
                    rows.append((pid, flagged, x["c2_indication"], x["deforested_px"]))
            res["report_csv"][rn] = {"c2_agree": agree, "of": len(rows),
                                     "rows": [{"plot": r[0], "csv_flag": r[1], "ours": r[2], "def_px": r[3]} for r in rows]}

    # ---------------- pixel fidelity ----------------
    with rasterio.open(cd / "cube.tif") as s:
        cube = s.read(1)
        ref = s
    pf = {}
    ly = rasterio.open(cfg["hansen"]["lossyear"]).read(1)
    tc = rasterio.open(cfg["hansen"]["treecover2000"]).read(1)
    dm = rasterio.open(cfg["hansen"]["datamask"]).read(1)
    pf["lossyear_exact_pct"] = round(100 * float((E.lossyear(cube) == ly).mean()), 4)
    pf["canopy_threshold_exact"] = {t: round(100 * float(((E.canopy(cube) >= E.canopy_class_above(t)) == (tc > t)).mean()), 4)
                                    for t in E.CANOPY_THRESHOLDS}
    err = E.CANOPY_MID[E.canopy(cube)] - tc
    pf["canopy_pct_mae"] = round(float(np.abs(err).mean()), 3)
    pf["canopy_pct_rmse"] = round(float(np.sqrt((err ** 2).mean())), 3)
    pf["canopy_class_exact_pct"] = round(100 * float((E.canopy_class(tc) == E.canopy(cube)).mean()), 4)
    pf["not_land_exact_pct"] = round(100 * float((E.flag(cube, "not_land") == (dm != 1)).mean()), 4)
    for k, flagname in (("forest2020", "forest2020"), ("plantation", "plantation")):
        if cfg.get(k):
            src = np.isin(rasterio.open(cfg[k]["path"]).read(1), cfg[k]["values"])
            pf[f"{k}_exact_pct"] = round(100 * float((E.flag(cube, flagname) == src).mean()), 4)
            pf[f"{k}_share_pct"] = round(100 * float(src.mean()), 3)
    if cfg.get("height"):
        src = rasterio.open(cfg["height"]["path"]).read(1) >= cfg["height"].get("min", 5)
        pf["height5m_exact_pct"] = round(100 * float((E.flag(cube, "height5m") == src).mean()), 4)
    # PA raster vs pixel-centre rasterisation of the exact vectors
    from rasterio.features import rasterize
    for name in ("strict_pa", "restricted"):
        feats = json.load(open(cd / f"{name}.geojson"))["features"]
        flagged = E.flag(cube, name)
        if feats:
            centre = rasterize([(shape(f["geometry"]), 1) for f in feats], out_shape=cube.shape,
                               transform=ref.transform).astype(bool)
            pf[f"{name}_px_flagged"] = int(flagged.sum())
            pf[f"{name}_px_centre_inside"] = int(centre.sum())
            pf[f"{name}_missed_px"] = int((centre & ~flagged).sum())
        else:
            pf[f"{name}_px_flagged"] = 0
    rule = R.EUDR
    db = rasterio.open(cd / "decision_eudr.tif").read(1)
    pf["decision_byte_matches_cube_pct"] = round(100 * float((((db & E.D_DEFOREST) != 0) == rule.deforested(cube)).mean()), 4)
    pf["eudr_forest2020_share_pct"] = round(100 * float(rule.forest_mask(cube).mean()), 3)
    pf["eudr_deforested_share_pct"] = round(100 * float(rule.deforested(cube).mean()), 3)
    pf["hansen_only_forest2020_share_pct"] = round(100 * float(R.EUDR_HANSEN_ONLY.forest_mask(cube).mean()), 3)
    pf["hansen_only_deforested_share_pct"] = round(100 * float(R.EUDR_HANSEN_ONLY.deforested(cube).mean()), 3)
    res["pixels"] = pf

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(a.out, "w"), indent=1, default=str)
    print(json.dumps({"storage": res["storage"], "pixels": res["pixels"]}, indent=1, default=str))
    for rn in a.regimes:
        print(rn, json.dumps({"dec": {k: v for k, v in res["decisions"][rn].items() if k != "positives_by_kind"},
                              "q": res["query"][rn],
                              "csv": {k: v for k, v in res["report_csv"].get(rn, {}).items() if k != "rows"}}, default=str))


if __name__ == "__main__":
    main()
