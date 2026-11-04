"""SP half-node benchmark. Prepare with: prepare INPUT.nc CONFIG.nam.

Run benchmark_gpu.sbatch inside a 64-physical-core/two-GPU allocation.
Timings include per-iteration transfers and are validated against saved fluxes.
"""
from pathlib import Path
import ctypes as ct
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time

import numpy as np
import xarray as xr

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "build/perf-sp"
INPUT = ROOT / "input-full.nc"
CONFIG = ROOT / "benchmark-config.nam"
GROUPS = 5
ITERATIONS = 6
CASES = {
    "acc-opt": ("acc-opt", "gpu", 0, "McICA ACC"),
    "acc-opt-large-block": ("acc-opt", "gpu", 0, "McICA ACC"),
    "optimized-cpu": ("optimized-cpu", "cpu", 0, "McICA"),
    "optimized-cpu-matched": ("optimized-cpu", "cpu", 0, "McICA ACC"),
    "optimized-acc": ("optimized-acc", "gpu", 0, "McICA ACC"),
    "optimized-acc-tiled": ("optimized-acc", "gpu", 32, "McICA ACC"),
    "optimized-omp": ("optimized-omp", "gpu", 0, "McICA ACC"),
    "optimized-omp-tiled": ("optimized-omp", "gpu", 32, "McICA ACC"),
    "main-cpu": ("main-cpu", "cpu", 0, "McICA"),
    "current-cpu": ("current-cpu", "cpu", 0, "McICA"),
    "current-cpu-matched": ("current-cpu", "cpu", 0, "McICA ACC"),
    "master-acc": ("master-acc", "gpu", 0, "McICA ACC"),
    "current-acc": ("current-acc", "gpu", 0, "McICA ACC"),
    "acc-tiled": ("acc-tiled", "gpu", 32, "McICA ACC"),
    "current-acc-tiled": ("current-acc", "gpu", 32, "McICA ACC"),
    "master-omp": ("master-omp", "gpu", 0, "McICA ACC"),
    "current-omp": ("current-omp", "gpu", 0, "McICA ACC"),
    "omp-tiled": ("omp-tiled", "gpu", 32, "McICA ACC"),
    "current-omp-tiled": ("current-omp", "gpu", 32, "McICA ACC"),
    "omp-fork-large-block": ("omp-tiled", "gpu", 0, "McICA ACC"),
}

def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")

def descendants(pid):
    result = {pid}
    try:
        for child in Path(f"/proc/{pid}/task/{pid}/children").read_text().split():
            result.update(descendants(int(child)))
    except FileNotFoundError:
        pass
    return result

class GPUProcessMemory:
    class Process(ct.Structure):
        _fields_ = [("pid", ct.c_uint), ("used", ct.c_ulonglong),
                    ("gpu_instance", ct.c_uint), ("compute_instance", ct.c_uint)]

    def __init__(self):
        self.lib = ct.CDLL("libnvidia-ml.so.1")
        assert self.lib.nvmlInit_v2() == 0
        count = ct.c_uint()
        assert self.lib.nvmlDeviceGetCount_v2(ct.byref(count)) == 0
        self.handles = []
        self.devices_used = set()
        for index in range(count.value):
            handle = ct.c_void_p()
            assert self.lib.nvmlDeviceGetHandleByIndex_v2(index, ct.byref(handle)) == 0
            self.handles.append(handle)

    def sample(self, pids):
        total = 0
        for handle in self.handles:
            count = ct.c_uint(128)
            processes = (self.Process * 128)()
            code = self.lib.nvmlDeviceGetComputeRunningProcesses_v2(handle, ct.byref(count), processes)
            assert code == 0, code
            used = sum(p.used for p in processes[:count.value]
                       if p.pid in pids and p.used < 2**63)
            total += used
            if used:
                uuid = ct.create_string_buffer(96)
                assert self.lib.nvmlDeviceGetUUID(handle, uuid, 96) == 0
                self.devices_used.add(uuid.value.decode())
        return total

