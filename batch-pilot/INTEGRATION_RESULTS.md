# First full-radiation SW batch integration

Date: 2026-10-19. Branch: `nams-1.7.1-acc-opt-small-np-block-batch`.

## SP performance

Job **30116862**, completed 00:22:13, exit 0. NVIDIA OpenACC, NVHPC 24.5,
`-O3 -Mrecursive -gpu=cc80`, OpenMP disabled, bit-identity driver inputs.
Input: 16,960 columns, 137 levels, logical NPROMA=32.

The reference is pristine **2be60a2**, with timing instrumentation only.
Gas optics are unchanged in the candidate. These are not comparisons against
the later, uncommitted five-band gas candidate used in the solver replay.

Full-radiation seconds per full-input iteration, including input/output block
transfers, preparation, LW, SW staging/solving/scattering and postprocessing:

| Mode | K | Median | Process range | Reduction vs reference | Faster matched processes |
|---|---:|---:|---|---:|---:|
| Pristine parent | — | 4.1828151 | 4.1804869–4.1875892 | — | — |
| Refactored legacy path | 0 | 4.1715979 | 4.1714511–4.1761319 | 0.268% | 5/5 |
| Deferred SW | 1 | 4.0544710 | 4.0472881–4.0563951 | 3.068% | 5/5 |
| Deferred SW | 16 | 3.3643458 | 3.3557620–3.3697109 | 19.567% | 5/5 |
| Deferred SW | 32 | 3.3512959 | 3.3471620–3.3546338 | 19.879% | 5/5 |

Five rotating/reversed process groups, ten iterations per process; first
iteration excluded and median of the remaining nine used for each process.
The table takes the median of the five process medians. Setup and NetCDF I/O
are outside timing. Timing is unprofiled, single queue, without CUDA graphs.
The job uses its allocated CPU affinity; explicit single-core pinning was not
applied in this integration experiment.

Both large tiles improve the full radiation call materially. K=16 and K=32
remain useful controls; K=32 wins this median, while K=16 has a smaller tile
footprint. No speedup is inferred for the combination with five-band gas changes.

## What is integrated

The driver owns a bounded SW context and retains each block's physical
containers between preparation and finishing. Preparation still runs block by
block and includes LW. The radiation interface stages SW optics device-to-device
into the context, whose validated backend solves all active slots together.
The finishing phase scatters SW results on device, computes spectral diagnostics
and assembles the normal IFS outputs. Blocked model fields remain mapped across
iterations; optical intermediates never round-trip through the host.

This is the first complete-call milestone, not the final CLOUDSC-style blocked
physics call tree. LW and preparation have not yet been made block-parallel.

## Correctness

- SP full-domain and requested 65-column controls: candidate K=0/1/16/32 all
  match the parent with **zero saved-output differences**. The blocked loop
  ignored the requested range, so both controls processed the full input.
  Real truncated-input partial validation was subsequently added in job 30136159.
- All 25 timed SP process outputs match the reference with **zero differences**.
  Outputs are checked after each process, not separately after every iteration.
- DP job **30120707**, completed 00:05:28, exit 0: the same full-domain/requested-range
  controls and K=0/1/16/32 have **zero saved-output differences**. No DP performance
  conclusion is drawn.
- SP GPU CTest: 13/13 pass for each baseline/candidate build.
- DP GPU CTest: 13/13 pass for each baseline/candidate build.
- CPU DP+SP CTest: 182/182 pass, including legacy solver/configuration coverage.
- New/changed Fortran quick checks: zero findings. fortls launcher continues to
  exit 1 without diagnostics; actual compiler/device execution is recorded above.

Saved-output checks cover all NetCDF data variables, dimensions and finite
values, using 0.001 W/m² for LW, 0.1 for SP SW and 0.001 for DP. Exact
cloud-sample/RNG final-state/draw-count instrumentation remains open.

## Failure retained

Job **30114700** failed on the legacy K=0 control because the first extraction
removed the original `NO_CREATE` association for blocked surface-band fields.
The corrected code keeps caller-field `PRESENT`/`NO_CREATE` association in its
own structured data region while retaining owned containers with unstructured
data lifetimes. No failed experiment was overwritten.

## Reproduction and evidence

```bash
sbatch batch-pilot/run-integration.sbatch
sbatch batch-pilot/run-integration-dp.sbatch
```

Run from this worktree. Each job creates `batch-pilot/integration-JOBID/` with
source snapshots, SHA256 manifests, configuration/build/test logs, numerical
comparisons and raw iteration times. Source overlays in the runner preserve
uncommitted implementation work when making a snapshot.

Original local artifacts:

- `integration-30116862/{RESULTS.md,timing.json,validation.json,snapshots.json}`
- `integration-30120707/{validation.json,ctest-baseline.log,ctest-candidate.log}`
- `build-integration-cpu/Testing/Temporary/LastTest.log`

The DP snapshot also validates the final argument-declaration cleanup and
experimental-driver input guards made after the SP timing snapshot. Those
changes do not alter numerical formulas or the measured batching algorithm.
