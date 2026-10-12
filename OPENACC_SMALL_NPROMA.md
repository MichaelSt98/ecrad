# Small-NPROMA OpenACC optimization

Base: `8414ec356c97fc05ba3df2809920882309165a82` (`nams-1.7.1-acc-opt`).
Final measured implementation: `ae2884c`.
Measurements: NVIDIA A100-SXM4-40GB, NVHPC 24.5, double precision, identical
16,960-column/137-level input and physics for parent and optimized builds.
NPROMA remains the requested block size; no larger internal batch is substituted.

## Identification of the bottlenecks

CUDA traces capture one complete measured iteration after warm-up. Setup,
coefficient loading, first module loading and warm-up are outside capture.
NVHPC 24.5 compiles/runs the program; the newer Nsight Systems tool bundled
with NVHPC 26.1 collects traces because the older importer failed on event
ordering. Profiler timings are separate from unprofiled performance runs.

At NPROMA=32 the parent issued 136,740 kernel launches per iteration. Cloud
generation accounted for about 25% of summed GPU kernel time, and the four
clear/cloudy McICA kernels were also major contributors. Repeated per-band
tropopause reductions, column-only coefficient setup, serial cloud cropping
and small assembly launches added repeated small-call costs.

The CUDA API trace also showed many synchronization and small-copy calls.
Synchronization time includes waiting for useful device work; it must not be
added to kernel time or interpreted wholesale as removable overhead.

## Changes and incremental experiments

- `f42aa5a`: calculate tropopause bounds once per LW/SW gas call and supply
  optional read-only bounds to the band routines. CPU mixed-layer indexing and
  the standalone calculation when bounds are omitted remain available. Bounds
  are invocation-local, not shared module scratch. Matching interfaces are
  supplied in `include/`.
- `e3f4005`: use warp-sized vectors for at most 64 active columns in the
  scalar McICA kernels/cloud generator. Traces confirm grid sizes grow from
  28/35 blocks of 128 lanes to 112/140 blocks of 32 lanes. Geometry alone gave
  only a small measured gain; more gangs did not imply a large speedup.
- `6e0c266`: separate column tropopause counting from independent per-layer
  RRTM/SRTM coefficients. The layer formulas are unchanged, and night columns
  retain their existing LW tropopause as in the parent.
- `abad7dd`, `c914e3b`: use exact integer Mersenne reduction in the scalar ACC
  RNG instead of promoting the integer state to real MOD. The product is below
  2**47 in the normal range, so a bit fold and one subtraction give the same
  state. The legacy operation remains for out-of-range inputs and on CPU.
  Tests require actual device execution and compare both states and draws.
- `ae2884c`: expose independent cloud-cropping layers, preserving hydrometeor
  summation order with private scalar scratch; combine SW incoming-flux and
  optical-depth assembly; eliminate the separate SW molecular zero-fill launch.
  Band scratch is still consumed in queue order before the next band reuses it.

Three-execution staged medians at NPROMA=32 were 6.463 s (parent), 5.973 s
(bounds), 5.928 s (+ geometry), approximately 5.78 s (+ coefficients),
5.222 s (+ RNG), and approximately 5.10 s (+ layer/assembly cleanup).
These separate-job staged timings are attribution experiments, not a
replacement for the final matched comparison below.

The optimized warmed NPROMA=32 trace issued 100,700 kernels (26.4% fewer).
Summed GPU kernel time fell from 4.2845 to 3.2966 s; combined cloud generation
fell from 1.0670 to 0.6045 s. The exact counts, resource/grid observations,
top kernels and NPROMA=200 comparison are in the JSON evidence.

## Final matched five-run results

Job 37780023 on ac6-300, one allocated GPU and one host thread. Variant order
rotates between executions. Each process performs ten iterations, with one
warm-up and nine measured iterations. Primary time is synchronized radiation
plus per-block transfers, excluding setup, warm-up, output scattering and
NetCDF I/O. Median seconds per complete input iteration:

| NPROMA | Parent | Small-NPROMA branch | Time reduction |
| ---: | ---: | ---: | ---: |
| 32 | 6.5546 s | 5.1652 s | 21.2% |
| 64 | 3.5838 s | 2.8765 s | 19.7% |
| 128 | 2.0587 s | 1.6865 s | 18.1% |
| 200 | 1.4235 s | 1.1816 s | 17.0% |
| 1024 | 0.6793 s | 0.6173 s | 9.1% |
| 4096 | 0.5264 s | 0.4988 s | 5.2% |

All 60 executions passed, with maximum saved numerical error 0.0 against the
parent CPU reference. These benchmark files save single-precision values;
internal DP bit identity is not asserted. Full ranges are in the JSON.

## Correctness

- Final GPU CTest: 26/26; CPU fallback CTest: 182/182.
- Five incremental implementations each passed 14 extended DP/SP comparisons:
  70 comparisons with full surface/canopy diagnostics and CPU references.
- The RNG exhaustive GPU check compared all states 0..2147483647 and six
  out-of-range inputs against the original generator: 2,147,483,654 inputs per
  precision, zero state or draw differences in DP and SP. The shorter CTest
  version spans the same range; run the test binary with `--exhaustive` for
  the full check.
- CPU outputs matched the unmodified parent exactly at one and eight threads
  with multiple small concurrent blocks and four iterations.
- Eight further parent-GPU comparisons passed: all-night, solar scaling,
  weighted surface mapping and reduced vertical levels, in DP/SP. Separate
  LW-only/SW-only attempts were rejected by both snapshots during setup because
  the provided two-channel cloud coefficient files do not match a disabled
  channel's zero bands; those four attempts are recorded as unsupported setups,
  not successful numerical comparisons.
- DP LW/SW tolerance is 0.001 W/m²; SP LW is 0.001 and SW is 0.1 W/m², matching
  repository reference validation. CTest WILL_FAIL cases are not SP bit identity.
- The parent's explicitly zero nighttime SW-cover convention is retained.
- Built-in fortls diagnostics were attempted for changed sources but the
  launcher exits 1 on nested worktrees. Actual NVHPC compilation and CPU/GPU
  execution supply the validation.

No persistent shared workspace was introduced, and barriers were not removed
wholesale. Automatic scratch lifetimes and host-bound dependencies remain
ordered on the existing queue.

## Reproduction and evidence

See [benchmarks/openacc/small-np-results.json](benchmarks/openacc/small-np-results.json)
for medians/ranges, staged validation, warmed kernel counts/resource observations,
edge results, CPU reentry and exhaustive RNG checks. The input checksum and full
physics namelist are the same as the parent benchmark in
[benchmarks/openacc/README.md](benchmarks/openacc/README.md).

Production drivers are unchanged. The separate
`benchmarks/openacc/warmed-driver-capture.patch` adds the common synchronized
timer/device probe and CUDA profiler start/stop around the measured region.
Apply with `git apply --unidiff-zero` to detached benchmark snapshots. For that
instrumented executable only, link the compiler-compatible CUDA runtime using
`-L$NVHPC_CUDA_HOME/lib64 -Wl,-rpath,$NVHPC_CUDA_HOME/lib64 -lcudart` in
CMAKE_EXE_LINKER_FLAGS. Ordinary builds require no new CUDA-runtime link option.

Use Release `-O3 -Mrecursive -gpu=cc80`, ACC=ON, OMP=OFF, DP/SP and bit-identity
testing enabled, FIAT, static libraries and NVTX disabled for both GPU variants.
Retain actual compile commands. The CPU reference uses ACC=OFF, OMP=ON,
`-O3 -Mrecursive`. Profile separately with two iterations/one warm-up:

```sh
nsys profile --trace=cuda --sample=none --cpuctxsw=none \
  --capture-range=cudaProfilerApi --capture-range-end=stop \
  -o trace ./ecrad_ifs_blocked_dp case.nam input.nc output.nc
nsys stats --report=cuda_gpu_kern_sum,cuda_api_sum,cuda_gpu_mem_time_sum trace.nsys-rep
```

For unprofiled timings use five executions per case, ten iterations/one warm-up,
rotating order on one allocated GPU. Require the device probe and numerical
comparison for every execution. Hardware/compiler/configuration changes need
fresh measurements.
