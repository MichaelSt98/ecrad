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
SP/DP full-domain and partial-block checks have zero saved-output differences;
182 CPU tests and both precision-specific GPU regression suites pass. See
[batch-pilot/INTEGRATION_RESULTS.md](batch-pilot/INTEGRATION_RESULTS.md).

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
