# Reproduction package — ecRad CPU/GPU performance

This directory contains **benchmark-only tooling and records**. The stock ACC
patch instruments a detached reference worktree; the Intel build applies the
MIX change to a separate detached main worktree. **Nothing here changes the
ecRad production source in this checkout.**

The package supplements the tracked `test/ifs/benchmark_gpu*` harness and
[`report_2026_11_06.md`](../../report_2026_11_06.md). It reproduces the
original matrix and the later stock-ACC / Intel-MIX follow-up experiments.
Timing results will vary by machine; numerical output must be checked before
presenting a timing ratio as an equivalent-work speedup.

## Required input and software

- Obtain the original `rad4NN_inputs_2020010100_0c0.nc` independently. It is
  **not distributed in this repository**; the original was an ECMWF-local
  input. Coordinate with the benchmark authors for access and permission to
  redistribute. SHA-256:
  `214b418cccfaac72b2f772eb7a8135ac8e8f8a2719fed96764d6caef51d5cb89`.
  The harness rejects another checksum and requires 16,960 columns.
- Git, CMake (at least 3.24 for this branch), Ninja, ecbuild, NetCDF Fortran,
  Python 3 with `numpy`, `xarray` and a NetCDF backend, Slurm, and GNU time.
  Fetch access to the pinned upstream/fork commits is required.
- NVHPC with OpenACC and OpenMP target, an NVIDIA driver/NVML, and two GPUs
  for the GPU runs. Intel classic `ifort` and compiler-compatible NetCDF
  Fortran are required for the Intel follow-up. The tested versions were
  NVHPC 24.5 and ifort 2021.10 (Intel 2023.2).
- The tracked runner currently **requires a Slurm allocation with 64 physical
  CPU cores and two NVIDIA GPUs**, split into two 32-core GPU ranks. Even the
  CPU job requests two GPUs to use that same allocation pattern. It asserts
  topology/GPU bindings and uses NVML. The workload and rank split are fixed
  at 16,960 columns / 8,480 columns per GPU. Port the scheduler, affinity,
  rank-split and NVML code for other configurations; do not silently disable
  checks or describe those results as the identical protocol.

The scripts under `test/ifs/` hard-code ECMWF modules, Slurm QoS, GPU target
`cc80`, and the ecbuild path `build/acc/_deps/ecbuild-src/cmake`. On another
site adjust `benchmark_gpu.sbatch` and, if necessary, the build/runner scripts
in a local working copy. GPU hardware, compiler flags, setup and transfers
must be reported with the results. The follow-up builder's `--gpu-arch` changes
its stock ACC target only; adapt the original matrix builder separately.

## Prepare a clean checkout and matrix

Start from the integration snapshot `c3b364c` or this descendant. Preserve the
benchmark namelist and use the original input; do not confuse it with the
small test data in `test/ifs/`. This example uses the original site modules;
substitute local equivalents. Ensure `FC` selects the intended compiler.

```bash
module load cmake ninja prgenv/nvidia nvidia/24.5 netcdf4 python3
# Bootstrap ecbuild at the location expected by benchmark_gpu_build.py.
cmake -S . -B build/acc -G Ninja -DENABLE_TESTS=OFF -DENABLE_FIAT=OFF
mkdir -p build/perf-sp
python3 test/ifs/benchmark_gpu_build.py \
  main-cpu optimized-cpu acc-opt acc-tiled master-omp omp-tiled \
  optimized-acc optimized-omp
python3 test/ifs/benchmark_gpu.py prepare /absolute/path/INPUT.nc test/ifs/benchmark_gpu.nam
sbatch --export=ALL,ECRAD_ACC_RESIDENT_FIELDS=1 \
  test/ifs/benchmark_gpu.sbatch matrix-acc cpu tiled32 tiled64 tiled128
sbatch test/ifs/benchmark_gpu.sbatch matrix-acc cpu normal
sbatch --time=00:25:00 test/ifs/benchmark_gpu.sbatch matrix-omp cpu normal tiled32 tiled64 tiled128
python3 test/ifs/benchmark_gpu_matrix_analyse.py ACC_TILED_JOB ACC_NORMAL_JOB OMP_JOB
python3 test/ifs/benchmark_gpu_matrix_report.py ACC_TILED_JOB ACC_NORMAL_JOB OMP_JOB OUTPUT.json
```

