"""Assess the block/tile sweep without mixing allocations in timing ratios.

Usage: python3 test/ifs/benchmark_gpu_matrix_analyse.py JOB_ID [JOB_ID ...]
Results remain per allocation. A base is usable only after numerical validation.
"""
from itertools import product
from functools import lru_cache
import hashlib
import json
import math
import statistics
import sys

import numpy as np
import xarray as xr

from benchmark_gpu import ROOT, CASES, MATRIX, dump


@lru_cache(maxsize=4)
def outputs(directory, label, group):
    datasets = []
    for path in sorted(directory.glob(f"{label}-g{group}-r*/output.nc")):
        with xr.open_dataset(path) as data:
            datasets.append(data.load())
    assert datasets, (label, group)
    return datasets[0] if len(datasets) == 1 else xr.concat(
        datasets, dim="column", data_vars="minimal", coords="minimal", compat="override")


def compare(a, b):
    assert set(a.data_vars) == set(b.data_vars)
    errors = {}
    finite = True
    for name in a.data_vars:
        x, y = a[name], b[name]
        assert x.dims == y.dims and x.shape == y.shape, name
        if np.issubdtype(x.dtype, np.number):
            finite &= bool(np.isfinite(x).all() and np.isfinite(y).all())
            errors[name] = float(abs(x.astype(float) - y.astype(float)).max())
    lw = max(v for n, v in errors.items() if "lw" in n and "derivative" not in n)
    sw = max(v for n, v in errors.items() if "sw" in n)
    other = max((v for n, v in errors.items() if "lw" not in n and "sw" not in n), default=0)
    return {"finite": finite, "max_lw": lw, "max_sw": sw,
            "exact": finite and not any(errors.values()),
            "within_tolerance": finite and lw <= 0.001 and sw <= 0.1 and other == 0}


def paired(a, b):
    """Current/base ratio and exact one-sided paired sign-flip tests on log times.

    Five independent process-group pairs, not the 25 correlated inner iterations,
    are the statistical units. Report slowdown if p<0.05; this test's limited
    resolution does not establish non-inferiority or universal equivalence.
    """
    assert a.keys() == b.keys() and len(a) == 5
    differences = [math.log(b[g] / a[g]) for g in sorted(a)]
    observed = statistics.mean(differences)
    distribution = [statistics.mean(s*d for s, d in zip(signs, differences))
                    for signs in product((-1, 1), repeat=len(differences))]
    p_slower = sum(value >= observed - 1e-15 for value in distribution) / len(distribution)
    p_faster = sum(value <= observed + 1e-15 for value in distribution) / len(distribution)
    return {"time_ratio": statistics.median(b.values()) / statistics.median(a.values()),
            "paired_group_ratios": [b[g] / a[g] for g in sorted(a)],
            "p_slower": p_slower, "p_faster": p_faster,
            "resolved_slowdown": observed > 0 and p_slower < 0.05,
            "resolved_speedup": observed < 0 and p_faster < 0.05}