def configuration(path, sampler):
    text = CONFIG.read_text()
    for key, value in {"nblocksize": "32", "nrepeat": str(ITERATIONS), "nwarmup": "1",
                       "iverbose": "1", "directory_name": '"' + str(REPO / "data") + '"',
                       "sw_solver_name": '"' + sampler + '"',
                       "lw_solver_name": '"' + sampler + '"'}.items():
        text, n = re.subn(rf"(?m)^(\s*{key}\s*=)[^\n]*", rf"\g<1> {value},", text)
        assert n
    # These driver-side switches existed in the GPU forks but not main.
    text = re.sub(r"(?m)^\s*do_cloud_aerosol_per_[sl]w_g_point\s*=.*\n", "", text)
    if path.stem == "main-cpu":
        text = re.sub(r"(?m)^\s*nwarmup\s*=.*\n", "", text)
    if path.stem in ("omp-fork-large-block", "acc-opt-large-block"):
        text = re.sub(r"(?m)^(\s*nblocksize\s*=).*", r"\g<1> 1024,", text)
    path.write_text(text)

def prepare():
    ROOT.mkdir(parents=True, exist_ok=True)
    original_input = Path(sys.argv[2]).resolve()
    if INPUT.is_symlink():
        assert INPUT.resolve() == original_input, "Existing input link points to another workload"
    elif not INPUT.exists():
        INPUT.symlink_to(original_input)
    CONFIG.write_text(Path(sys.argv[3]).read_text())
    expected = "214b418cccfaac72b2f772eb7a8135ac8e8f8a2719fed96764d6caef51d5cb89"
    assert hashlib.sha256(INPUT.read_bytes()).hexdigest() == expected
    with xr.open_dataset(INPUT) as data:
        assert data.sizes["column"] == 16960
        for rank in range(2):
            data.isel(column=slice(rank * 8480, (rank + 1) * 8480)).to_netcdf(ROOT / f"input-{rank}.nc")
    for label, (_, _, _, sampler) in CASES.items():
        configuration(ROOT / f"{label}.nam", sampler)
    for source in (ROOT / "sources").iterdir():
        with (ROOT / f"{source.name}-benchmark.patch").open("w") as patch:
            subprocess.run(["git", "diff"], cwd=source, stdout=patch, check=True)

