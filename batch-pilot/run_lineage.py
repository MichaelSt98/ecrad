"""Quarter-node SP lineage experiment with frozen sources and recorded binary reuse."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tarfile
from typing import Any

import numpy as np
import xarray as xr

ROOT = Path(__file__).resolve().parent
WORK = ROOT.parent
CI = WORK.parent
RUN = ROOT / f"lineage-{os.environ['SLURM_JOB_ID']}"
DEPS = CI / "pr119/deps"
INPUT = CI / "benchmark-nvhpc/input/rad4NN_inputs_2020010100_0c0.nc"
REVISIONS = {"cpu": "2be60a2", "master-acc": "4dad0bd",
             "master-opt": "7ea1fef", "release-opt": "8414ec3",
             "small-np": "2be60a2", "batch": "9ee11eb"}
VARIANTS = [(name, 0) for name in REVISIONS if name != "batch"] + [("batch", 16), ("batch", 32)]


def logged(command, path, env=None, cwd=None):
    print("RUN", " ".join(map(str, command)), flush=True)
    with path.open("w") as output:
        # Site subprocess helpers can leave SIGCHLD blocked after check_output;
        # CMake's child-process event loop requires it unblocked in the child.
        subprocess.run(list(map(str, command)), cwd=cwd or RUN, env=env, stdout=output,
                       stderr=subprocess.STDOUT, check=True, timeout=900,
                       preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGCHLD}))


def prepare(reuse=None):
    RUN.mkdir()
    topology = subprocess.check_output(["lscpu", "-p=CPU,CORE,SOCKET,NODE"]).decode()
    affinity = sorted(os.sched_getaffinity(0))
    topology_rows = [line.split(",") for line in topology.splitlines() if not line.startswith("#")]
    cores = {(int(v[1]), int(v[2])) for v in topology_rows if int(v[0]) in affinity}
    assert len(cores) == 32, ("Expected 32 physical cores", affinity, cores)
    metadata = dict(host=os.uname().nodename, affinity=affinity, physical_cores=len(cores),
                    environment=dict(os.environ), topology=topology,
                    input_sha256=hashlib.sha256(INPUT.read_bytes()).hexdigest())
    assert metadata["input_sha256"] == "214b418cccfaac72b2f772eb7a8135ac8e8f8a2719fed96764d6caef51d5cb89"
    # Avoid archiving environment variables unrelated to reproducibility.
    metadata["environment"] = {k: v for k, v in os.environ.items()
                               if k.startswith(("SLURM_", "OMP_", "CUDA_", "ACC_"))}
    (RUN / "allocation.json").write_text(json.dumps(metadata, indent=2))
    logged(["nvidia-smi", "-q"], RUN / "gpu.log")
    logged(["scontrol", "show", "job", os.environ["SLURM_JOB_ID"]], RUN / "allocation.log")
    if reuse is not None:
        for name in ("sources", "builds"):
            (RUN / name).symlink_to(reuse / name, target_is_directory=True)
        shutil.copy2(reuse / "snapshots.json", RUN / "snapshots.json")
        (RUN / "reused-builds.json").write_text(json.dumps(dict(origin=str(reuse),
            binaries={label: hashlib.sha256((reuse / "builds" / label / "bin/ecrad_ifs_blocked_sp").read_bytes()).hexdigest()
                      for label in REVISIONS}), indent=2))
        shutil.copy2(__file__, RUN / "run_lineage.py")
        return
    (RUN / "sources").mkdir()
    manifest = {}
    for label, revision in REVISIONS.items():
        source = RUN / "sources" / label
        source.mkdir()
        archive = subprocess.check_output(["git", "archive", revision], cwd=WORK)
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            stream.extractall(source, filter="data")
        driver = source / "driver/ecrad_ifs_driver_blocked.F90"
        text = driver.read_text()
        if label != "batch":
            text = text.replace("only : jprb, jprd", "only : jprb, jprd, jpib")
            text = text.replace("  integer :: jrepeat", "  integer(kind=jpib) :: it0,it1,irate\n  integer :: jrepeat")
            text = text.replace("  do jrepeat = 1,driver_config%nrepeat",
                                "  do jrepeat = 1,driver_config%nrepeat\n    !$acc wait\n    call system_clock(it0,irate)")
            # END PARALLEL DO is conditional in older drivers. Time after its #endif.
            marker = "\n!    else\n"
            assert text.count(marker) == 1
            text = text.replace(marker, "\n      !$acc wait\n      call system_clock(it1)\n"
                "      write(nulout,'(a,2i6,es24.16)') 'BATCH_ITER ',0,jrepeat, &\n"
                "        & real(it1-it0,jprd)/real(irate,jprd)\n" + marker)
        probe = ""
        if label == "cpu":
            text = text.replace("  implicit none", "  use omp_lib, only : omp_get_num_threads\n  implicit none")
            probe = ("  !$omp parallel\n  !$omp single\n"
                     "  write(nulout,'(a,i0)') 'LINEAGE_THREADS ',omp_get_num_threads()\n"
                     "  !$omp end single\n  !$omp end parallel\n")
        else:
            text = text.replace("  implicit none", "  use openacc, only : acc_on_device, acc_device_nvidia\n  implicit none")
            text = text.replace("  integer :: jrepeat", "  integer :: lineage_device\n  integer :: jrepeat")
            probe = ("  lineage_device=0\n  !$acc serial copyout(lineage_device)\n"
                     "  lineage_device=merge(1,0,acc_on_device(acc_device_nvidia))\n"
                     "  !$acc end serial\n  if (lineage_device /= 1) stop 2\n"
                     "  write(nulout,'(a,i0)') 'LINEAGE_GPU ',lineage_device\n")
        text = text.replace("  do jrepeat = 1,driver_config%nrepeat", probe +
                            "  write(nulout,'(a,2i8)') 'LINEAGE_DOMAIN ',ncol,nlev\n"
                            "  do jrepeat = 1,driver_config%nrepeat")
        driver.write_text(text)
        manifest[label] = dict(commit=subprocess.check_output(["git", "rev-parse", revision], cwd=WORK).decode().strip(),
            files={str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in source.rglob("*") if p.is_file()})
    (RUN / "snapshots.json").write_text(json.dumps(manifest, indent=2))
    shutil.copy2(__file__, RUN / "run_lineage.py")


def build(label):
    cpu = label == "cpu"
    path = RUN / "builds" / label
    logged(["cmake", "-S", RUN / "sources" / label, "-B", path, "-G", "Ninja",
        f"-Decbuild_ROOT={DEPS / 'ecbuild'}", f"-Dfiat_ROOT={DEPS / 'fiat-install'}",
        "-DCMAKE_Fortran_COMPILER=nvfortran", "-DCMAKE_BUILD_TYPE=Release",
        "-DECRAD_Fortran_FLAGS=-O3 -Mrecursive" + ("" if cpu else " -gpu=cc80"),
        "-DCMAKE_EXE_LINKER_FLAGS=-L/usr/local/apps/nvidia/24.5/Linux_x86_64/24.5/cuda/lib64 -lcudart",
        "-DENABLE_FIAT=ON", f"-DENABLE_OMP={'ON' if cpu else 'OFF'}",
        f"-DENABLE_ACC={'OFF' if cpu else 'ON'}", f"-DENABLE_GPU={'OFF' if cpu else 'ON'}",
        "-DENABLE_NVTX=OFF", "-DENABLE_ROCTX=OFF", "-DENABLE_BITIDENTITY_TESTING=ON",
        "-DENABLE_SINGLE_PRECISION=ON", "-DENABLE_DOUBLE_PRECISION=OFF",
        "-DBUILD_SHARED_LIBS=OFF", "-DENABLE_TESTS=ON"], RUN / f"configure-{label}.log")
    logged(["cmake", "--build", path, "-j", "16"], RUN / f"build-{label}.log")


def execute(label, batch, repeat, tag) -> dict[str, Any]:
    folder = RUN / tag
    folder.mkdir()
    text = (CI / "benchmark-nvhpc/config.nam").read_text()
    for key, value in dict(nblocksize=32, nrepeat=repeat, nwarmup=1, istartcol=0, iendcol=0).items():
        text, count = re.subn(rf"(?m)^(\s*{key}\s*=\s*)\d+", rf"\g<1>{value}", text)
        assert count == 1
    text = text.replace('directory_name = "data"', f'directory_name = "{RUN / "sources" / label / "data"}"')
    cpu_threads = int(os.environ.get("ECRAD_CPU_THREADS", "32"))
    if label == "cpu" and os.environ.get("ECRAD_CPU_NATIVE") == "1":
        text = text.replace('solver_name = "McICA ACC"', 'solver_name = "McICA"')
    config = folder / "config.nam"
    config.write_text(text)
    env = os.environ.copy()
    env.update(ECRAD_SW_BATCH_SIZE=str(batch), OMP_NUM_THREADS=str(cpu_threads) if label == "cpu" else "1",
               OMP_PLACES="cores", OMP_PROC_BIND="close", OMP_DYNAMIC="FALSE", OMP_DISPLAY_ENV="TRUE")
    log = folder / "run.log"
    command = [RUN / "builds" / label / "bin/ecrad_ifs_blocked_sp", config, INPUT, folder / "output.nc"]
    if label != "cpu":
        command = ["taskset", "-c", str(min(os.sched_getaffinity(0))), *command]
    logged(command, log, env, folder)
    content = log.read_text()
    assert "Floating-point precision: single" in content
    assert re.search(r"LINEAGE_DOMAIN\s+16960\s+137", content)
    assert (f"LINEAGE_THREADS {cpu_threads}" if label == "cpu" else "LINEAGE_GPU 1") in content
    times = [float(line.split()[-1]) for line in content.splitlines() if line.startswith("BATCH_ITER ")]
    assert len(times) == repeat
    return dict(label=label, batch=batch, tag=tag, times=times,
                median=statistics.median(times[1:]))


def compare(reference, output, strict=True):
    errors, conventions, failures = {}, {}, {}
    with xr.open_dataset(reference) as ref, xr.open_dataset(output) as actual, xr.open_dataset(INPUT) as inputs:
        assert dict(ref.sizes) == dict(actual.sizes)
        assert actual.sizes["column"] == 16960
        assert set(ref.data_vars) == set(actual.data_vars)
        day = np.asarray(inputs["cos_solar_zenith_angle"]).reshape(-1) > 0
        for name in ref.data_vars:
            assert ref[name].dims == actual[name].dims
            a, b = np.asarray(ref[name], dtype=float), np.asarray(actual[name], dtype=float)
            assert np.isfinite(a).all() and np.isfinite(b).all(), name
            error = float(np.max(np.abs(a-b)))
            tolerance = 0.1 if "sw" in name.lower() else 0.001 if "lw" in name.lower() else 0.0
            if name == "cloud_cover_sw" and error > tolerance:
                daytime = float(np.max(np.abs(a.reshape(-1)[day]-b.reshape(-1)[day])))
                assert daytime <= 1.e-6 and (b.reshape(-1)[~day] == 0).all(), (name, error, daytime)
                conventions[name] = dict(daytime_max=daytime, actual_night="zero", raw_max=error)
            else:
                if error > tolerance:
                    failures[name] = dict(error=error, tolerance=tolerance)
            errors[name] = error
    if strict:
        assert not failures, failures
    return dict(errors=errors, conventions=conventions, passed=not failures, failures=failures)


def main():
    reuse = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 and sys.argv[1] == "--reuse" else None
    if reuse is None and os.environ.get("ECRAD_LINEAGE_REUSE"):
        reuse = Path(os.environ["ECRAD_LINEAGE_REUSE"]).resolve()
    prepare(reuse)
    if reuse is None:
        for label in REVISIONS:
            build(label)
    records = []
    reference = None
    controls = {}
    for process in range(5):
        order = VARIANTS[process:] + VARIANTS[:process]
        if process % 2:
            order = order[::-1]
        for label, batch in order:
            row = execute(label, batch, 10, f"time-{process}-{label}-K{batch}")
            output = RUN / row["tag"] / "output.nc"
            if reference is None:
                assert label == "cpu"
                reference = output
            # Preserve cross-version failures explicitly rather than silently
            # relaxing numerical tolerances or suppressing historical timings.
            row.update(compare(reference, output, strict=False))
            key = (label, batch)
            if key not in controls:
                controls[key] = output
            row["repeat_validation"] = compare(controls[key], output)
            if label == "batch":
                row["batch_parent_validation"] = compare(controls[("small-np", 0)], output)
            records.append(row)
            (RUN / "timing.json").write_text(json.dumps(records, indent=2))
    lines = ["# Quarter-node SP lineage matrix", "", f"Job {os.environ['SLURM_JOB_ID']}; {os.uname().nodename}.",
             "NPROMA=32; one GPU / 32 physical CPU cores; five processes, nine measured iterations each.", "",
             "| Version | K | Seconds | Process range | CPU / version | CPU agreement |", "|---|---:|---:|---|---:|---|"]
    cpu = statistics.median(r["median"] for r in records if r["label"] == "cpu")
    for label, batch in VARIANTS:
        values = [r["median"] for r in records if (r["label"], r["batch"]) == (label, batch)]
        median = statistics.median(values)
        passed = all(r["passed"] for r in records if (r["label"], r["batch"]) == (label, batch))
        lines.append(f"| {label} | {batch} | {median:.7f} | {min(values):.7f}–{max(values):.7f} | {cpu/median:.3f} | {'pass' if passed else 'FAIL'} |")
    (RUN / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    for label in ("cpu", "batch"):
        env = os.environ.copy()
        env["OMP_NUM_THREADS"] = "32" if label == "cpu" else "1"
        logged(["ctest", "--test-dir", RUN / "builds" / label, "--output-on-failure"], RUN / f"ctest-{label}.log", env)


def diagnose(previous):
    global RUN
    RUN.mkdir()
    for name in ("sources", "builds"):
        (RUN / name).symlink_to(previous / name, target_is_directory=True)
    records = []
    for native in (False, True):
        os.environ["ECRAD_CPU_NATIVE"] = str(int(native))
        for threads in (1, 32):
            os.environ["ECRAD_CPU_THREADS"] = str(threads)
            records.append(execute("cpu", 0, 2, f"diag-native{int(native)}-threads{threads}"))
    for label in ("master-opt", "small-np", "batch"):
        records.append(execute(label, 32 if label == "batch" else 0, 2, f"diag-{label}"))
    outputs = {r["tag"]: RUN / r["tag"] / "output.nc" for r in records}
    outputs["original-master-acc"] = previous / "time-0-master-acc-K0/output.nc"
    outputs["original-cpu32"] = previous / "time-0-cpu-K0/output.nc"
    reference = outputs["diag-native1-threads1"]
    differences = {}
    with xr.open_dataset(reference) as ref:
        for tag, output in outputs.items():
            with xr.open_dataset(output) as data:
                differences[tag] = {name: float(np.max(np.abs(np.asarray(ref[name], dtype=float)-np.asarray(data[name], dtype=float))))
                                    for name in ref.data_vars}
    (RUN / "diagnosis.json").write_text(json.dumps(dict(records=records, differences=differences), indent=2))
    print(json.dumps(differences, indent=2), flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--diagnose":
        diagnose(Path(sys.argv[2]).resolve())
    else:
        main()