def analyse(job):
    directory = ROOT / "results" / job
    raw = json.loads((directory / "summary.json").read_text())
    grouped = {}
    failures = []
    for row in raw:
        if row["step_exit_code"]:
            failures.append({"label": row["label"], "group": row["group"]})
        else:
            grouped.setdefault(row["label"], []).append(row)
    checkpoint = directory / "matrix-rows.json"
    digest = hashlib.sha256((directory / "summary.json").read_bytes()).hexdigest()
    saved = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    rows = saved.get("rows", {}) if saved.get("summary_sha256") == digest else {}
    for label, groups in grouped.items():
        if len(groups) != 5:
            continue
        if label in rows:
            rows[label]["group_seconds"] = {int(g): t for g, t in rows[label]["group_seconds"].items()}
            continue
        spec = MATRIX[label]
        output = outputs(directory, label, 0)
        repeats = [compare(output, outputs(directory, label, g)) for g in range(1, 5)]
        times = {r["group"]: r["median_s"] for r in groups}
        rows[label] = dict(spec, seconds=statistics.median(times.values()),
                           group_seconds=times, range_s=[min(times.values()), max(times.values())],
                           host_MiB=max(r["sum_host_peak_rss_MiB"] for r in groups),
                           device_MiB=max(r["sum_gpu_peak_MiB"] for r in groups),
                           acc_source_resident=all(rank.get("acc_source_resident", False)
                                                   for group in groups for rank in group["ranks"]),
                           repeats_exact=all(r["exact"] for r in repeats),
                           binary_sha256=groups[0]["ranks"][0]["binary_sha256"])
        assert rows[label]["repeats_exact"], label
        dump(checkpoint, {"summary_sha256": digest, "rows": rows})
    cpu = [label for label in rows if CASES[label][1] == "cpu"]
    best_cpu = min(cpu, key=lambda label: rows[label]["seconds"]) if cpu else None
    comparisons = []

    def pair(base, current, role):
        if base not in rows or current not in rows:
            return
        timing = paired(rows[base]["group_seconds"], rows[current]["group_seconds"])
        numerical = compare(outputs(directory, base, 0), outputs(directory, current, 0))
        # Native CPU and GPU use different samplers; CPU speed comparisons are
        # throughput comparisons, not numerical validation against native McICA.
        usable = numerical["within_tolerance"] if role not in ("best_cpu", "cpu9") else None
        comparisons.append(dict(base=base, current=current, role=role, **timing,
                                numerical=numerical, valid_base=usable,
                                host_ratio=rows[current]["host_MiB"] / rows[base]["host_MiB"],
                                device_delta_MiB=rows[current]["device_MiB"] - rows[base]["device_MiB"]))

    for current, row in rows.items():
        build = CASES[current][0].replace("candidate-", "optimized-", 1)
        if build == "optimized-cpu":
            pair(current.replace("optimized-cpu", "main-cpu"), current, "cpu_base")
        if build not in ("optimized-acc", "optimized-omp"):
            continue
        if best_cpu:
            pair(best_cpu, current, "best_cpu")
        pair("matrix-optimized-cpu-b9", current, "cpu9")
        if "-w" in current:
            if build == "optimized-acc":
                pair(current.replace("optimized-acc", "acc-tiled"), current, "tiled_base")
            else:
                pair(current.replace("optimized-omp", "omp-tiled"), current, "packed_base_diagnostic")
                pair(f'matrix-omp-tiled-b{row["width"]}', current, "equivalent_width_control")
        else:
            for base in (("acc-opt",) if build == "optimized-acc" else ("master-omp", "omp-tiled")):
                pair(current.replace(build, base), current, "normal_base")
    result = {"job": job, "best_cpu": best_cpu, "rows": rows, "comparisons": comparisons,
              "failed_process_groups": failures,
              "complete_cases": len(rows), "expected_cases": len(json.loads((directory / "cases.json").read_text())),
              "statistics": "Five-pair exact one-sided log-time sign-flip test; resolved at p<0.05. No resolved slowdown is not proof of equivalence. Best CPU is selected from the small-block main and integrated CPU configurations measured in this allocation.",
              "numerical_limits": "Native CPU/GPU samplers differ; CPU throughput comparisons do not establish numerical agreement. Validity is evaluated against matched GPU bases at unchanged LW/SW thresholds. Known full-domain matched-CPU LW discrepancy remains separate."}
    dump(directory / "matrix-assessment.json", result)
    print(job, "complete", len(rows), "CPU", best_cpu,
          rows[best_cpu]["seconds"] if best_cpu else None, "failed", len(failures))
    for c in comparisons:
        if c["role"] in ("best_cpu", "cpu9"):
            continue
        print(c["current"], "vs", c["base"], f'{c["time_ratio"]:.4f}x',
              "SLOWER" if c["resolved_slowdown"] else "FASTER" if c["resolved_speedup"] else "unresolved",
              "numeric", c["numerical"])


if __name__ == "__main__":
    for job in sys.argv[1:]:
        analyse(job)
