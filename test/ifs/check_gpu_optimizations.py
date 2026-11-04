#!/usr/bin/env python3
"""Compare optimized kernels with matched-sampling CPU references.

Prepare on a CPU host with --cpu-build, then use --gpu-build in an allocation.
Both builds need SP/DP and BITIDENTITY_TESTING. No tolerances are relaxed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from check_gpu_blocking import compare

CASES = {
    "default": {},
    "weighted": {"do_nearest_spectral_sw_albedo": "false", "do_nearest_spectral_lw_emiss": "false"},
    "no_scattering": {"do_lw_cloud_scattering": "false"},
    "no_derivatives": {"do_lw_derivatives": "false"},
    "delta": {"do_sw_delta_scaling_with_gases": "true"},
    "beta": {"use_beta_overlap": "true"},
    "slingo": {"liquid_model_name": '"Slingo"'},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--cpu-build", type=Path)
    mode.add_argument("--gpu-build", type=Path)
    parser.add_argument("--reference-directory", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--namelist", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build = (args.cpu_build or args.gpu_build).resolve()
    reference = args.reference_directory.resolve()
    output = args.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.cpu_build:
        reference.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, OMP_NUM_THREADS="1")
    env.pop("ECRAD_FULL_BATCH_SIZE", None)
    result = {"runs": [], "completed": False, "limitations": [
        "The no-derivatives case uses only the standalone driver: IFS CPU drivers retain LAPPROXLWUPDATE and require derivatives.",
        "The blocked IFS driver ignores istartcol/iendcol; weighted offset cases use standalone and flat IFS drivers.",
    ]}
    for precision in ("dp", "sp"):
        for case, changes in CASES.items():
            config = args.namelist.read_text()
            for key, value in dict(nrepeat="3", nblocksize="15", **changes).items():
                config, count = re.subn(rf"(?m)^(\s*{key}\s*=)[^\n]*", rf"\g<1>{value},", config)
                assert count == 1, key
            namelist = output / f"{case}-{precision}.nam"
            namelist.write_text(config)
            drivers = ("ecrad",) if case == "no_derivatives" else ("ecrad", "ecrad_ifs", "ecrad_ifs_blocked")
            if args.cpu_build:
                runs = [(driver, False) for driver in drivers]
            else:
                runs = [(driver, offset) for driver in drivers
                        for offset in ((False, True) if case == "weighted" and driver != "ecrad_ifs_blocked"
                                       else (False,))]
            for driver, offset in runs:
                reference_file = reference / f"{case}-{precision}-{driver}.nc"
                label = f"{case}-{precision}-{driver}" + ("-offset" if offset else "")
                run_config = config
                if offset:
                    for key, value in (("istartcol", 5), ("iendcol", 29)):
                        run_config = re.sub(rf"(?m)^(\s*{key}\s*=)[^\n]*", rf"\g<1>{value},", run_config)
                run_namelist = output / f"{label}.nam"
                run_namelist.write_text(run_config)
                destination = reference_file if args.cpu_build else output / f"{label}.nc"
                if args.gpu_build:
                    env["ECRAD_FULL_BATCH_SIZE"] = "2"
                row = {"case": label}
                try:
                    with (output / f"{label}.log").open("w") as log:
                        subprocess.run([str(build / "bin" / f"{driver}_{precision}"), str(run_namelist),
                                        str(root / "test/ifs/ecrad_meridian.nc"), str(destination)],
                                       env=env, cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True)
                    if args.gpu_build:
                        row["errors"] = compare(reference_file, destination, 5 if offset else 1,
                                                29 if offset else 32, 0.001, 0.1)
                except (subprocess.CalledProcessError, AssertionError) as error:
                    row["failure"] = str(error)
                result["runs"].append(row)
                (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
                print("FAILED" if "failure" in row else "Passed", label, flush=True)
    result["completed"] = not any("failure" in row for row in result["runs"])
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    if not result["completed"]:
        raise SystemExit("Optimized-kernel checks failed; inspect summary.json and per-case logs")


if __name__ == "__main__":
    main()
