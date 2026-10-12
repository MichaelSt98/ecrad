# OpenACC performance on master-omp

Base: `b97c15f6be31a5a48c7c76f5437a9d46800e8bee` (upstream master-omp).
Measured on 12 October 2026 with NVHPC 24.5 and an NVIDIA A100-SXM4-40GB.

## Changes

OpenACC now executes the existing scalar column/g-point McICA algorithms,
including split clear/cloudy kernels, recomputing adding sweeps and two-pass
LW derivatives. Scalar helpers generate ACC sequential device routines.
Atmospheric Planck output, SW gas-optics assembly, and cloud/aerosol optics
use independent spectral/vertical work items instead of serial inner loops.
Zero-sized scattering arrays have explicit PRESENT clauses. LW cloud-layer
bookkeeping has one writer per column/layer. Automatic scratch outlives its
queued device work.

This is an ACC build with OpenMP disabled, not an OpenMP execution fallback.
The shared implementation retains its existing `_omp` names.

## Matched results

Median synchronized seconds per complete 16,960-column iteration:

| NPROMA | Original ACC | OMP | Optimized ACC | Optimized ACC / OMP |
| ---: | ---: | ---: | ---: | ---: |
| 32 | 13.2573 | 6.1724 | 5.8699 | 0.951 |
| 200 | 3.3840 | 1.3273 | 1.3166 | 0.992 |
| 1024 | 1.4219 | 0.7252 | 0.6641 | 0.916 |
| 4096 | 0.9777 | 0.6268 | 0.5183 | 0.827 |

Five executions per variant/block, ten iterations each with one warm-up,
rotated variant order, one allocated GPU and one host thread. Timing includes
per-block transfers and radiation; excludes setup, warm-up and NetCDF I/O.
Separate untimed Nsight Systems profiles identified the bottlenecks.
The final matched job was 37666621 on ac6-300.

GPU builds used Release, `-O3 -Mrecursive -gpu=cc80`, double precision,
bit-identity inputs, FIAT, static libraries, and NVTX/ROCTX disabled. ACC:
ENABLE_ACC=ON, ENABLE_OMP=OFF, ENABLE_GPU=ON. OMP reference: ENABLE_ACC=OFF,
ENABLE_OMP=ON, ENABLE_GPU=ON. Both used identical input and physics.

Input: `rad4NN_inputs_2020010100_0c0.nc`, 16,960 columns and 137 levels,
from https://object-store.os-api.cci1.ecmwf.int/maelstrom-ap3/rad4NN_inputs_2020010100_0c0.nc
with SHA-256 `214b418cccfaac72b2f772eb7a8135ac8e8f8a2719fed96764d6caef51d5cb89`.
Physics included McICA ACC LW/SW, RRTMG-IFS, SOCRATES/Fu-IFS cloud optics,
12 general aerosol classes, clear-sky fluxes, LW derivatives, spectral surface
mapping and LW/SW canopy fluxes.

## Correctness and scope

- Optimized ACC: 24/24 applicable CTest checks passed.
- All 60 matched executions passed, with maximum saved-output error 0.0
  against CPU. Saved benchmark outputs are single precision, so this is not
  a claim of internal DP bit identity.
- All 42 extended comparisons passed: seven configurations, DP/SP, three GPU
  variants, with CPU references. Cases include surface/canopy diagnostics,
  offset columns, cloud scattering off, derivatives off, delta scaling with
  gases, beta overlap and Slingo liquid optics.
- DP LW/SW tolerance: 0.001 W/m². SP LW: 0.001; SP SW: 0.1 W/m², matching the
  repository's reference-validation thresholds. Unmodified SP ACC already
  differs from CPU by up to about 0.049 W/m² in a shortwave diagnostic.
- SW cloud-cover diagnostics at night now explicitly follow OMP's zero
  convention. Legacy ACC/CPU can retain nonzero cover at night. Daytime cover
  and radiative fluxes were checked separately.
- No performance claim is made for other hardware/compiler configurations or
  NPROMA=16960, which failed in the earlier 40-GB baseline experiment.

The built-in fortls launcher exited 1 on the nested experimental worktrees;
actual NVHPC compilation and runtime checks supplied validation. Timing/probe
instrumentation was confined to benchmark worktrees and is not in the driver
on this branch.
