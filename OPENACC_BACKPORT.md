# Incremental OpenACC optimization backport to 1.7.1-acc

Release base: `a21f2779b286f7a18c2985a744098d903d6d745e` (`1.7.1-acc`).
Donor: `7ea1fefdad3feaf996ff17b4d5e25c79b2e98635` (`nams-master-omp-acc-opt`).
Final measured implementation: `52be10ca809e8ca6d50b1ea1254af8018cfee670`.

The backport retains the release's CPU implementations and adds ACC-oriented
scalar spectral helpers. It uses the release's existing OpenACC build setup.
The source changes are split into five incremental commits:

1. `16b6356`: atmospheric Planck interpolation and spectral output.
2. `8780033`: SW gas assembly with a host spectral offset.
3. `fe5967a`: scalar-band cloud optics and Fu/SOCRATES/Slingo helpers.
4. `6f63a09`: spectral-parallel explicit-array aerosol optics, adapted to the
   older gas-access interface.
5. `52be10c`: scalar g-point McICA LW/SW solvers, recomputing adding sweeps,
   two-stream helpers, whole-array cloud generation and two-pass LW derivatives.

## Baseline and incremental measurements

Measured on 12 October 2026, A100-SXM4-40GB, NVHPC 24.5, double precision,
Release `-O3 -Mrecursive -gpu=cc80`, OpenACC enabled and OpenMP disabled.
The input has 16,960 columns and 137 levels. Each execution has ten iterations
with one warm-up. These are synchronized seconds per full-input iteration,
including radiation and per-block transfers, excluding setup and file I/O.

Three independent executions per incremental case, medians:

| Cumulative stage | NPROMA 200 | NPROMA 4096 |
| --- | ---: | ---: |
| Unmodified release | 3.4931 s | 0.9808 s |
| Planck | 2.4475 s | 0.9213 s |
| + SW gas assembly | 2.0274 s | 0.8804 s |
| + Cloud optics | 1.7526 s | 0.8568 s |
| + Aerosol optics | 1.6022 s | 0.7923 s |
| + McICA | 1.4074 s | 0.5214 s |

The initial release baseline also covered NPROMA=32 and 1024. Separate untimed
Nsight Systems profiling confirmed that Planck, SW gas assembly and the old
McICA/cloud/aerosol kernels dominate release GPU kernel time.

## Final matched five-run comparison

The release and complete backport were rerun in rotated order on the same
allocated A100 (`ac6-301`, benchmark job 37698436). Five executions per case,
nine measured iterations after warm-up:

| NPROMA | Release baseline | Complete backport | Speedup |
| ---: | ---: | ---: | ---: |
| 32 | 14.0846 s | 6.5620 s | 2.15× |
| 200 | 3.5028 s | 1.4238 s | 2.46× |
| 1024 | 1.4362 s | 0.6780 s | 2.12× |
| 4096 | 0.9831 s | 0.5260 s | 1.87× |

All 40 matched executions passed saved-output comparisons against the release
CPU reference, with maximum error 0.0 for every saved numerical variable.
The saved benchmark outputs are single precision; internal DP bit identity
is not asserted. Timings are specific to this hardware/compiler/configuration.

## Validation

- Each of the six snapshots (baseline plus five stages) passed 24/24 applicable
  GPU CTest checks and 14 additional DP/SP comparisons: 84 additional checks
  in total, with CPU references and full surface/canopy diagnostics.
- Extended cases cover full diagnostics, non-unit starting column, no cloud
  scattering, no derivatives, delta scaling with gases, beta overlap and
  Slingo liquid optics. Only processed columns are compared for subrange runs.
- DP LW/SW tolerance: 0.001 W/m². SP LW: 0.001; SP SW: 0.1 W/m², matching the
  repository's reference-validation thresholds. Raw errors were retained.
  CTest SP comparisons with WILL_FAIL are not evidence of SP bit identity.
- The complete backport's CPU build passed 182/182 CTest checks, including
  non-McICA solvers and ecCKD configurations.
- The McICA stage explicitly zeros SW cloud cover at night, following the
  donor's OMP convention. Legacy release/CPU can retain nonzero cover at night.
  Daytime cover agreement and night zeros were checked separately from fluxes.
- Zero-sized scattering arrays have explicit ACC PRESENT clauses. Scratch
  storage remains alive until its queued work completes. Whole-array cloud
  generation preserves the donor's NVHPC argument-association workaround.
- The built-in fortls launcher exited 1 for nested worktrees; NVHPC compilation
  and actual CPU/GPU execution supplied validation.

Benchmark instrumentation is a separate patch, not a production driver change.
The final matched benchmark completed successfully; the subsequent first
Nsight report import failed on an event-ordering error and was retried separately
with CUDA-only tracing. That retry (job 37706174) completed successfully,
including report statistics. It does not affect the unprofiled timings or comparisons.

See [benchmarks/openacc/README.md](benchmarks/openacc/README.md) for reproduction
and [backport-results.json](benchmarks/openacc/backport-results.json) for exact
commits, run counts, medians, ranges, validation counts and input checksum.