The `prepare` command copies the namelist into `build/perf-sp`, verifies the
input checksum, splits the GPU input and writes configurations. The default
matrix build assumes the ecbuild bootstrap location above; if a system ecbuild
is used, adjust that path. Review `benchmark_gpu.sbatch` before submission.
When comparing tiled ACC against its donor, the resident-source option matches
its source lifetime; default bounded staging is a distinct experiment.

## Stock upstream master-acc follow-up

Ensure `0614ad1221c579e382872ce6a409fffff5acf8cd` exists locally (fetch
`origin/master-acc` if necessary). Use the same prepared input, benchmark
configuration, NVHPC environment and ecbuild source as above:

```bash
python3 test/reproduction/build_followup.py stock --ecbuild "$PWD/build/acc/_deps/ecbuild-src/cmake" --gpu-arch cc80
sbatch --job-name=ecrad-stock-acc --qos=dg --nodes=1 --ntasks=1 \
  --cpus-per-task=64 --hint=nomultithread --gpus-per-task=2 --mem=200000M \
  --time=00:40:00 --output=build/perf-sp/repro-stock-%j.log \
  --wrap='module load cmake prgenv/nvidia nvidia/24.5 netcdf4 python3 && ulimit -s unlimited && export PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 && python3 test/reproduction/run_followup.py stock'
python3 test/reproduction/analyse_followup.py stock STOCK_JOB --acc-job ACC_NORMAL_JOB
```

The stock case uses the uncorrected upstream branch and the benchmark-only
timing patch in this directory. The saved stock/main-gpu output comparison
requires the new normal ACC matrix job; omit `--acc-job` to check stock repeat
outputs only. **Stock SP gas metadata differs**; see
[PR #122](https://github.com/ecmwf-ifs/ecrad/pull/122). Do not label timing
ratios validated equal-output speedups without fixing and checking outputs.

## Intel CPU: pinned main versus main plus MIX

Use Intel classic `ifort`, an Intel-compatible NetCDF Fortran and the same
prepared input. The builder creates two new detached worktrees from pinned
main `54fe9cb`, instruments both identically, and applies only the MIX file
diff from commit `9e156b8` to the second. This is **not a full main-gpu Intel
build**. The tested full branch failed with `ALLOCATED` on pointer components.

```bash
module load cmake ninja prgenv/intel intel/2023.2.0 netcdf4 python3
python3 test/reproduction/build_followup.py intel --ecbuild "$PWD/build/acc/_deps/ecbuild-src/cmake"
sbatch --job-name=ecrad-intel-cpu --qos=dg --nodes=1 --ntasks=1 \
  --cpus-per-task=64 --hint=nomultithread --gpus-per-task=2 --mem=200000M \
  --time=00:30:00 --output=build/perf-sp/repro-intel-%j.log \
  --wrap='module load cmake prgenv/intel intel/2023.2.0 netcdf4 python3 && ulimit -s unlimited && export PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 && python3 test/reproduction/run_followup.py intel'
python3 test/reproduction/analyse_followup.py intel INTEL_JOB
```

Each follow-up has five alternating-order groups, six iterations per process,
and discards the first iteration. The Intel analysis compares saved outputs in
all five pairs and computes a one-sided exact paired sign-flip test. The stock
analysis checks all repeat outputs and optionally compares each GPU rank with
the normal ACC matrix. Inspection of finiteness, variables and output errors is
required; these analyses do not automatically claim scientific equivalence.

The builder refuses to overwrite existing `repro-*` source or build directories.
For a fresh run, use a fresh checkout/build area or remove **only** worktrees
and directories you created after verifying their contents. Results are in
`build/perf-sp/results/JOB/`, including per-rank outputs, logs, binary hashes,
and `reproduction-analysis.json`. Raw data are not committed. The compact
[`followup-results.json`](followup-results.json) records the measured follow-up
results for context; original matrix details are in the root JSON report.
