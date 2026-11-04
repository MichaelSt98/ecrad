"""Build the pinned SP half-node benchmark variants; see gpu-performance-sp.json."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import subprocess
import sys
from threading import Lock

ROOT = Path(__file__).resolve().parents[2] / "build/perf-sp"
VARIANTS = {
    "main-cpu": ("main", "CPU"),
    "optimized-cpu": ("optimized", "CPU"),
    "master-omp": ("master-omp", "OMP"),
    "acc-opt": ("acc-opt", "ACC"),
    "acc-tiled": ("acc-tiled", "ACC"),
    "omp-tiled": ("omp-tiled", "OMP"),
    "optimized-acc": ("optimized", "ACC"),
    "optimized-omp": ("optimized", "OMP"),
}
PINS = {
    "acc-opt": "0d7a8092e884ccb8c4d7f60f1cccb461c876ed86",
    "main": "54fe9cb77abb63adf7f746f0adee449e73291d79",
    "master-omp": "b97c15f6be31a5a48c7c76f5437a9d46800e8bee",
    "acc-tiled": "475b28a9f000cd509f9bec9d9868dae2bc726d72",
    "omp-tiled": "01f3110738aad100bb9bab8473396f221ef3c242",
}
REPOSITORIES = {
    "acc-opt": "https://github.com/MichaelSt98/ecrad.git",
    "main": "https://github.com/ecmwf-ifs/ecrad.git",
    "master-omp": "https://github.com/ecmwf-ifs/ecrad.git",
    "acc-tiled": "https://github.com/MichaelSt98/ecrad.git",
    "omp-tiled": "https://github.com/PaulMullowney/ecrad.git",
}
SOURCE_LOCK = Lock()

def source_tree(name):
    source = ROOT / "sources" / name
    patch = Path(__file__).with_name("benchmark_gpu_" + name + ".patch")
    with SOURCE_LOCK:
        if not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            available = subprocess.run(["git", "cat-file", "-e", PINS[name]], cwd=ROOT.parents[1],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if available.returncode:
                subprocess.run(["git", "fetch", "--no-tags", REPOSITORIES[name], PINS[name]],
                               cwd=ROOT.parents[1], check=True)
            subprocess.run(["git", "worktree", "add", "--detach", str(source), PINS[name]],
                           cwd=ROOT.parents[1], check=True)
            subprocess.run(["git", "apply", str(patch)], cwd=source, check=True)
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip() == PINS[name]
    # Existing benchmark trees must already contain the known instrumentation.
    # Never reset or overwrite an existing tree to make this check pass.
    subprocess.run(["git", "apply", "--reverse", "--check", str(patch)], cwd=source, check=True)
    actual = subprocess.check_output(["git", "diff", "--no-ext-diff", "--no-color", "--unified=3",
                                      "--src-prefix=a/", "--dst-prefix=b/"], cwd=source, text=True)
    normalise = lambda text: "\n".join(line.rstrip() for line in text.splitlines()
                                     if not line.startswith(("index ", "@@")))
    assert normalise(actual) == normalise(patch.read_text()), "Unexpected reference-tree changes: " + str(source)
    return source

def build(label):
    source, mode = VARIANTS[label]
    directory = ROOT / "builds" / label
    flags = "-O3 -Mrecursive" + (" -gpu=cc80" if mode != "CPU" else "")
    source_path = ROOT.parents[1] if source == "optimized" else source_tree(source)
    if source == "optimized":
        flags += " -DECRAD_BENCHMARK"
    command = [
        "cmake", "-S", str(source_path), "-B", str(directory), "-G", "Ninja",
        "-Decbuild_DIR=" + str(ROOT.parents[1] / "build/acc/_deps/ecbuild-src/cmake"),
        "-DCMAKE_BUILD_TYPE=Release", "-DECRAD_Fortran_FLAGS=" + flags,
        "-DENABLE_SINGLE_PRECISION=ON", "-DENABLE_DOUBLE_PRECISION=OFF",
        "-DENABLE_BITIDENTITY_TESTING=ON", "-DENABLE_TESTS=OFF", "-DENABLE_FIAT=OFF",
        "-DENABLE_FIELD_API=OFF", "-DENABLE_NVTX=OFF", "-DENABLE_ROCTX=OFF",
        "-DBUILD_SHARED_LIBS=OFF", "-DECRAD_ENABLE_NVHPC_INLINE_LIBRARY=OFF",
        "-DENABLE_ACC=" + ("ON" if mode == "ACC" else "OFF"),
        "-DENABLE_OMP=" + ("OFF" if mode == "ACC" else "ON"),
        "-DENABLE_GPU=" + ("OFF" if mode == "CPU" else "ON"),
    ]
    if source in ("current", "optimized"):
        command.append("-DECRAD_GPU_BACKEND=" + mode)
    for stage, args in (("configure", command), ("build", [
        "cmake", "--build", str(directory), "--target", "ecrad_ifs_blocked_sp", "--parallel", "6"
    ])):
        with (ROOT / f"{label}-{stage}.log").open("w") as log:
            run = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT)
        if run.returncode:
            print(label, stage, "FAILED", flush=True)
            return label, stage + "-failed"
    commands = json.loads((directory / "compile_commands.json").read_text())
    assert not any("-Mextract" in c["command"] or "-Minline=lib" in c["command"] or "-gpu=pinned" in c["command"]
                   for c in commands)
    selected = [c for c in commands if "ecrad_ifs_driver_blocked" in c["file"]]
    assert selected and all("-O3" in c["command"] and "-Mrecursive" in c["command"] for c in selected)
    if mode == "ACC":
        assert all("-acc" in c["command"] for c in selected)
    if mode == "OMP":
        assert all("-mp=gpu" in c["command"] for c in selected)
    (ROOT / f"{label}-flags.json").write_text(json.dumps(selected, indent=2) + "\n")
    print(label, "built", flush=True)
    return label, "built"

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(pool.map(build, sys.argv[1:] or VARIANTS))
    (ROOT / "build-summary.json").write_text(json.dumps(results, indent=2) + "\n")
    sys.exit(any(value != "built" for value in results.values()))
