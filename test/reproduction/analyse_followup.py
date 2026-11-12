"""Analyze follow-up runs; optionally compare stock outputs with an ACC matrix job."""
import argparse
from itertools import product
import json
import math
from pathlib import Path
import statistics

import numpy as np
import xarray as xr

ROOT = Path(__file__).resolve().parents[2] / "build/perf-sp/results"


def output(job, label, group, rank):
    with xr.open_dataset(ROOT / job / f"{label}-g{group}-r{rank}/output.nc") as dataset:
        return dataset.load()


def compare(a, b):
    names = sorted(set(a.data_vars) & set(b.data_vars))
    missing = sorted(set(a.data_vars) ^ set(b.data_vars))
    errors = {}
    finite = True
    for name in names:
        x, y = a[name], b[name]
        if not np.issubdtype(x.dtype, np.number):
            continue
        if x.shape != y.shape or x.dims != y.dims:
            raise ValueError(f"Output dimensions differ: {name}")
        finite &= bool(np.isfinite(x).all() and np.isfinite(y).all())
        errors[name] = float(np.abs(x.values.astype(float) - y.values.astype(float)).max())
    return {"finite": finite, "missing": missing, "variables": len(errors),
            "max_lw": max((v for k, v in errors.items() if "lw" in k and "derivative" not in k), default=None),
            "max_sw": max((v for k, v in errors.items() if "sw" in k), default=None),
            "max_other": max((v for k, v in errors.items() if "lw" not in k and "sw" not in k), default=0),
            "exact": finite and not missing and not any(errors.values())}


def grouped(job):
    rows = json.loads((ROOT / job / "summary.json").read_text())
    groups = {}
    for row in rows:
        if row["step_exit_code"]:
            raise ValueError(f"Failed case: {row['label']} group {row['group']}")
        groups.setdefault(row["label"], {})[row["group"]] = row["median_s"]
    if not groups or any(set(value) != set(range(5)) for value in groups.values()):
        raise ValueError("Expected five groups per case")
    return groups


def paired(base, candidate):
    differences = [math.log(candidate[i] / base[i]) for i in range(5)]
    observed = statistics.mean(differences)
    distribution = [statistics.mean(s*d for s, d in zip(signs, differences))
                    for signs in product((-1, 1), repeat=5)]
    baseline = statistics.median(base.values())
    current = statistics.median(candidate.values())
    return {"base_seconds": baseline, "candidate_seconds": current,
            "runtime_change_percent": 100 * (current / baseline - 1),
            "p_faster": sum(v <= observed + 1e-15 for v in distribution) / 32}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=("stock", "intel"))
    parser.add_argument("job", help="Follow-up Slurm job ID")
    parser.add_argument("--acc-job", help="Optional completed normal ACC matrix job ID")
    args = parser.parse_args()
    groups = grouped(args.job)
    result = {"suite": args.suite, "job": args.job, "cases": {}}
    if args.suite == "intel":
        for block in (8, 16, 32):
            base, new = f"repro-intel-main-b{block}", f"repro-intel-mix-b{block}"
            result["cases"][block] = {**paired(groups[base], groups[new]),
                "outputs": [compare(output(args.job, base, g, 0), output(args.job, new, g, 0))
                            for g in range(5)]}
    else:
        for block in (32, 128, 1024, 2048, 4096, 8192, 8480):
            label = f"repro-stock-acc-b{block}"
            case = {"seconds": statistics.median(groups[label].values()),
                    "repeat_outputs": [compare(output(args.job, label, 0, rank),
                                               output(args.job, label, g, rank))
                                       for g in range(1, 5) for rank in range(2)]}
            if args.acc_job and block >= 1024:
                candidate = f"matrix-optimized-acc-b{block}"
                case["main_gpu_outputs"] = [compare(output(args.job, label, 0, rank),
                    output(args.acc_job, candidate, 0, rank)) for rank in range(2)]
            result["cases"][block] = case
    path = ROOT / args.job / "reproduction-analysis.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
