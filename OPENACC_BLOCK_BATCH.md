# Block-aware radiation port

This branch develops a blocked OpenACC execution path for small logical NPROMA.
The model-facing fields retain their block dimension. Large spectral scratch is
bounded by an execution tile rather than allocated for the entire domain.

## Implemented checkpoints

### SW solver pilot and focused SP experiment

The validated SW McICA backend parallelises over block, column and g-point,
preserving vertical recurrences, RNG traversal and sequential spectral sums.
The separate capture/replay experiment used all 530 NPROMA=32 blocks from the
16,960-column, 137-level input on A100/NVHPC 24.5.

| Solver replay | Existing | Pilot K=1 | Pilot K=16 | Pilot K=32 |
|---|---:|---:|---:|---:|
| Resident, seconds | 0.779595 | 0.701517 | 0.158061 | 0.139473 |
| Host staging included, seconds | 1.571862 | 1.496167 | 0.763569 | 0.802123 |

Five matched processes per configuration/boundary, one warmup plus nine measured
iterations; all 400 confirmation iterations had zero surface/broadband flux
differences. Separate profiles show 5,830 launches reduced to 102 at K=32, and
warmed kernel time reduced from 0.7040 s to 0.1389 s. These are solver replay
numbers, not full-radiation timings. Job: 30101373.

### Full-radiation SW continuation

The first integrated SP B32 measurement passes its performance gate: **4.182815 s
parent -> 3.351296 s at K=32 (19.879% reduction)**. K=16 reduces time by 19.567%.
SP/DP full-domain checks have zero saved-output differences;
182 CPU tests and both precision-specific GPU regression suites pass. See
[batch-pilot/INTEGRATION_RESULTS.md](batch-pilot/INTEGRATION_RESULTS.md).

The original requested 65-column controls did not truncate the blocked driver.
Actual truncated-input validation now passes in phase job 30136159. Current SP
lineage, phase costs and subsequent LW tile milestones are recorded in
[`sp_bench_versions.md`](sp_bench_versions.md) in this branch.

`radiation_sw_batch_context_mod.F90` owns the device-resident input tile,
SW solver workspace and output buffers. It also defines retained physical
container state for each block. The first integrated caller has two phases:

1. `RADIATION_SCHEME(...,KPHASE=1,...)` performs the existing preparation,
   optics and LW calculation. SW inputs are staged device-to-device into the
   tile. Its physical containers remain alive; no SW outputs are consumed yet.
2. After all active slots are ready, the caller invokes the tiled SW solver.
   `RADIATION_SCHEME(...,KPHASE=2,...)` scatters SW results on device, computes
   surface/TOA spectral diagnostics and the normal IFS outputs, then releases
   that block's containers.

Omitting the continuation arguments retains the original single-call API.
The blocked driver opts in via:

```bash
export ECRAD_SW_BATCH_SIZE=16  # also test 1 and 32; unset/0 selects legacy path
build/bin/ecrad_ifs_blocked_sp config.nam input.nc output.nc
```

The integrated driver maps blocked model fields once and transfers their input
and output regions per iteration. It keeps optical intermediates on device;
there is no host optical-property packing or replay in this path. Queue 1
orders preparation, staging, solve, scatter and postprocessing. Tile ownership
is single-caller; concurrent callers need independent contexts.

This is a transitional caller integration: preparation and LW still launch
per block. The end-state is a CLOUDSC-style blocked GPU call tree with the
appropriate column/layer/g-point mapping at each stage, not a host block loop
launching the complete pipeline for every block.

### Optional bounded LW continuation

The next measured milestone stages LW optics, Planck functions, cloud fields
and original seeds on device and invokes the existing intrinsic-array LW core
over a contiguous K×NPROMA tile. Finishing scatters LW before spectral diagnostics.
Enable both continuations with:

```bash
export ECRAD_SW_BATCH_SIZE=32
export ECRAD_LW_BATCH=1
```

Job 30140239 reduces matched SP full-call time from 3.302355 to 2.779014 s
(15.848%); K16 reduces it by 14.958%. Both win all five matched process pairs.
SP/DP full and actually truncated 65-column inputs have zero saved-output
differences. Both GPU suites pass 13/13 and rebuilt CPU DP+SP passes 182/182.
First failed derivative checks are retained; late-initialized LW owner flags
must be updated on device before scatter. Optional derivative output uses its
own contiguous allocation.

Initial LW support requires McICA ACC, cloud scattering enabled, aerosol LW
scattering disabled and clear-sky diagnostics. The bridge keeps logical NPROMA=32
but does not yet expose an explicit LW block dimension or persistent internal
solver scratch. Profiled SW staging/solve/scatter costs only ~0.19 s; the old
per-block LW cost is ~0.667 s and optics remains ~1.65 s. The next major target
is blocked preparation/optics. `ECRAD_BATCH_PROFILE=1` enables separate
synchronized diagnostic buckets; leave it unset for headline timings.

### Optional bounded gas-optics continuation

`ECRAD_GAS_BATCH=1` adds phases 3 (prepare physical inputs and stage gas fields)
and 4 (resume cloud/aerosol optics using tile gas results). One RRTMG/albedo call
per tile replaces one per logical block; LW/SW solve/finish remains phase 2.
The resident tile is owned by `radiation_gas_batch_context_mod.F90`; logical
NPROMA and original solver seeds remain unchanged.

```bash
export ECRAD_SW_BATCH_SIZE=32 ECRAD_LW_BATCH=1 ECRAD_GAS_BATCH=1
```

