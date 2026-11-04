from pathlib import Path
import hashlib
import json
import statistics
import sys

import numpy as np
import xarray as xr

from benchmark_gpu import CASES, ROOT

directory = ROOT / "results" / sys.argv[1]
raw = json.loads((directory / "summary.json").read_text())
for row in raw:
    row["measurement_job"] = directory.name
previous = ROOT / "results" / sys.argv[2] if len(sys.argv) > 2 else directory
if previous != directory:
    replaced = {r["label"] for r in raw}
    old = json.loads((previous / "summary.json").read_text())
    for row in old:
        row["measurement_job"] = previous.name
    raw += [r for r in old if r["label"] not in replaced]
labels = [label for label in CASES if any(r["label"] == label for r in raw)]
assert all(r["step_exit_code"] == 0 for r in raw)
rows = {}
for label in labels:
    groups = [r for r in raw if r["label"] == label]
    assert len(groups) >= 2
    rows[label] = {
        "process_groups": len(groups),
        "measurement_job": groups[0]["measurement_job"],
        "seconds": statistics.median(r["median_s"] for r in groups),
        "process_median_range_s": [min(r["median_s"] for r in groups), max(r["median_s"] for r in groups)],
        "columns_per_second": 16960 / statistics.median(r["median_s"] for r in groups),
        "sum_host_peak_rss_MiB": max(r["sum_host_peak_rss_MiB"] for r in groups),
        "sum_gpu_peak_MiB": max(r["sum_gpu_peak_MiB"] for r in groups),
        "gpu_rank_peak_MiB": [max(r["ranks"][rank]["gpu_peak_MiB"] for r in groups)
                              for rank in range(len(groups[0]["ranks"]))],
        "binary_sha256": groups[0]["ranks"][0]["binary_sha256"],
    }

def output(label, group=0):
    paths = sorted(directory.glob(f"{label}-g{group}-r*/output.nc"))
    if not paths:
        paths = sorted(previous.glob(f"{label}-g{group}-r*/output.nc"))
    datasets = [xr.open_dataset(p).load() for p in paths]
    for ds in datasets:
        ds.close()
    return (datasets[0] if len(datasets) == 1 else xr.concat(
        datasets, dim="column", data_vars="minimal", coords="minimal", compat="override"))

def compare(ref, actual):
    result = {"variables_match": set(ref.data_vars) == set(actual.data_vars),
              "all_finite": True, "errors": {}}
    for name in set(ref.data_vars) & set(actual.data_vars):
        a, b = ref[name], actual[name]
        assert a.dims == b.dims and a.shape == b.shape, name
        if not np.issubdtype(a.dtype, np.number):
            continue
        result["all_finite"] &= bool(np.isfinite(a).all() and np.isfinite(b).all())
        result["errors"][name] = float(abs(a.astype(float)-b.astype(float)).max())
    result["max_lw_W_m2"] = max(v for n, v in result["errors"].items() if "lw" in n and "derivative" not in n)
    result["max_sw_W_m2"] = max(v for n, v in result["errors"].items() if "sw" in n)
    result["all_numeric_exact"] = result["variables_match"] and result["all_finite"] and not any(result["errors"].values())
    return result

native_reference = "main-cpu" if "main-cpu" in labels else "omp-fork-large-block"
matched_reference = ("optimized-cpu-matched" if "optimized-cpu-matched" in labels else
                     "current-cpu-matched" if "current-cpu-matched" in labels else "omp-fork-large-block")
