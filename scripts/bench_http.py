#!/usr/bin/env python3
"""Cloud-style query benchmark: serve the COGs over HTTP with injected latency.

Local-disk timings hide what dominates in production: every COG tile read is
an HTTP range request to object storage (~20-80 ms first byte). This serves
the repo over a local threaded HTTP server that sleeps --latency-ms per request
and counts requests, then runs both query paths through GDAL /vsicurl/.

    python scripts/bench_http.py --config eudr/config.json --cube-dir data/cube \
        --plots tests/data/17plots-valid.geojson --latency-ms 40
"""
import argparse
import json
import os
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import rasterio
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eudr import rules as R  # noqa: E402
from eudr.query import CubeLayers, OriginalLayers, VectorIndex  # noqa: E402

COUNT = {"req": 0, "bytes": 0}
LOCK = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    latency = 0.0

    def log_message(self, *a):
        pass

    def send_head(self):
        time.sleep(self.latency)
        with LOCK:
            COUNT["req"] += 1
        return super().send_head()

    # minimal Range support (http.server has none)
    def do_GET(self):
        rng = self.headers.get("Range")
        if not rng:
            return super().do_GET()
        time.sleep(self.latency)
        path = self.translate_path(self.path)
        size = os.path.getsize(path)
        a, b = rng.split("=")[1].split("-")
        a, b = int(a), min(int(b) if b else size - 1, size - 1)
        with open(path, "rb") as f:
            f.seek(a)
            data = f.read(b - a + 1)
        with LOCK:
            COUNT["req"] += 1
            COUNT["bytes"] += len(data)
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="eudr/config.json")
    ap.add_argument("--cube-dir", default="data/cube")
    ap.add_argument("--plots", nargs="+", default=["tests/data/17plots-valid.geojson"])
    ap.add_argument("--regime", default="eudr")
    ap.add_argument("--latency-ms", type=float, default=40)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    root = Path.cwd()
    Handler.latency = a.latency_ms / 1000
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory="/"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"/vsicurl/http://127.0.0.1:{srv.server_address[1]}/"
    os.environ.update(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                      GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", VSI_CACHE="FALSE",
                      CPL_VSIL_CURL_NON_CACHED="/vsicurl/", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(k, None)

    cfg = json.load(open(a.config))
    cd = Path(a.cube_dir)
    rule = R.REGIMES[a.regime]
    plots = [shape(f["geometry"]) for p in a.plots for f in json.load(open(p))["features"]]
    strict = VectorIndex(json.load(open(cd / "strict_pa.geojson"))["features"])
    restr = VectorIndex(json.load(open(cd / "restricted.geojson"))["features"])

    def url(p):
        return base + str(Path(p).resolve()).lstrip("/")

    orig = {k: url(v) for k, v in cfg["hansen"].items()}
    for k in ("forest2020", "plantation", "height"):
        if cfg.get(k):
            orig[k] = url(cfg[k]["path"])
            if "values" in cfg[k]:
                orig[k + "_values"] = cfg[k]["values"]

    out = {"latency_ms": a.latency_ms, "plots": len(plots), "regime": a.regime}
    engines = {
        "original": lambda: OriginalLayers(orig, strict, restr),
        "cube": lambda: CubeLayers(url(cd / "cube.tif"), strict, restr),
        "cube+overview": lambda: CubeLayers(url(cd / "cube.tif"), strict, restr, overview_path=url(cd / "overview.tif")),
    }
    for name, mk in engines.items():
        with rasterio.Env():
            COUNT.update(req=0, bytes=0)
            t0 = time.perf_counter()
            eng = mk()                      # opening = header requests
            t_open = time.perf_counter() - t0
            open_req = COUNT["req"]
            for g in plots:
                eng.query(rule, g)
            dt = time.perf_counter() - t0
            eng.close()
        out[name] = {"open_requests": open_req, "open_s": round(t_open, 3),
                     "query_requests": COUNT["req"] - open_req, "bytes": COUNT["bytes"],
                     "total_s": round(dt, 3), "ms_per_plot": round(1000 * (dt - t_open) / len(plots), 2)}
    srv.shutdown()
    print(json.dumps(out, indent=1))
    if a.out:
        json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
