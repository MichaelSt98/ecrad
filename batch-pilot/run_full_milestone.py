"""Full device-tile pipeline: frozen GPU parent validation, timing and Nsight."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
from typing import Any

import numpy as np
import xarray as xr
import run_integration as bench
import run_lineage as lineage

ROOT = Path(__file__).resolve().parent
RUN = ROOT / f"milestone-full-{os.environ['SLURM_JOB_ID']}"
PARENT = ROOT / "milestone-gas-30153332"
bench.RUN = RUN
lineage.RUN = RUN
bench.logged = lineage.logged


def execute(label, batch, repeat, tag, input_file=None, profile=False, capture=False, nsys=False):
    folder = RUN / tag
    folder.mkdir()
    source_label = "candidate" if bench.PRECISION == "sp" else "candidate-dp"
    base = PARENT if label == "parent" else RUN
    text = (PARENT / f"check-{bench.PRECISION}-16960-K0-gas0/config.nam").read_text()
    for key, value in dict(nrepeat=repeat, nwarmup=1).items():
        text, count = re.subn(rf"(?m)^(\s*{key}\s*=\s*)\d+", rf"\g<1>{value}", text)
        assert count == 1
    text = re.sub(r'(?m)^(\s*directory_name\s*=\s*)"[^"]+"',
                  lambda m: m[1] + '"' + str(base / "sources" / source_label / "data") + '"', text)
    config = folder / "config.nam"
    config.write_text(text)
    env = os.environ.copy()
    env.update(ECRAD_SW_BATCH_SIZE="32" if label == "parent" or batch == 0 else "0",
               ECRAD_LW_BATCH="1", ECRAD_GAS_BATCH="1", ECRAD_FULL_BATCH_SIZE=str(batch),
               ECRAD_BATCH_PROFILE=str(int(profile)), ECRAD_NSYS_CAPTURE=str(int(capture)), OMP_NUM_THREADS="1")
    executable = base / "builds" / source_label / f"bin/ecrad_ifs_blocked_{bench.PRECISION}"
    output = folder / "output.nc"
    command = [executable, config, input_file or bench.INPUT, output]
    if nsys:
        command = ["nsys", "profile", "--trace=cuda,osrt", "--sample=none", "--cpuctxsw=none",
                   "--capture-range=cudaProfilerApi", "--capture-range-end=stop", "--cuda-memory-usage=true",
                   "--force-overwrite=true", "-o", folder / "trace", *command]
    lineage.logged(command, folder / "run.log", env, folder)
    content = (folder / "run.log").read_text()
    if batch:
        assert re.search(rf"FULL_BATCH_LAYOUT\s+32\s+{32*batch}\s+\d+", content), tag
    times = [float(line.split()[-1]) for line in content.splitlines() if line.startswith("BATCH_ITER ")]
    assert len(times) == repeat, (tag, times)
    row: dict[str, Any] = dict(label=label, batch=batch, tag=tag, times=times, median=statistics.median(times[1:]))
    if profile and batch:
        row["phases"] = [[float(v) for v in line.split()[2:]] for line in content.splitlines()
                         if line.startswith("FULL_PHASE ")]
        assert len(row["phases"]) == repeat
    if nsys:
        bench.logged(["nsys", "stats", "--report=cuda_gpu_kern_sum,cuda_api_sum,cuda_gpu_mem_time_sum",
                      "--format=csv", str(folder / "trace.nsys-rep")], folder / "stats.log")
    return row, output


def main():
    os.environ["ECRAD_PROFILE_API"] = "1"
    bench.prepare()
    shutil.copy2(__file__, RUN / "run_full_milestone.py")
    (RUN / "parent.json").write_text(json.dumps(dict(origin=str(PARENT),
        binaries={p: hashlib.sha256((PARENT / "builds" / label / f"bin/ecrad_ifs_blocked_{p}").read_bytes()).hexdigest()
                  for p, label in (("sp", "candidate"), ("dp", "candidate-dp"))}), indent=2))
    with xr.open_dataset(bench.INPUT) as data:
        mixed = data.isel(column=slice(0, 65)).load()
        solar = np.asarray(mixed["cos_solar_zenith_angle"]).copy()
        solar.reshape(-1)[1::2] = 0.0
        mixed["cos_solar_zenith_angle"].data = solar
        mixed.to_netcdf(RUN / "mixed-65.nc")
    checks = []
    for precision in ("sp", "dp"):
        bench.PRECISION = precision
        build_label = "candidate" if precision == "sp" else "candidate-dp"
        if precision == "dp":
            (RUN / "sources" / build_label).symlink_to(RUN / "sources/candidate", target_is_directory=True)
        bench.build(build_label)
        for input_file, columns in ((RUN / "mixed-65.nc", 65), (bench.INPUT, 16960)):
            _, reference = execute("parent", 0, 2, f"check-{precision}-{columns}-parent", input_file)
            for batch in (0, 1, 16, 32, 64, 128):
                row, output = execute("candidate", batch, 2, f"check-{precision}-{columns}-K{batch}", input_file)
                with xr.open_dataset(output) as data:
                    assert data.sizes["column"] == columns
                row["errors"] = bench.compare(reference, output)
                checks.append(row)
                (RUN / "validation.json").write_text(json.dumps(checks, indent=2))
        bench.logged(["ctest", "--test-dir", RUN / "builds" / build_label, "--output-on-failure"], RUN / f"ctest-{precision}.log")
    bench.PRECISION = "sp"
    reference = RUN / "check-sp-16960-parent/output.nc"
    variants = [("parent", 0), ("candidate", 16), ("candidate", 32), ("candidate", 64), ("candidate", 128)]
    rows = []
    for process in range(5):
        offset = process % len(variants)
        order = variants[offset:] + variants[:offset]
        if process % 2:
            order = order[::-1]
        for label, batch in order:
            row, output = execute(label, batch, 10, f"time-{process}-{label}-K{batch}")
            row["errors"] = bench.compare(reference, output)
            rows.append(row)
            (RUN / "timing.json").write_text(json.dumps(rows, indent=2))
    lines = ["# Full pipeline device-tile SP milestone", "", "| Version | K | Seconds | Process range |", "|---|---:|---:|---|"]
    for label, batch in variants:
        values = [r["median"] for r in rows if (r["label"], r["batch"]) == (label, batch)]
        lines.append(f"| {label} | {batch} | {statistics.median(values):.7f} | {min(values):.7f}–{max(values):.7f} |")
    (RUN / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    best = min((16, 32, 64, 128), key=lambda k: statistics.median(r["median"] for r in rows if r["batch"] == k))
    profiles = []
    for batch in dict.fromkeys((32, best)):
        row, output = execute("candidate", batch, 5, f"phases-K{batch}", profile=True)
        row["errors"] = bench.compare(reference, output)
        profiles.append(row)
        (RUN / "phases.json").write_text(json.dumps(profiles, indent=2))
        row, output = execute("candidate", batch, 2, f"nsys-K{batch}", capture=True, nsys=True)
        row["errors"] = bench.compare(reference, output)
        (RUN / f"nsys-K{batch}.json").write_text(json.dumps(row, indent=2))


if __name__ == "__main__":
    main()