Job 30153332 measures **2.827999 -> 1.459260 s (48.400% reduction)** against
the matched LW+SW-only control; all five process pairs improve. K16 improves
by 45.961%. SP/DP full-domain and mixed-day/night partial tests have zero saved
output differences; GPU suites pass 13/13 each and native CPU DP/SP 182/182.
These are GPU-parent validation results; the recorded large-input CPU/GPU SP
discrepancy remains an independent open issue.

Initial gas support requires RRTMG in both spectra, simple surfaces,
half-level thermodynamics and no spectral solar scaling. The current bridge
uses a contiguous tile view and retains per-call RRTMG internal scratch.
Remaining preparation/finishing costs ~0.831 s and cloud/aerosol optics ~0.118 s
in the separate K32 diagnostic run; these are the next block-port targets.

### Full-pipeline execution tile

The full-tile path gathers model fields `(NPROMA,field,block)` on device into a
bounded contiguous compute tile, executes preparation, all optics, solvers,
spectral diagnostics and finishing for that tile, then scatters outputs to the
unchanged model layout. Model NPROMA stays 32; execution width is K×32. This
is the compute-tile bridge, with host orchestration per tile and existing
intrinsic-array physics, rather than the final explicit-block leaf interfaces.

```bash
export ECRAD_FULL_BATCH_SIZE=256
```

This switch selects the full pipeline in place of the staged SW/LW/gas modes.
No host optical-property packing is used. Bit-identity input seeds are gathered;
ordinary initialization preserves each original logical block's seed indices.
The immutable field-index metadata is mapped for the gather/scatter lifetime.

- Job 30164046: matched frozen GPU parent **1.431343 -> 0.342256 s** at K128,
  **76.088% reduction / 4.182× faster**; all five pairs improve.
- Job 30166891: K256 gives **0.331322 s**, versus **0.345101 s** at K128 in the
  same tuning job. Fresh same-allocation CPU NPROMA=32, 32 physical cores gives
  **0.773025 s**, so the GPU is **2.333× faster** at the same quarter-node share.
- SP/DP full-domain and mixed-day/night partial checks have zero saved-output
  deviation from the GPU parent. Selected normal-mode SP checks against a
  separately rebuilt non-bit-identity parent also match exactly.
- GPU CTest 13/13 per precision; native CPU DP/SP regression 182/182.
- Nsight iteration-2 captures exclude setup/warmup: K128 has 965 launches;
  K256 has 579 launches and ~0.28877 s total GPU kernel duration. CPU API/wait
  durations overlap device work and are not added to this total.

The native SW vector policy wins over tested fixed 32/64/256 lengths. The
`ECRAD_SW_VECTOR_LENGTH` build macro is an optional experimental control,
not a default algorithm change. No graphs, approximate math or RNG changes
are used. The default full-tile physics still allocates internal scratch per
execution tile; explicit-block interfaces and persistent leaf workspaces can
be developed further without reintroducing per-model-block orchestration.

The benchmark build optionally defines `ECRAD_NSYS_CAPTURE_API` and links
CUDA runtime profiling entry points; `ECRAD_NSYS_CAPTURE=1` captures the second
iteration through the CUDA Profiler API. Normal builds need no profiling API.
See `batch-pilot/run-full-milestone.sbatch`, `run-full-tuning.sbatch` and the
branch-root `sp_bench_versions.md` for complete results and source artifacts.

## Supported initial path

- OpenACC, McICA ACC SW, clear and direct diagnostics enabled.
- Pre-scaled aerosol optics (`do_sw_delta_scaling_with_gases=false`).
- At least two levels, positive cloud threshold/decorrelation scaling.
- Common coefficient/configuration tables for the tile, stable until all its
  outputs have been consumed.
- Caller input/output arrays must remain present and refer to the same model
  block between its prepare and finish calls.
- Partial model blocks and partial tiles; immutable original column seeds.
- Explicit `INIT`/`RELEASE`; do not intrinsically copy mapped owners or discard
  a block state between its prepare and finish calls.
- The optional `COPY_ASYNC` driver mode is not combined with batching.

## Validation and reproduction

- `batch-pilot/run-gpu.sbatch`: original DP/SP synthetic solver comparisons.
- `batch-pilot/run-sp-perf.sbatch`: frozen-source real-input SP solver experiment.
- `batch-pilot/run-integration.sbatch`: full-radiation SP validation and timing.
 - `batch-pilot/run-integration-dp.sbatch`: DP integration correctness, no DP timing.

Integration comparisons use pristine **2be60a2**, not the later five-band gas
candidate. A candidate K=0 control isolates the lifetime refactor; K=1/16/32
exercise actual deferred SW solving. Both baselines must be stated explicitly
when comparing this work with previous experiments.

Per-job source snapshots, numerical comparisons, raw times and build logs are
kept below `batch-pilot/integration-JOBID/`; large artifacts are ignored by Git.
The first failed integration job (30114700) is retained: it exposed the need to
preserve the original blocked-field `NO_CREATE` association independently of
owned container lifetime.

## Next checkpoints

1. Keep the five-band source integration distinct and remeasure the combination.
2. Complete direct cloud-sample/RNG-state/draw-count validation and configuration
   coverage; flux agreement does not prove every RNG state transition.
3. Port LW sampling/transfer/derivatives to the same bounded tile infrastructure.
4. Profile the combined full caller, then move preparation/optics across the
   blocked dimension, eliminating per-block launches and avoidable staging.
5. Extend B64 and larger-block controls, measure integrated peak memory, and
   retain DP/CPU correctness coverage while SP remains the performance target.
6. Assess CUDA graphs after ordinary blocked execution is established.
