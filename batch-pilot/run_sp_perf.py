"""Frozen-source SP capture/replay sweep and matched A100 confirmation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import time
from typing import Any

ROOT = Path(__file__).resolve().parent
CI = ROOT.parents[1]
FROZEN = CI / "further-1/five-band-20261014-integrated-v1"
DEPS = CI / "pr119/deps"
INPUT = CI / "benchmark-nvhpc/input/rad4NN_inputs_2020010100_0c0.nc"
EXPECTED_INPUT = "214b418cccfaac72b2f772eb7a8135ac8e8f8a2719fed96764d6caef51d5cb89"
FILES = (
    "CMakeLists.txt", "reference_sw.F90", "test_sw_batch.F90", "capture_sw.F90",
    "sw_replay_io_mod.F90", "sw_replay_bench_mod.F90", "bench_sw_replay.F90",
)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def logged(command, path, cwd, env=None):
    print("RUN", " ".join(map(str, command)), flush=True)
    assert not path.exists(), path
    with path.open("w") as output:
        subprocess.run(list(map(str, command)), cwd=cwd, env=env, stdout=output,
                       stderr=subprocess.STDOUT, check=True)


def prepare(run):
    run.mkdir()
    expected = json.loads((FROZEN / "snapshots.json").read_text())["candidate"]
    assert all(digest(FROZEN / "sources/candidate" / name) == sha for name, sha in expected.items())
    assert digest(INPUT) == EXPECTED_INPUT
    source = run / "sources/batch-pilot"
    source.mkdir(parents=True)
    radiation = source.parent / "radiation"
    radiation.mkdir()
    for name in FILES:
        shutil.copy2(ROOT / name, source / name)
    for name in ("radiation_mcica_sw_batch_mod.F90", "radiation_mcica_acc_gpoint_sw.F90"):
        shutil.copy2(ROOT.parent / "radiation" / name, radiation / name)
    assert digest(radiation / "radiation_mcica_acc_gpoint_sw.F90") == expected[
        "radiation/radiation_mcica_acc_gpoint_sw.F90"]
    for name in ("run_sp_perf.py", "run-sp-perf.sbatch"):
        shutil.copy2(ROOT / name, run / name)
    manifest = {str(p.relative_to(run / "sources")): digest(p)
                for p in (run / "sources").rglob("*") if p.is_file()}
    (run / "source-manifest.json").write_text(json.dumps(manifest, indent=2))
    (run / "provenance.json").write_text(json.dumps(dict(
        input=str(INPUT), input_sha256=EXPECTED_INPUT, reference=str(FROZEN),
        precision="SP", nproma=32, ncolumns=16960, nlev=137,
        boundary_0="sum of synchronized per-batch resident solve wall times; packing/transfers excluded",
        boundary_1="pack, HtoD, synchronized solve, DtoH and scatter; fixture I/O/setup/validation excluded",
        whole_application_timing=False, repeats=10, warmup=1, processes=5,
        started=time.time(), slurm_job_id=os.environ.get("SLURM_JOB_ID")), indent=2))
    return source


def build(run, source):
    executable = shutil.which("nvfortran")
    assert executable, "nvfortran not on PATH"
    compiler = Path(executable).resolve()
    cuda = compiler.parents[2] / "cuda/lib64"
    assert (cuda / "libcudart.so").exists()
    binary = run / "build"
    logged(["cmake", "-S", source, "-B", binary, "-G", "Ninja",
        f"-DECRAD_BATCH_REFERENCE_SOURCE={FROZEN / 'sources/candidate'}",
        f"-Decbuild_ROOT={DEPS / 'ecbuild'}", f"-Dfiat_ROOT={DEPS / 'fiat-install'}",
        "-DCMAKE_Fortran_COMPILER=nvfortran", "-DCMAKE_BUILD_TYPE=Release",
        "-DECRAD_Fortran_FLAGS=-O3 -Mrecursive -gpu=cc80,ptxinfo -Minfo=accel,inline",
        f"-DCMAKE_EXE_LINKER_FLAGS=-L{cuda} -Wl,-rpath,{cuda} -lcudart",
        "-DENABLE_FIAT=ON", "-DENABLE_OMP=OFF", "-DENABLE_ACC=ON", "-DENABLE_NVTX=OFF",
        "-DENABLE_BITIDENTITY_TESTING=ON", "-DENABLE_SINGLE_PRECISION=ON",
        "-DENABLE_DOUBLE_PRECISION=OFF", "-DBUILD_SHARED_LIBS=OFF", "-DENABLE_TESTS=OFF",
        "-DSW_BATCH_PERF=ON"], run / "configure.log", run)
    logged(["cmake", "--build", binary, "--target", "capture_sw_sp", "bench_sw_replay_sp",
            "test_sw_batch_sp", "-j", "6"], run / "build.log", run)
    logged(["ctest", "--test-dir", binary, "-V"], run / "synthetic-validation.log", run)
    commands = json.loads((binary / "compile_commands.json").read_text())
    selected = [c for c in commands if Path(c["file"]).name in (
        "reference_sw.F90", "capture_sw.F90", "radiation_mcica_sw_batch_mod.F90",
        "sw_replay_bench_mod.F90", "ecrad_ifs_driver_blocked.F90")]
    assert selected and all("-acc" in c["command"] and "-O3" in c["command"] for c in selected)
    assert all("parkind_sp" in c["command"] for c in selected)
    (run / "flags.json").write_text(json.dumps(selected, indent=2))
    return binary


def capture(run, binary):
    text = (FROZEN / "config.nam").read_text()
    for key, value in {"nblocksize": "32", "nrepeat": "1", "nwarmup": "0",
                       "do_parallel": ".false."}.items():
        text, count = re.subn(rf"(?m)^(\s*{key}\s*=\s*)[^,\n]+", rf"\g<1>{value}", text)
        assert count == 1, key
    text = text.replace('directory_name = "data"', f'directory_name = "{FROZEN / "sources/candidate/data"}"')
    config = run / "capture.nam"
    config.write_text(text)
    fixture = run / "fixture-sp.bin"
    assert not fixture.exists()
    env = os.environ.copy()
    env["ECRAD_SW_FIXTURE"] = str(fixture)
    logged([binary / "capture_sw_sp", config, INPUT, run / "capture-output.nc"],
           run / "capture.log", run, env)
    assert fixture.stat().st_size > 1_000_000_000, fixture.stat()
    (run / "fixture.json").write_text(json.dumps(dict(
        path=str(fixture), bytes=fixture.stat().st_size, sha256=digest(fixture)), indent=2))
    return fixture


def replay(run, binary, fixture, tag, mode, batch, boundary, repeat):
    path = run / f"{tag}-m{mode}-K{batch}-b{boundary}.log"
    logged([binary / "bench_sw_replay_sp", fixture, mode, batch, boundary, repeat], path, run)
    rows = []
    for line in path.read_text().splitlines():
        if line.startswith("REPLAY "):
            fields = line.split()[1:]
            row: dict[str, Any] = dict(zip(("mode", "batch", "boundary", "iteration", "blocks"), map(int, fields[:5])))
            row.update(zip(("seconds", "max_error", "checksum"), map(float, fields[5:])))
            assert row["blocks"] == 530 and row["max_error"] <= 0.1
            rows.append(row)
    assert len(rows) == repeat, path
    return dict(tag=tag, mode=mode, batch=batch, boundary=boundary,
                median=statistics.median(r["seconds"] for r in rows[1:]), rows=rows)


def analyse(run, results):
    groups = {}
    for record in results:
        key = (record["mode"], record["batch"], record["boundary"])
        groups.setdefault(key, []).append(record["median"])
    lines = ["# SP B32 real-input replay results", "",
        "All times are seconds per 16,960-column solver replay, not full radiation.", "",
        "| Boundary | Mode | K | Median | Range | Reduction vs existing | Faster matched processes |",
        "|---|---|---:|---:|---|---:|---:|"]
    summary = []
    for (mode, batch, boundary), values in sorted(groups.items(), key=lambda p: (p[0][2], p[0][0], p[0][1])):
        reference = groups[(0, 1, boundary)]
        median = statistics.median(values)
        reduction = 100 * (1 - median / statistics.median(reference))
        faster = sum(a < b for a, b in zip(values, reference))
        label = "resident" if boundary == 0 else "staging-inclusive"
        lines.append(f"| {label} | {'existing' if mode == 0 else 'pilot'} | {batch} | {median:.7f} | "
                     f"{min(values):.7f}–{max(values):.7f} | {reduction:.2f}% | {faster}/{len(values)} |")
        summary.append(dict(mode=mode, batch=batch, boundary=boundary, process_medians=values,
                            median=median, reduction_percent=reduction, faster_pairs=faster))
    lines += ["", "One warmup and nine measured iterations in each of five processes.",
              "Validation/scatter checksums and all raw iteration times are in confirmation.json.",
              "Packing and transfers are excluded from resident timing and included in staging timing.",
              "No CUDA graphs or multi-queue overlap are used."]
    (run / "analysis.json").write_text(json.dumps(summary, indent=2))
    (run / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)


def profiles(run, binary, fixture, best):
    nsys = shutil.which("nsys")
    if not nsys:
        (run / "profile-unavailable.txt").write_text("nsys not available on PATH\n")
        return
    for mode, batch in [(0, 1), (1, 1), (1, best)]:
        name = f"profile-m{mode}-K{batch}"
        logged([nsys, "profile", "--trace=cuda,openacc", "--sample=none", "--cpuctxsw=none",
                "--output", run / name, binary / "bench_sw_replay_sp", fixture, mode, batch, 0, 2],
               run / f"{name}.log", run)
        logged([nsys, "stats", "--report", "cuda_gpu_kern_sum,cuda_api_sum,cuda_gpu_mem_size_sum",
                "--format", "csv", run / f"{name}.nsys-rep"], run / f"{name}-stats.csv", run)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--smoke-only", action="store_true")
    args = parser.parse_args()
    run = args.run.resolve()
    source = prepare(run)
    binary = build(run, source)
    # One fixed CPU drives all measurements; compilation above uses the allocation.
    affinity = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {affinity[0]})
    (run / "cpu-affinity.json").write_text(json.dumps(dict(available=affinity, selected=affinity[0])))
    fixture = capture(run, binary)
    sweep = []
    variants = [(0, 1)] + [(1, k) for k in (1, 2, 4, 8, 16, 32)]
    for boundary in (0, 1):
        for mode, batch in variants:
            sweep.append(replay(run, binary, fixture, "sweep", mode, batch, boundary, 3))
            (run / "sweep.json").write_text(json.dumps(sweep, indent=2))
    if args.smoke_only:
        return
    best = {b: min((r for r in sweep if r["boundary"] == b and r["batch"] > 1),
                   key=lambda r: r["median"])["batch"] for b in (0, 1)}
    selected = [(0, 1), (1, 1)] + [(1, k) for k in sorted(set(best.values()))]
    (run / "selection.json").write_text(json.dumps(dict(best=best, variants=selected), indent=2))
    results = []
    for process in range(5):
        ordered = selected[process % len(selected):] + selected[:process % len(selected)]
        if process % 2:
            ordered = ordered[::-1]
        for boundary in ((0, 1) if process % 2 == 0 else (1, 0)):
            for mode, batch in ordered:
                results.append(replay(run, binary, fixture, f"confirm-{process}", mode, batch, boundary, 10))
                (run / "confirmation.json").write_text(json.dumps(results, indent=2))
    analyse(run, results)
    profiles(run, binary, fixture, best[0])
    expected = json.loads((run / "source-manifest.json").read_text())
    assert all(digest(run / "sources" / name) == sha for name, sha in expected.items())


if __name__ == "__main__":
    main()
