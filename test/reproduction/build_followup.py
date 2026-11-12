"""Build isolated benchmark references. Never modify existing source trees."""
import argparse
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "build/perf-sp"
MAIN = "54fe9cb77abb63adf7f746f0adee449e73291d79"
MIX = "9e156b8"
ACC = "0614ad1221c579e382872ce6a409fffff5acf8cd"


def run(*args, cwd=REPO, input=None):
    return subprocess.run(args, cwd=cwd, input=input, check=True)


def source(name, revision, patch):
    path = ROOT / "sources" / name
    if path.exists():
        raise SystemExit(f"Refusing to overwrite existing source tree: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    run("git", "worktree", "add", "--detach", str(path), revision)
    if name == "repro-stock-acc":
        run("git", "apply", "--unidiff-zero", str(patch), cwd=path)
    else:
        run("git", "apply", str(patch), cwd=path)
    return path


def build(name, path, mode, ecbuild, gpu_arch):
    target = ROOT / "builds" / name
    if target.exists():
        raise SystemExit(f"Refusing to overwrite existing build: {target}")
    flags = "-O3 -Mrecursive -gpu=" + gpu_arch if mode == "ACC" else "-O3"
    run("cmake", "-S", str(path), "-B", str(target), "-G", "Ninja",
        "-Decbuild_DIR=" + str(ecbuild), "-DCMAKE_BUILD_TYPE=Release",
        "-DECRAD_Fortran_FLAGS=" + flags, "-DENABLE_SINGLE_PRECISION=ON",
        "-DENABLE_DOUBLE_PRECISION=OFF", "-DENABLE_BITIDENTITY_TESTING=ON",
        "-DENABLE_TESTS=OFF", "-DENABLE_FIAT=OFF", "-DENABLE_FIELD_API=OFF",
        "-DENABLE_NVTX=OFF", "-DENABLE_ROCTX=OFF", "-DBUILD_SHARED_LIBS=OFF",
        "-DENABLE_ACC=" + ("ON" if mode == "ACC" else "OFF"),
        "-DENABLE_OMP=" + ("OFF" if mode == "ACC" else "ON"),
        "-DENABLE_GPU=" + ("ON" if mode == "ACC" else "OFF"))
    run("cmake", "--build", str(target), "--target", "ecrad_ifs_blocked_sp", "--parallel", "6")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=("stock", "intel"))
    parser.add_argument("--ecbuild", required=True, type=Path, help="Path to ecbuild's cmake directory")
    parser.add_argument("--gpu-arch", default="cc80", help="NVHPC GPU target for stock ACC")
    args = parser.parse_args()
    if not (args.ecbuild / "ecbuild-config.cmake").is_file():
        raise SystemExit("--ecbuild must contain ecbuild-config.cmake")
    if args.suite == "stock":
        path = source("repro-stock-acc", ACC, Path(__file__).with_name("stock-acc-timing.patch"))
        build("repro-stock-acc", path, "ACC", args.ecbuild.resolve(), args.gpu_arch)
    else:
        patch = REPO / "test/ifs/benchmark_gpu_main.patch"
        for name in ("repro-intel-main", "repro-intel-mix"):
            path = source(name, MAIN, patch)
            if name.endswith("mix"):
                difference = subprocess.check_output(["git", "diff", MAIN, MIX, "--",
                                                      "utilities/radiation_random_numbers_mix.F90"], cwd=REPO)
                if not difference:
                    raise SystemExit("MIX patch unexpectedly empty")
                run("git", "apply", "-", cwd=path, input=difference)
            build(name, path, "CPU", args.ecbuild.resolve(), args.gpu_arch)


if __name__ == "__main__":
    main()