main = output(native_reference)
cpu_matched = output(matched_reference)
for label in labels:
    reference = main if label in ("main-cpu", "current-cpu", "optimized-cpu") else cpu_matched
    rows[label]["output_reference"] = native_reference if label in ("main-cpu", "current-cpu", "optimized-cpu") else matched_reference
    rows[label]["output_comparison"] = compare(reference, output(label))
    c = rows[label]["output_comparison"]
    rows[label]["reference_flux_tolerance_pass"] = (
        c["all_finite"] and c["variables_match"] and c["max_lw_W_m2"] <= 0.001 and c["max_sw_W_m2"] <= 0.1)
    rows[label]["usable_performance_reference"] = label != "omp-tiled"
    groups = sorted({r["group"] for r in raw if r["label"] == label})
    checks = [compare(output(label), output(label, group)) for group in groups[1:]]
    rows[label]["repeat_output_comparison"] = checks[0]
    rows[label]["all_process_group_outputs_exact"] = all(c["all_numeric_exact"] for c in checks)
    assert rows[label]["all_process_group_outputs_exact"], label
    assert rows[label]["output_comparison"]["all_finite"]

comparisons = {}
for base, current in (("main-cpu", "current-cpu"), ("master-acc", "current-acc"),
                      ("acc-opt", "optimized-acc"),
                      ("acc-opt-large-block", "optimized-acc-tiled"),
                      ("main-cpu", "optimized-cpu"),
                      ("master-omp", "current-omp"), ("acc-tiled", "current-acc-tiled"),
                      ("omp-tiled", "current-omp-tiled"),
                      ("omp-fork-large-block", "current-omp-tiled"),
                      ("acc-tiled", "optimized-acc-tiled"),
                      ("omp-fork-large-block", "optimized-omp-tiled"),
                      ("master-omp", "optimized-omp"),
                      ("optimized-omp", "optimized-acc"),
                      ("optimized-omp-tiled", "optimized-acc-tiled"),
                      ("current-acc", "optimized-acc"),
                      ("current-acc-tiled", "optimized-acc-tiled"),
                      ("current-omp-tiled", "optimized-omp-tiled")):
    if base not in rows or current not in rows:
        continue
    a, b = rows[base], rows[current]
    if a["measurement_job"] != b["measurement_job"]:
        # Older rows provide numerical references and historical context,
        # but paired timing ratios require the same allocation.
        continue
    name = current + "_vs_" + base
    comparisons[name] = {
        "time_ratio": b["seconds"] / a["seconds"],
        "time_change_percent": 100 * (b["seconds"] / a["seconds"] - 1),
        "host_peak_change_percent": 100 * (b["sum_host_peak_rss_MiB"] / a["sum_host_peak_rss_MiB"] - 1),
        "gpu_peak_change_MiB": b["sum_gpu_peak_MiB"] - a["sum_gpu_peak_MiB"],
        "direct_output_comparison": compare(output(base), output(current)),
    }
    c = comparisons[name]["direct_output_comparison"]
    comparisons[name]["paired_flux_comparison_within_tolerance"] = (
        c["all_finite"] and c["variables_match"] and c["max_lw_W_m2"] <= 0.001 and c["max_sw_W_m2"] <= 0.1)
    if current.startswith("optimized"):
        assert comparisons[name]["paired_flux_comparison_within_tolerance"], name

result = {"job": sys.argv[1], "rows": rows, "comparisons": comparisons}
(directory / "assessment.json").write_text(json.dumps(result, indent=2) + "\n")
for label, row in rows.items():
    c = row["output_comparison"]
    print(label, f'{row["seconds"]:.6f}s', f'RSS {row["sum_host_peak_rss_MiB"]:.1f} MiB',
          f'GPU {row["sum_gpu_peak_MiB"]:.0f} MiB',
          f'Reference error LW={c["max_lw_W_m2"]:.8g}, SW={c["max_sw_W_m2"]:.8g}',
          "repeat exact", row["repeat_output_comparison"]["all_numeric_exact"])
for label, c in comparisons.items():
    print(label, f'{c["time_ratio"]:.4f}x', f'{c["time_change_percent"]:+.2f}%',
          "direct output LW", c["direct_output_comparison"]["max_lw_W_m2"],
          "SW", c["direct_output_comparison"]["max_sw_W_m2"])
