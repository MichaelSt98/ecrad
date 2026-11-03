#!/usr/bin/env python3
"""Check controlled McICA ACC samples across driver blocks and repeated calls.

Run once with a BITIDENTITY_TESTING CPU build, then pass its output directory
as --reference-directory when running an offload build on a GPU. Requires the
numpy/xarray environment used by test/common/nccmp. Both precisions and all
three drivers must be built. Output/log files are retained for diagnosis.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

import numpy as np
import xarray as xr


DRIVERS = ("ecrad", "ecrad_ifs", "ecrad_ifs_blocked")
PRECISIONS = ("dp", "sp")


def compare(reference, output, first, last, lw_threshold, sw_threshold):
    """Compare active columns only; inactive driver output is not prescribed."""
    errors = {}
    with xr.open_dataset(reference) as ref, xr.open_dataset(output) as out:
        if set(ref.data_vars) != set(out.data_vars):
            raise AssertionError("Output variables changed: " + str(output))
        for name in ref.data_vars:
            a, b = ref[name], out[name]
            if a.dims != b.dims or a.shape != b.shape:
                raise AssertionError("Output shape changed: " + name)
            if "column" in a.dims:
                a = a.isel(column=slice(first - 1, last))
                b = b.isel(column=slice(first - 1, last))
            if not np.issubdtype(a.dtype, np.number):
                continue
            if not np.all(np.isfinite(a.values)) or not np.all(np.isfinite(b.values)):
                raise AssertionError("Nonfinite active output: " + name)
            error = float(np.max(np.abs(a.values - b.values)))
            threshold = 0.0
            if "_lw" in name or "lw_" in name:
                threshold = lw_threshold
            elif "_sw" in name or "sw_" in name:
                threshold = sw_threshold
            if error > threshold:
                raise AssertionError(
                    f"{output}: {name}: {error} exceeds {threshold}"
                )
            errors[name] = error
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-directory", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--namelist", required=True, type=Path)
    parser.add_argument("--reference-directory", type=Path)
    parser.add_argument("--drivers", nargs="+", choices=DRIVERS, default=DRIVERS)
    parser.add_argument("--precisions", nargs="+", choices=PRECISIONS, default=PRECISIONS)
    parser.add_argument("--batch-sizes", nargs="+", type=int, default=[],
                        help="Also test ECRAD_FULL_BATCH_SIZE in flat GPU drivers")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build = args.build_directory.resolve()
    output_dir = args.output_directory.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    reference_dir = args.reference_directory
    if reference_dir is not None:
        reference_dir = reference_dir.resolve()
    env = dict(os.environ, OMP_NUM_THREADS="1")
    env.pop("ECRAD_FULL_BATCH_SIZE", None)
    # Full input has 32 columns. Include singleton, odd, exact and oversized
    # blocks, plus a non-unit active range with a partial final block.
    cases = [(size, repeat, 1, 32) for size in (1, 7, 15, 32, 33)
             for repeat in (1, 3)]
    cases += [(7, repeat, 5, 29) for repeat in (1, 3)]
    summary = {
        "build_directory": str(build), "runs": [], "completed": False,
        "limitations": [
            "The IFS blocked drivers ignore istartcol/iendcol; their offset-range cases are skipped.",
            "OMP_NUM_THREADS=1 tests sequential calls, not host-thread concurrency.",
        ],
    }
    summary_path = output_dir / "summary.json"

    for precision in args.precisions:
        for driver in args.drivers:
            binary = build / "bin" / f"{driver}_{precision}"
            baseline = output_dir / f"baseline_{driver}_{precision}.nc"
            runs = [(32, 1, 1, 32, False, 0)]
            runs += [(size, repeat, first, last, True, 0)
                     for size, repeat, first, last in cases]
            if driver != "ecrad_ifs_blocked":
                runs += [(size, 3, first, last, True, batch)
                         for size in (1, 7, 15) for batch in args.batch_sizes
                         for first, last in ((1, 32), (5, 29))]
            for size, repeat, first, last, blocked, batch in runs:
                if driver == "ecrad_ifs_blocked" and first != 1:
                    continue
                label = (f"{driver}_{precision}_b{size}_r{repeat}_"
                         f"c{first}-{last}_{'blocks' if blocked else 'baseline'}")
                if batch:
                    label += f"_tile{batch}"
                namelist = output_dir / f"{label}.nam"
                output = output_dir / f"{label}.nc" if blocked else baseline
                subprocess.run([
                    str(root / "test/common/change_namelist.sh"),
                    str(args.namelist.resolve()), str(namelist),
                    f"nblocksize={size}", f"nrepeat={repeat}",
                    f"istartcol={first}", f"iendcol={last}",
                    f"do_parallel={'true' if blocked and not batch else 'false'}",
                    f"iverbose={3 if batch else 2}",
                ], check=True)
                print("Running " + label, flush=True)
                run_env = dict(env)
                if batch:
                    run_env["ECRAD_FULL_BATCH_SIZE"] = str(batch)
                log_path = output_dir / f"{label}.log"
                with log_path.open("w") as log:
                    subprocess.run([
                        str(binary), str(namelist),
                        str(root / "test/ifs/ecrad_meridian.nc"), str(output),
                    ], cwd=output_dir, env=run_env, stdout=log,
                        stderr=subprocess.STDOUT, check=True)
                if batch:
                    # Observe the actual radiation-call ranges, so a driver
                    # silently ignoring the batching switch cannot pass.
                    width = min(32, batch * size)
                    expected = [(start, min(start + width - 1, last))
                                for start in range(first, last + 1, width)] * repeat
                    actual = [(int(a), int(b)) for a, b in re.findall(
                        r"processing columns (\d+)-(\d+)", log_path.read_text(), re.IGNORECASE
                    )]
                    if actual != expected:
                        raise AssertionError(f"Unexpected tile call ranges in {log_path}: {actual}")
                # Use existing same-driver thresholds within one backend.
                # The baseline self-comparison also checks finite outputs.
                lw = 2e-4 if precision == "sp" else 0.0
                sw = 1e-6 if precision == "sp" else 0.0
                errors = compare(baseline, output, first, last, lw, sw)
                entry = {"case": label, "within_backend_errors": errors}
                if reference_dir is not None:
                    reference = reference_dir / f"baseline_{driver}_{precision}.nc"
                    entry["cpu_reference_errors"] = compare(
                        reference, output, first, last, 0.001, 0.1
                    )
                summary["runs"].append(entry)
                summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    summary["completed"] = True
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Passed {len(summary['runs'])} executions and comparisons", flush=True)


if __name__ == "__main__":
    main()