def worker(label, group, directory):
    build, mode, batch, _ = CASES[label]
    rank = int(os.environ.get("SLURM_PROCID", "0"))
    directory = Path(directory) / f"{label}-g{group}-r{rank}"
    directory.mkdir()
    config_path = directory / "config.nam"
    config_path.write_text((ROOT / f"{label}.nam").read_text())
    env = dict(os.environ, OMP_NUM_THREADS="64" if mode == "cpu" else "1",
               OMP_PROC_BIND="close", OMP_PLACES="cores", OMP_STACKSIZE="256M",
               OMP_TARGET_OFFLOAD="MANDATORY", ACC_DEVICE_TYPE="nvidia",
               OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    for key in ("ECRAD_FULL_BATCH_SIZE", "ECRAD_BATCH_SIZE", "ECRAD_NPARTS", "ECRAD_PART",
                "ECRAD_TILE_OVERLAP", "NVCOMPILER_ACC_CUDA_STACKSIZE"):
        env.pop(key, None)
    if batch:
        env["ECRAD_FULL_BATCH_SIZE"] = str(batch)
    topology = subprocess.check_output(["lscpu", "-p=CPU,CORE,SOCKET"], text=True)
    cores = {int(a): (int(b), int(c)) for line in topology.splitlines() if not line.startswith("#")
             for a, b, c in [line.split(",")]}
    affinity = sorted(os.sched_getaffinity(0))
    physical = {cores[cpu] for cpu in affinity}
    assert len(physical) == (64 if mode == "cpu" else 32), (affinity, physical)
    binary = ROOT / "builds" / build / "bin/ecrad_ifs_blocked_sp"
    metadata = {"label": label, "group": group, "rank": rank,
                "affinity": affinity, "physical_cores": sorted(physical),
                "CUDA_VISIBLE_DEVICES": env.get("CUDA_VISIBLE_DEVICES"),
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    dump(directory / "metadata.json", metadata)
    sampler = GPUProcessMemory() if mode == "gpu" else None
    peak = 0
    samples = []
    start = time.monotonic()
    with (directory / "run.log").open("w") as log:
        child = subprocess.Popen([
            "/usr/bin/time", "-v", "-o", str(directory / "rss.txt"), str(binary),
            str(config_path),
            str(INPUT if mode == "cpu" else ROOT / f"input-{rank}.nc"),
            str(directory / "output.nc"),
        ], cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT)
        while child.poll() is None:
            if sampler:
                used = sampler.sample(descendants(child.pid))
                peak = max(peak, used)
                samples.append([time.time(), used])
            time.sleep(0.02)
    elapsed = time.monotonic() - start
    log_text = (directory / "run.log").read_text()
    iterations = [float(t) for t in re.findall(r"PERF_ITER\s+\d+\s+([\d.]+)", log_text)]
    rss_text = (directory / "rss.txt").read_text()
    rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", rss_text)
    result = dict(metadata, exit_code=child.returncode, iterations_s=iterations,
                  host_peak_rss_MiB=int(rss[1]) / 1024 if rss else None,
                  gpu_peak_MiB=peak / 2**20, process_wall_s=elapsed,
                  gpu_uuids=sorted(sampler.devices_used) if sampler else [],
                  gpu_memory_sample_count=len(samples))
    dump(directory / "gpu-memory.json", samples)
    dump(directory / "result.json", result)
    assert child.returncode == 0 and len(iterations) == ITERATIONS, result
    if mode == "gpu":
        assert peak > 0, "No actual device allocation observed"

def controller():
    directory = ROOT / "results" / os.environ["SLURM_JOB_ID"]
    directory.mkdir()
    dump(directory / "environment.json", dict(os.environ))
    dump(directory / "protocol.json", {"process_groups": GROUPS, "iterations_per_process": ITERATIONS,
                                      "warmup_iterations_excluded": 1,
                                      "numpy": np.__version__, "xarray": xr.__version__})
    (directory / "topology.txt").write_text(subprocess.check_output(["lscpu"], text=True))
    (directory / "gpus.txt").write_text(subprocess.check_output(["nvidia-smi"], text=True))
    labels = sys.argv[2:] or ["optimized-acc-tiled", "optimized-omp-tiled", "acc-tiled",
                             "omp-fork-large-block", "optimized-acc", "optimized-omp", "master-omp",
                             "main-cpu", "optimized-cpu", "optimized-cpu-matched"]
    results = []
    for group in range(GROUPS):
        order = labels if group % 2 == 0 else labels[::-1]
        for label in order:
            _, mode, _, _ = CASES[label]
            command = ["srun", "--ntasks=" + ("1" if mode == "cpu" else "2"),
                       "--cpus-per-task=" + ("64" if mode == "cpu" else "32"),
                       "--gpus-per-task=" + ("2" if mode == "cpu" else "1"), "--cpu-bind=cores",
                       "--tres-bind=gres/gpu:per_task:" + ("2" if mode == "cpu" else "1"),
                       sys.executable, str(Path(__file__).resolve()), "worker", label, str(group), str(directory)]
            print("RUN", label, "group", group, flush=True)
            run = subprocess.run(command)
            row = {"label": label, "group": group, "step_exit_code": run.returncode}
            ranks = [json.loads(p.read_text()) for p in sorted(directory.glob(f"{label}-g{group}-r*/result.json"))]
            row["ranks"] = ranks
            if run.returncode == 0 and ranks:
                if mode == "gpu":
                    assert len(ranks) == 2 and all(len(r["gpu_uuids"]) == 1 for r in ranks)
                    assert ranks[0]["gpu_uuids"] != ranks[1]["gpu_uuids"], "Ranks shared a GPU"
                times = [max(rank["iterations_s"][i] for rank in ranks) for i in range(1, ITERATIONS)]
                row.update(iterations_s=times, median_s=statistics.median(times),
                           sum_host_peak_rss_MiB=sum(r["host_peak_rss_MiB"] for r in ranks),
                           sum_gpu_peak_MiB=sum(r["gpu_peak_MiB"] for r in ranks))
                print(label, row["median_s"], "s", row["sum_host_peak_rss_MiB"], "MiB host",
                      row["sum_gpu_peak_MiB"], "MiB device", flush=True)
            results.append(row)
            dump(directory / "summary.json", results)
    assert all(row["step_exit_code"] == 0 for row in results), "At least one benchmark process failed"

if __name__ == "__main__":
    if sys.argv[1] == "prepare":
        prepare()
    elif sys.argv[1] == "worker":
        worker(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    else:
        controller()
