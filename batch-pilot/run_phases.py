"""Separate opt-in synchronized phase diagnostics and real partial-input checks."""
import json
import os
from pathlib import Path
import shutil
import statistics
import sys

import xarray as xr

import run_integration as integration
import run_lineage as lineage

ROOT = Path(__file__).resolve().parent
RUN = ROOT / f"milestone-phases-{os.environ['SLURM_JOB_ID']}"
if len(sys.argv) == 3 and sys.argv[1] == "--freeze":
    RUN = ROOT / f"milestone-phases-{int(sys.argv[2])}"
integration.RUN = RUN
lineage.RUN = RUN
integration.logged = lineage.logged


def exclusive(raw, total):
    stage, solve, scatter, optics, lw, prepare, finish, io = raw[:8]
    lw_stage, lw_solve, lw_scatter = raw[8:11] if len(raw) >= 11 else (0.0, 0.0, 0.0)
    gas_stage, gas_solve, gas_load = raw[11:14] if len(raw) == 14 else (0.0, 0.0, 0.0)
    result = dict(sw_stage=stage, sw_solve=solve, sw_scatter=scatter,
                  optics=optics-gas_load, lw=lw-lw_stage, other_preparation=prepare-stage-optics-lw-gas_stage,
                  other_finishing=finish-scatter-lw_scatter, transfers=io,
                  orchestration=total-prepare-solve-lw_solve-gas_solve-finish-io)
    if len(raw) >= 11:
        result.update(lw_stage=lw_stage, lw_solve=lw_solve, lw_scatter=lw_scatter)
    if len(raw) == 14:
        result.update(gas_stage=gas_stage, gas_solve=gas_solve, gas_load=gas_load)
    assert min(result.values()) >= -1.e-5, result
    assert abs(sum(result.values())-total) < 1.e-8
    return result


def main():
    os.environ["ECRAD_LW_BATCH"] = "0"
    if not RUN.exists():
        integration.prepare()
        shutil.copy2(__file__, RUN / "run_phases.py")
    if len(sys.argv) == 3 and sys.argv[1] == "--freeze":
        return
    integration.build("candidate")
    checks = []
    original_input = integration.INPUT
    # The blocked driver ignores range controls in its block loop. Actually
    # truncate the file so 65 columns exercises a partial block and tile.
    with xr.open_dataset(original_input) as source:
        assert source.sizes["column"] == 16960
        source.isel(column=slice(0, 65)).to_netcdf(RUN / "input-65.nc")
    reference = None
    for input_path, count in ((RUN / "input-65.nc", 65), (original_input, 16960)):
        integration.INPUT = input_path
        reference = None
        for batch in (0, 1, 16, 32):
            row, output = integration.execute("candidate", batch, 2, f"check-{count}-K{batch}")
            with xr.open_dataset(output) as data:
                assert data.sizes["column"] == count
            if reference is None:
                reference = output
            row["errors"] = integration.compare(reference, output)
            row["columns"] = count
            checks.append(row)
            (RUN / "validation.json").write_text(json.dumps(checks, indent=2))
    integration.INPUT = original_input
    assert reference is not None
    records = []
    for process in range(3):
        for batch in ((16, 32) if process % 2 == 0 else (32, 16)):
            for profile in (False, True):
                os.environ["ECRAD_BATCH_PROFILE"] = str(int(profile))
                tag = f"phase-{process}-K{batch}-profile{int(profile)}"
                row, output = integration.execute("candidate", batch, 5, tag)
                row["errors"] = integration.compare(reference, output)
                row["profile"] = profile
                if profile:
                    rows = [line.split() for line in (RUN / tag / "run.log").read_text().splitlines()
                            if line.startswith("BATCH_PHASE ")]
                    assert len(rows) == 5
                    row["raw_phases"] = [[float(v) for v in r[2:]] for r in rows]
                    row["exclusive"] = [exclusive(raw, total) for raw, total in zip(row["raw_phases"], row["times"])]
                records.append(row)
                (RUN / "timing.json").write_text(json.dumps(records, indent=2))
    lines = ["# Integrated SW phase costs (diagnostic runs)", "", "| K | Phase | Seconds | Share |",
             "|---:|---|---:|---:|"]
    for batch in (16, 32):
        profiled = [r for r in records if r["batch"] == batch and r["profile"]]
        control = [r["median"] for r in records if r["batch"] == batch and not r["profile"]]
        total = statistics.median(r["median"] for r in profiled)
        for key in profiled[0]["exclusive"][0]:
            value = statistics.median(statistics.median(p[key] for p in r["exclusive"][1:]) for r in profiled)
            lines.append(f"| {batch} | {key} | {value:.7f} | {100*value/total:.2f}% |")
        lines.append(f"\nK{batch}: instrumented total {total:.7f} s; control {statistics.median(control):.7f} s.\n")
    (RUN / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    os.environ["ECRAD_BATCH_PROFILE"] = "0"
    integration.logged(["ctest", "--test-dir", RUN / "builds/candidate", "--output-on-failure"], RUN / "ctest.log")


if __name__ == "__main__":
    main()
