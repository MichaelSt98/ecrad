"""Matched gas-optics continuation milestone, against the LW+SW-only path."""
import json
import os
from pathlib import Path
import shutil
import statistics

import numpy as np
import xarray as xr
import run_integration as bench
import run_lineage as lineage
from run_phases import exclusive

ROOT = Path(__file__).resolve().parent
RUN = ROOT / f"milestone-gas-{os.environ['SLURM_JOB_ID']}"
bench.RUN = RUN
lineage.RUN = RUN
bench.logged = lineage.logged


def execute(label, batch, gas, repeat, tag, profile=False):
    os.environ.update(ECRAD_LW_BATCH=str(int(batch > 0)), ECRAD_GAS_BATCH=str(gas),
                      ECRAD_BATCH_PROFILE=str(int(profile)))
    row, output = bench.execute(label, batch, repeat, tag)
    row.update(gas=gas, profile=profile)
    if profile:
        rows = [line.split() for line in (RUN / tag / "run.log").read_text().splitlines()
                if line.startswith("BATCH_PHASE ")]
        assert len(rows) == repeat
        row["raw_phases"] = [[float(v) for v in r[2:]] for r in rows]
        row["exclusive"] = [exclusive(raw, total) for raw, total in zip(row["raw_phases"], row["times"])]
    return row, output


def main():
    bench.prepare()
    shutil.copy2(__file__, RUN / "run_gas_milestone.py")
    original = bench.INPUT
    with xr.open_dataset(original) as data:
        subset = data.isel(column=slice(0, 65)).load()
        solar = np.asarray(subset["cos_solar_zenith_angle"]).copy()
        solar.reshape(-1)[1::2] = 0.0
        subset["cos_solar_zenith_angle"].data = solar
        subset.to_netcdf(RUN / "mixed-65.nc")
    checks = []
    for precision in ("sp", "dp"):
        bench.PRECISION = precision
        label = "candidate" if precision == "sp" else "candidate-dp"
        if precision == "dp":
            (RUN / "sources" / label).symlink_to(RUN / "sources/candidate", target_is_directory=True)
        bench.build(label)
        for input_file, columns in ((RUN / "mixed-65.nc", 65), (original, 16960)):
            bench.INPUT = input_file
            reference = None
            for batch, gas in ((0, 0), (1, 1), (16, 0), (16, 1), (32, 0), (32, 1)):
                row, output = execute(label, batch, gas, 2, f"check-{precision}-{columns}-K{batch}-gas{gas}")
                with xr.open_dataset(output) as data:
                    assert data.sizes["column"] == columns
                if reference is None:
                    reference = output
                row["errors"] = bench.compare(reference, output)
                checks.append(row)
                (RUN / "validation.json").write_text(json.dumps(checks, indent=2))
        bench.logged(["ctest", "--test-dir", RUN / "builds" / label, "--output-on-failure"], RUN / f"ctest-{precision}.log")
    bench.INPUT = original
    bench.PRECISION = "sp"
    reference = RUN / "check-sp-16960-K0-gas0/output.nc"
    variants = [(16, 0), (16, 1), (32, 0), (32, 1)]
    records = []
    for process in range(5):
        offset = process % len(variants)
        order = variants[offset:] + variants[:offset]
        if process % 2:
            order = order[::-1]
        for batch, gas in order:
            row, output = execute("candidate", batch, gas, 10, f"time-{process}-K{batch}-gas{gas}")
            row["errors"] = bench.compare(reference, output)
            records.append(row)
            (RUN / "timing.json").write_text(json.dumps(records, indent=2))
    lines = ["# RRTMG tile milestone SP", "", "| K | Gas batching | Seconds | Process range | Reduction |",
             "|---:|---:|---:|---|---:|"]
    for batch, gas in variants:
        values = [r["median"] for r in records if (r["batch"], r["gas"]) == (batch, gas)]
        control = statistics.median(r["median"] for r in records if r["batch"] == batch and r["gas"] == 0)
        value = statistics.median(values)
        lines.append(f"| {batch} | {gas} | {value:.7f} | {min(values):.7f}–{max(values):.7f} | {100*(1-value/control):.3f}% |")
    (RUN / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    profiles = []
    for batch in (16, 32):
        for gas in (0, 1):
            row, output = execute("candidate", batch, gas, 5, f"profile-K{batch}-gas{gas}", profile=True)
            row["errors"] = bench.compare(reference, output)
            profiles.append(row)
            (RUN / "phases.json").write_text(json.dumps(profiles, indent=2))


if __name__ == "__main__":
    main()
