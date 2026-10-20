"""Additional mixed-day/night partial-tile coverage using milestone binaries."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

import numpy as np
import xarray as xr
import run_integration as bench
import run_lineage as lineage

ROOT = Path(__file__).resolve().parent
ORIGIN = ROOT / "milestone-lw-30140239"
REUSE = os.environ.get("ECRAD_COVERAGE_REUSE")
RUN = ROOT / f"milestone-lw-coverage-{os.environ['SLURM_JOB_ID']}"
lineage.RUN = RUN


def main():
    bench.RUN = RUN
    bench.logged = lineage.logged
    if REUSE:
        RUN.mkdir()
        origin = Path(REUSE).resolve()
        for name in ("sources", "builds"):
            (RUN / name).symlink_to(origin / name, target_is_directory=True)
        shutil.copy2(origin / "snapshots.json", RUN / "snapshots.json")
        (RUN / "reused-builds.json").write_text(json.dumps(dict(origin=str(origin), gas_batch=True,
            binaries={label: hashlib.sha256((origin / "builds" / label / f"bin/ecrad_ifs_blocked_{precision}").read_bytes()).hexdigest()
                      for label, precision in (("candidate", "sp"), ("candidate-dp", "dp"))}), indent=2))
        os.environ["ECRAD_GAS_BATCH"] = "1"
    else:
        bench.prepare()
    shutil.copy2(__file__, RUN / "run_lw_coverage.py")
    with xr.open_dataset(bench.INPUT) as data:
        solar = np.asarray(data["cos_solar_zenith_angle"]).reshape(-1)
        # This input contains only sunlit profiles. Construct night columns
        # explicitly instead of assuming the source contains both regimes.
        indices = np.flatnonzero(solar > 0)[:65]
        assert len(indices) == 65
        subset = data.isel(column=indices).load()
        cos_sza = np.asarray(subset["cos_solar_zenith_angle"]).copy()
        cos_sza.reshape(-1)[1::2] = 0.0
        subset["cos_solar_zenith_angle"].data = cos_sza
        subset.to_netcdf(RUN / "mixed-65.nc")
    assert solar[indices[-1]] > 0
    (RUN / "fixture.json").write_text(json.dumps(dict(original_indices=indices.tolist(), day=33, night=32,
        modification="cos_solar_zenith_angle=0 in odd zero-based columns",
        sha256=hashlib.sha256((RUN / "mixed-65.nc").read_bytes()).hexdigest(), origin=str(ORIGIN)), indent=2))
    records = []
    for precision in ("sp", "dp"):
        bench.PRECISION = precision
        label = "candidate" if precision == "sp" else "candidate-dp"
        if not REUSE:
            if precision == "dp":
                (RUN / "sources" / label).symlink_to(RUN / "sources/candidate", target_is_directory=True)
            bench.build(label)
        executable = RUN / "builds" / label / f"bin/ecrad_ifs_blocked_{precision}"
        for case, changes in (("default", {}), ("no-derivatives", {"do_lw_derivatives": "false"}),
                              ("beta-overlap", {"use_beta_overlap": "true"})):
            reference = None
            for batch in (0, 1, 16, 32):
                tag = f"{precision}-{case}-K{batch}"
                folder = RUN / tag
                folder.mkdir()
                text = (ORIGIN / f"check-{precision}-65-K0-LW0/config.nam").read_text()
                for key, value in changes.items():
                    text, count = re.subn(rf"(?m)^(\s*{key}\s*=\s*)[^,\n]+", rf"\g<1>{value}", text)
                    assert count == 1
                config = folder / "config.nam"
                config.write_text(text)
                env = os.environ.copy()
                env.update(ECRAD_SW_BATCH_SIZE=str(batch), ECRAD_LW_BATCH=str(int(batch > 0)), ECRAD_BATCH_PROFILE="0")
                output = folder / "output.nc"
                lineage.logged([executable, config, RUN / "mixed-65.nc", output], folder / "run.log", env, folder)
                with xr.open_dataset(output) as data:
                    assert data.sizes["column"] == 65
                if reference is None:
                    reference = output
                records.append(dict(precision=precision, case=case, batch=batch, errors=bench.compare(reference, output)))
                (RUN / "validation.json").write_text(json.dumps(records, indent=2))
        # Fresh full-domain control with the caller-consistency fix; not a new
        # five-process performance claim. The default hot solver is unchanged.
        os.environ.update(ECRAD_SW_BATCH_SIZE="32", ECRAD_LW_BATCH="1", ECRAD_BATCH_PROFILE="0")
        row, output = bench.execute(label, 32, 3, f"full-{precision}")
        row["errors"] = bench.compare(ORIGIN / f"check-{precision}-16960-K0-LW0/output.nc", output)
        (RUN / f"full-{precision}.json").write_text(json.dumps(row, indent=2))
        bench.logged(["ctest", "--test-dir", RUN / "builds" / label, "--output-on-failure"], RUN / f"ctest-{precision}.log")
    print("PASSED", len(records), "mixed-day/night partial-tile comparisons", flush=True)


if __name__ == "__main__":
    main()
