# Reproducing the incremental 1.7.1-acc backport measurements

`backport-results.json` records the release/donor/checkpoint commits, incremental
three-run medians and ranges, final five-run results and validation counts.
`config.nam` is the full benchmark physics configuration. The driver patch is
benchmark-only; production drivers on this branch are unchanged.

## Reference environment

NVIDIA A100-SXM4-40GB, NVHPC 24.5, CMake 4.2.4, NetCDF C/Fortran 4.9.2,
compiler-compatible FIAT and ecbuild. Calculations are double precision;
the benchmark namelist saves single-precision outputs. Input:

https://object-store.os-api.cci1.ecmwf.int/maelstrom-ap3/rad4NN_inputs_2020010100_0c0.nc

SHA-256: `214b418cccfaac72b2f772eb7a8135ac8e8f8a2719fed96764d6caef51d5cb89`.
It contains 16,960 columns, 137 levels, prescribed seeds, effective radii and
overlap parameters. Use the same file and settings for every checkpoint.

## Prepare and build each checkpoint

Use the commit IDs in the JSON, including the unmodified release baseline.
Create a separate detached source worktree and build directory for each:

```sh
git worktree add --detach "$BENCH/source-$LABEL" "$COMMIT"
git -C "$BENCH/source-$LABEL" apply --unidiff-zero \
  "$CHECKOUT/benchmarks/openacc/blocked-driver-timing.patch"

cmake -S "$BENCH/source-$LABEL" -B "$BENCH/build-$LABEL" -G Ninja \
  -DCMAKE_Fortran_COMPILER=nvfortran \
  -DCMAKE_C_COMPILER=nvc -DCMAKE_CXX_COMPILER=nvc++ \
  -Decbuild_ROOT="$ECBUILD_ROOT" -Dfiat_ROOT="$FIAT_ROOT" \
  -DCMAKE_BUILD_TYPE=Release \
  -DECRAD_Fortran_FLAGS="-O3 -Mrecursive -gpu=cc80" \
  -DENABLE_ACC=ON -DENABLE_OMP=OFF -DENABLE_FIAT=ON \
  -DENABLE_BITIDENTITY_TESTING=ON -DENABLE_NVTX=OFF \
  -DENABLE_SINGLE_PRECISION=ON -DENABLE_DOUBLE_PRECISION=ON \
  -DBUILD_SHARED_LIBS=OFF
cmake --build "$BENCH/build-$LABEL" --parallel 16
```

Set CHECKOUT to this checkout, BENCH to a fresh existing benchmark directory,
LABEL/COMMIT to the checkpoint, and the dependency roots to your installations.
Use NVHPC as the Fortran compiler and retain actual compilation commands.
The older release uses ENABLE_ACC directly; it does not need the newer
OpenMP-target build framework or ENABLE_GPU option.

## Validate and measure

Run inside a GPU allocation. Provide the NetCDF/FIAT runtime library paths.

```sh
ulimit -s unlimited
export OMP_NUM_THREADS=1 OMP_DYNAMIC=FALSE ACC_DEVICE_TYPE=nvidia
export DR_HOOK_ASSERT_MPI_INITIALIZED=0
ctest --test-dir "$BENCH/build-$LABEL" --output-on-failure
```

For each process execution, create a new run directory with `data` linked to
the checkpoint's data directory. Use the shipped namelist, changing only
`nblocksize`, `nrepeat` and `nwarmup`. For example, from that run directory:

```sh
"$BENCH/source-$LABEL/test/common/change_namelist.sh" \
  "$CHECKOUT/benchmarks/openacc/config.nam" case.nam \
  nblocksize=200 nrepeat=10 nwarmup=1
"$BENCH/build-$LABEL/bin/ecrad_ifs_blocked_dp" \
  case.nam "$INPUT" output.nc > run.log 2>&1
```

Require `BENCHMARK_GPU_EXECUTION 1` and compare all saved numerical variables
against a CPU reference from the release, checking variable sets, dimensions,
dimension order and finiteness. The CPU build uses ACC=OFF, OMP=ON and flags
`-O3 -Mrecursive`; its blocked reference uses NPROMA=200, one iteration, no
warm-up and otherwise identical physics.

Primary seconds/iteration = BENCHMARK_WALL_SECONDS divided by
BENCHMARK_MEASURED_ITERATIONS. Timing includes radiation/per-block transfers;
excludes reading, setup, warm-up, output scattering and NetCDF writing.
The common 64-bit timer waits for queued ACC work before starting/stopping.
Do not substitute process wall time or unsynchronized radiation-call timing.

Incremental stages used three executions each at NPROMA=200 and 4096. The
final baseline/backport comparison used five executions at NPROMA=32, 200,
1024 and 4096, rotating variant order on one allocated GPU. Report medians,
minima and maxima; profile separately with diagnostic settings disabled for
timed runs. Do not mix the initial baseline job with the final matched job.

Extended checks used the standalone driver and the release's meridian input
in DP/SP, saving double-precision full fluxes plus surface/canopy diagnostics:
full diagnostics, offset columns, no cloud scattering, no derivatives, delta
scaling with gases, beta overlap and Slingo liquid optics. Compare processed
columns only. DP LW/SW tolerance is 0.001 W/m²; SP LW is 0.001 and SW is
0.1 W/m² (the repository's reference-validation thresholds). Record all raw
errors. The final McICA code explicitly zeros SW cloud cover at night; check
daytime agreement and this night convention separately from radiative fluxes.
