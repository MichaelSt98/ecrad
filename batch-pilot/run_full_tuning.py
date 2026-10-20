"""Nsight-guided SW launch tuning, with a fresh quarter-node CPU comparison."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
from typing import Any

import xarray as xr
import run_integration as bench
import run_lineage as lineage

ROOT = Path(__file__).resolve().parent
RUN = ROOT / f"milestone-tuning-{os.environ['SLURM_JOB_ID']}"
BASE = ROOT / "milestone-full-30164046"
OLD = ROOT / "milestone-gas-30153332"
CPU = ROOT / "lineage-30135604/builds/cpu/bin/ecrad_ifs_blocked_sp"
bench.RUN = RUN
lineage.RUN = RUN
bench.logged = lineage.logged
BINS = {"default": BASE / "builds/candidate/bin/ecrad_ifs_blocked_sp", "cpu": CPU}


def execute(label, batch, repeat, tag, input_file=None, precision="sp", nsys=False):
    folder = RUN / tag
    folder.mkdir()
    text = (BASE / "check-sp-16960-parent/config.nam").read_text()
    for key, value in dict(nrepeat=repeat, nwarmup=1).items():
        text, count = re.subn(rf"(?m)^(\s*{key}\s*=\s*)\d+", rf"\g<1>{value}", text)
        assert count == 1
    config = folder / "config.nam"
    config.write_text(text)
    env = os.environ.copy()
    env.update(OMP_NUM_THREADS="32" if label == "cpu" else "1", OMP_PLACES="cores", OMP_PROC_BIND="close",
               OMP_DYNAMIC="FALSE", ECRAD_FULL_BATCH_SIZE=str(batch), ECRAD_BATCH_PROFILE="0",
               ECRAD_SW_BATCH_SIZE="32" if label == "parent-nobit" else "0",
               ECRAD_LW_BATCH="1", ECRAD_GAS_BATCH="1", ECRAD_NSYS_CAPTURE=str(int(nsys)))
    output = folder / "output.nc"
    command = [BINS[label], config, input_file or bench.INPUT, output]
    if nsys:
        command = ["nsys", "profile", "--trace=cuda,osrt", "--sample=none", "--cpuctxsw=none",
                   "--capture-range=cudaProfilerApi", "--capture-range-end=stop", "--cuda-memory-usage=true",
                   "--force-overwrite=true", "-o", folder / "trace", *command]
    lineage.logged(command, folder / "run.log", env, folder)
    content = (folder / "run.log").read_text()
    times = [float(line.split()[-1]) for line in content.splitlines() if line.startswith("BATCH_ITER ")]
    assert len(times) == repeat
    if label == "cpu":
        assert "LINEAGE_THREADS 32" in content
    row: dict[str, Any] = dict(label=label, batch=batch, tag=tag, times=times, median=statistics.median(times[1:]))
    if nsys:
        lineage.logged(["nsys", "stats", "--report=cuda_gpu_kern_sum,cuda_api_sum,cuda_gpu_mem_time_sum",
                        "--format=csv", str(folder / "trace.nsys-rep")], folder / "stats.log")
    return row, output


def main():
    os.environ["ECRAD_PROFILE_API"] = "1"
    bench.prepare()
    shutil.copy2(__file__, RUN / "run_full_tuning.py")
    topology = subprocess.check_output(["lscpu", "-p=CPU,CORE,SOCKET,NODE"]).decode()
    affinity = sorted(os.sched_getaffinity(0))
    entries = [line.split(",") for line in topology.splitlines() if not line.startswith("#")]
    cores = {(v[1], v[2]) for v in entries if int(v[0]) in affinity}
    assert len(cores) == 32
    (RUN / "allocation.json").write_text(json.dumps(dict(host=os.uname().nodename, affinity=affinity,
        physical_cores=len(cores), topology=topology, input_sha256=hashlib.sha256(bench.INPUT.read_bytes()).hexdigest()), indent=2))
    lineage.logged(["nvidia-smi", "-q"], RUN / "gpu.log")
    reference = BASE / "check-sp-16960-parent/output.nc"
    validation = []
    pilots = []
    for vector in (32, 64, 256):
        label = f"vl{vector}"
        (RUN / "sources" / label).symlink_to(RUN / "sources/candidate", target_is_directory=True)
        os.environ["ECRAD_EXTRA_FLAGS"] = f"-DECRAD_SW_VECTOR_LENGTH={vector}"
        bench.build(label)
        BINS[label] = RUN / "builds" / label / "bin/ecrad_ifs_blocked_sp"
        for input_file, count in ((BASE / "mixed-65.nc", 65), (bench.INPUT, 16960)):
            row, output = execute(label, 128, 3, f"pilot-{label}-{count}", input_file)
            ref = reference if count == 16960 else BASE / "check-sp-65-parent/output.nc"
            row["errors"] = bench.compare(ref, output)
            validation.append(row)
            if count == 16960:
                pilots.append(row)
            (RUN / "validation.json").write_text(json.dumps(validation, indent=2))
    row, output = execute("default", 256, 3, "pilot-default-K256")
    row["errors"] = bench.compare(reference, output)
    pilots.append(row)
    validation.append(row)
    (RUN / "pilots.json").write_text(json.dumps(pilots, indent=2))
    variants = [("cpu", 0), ("default", 128), ("default", 256), ("vl32", 128), ("vl64", 128), ("vl256", 128)]
    best_vector = min(pilots[:3], key=lambda r: r["median"])["label"]
    variants.append((best_vector, 256))
    (RUN / "binaries.json").write_text(json.dumps({label: dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                                                   for label, path in BINS.items()}, indent=2))
    records = []
    cpu_reference = None
    for process in range(5):
        offset = process % len(variants)
        order = variants[offset:] + variants[:offset]
        if process % 2:
            order = order[::-1]
        for label, batch in order:
            row, output = execute(label, batch, 10, f"time-{process}-{label}-K{batch}")
            if label == "cpu":
                if cpu_reference is None:
                    cpu_reference = output
                row["errors"] = bench.compare(cpu_reference, output)
            else:
                row["errors"] = bench.compare(reference, output)
            records.append(row)
            (RUN / "timing.json").write_text(json.dumps(records, indent=2))
    cpu = statistics.median(r["median"] for r in records if r["label"] == "cpu")
    summary = {}
    lines = ["# Full-pipeline launch tuning and fresh quarter-node CPU baseline", "",
             "| Version | K | Seconds | Process range | CPU / version |", "|---|---:|---:|---|---:|"]
    for label, batch in variants:
        values = [r["median"] for r in records if (r["label"], r["batch"]) == (label, batch)]
        value = statistics.median(values)
        summary[(label, batch)] = value
        lines.append(f"| {label} | {batch} | {value:.7f} | {min(values):.7f}–{max(values):.7f} | {cpu/value:.3f} |")
    (RUN / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    best_label, best_batch = min((v for v in variants if v[0] != "cpu"), key=lambda v: summary[v])
    selected_vector = "" if best_label == "default" else f"-DECRAD_SW_VECTOR_LENGTH={best_label[2:]}"
    (RUN / "selected.json").write_text(json.dumps(dict(label=best_label, batch=best_batch,
        median=summary[(best_label, best_batch)], cpu=cpu, flags=selected_vector), indent=2))
    row, output = execute(best_label, best_batch, 2, "nsys-selected", nsys=True)
    row["errors"] = bench.compare(reference, output)
    (RUN / "nsys.json").write_text(json.dumps(row, indent=2))
    # Check the selected launch configuration in DP, then check ordinary (not
    # bit-identity) inputs against a separately built frozen-parent GPU source.
    os.environ["ECRAD_EXTRA_FLAGS"] = selected_vector
    bench.PRECISION = "dp"
    (RUN / "sources/selected-dp").symlink_to(RUN / "sources/candidate", target_is_directory=True)
    bench.build("selected-dp")
    BINS["selected-dp"] = RUN / "builds/selected-dp/bin/ecrad_ifs_blocked_dp"
    for input_file, count in ((BASE / "mixed-65.nc", 65), (bench.INPUT, 16960)):
        row, output = execute("selected-dp", best_batch, 2, f"check-dp-{count}", input_file, "dp")
        row["errors"] = bench.compare(BASE / f"check-dp-{count}-parent/output.nc", output)
        validation.append(row)
    bench.PRECISION = "sp"
    os.environ["ECRAD_BITIDENTITY"] = "OFF"
    shutil.copytree(OLD / "sources/candidate", RUN / "sources/parent-nobit")
    (RUN / "sources/candidate-nobit").symlink_to(RUN / "sources/candidate", target_is_directory=True)
    for label in ("parent-nobit", "candidate-nobit"):
        os.environ["ECRAD_EXTRA_FLAGS"] = "" if label == "parent-nobit" else selected_vector
        bench.build(label)
        BINS[label] = RUN / "builds" / label / "bin/ecrad_ifs_blocked_sp"
    for input_file, count in ((BASE / "mixed-65.nc", 65), (bench.INPUT, 16960)):
        _, ref = execute("parent-nobit", 0, 2, f"nobit-parent-{count}", input_file)
        row, output = execute("candidate-nobit", best_batch, 2, f"nobit-candidate-{count}", input_file)
        row["errors"] = bench.compare(ref, output)
        validation.append(row)
    (RUN / "validation.json").write_text(json.dumps(validation, indent=2))
    (RUN / "binaries.json").write_text(json.dumps({label: dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                                                   for label, path in BINS.items()}, indent=2))
    for label in (best_label, "selected-dp"):
        if label != "default":
            lineage.logged(["ctest", "--test-dir", RUN / "builds" / label, "--output-on-failure"], RUN / f"ctest-{label}.log")


if __name__ == "__main__":
    main()
