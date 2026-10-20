"""Validate/time the full-radiation SW continuation against pristine 2be60a2."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import tarfile
from typing import Any

import numpy as np
import xarray as xr

ROOT = Path(__file__).resolve().parent
WORK = ROOT.parent
CI = WORK.parent
RUN = ROOT / f"integration-{os.environ['SLURM_JOB_ID']}"
DEPS = CI / "pr119/deps"
INPUT = CI / "benchmark-nvhpc/input/rad4NN_inputs_2020010100_0c0.nc"
PRECISION = os.environ.get("ECRAD_INTEGRATION_PRECISION", "sp")
assert PRECISION in ("sp", "dp")


def logged(command, path, env=None):
    print("RUN", " ".join(map(str, command)), flush=True)
    with path.open("w") as output:
        subprocess.run(list(map(str, command)), cwd=RUN, env=env, stdout=output,
                       stderr=subprocess.STDOUT, check=True)


def prepare():
    RUN.mkdir()
    (RUN / "sources").mkdir()
    for label, commit in (("baseline", "2be60a2"), ("candidate", "HEAD")):
        source = RUN / "sources" / label
        source.mkdir()
        archive = subprocess.check_output(["git", "archive", commit], cwd=WORK)
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            stream.extractall(source, filter="data")
        if label == "candidate":
            names = subprocess.check_output(["git", "diff", "--name-only", "HEAD"], cwd=WORK).decode().splitlines()
            names += [name for name in subprocess.check_output(
                ["git", "ls-files", "--others", "--exclude-standard"], cwd=WORK).decode().splitlines()
                if Path(name).suffix.lower() in (".f90", ".h")]
            names += ["radiation/radiation_sw_batch_context_mod.F90"]
            for name in names:
                shutil.copy2(WORK / name, source / name)
        else:
            driver = source / "driver/ecrad_ifs_driver_blocked.F90"
            text = driver.read_text().replace("only : jprb, jprd", "only : jprb, jprd, jpib")
            text = text.replace("  integer :: jrepeat", "  integer(kind=jpib) :: it0,it1,irate\n  integer :: jrepeat")
            text = text.replace("  do jrepeat = 1,driver_config%nrepeat", "  do jrepeat = 1,driver_config%nrepeat\n    !$acc wait\n    call system_clock(it0,irate)")
            text = text.replace("      !$OMP END PARALLEL DO", "      !$OMP END PARALLEL DO\n      !$acc wait\n      call system_clock(it1)\n      write(nulout,'(a,2i6,es24.16)') 'BATCH_ITER ',0,jrepeat,real(it1-it0,jprd)/real(irate,jprd)")
            driver.write_text(text)
    manifest = {str(p.relative_to(RUN / "sources")): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (RUN / "sources").rglob("*") if p.is_file()}
    (RUN / "snapshots.json").write_text(json.dumps(manifest, indent=2))
    shutil.copy2(__file__, RUN / "run_integration.py")


def build(label):
    path = RUN / "builds" / label
    capture_flags = " -DECRAD_NSYS_CAPTURE_API" if os.environ.get("ECRAD_PROFILE_API") == "1" else ""
    capture_flags += " " + os.environ.get("ECRAD_EXTRA_FLAGS", "")
    logged(["cmake", "-S", RUN / "sources" / label, "-B", path, "-G", "Ninja",
        f"-Decbuild_ROOT={DEPS / 'ecbuild'}", f"-Dfiat_ROOT={DEPS / 'fiat-install'}",
        "-DCMAKE_Fortran_COMPILER=nvfortran", "-DCMAKE_BUILD_TYPE=Release",
        "-DECRAD_Fortran_FLAGS=-O3 -Mrecursive -gpu=cc80" + capture_flags,
        "-DCMAKE_EXE_LINKER_FLAGS=-L/usr/local/apps/nvidia/24.5/Linux_x86_64/24.5/cuda/lib64 -lcudart",
        "-DENABLE_FIAT=ON", "-DENABLE_OMP=OFF", "-DENABLE_ACC=ON", "-DENABLE_NVTX=OFF",
        f"-DENABLE_BITIDENTITY_TESTING={os.environ.get('ECRAD_BITIDENTITY', 'ON')}",
        f"-DENABLE_SINGLE_PRECISION={'ON' if PRECISION == 'sp' else 'OFF'}",
        f"-DENABLE_DOUBLE_PRECISION={'ON' if PRECISION == 'dp' else 'OFF'}",
        "-DBUILD_SHARED_LIBS=OFF", "-DENABLE_TESTS=ON"], RUN / f"configure-{label}.log")
    logged(["cmake", "--build", path, "-j", "6"], RUN / f"build-{label}.log")


def execute(label, batch, repeat, tag, columns=0):
    folder = RUN / tag
    folder.mkdir()
    text = (CI / "further-1/five-band-20261014-integrated-v1/config.nam").read_text()
    for key, value in dict(nblocksize=32, nrepeat=repeat, nwarmup=1, istartcol=1 if columns else 0,
                           iendcol=columns).items():
        text, count = re.subn(rf"(?m)^(\s*{key}\s*=\s*)\d+", rf"\g<1>{value}", text)
        assert count == 1
    text = text.replace('directory_name = "data"', f'directory_name = "{RUN / "sources" / label / "data"}"')
    config = folder / "config.nam"
    config.write_text(text)
    env = os.environ.copy()
    env["ECRAD_SW_BATCH_SIZE"] = str(batch)
    log = folder / "run.log"
    output = folder / "output.nc"
    logged([RUN / "builds" / label / f"bin/ecrad_ifs_blocked_{PRECISION}", config, INPUT, output], log, env)
    rows = [float(line.split()[-1]) for line in log.read_text().splitlines() if line.startswith("BATCH_ITER ")]
    assert len(rows) == repeat
    result: dict[str, Any] = dict(label=label, batch=batch, tag=tag, times=rows,
                                  median=statistics.median(rows[1:]))
    return result, output


def compare(reference, output):
    errors = {}
    with xr.open_dataset(reference) as ref, xr.open_dataset(output) as actual:
        assert dict(ref.sizes) == dict(actual.sizes)
        assert set(ref.data_vars) == set(actual.data_vars)
        for name in ref.data_vars:
            a, b = np.asarray(ref[name]), np.asarray(actual[name])
            assert np.isfinite(a).all() and np.isfinite(b).all(), name
            error = float(np.max(np.abs(a-b)))
            assert error <= (0.001 if PRECISION == "dp" or "lw" in name.lower() else 0.1), (name, error)
            errors[name] = error
    return errors


def main():
    prepare()
    for label in ("baseline", "candidate"):
        build(label)
    variants = [("baseline", 0), ("candidate", 0), ("candidate", 1), ("candidate", 16), ("candidate", 32)]
    checks = []
    reference = None
    for columns in (65, 0):
        reference = None
        for label, batch in variants:
            row, output = execute(label,batch,2,f"check-{columns}-{label}-K{batch}",columns)
            if reference is None:
                reference = output
            assert reference is not None
            row["errors"] = compare(reference,output)
            checks.append(row)
            (RUN / "validation.json").write_text(json.dumps(checks,indent=2))
    for label in ("baseline", "candidate"):
        logged(["ctest", "--test-dir", RUN / "builds" / label, "--output-on-failure"], RUN / f"ctest-{label}.log")
    if os.environ.get("ECRAD_INTEGRATION_VALIDATE_ONLY") == "1":
        print("VALIDATION_PASSED", PRECISION, flush=True)
        return
    records = []
    assert reference is not None
    for process in range(5):
        order = variants[process:] + variants[:process]
        if process % 2:
            order = order[::-1]
        for label,batch in order:
            row,output = execute(label,batch,10,f"time-{process}-{label}-K{batch}")
            row["errors"] = compare(reference,output)
            records.append(row)
            (RUN / "timing.json").write_text(json.dumps(records,indent=2))
    lines = ["# Integrated SW batch SP B32 results", "", "Baseline is pristine 2be60a2, not the later five-band gas candidate.", "", "| Mode | K | Median seconds | Reduction |", "|---|---:|---:|---:|"]
    ref = statistics.median(r["median"] for r in records if r["label"] == "baseline")
    for label,batch in variants:
        values = [r["median"] for r in records if (r["label"],r["batch"]) == (label,batch)]
        median = statistics.median(values)
        lines.append(f"| {label} | {batch} | {median:.7f} | {100*(1-median/ref):.3f}% |")
    (RUN / "RESULTS.md").write_text("\n".join(lines)+"\n")
    print("\n".join(lines),flush=True)


if __name__ == "__main__":
    main()
