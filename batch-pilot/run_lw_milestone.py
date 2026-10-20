"""Validate/measure opt-in LW tile against the unchanged per-block LW path."""
import json
import os
from pathlib import Path
import shutil
import statistics

import xarray as xr
import run_integration as bench
import run_lineage as lineage

ROOT = Path(__file__).resolve().parent
RUN = ROOT / f"milestone-lw-{os.environ['SLURM_JOB_ID']}"
bench.RUN = RUN
lineage.RUN = RUN
bench.logged = lineage.logged


def execute(label, batch, lw, repeat, tag, profile=False):
    os.environ["ECRAD_LW_BATCH"] = str(lw)
    os.environ["ECRAD_BATCH_PROFILE"] = str(int(profile))
    row, output = bench.execute(label, batch, repeat, tag)
    row.update(lw=lw, profile=profile)
    if profile:
        rows = [line.split() for line in (RUN / tag / "run.log").read_text().splitlines()
                if line.startswith("BATCH_PHASE ")]
        assert len(rows) == repeat
        row["raw_phases"] = [[float(v) for v in r[2:]] for r in rows]
    return row, output


def main():
    bench.prepare()
    shutil.copy2(__file__, RUN / "run_lw_milestone.py")
    original = bench.INPUT
    with xr.open_dataset(original) as data:
        data.isel(column=slice(0, 65)).to_netcdf(RUN / "input-65.nc")
    checks = []
    for precision in ("sp", "dp"):
        bench.PRECISION = precision
        label = "candidate" if precision == "sp" else "candidate-dp"
        if precision == "dp":
            (RUN / "sources" / label).symlink_to(RUN / "sources/candidate", target_is_directory=True)
        bench.build(label)
        for input_file, columns in ((RUN / "input-65.nc", 65), (original, 16960)):
            bench.INPUT = input_file
            reference = None
            for batch, lw in ((0, 0), (1, 1), (16, 0), (16, 1), (32, 0), (32, 1)):
                row, output = execute(label, batch, lw, 2, f"check-{precision}-{columns}-K{batch}-LW{lw}")
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
    reference = RUN / "check-sp-16960-K0-LW0/output.nc"
    variants = [(16, 0), (16, 1), (32, 0), (32, 1)]
    records = []
    for process in range(5):
        offset = process % len(variants)
        order = variants[offset:] + variants[:offset]
        if process % 2:
            order = order[::-1]
        for batch, lw in order:
            row, output = execute("candidate", batch, lw, 10, f"time-{process}-K{batch}-LW{lw}")
            row["errors"] = bench.compare(reference, output)
            records.append(row)
            (RUN / "timing.json").write_text(json.dumps(records, indent=2))
    lines = ["# LW tile milestone SP", "", "| K | LW batched | Seconds | Process range | Reduction |",
             "|---:|---:|---:|---|---:|"]
    for batch, lw in variants:
        values = [r["median"] for r in records if (r["batch"], r["lw"]) == (batch, lw)]
        control = statistics.median(r["median"] for r in records if r["batch"] == batch and r["lw"] == 0)
        value = statistics.median(values)
        lines.append(f"| {batch} | {lw} | {value:.7f} | {min(values):.7f}–{max(values):.7f} | {100*(1-value/control):.3f}% |")
    (RUN / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    profiles = []
    for batch in (16, 32):
        for lw in (0, 1):
            row, output = execute("candidate", batch, lw, 5, f"profile-K{batch}-LW{lw}", profile=True)
            row["errors"] = bench.compare(reference, output)
            profiles.append(row)
            (RUN / "phases.json").write_text(json.dumps(profiles, indent=2))


if __name__ == "__main__":
    main()
