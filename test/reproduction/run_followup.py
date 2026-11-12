"""Run stock ACC or isolated Intel MIX cases inside the benchmark allocation."""
import importlib.util
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "build/perf-sp"
spec = importlib.util.spec_from_file_location("benchmark_gpu", REPO / "test/ifs/benchmark_gpu.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

for block in (32, 128, 1024, 2048, 4096, 8192, 8480):
    label = f"repro-stock-acc-b{block}"
    bench.CASES[label] = ("repro-stock-acc", "gpu", 0, "McICA ACC")
    bench.MATRIX[label] = {"suite": "stock", "block": block, "width": block}
for block in (8, 16, 32):
    for build in ("repro-intel-main", "repro-intel-mix"):
        label = f"{build}-b{block}"
        bench.CASES[label] = (build, "cpu", 0, "McICA")
        bench.MATRIX[label] = {"suite": "intel", "block": block, "width": block}

original_configuration = bench.configuration


def configuration(path, sampler, label=None):
    original_configuration(path, sampler, label)
    if label and bench.CASES[label][0].startswith("repro-intel-"):
        path.write_text(re.sub(r"(?m)^\s*nwarmup\s*=.*\n", "", path.read_text()))


bench.configuration = configuration


def controller(suite):
    if suite not in ("stock", "intel"):
        raise SystemExit("Choose stock or intel")
    directory = ROOT / "results" / os.environ["SLURM_JOB_ID"]
    directory.mkdir(parents=True)
    labels = [label for label, item in bench.MATRIX.items() if item["suite"] == suite]
    bench.dump(directory / "cases.json", {label: bench.MATRIX[label] for label in labels})
    bench.dump(directory / "environment.json", dict(os.environ))
    rows = []
    for group in range(5):
        for label in labels if group % 2 == 0 else labels[::-1]:
            mode = bench.CASES[label][1]
            tasks, cores, gpus = ("1", "64", "2") if mode == "cpu" else ("2", "32", "1")
            command = ["srun", "--ntasks=" + tasks, "--cpus-per-task=" + cores,
                       "--gpus-per-task=" + gpus, "--cpu-bind=cores",
                       "--tres-bind=gres/gpu:per_task:" + gpus, sys.executable,
                       str(Path(__file__).resolve()), "worker", label, str(group), str(directory)]
            print("RUN", label, group, flush=True)
            run = subprocess.run(command)
            ranks = [json.loads(p.read_text()) for p in sorted(directory.glob(f"{label}-g{group}-r*/result.json"))]
            row = {"label": label, "group": group, "step_exit_code": run.returncode, "ranks": ranks}
            if run.returncode == 0 and len(ranks) == int(tasks):
                if mode == "gpu":
                    assert all(len(r["gpu_uuids"]) == 1 for r in ranks)
                    assert ranks[0]["gpu_uuids"] != ranks[1]["gpu_uuids"]
                times = [max(rank["iterations_s"][i] for rank in ranks) for i in range(1, 6)]
                row["median_s"] = statistics.median(times)
                print(label, row["median_s"], flush=True)
            else:
                row["step_exit_code"] = row["step_exit_code"] or 1
            rows.append(row)
            bench.dump(directory / "summary.json", rows)
    if any(row["step_exit_code"] for row in rows):
        raise SystemExit("One or more benchmark cases failed; see summary.json")


if __name__ == "__main__":
    if sys.argv[1] == "worker":
        bench.worker(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    else:
        controller(sys.argv[1])
