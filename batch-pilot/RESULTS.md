# Initial block-aware SW McICA pilot results

Date: 2026-10-19. Status: synthetic correctness passed; no performance claim.

Follow-up: the focused **SP real-input performance experiment** is now complete.
See [perf-sp-30101373/INTERPRETATION.md](perf-sp-30101373/INTERPRETATION.md):
5.59× resident solver speedup at K=32 and 2.06× staging-inclusive replay speedup
at K=16. These are SW solver replay results, not full-radiation timings. The
initial synthetic pilot results below are retained as their own historical step.

## Implemented

- Host-owned, reusable scratch and explicit release/queue lifetime.
- Explicit block dimension in every computational stage.
- Six kernels per active batch: preparation, cumulative cloud cover/bounds,
  cloud sampling, clear transfer, cloudy transfer/surface mixing, spectral
  summation/broadband mixing.
- Validated numerical device helpers reused for transfer; original inlined
  NVIDIA cloud-generator algorithm retained with block indexing.
- Active-block/column bounds, partial/empty blocks, night conventions and
  independent workspaces/queues.

The experimental path supports pre-scaled aerosol optics, clear diagnostics,
positive cloud threshold/decorrelation scaling and at least two levels. It is
not yet wired into the full radiation caller.

## Reference provenance

Reference: frozen five-band candidate from job 38287400, built afresh by the
standalone pilot project. `verify_reference.py` verified all 569 source hashes
and byte identity of the four original computational dependency files.

Test-only preprocessing renames/exposes the original solver module without
changing its numerical code. DP/SP use separate module directories.

Validated numerical implementation SHA256:

```text
e691e4dc3146315216037c3503dc0154377427f7f3fea43c6c265e0cf19f7036
```

Final comparison harness SHA256:

```text
124edbde2d088f2181689d1c50c8d3629452c065d3ffb8b3edf8c28fc569c07d
```

## Final checks

| Check | Result |
|---|---|
| NVHPC 24.5 CPU DP comparison | Passed; maximum flux error 5.6843e-14 W/m² |
| NVHPC 24.5 CPU SP comparison | Passed; maximum flux error 6.1035e-5 W/m² |
| A100 NVHPC 24.5 DP comparison | Passed; maximum flux error zero |
| A100 NVHPC 24.5 SP comparison | Passed; maximum flux error zero |
| Cloud cover, inactive masks and nighttime values | Exact checks passed |
| Separate workspaces/queues with different seeds | Both checked against their own reference; passed |
| Repeated inputs/seeds/cloud transitions/threshold changes | Passed |
| Reuse, resize, empty blocks/batches, repeated release | Passed |
| Fortran quick checker, new numerical module and harness | 0 findings |
| Shell/Python syntax | Passed |
| fortls diagnostics | Launcher exited 1 without diagnostics for both new Fortran files |

Final GPU job: **30097169**, COMPLETED, exit 0, elapsed 23 seconds (build plus
tests; **not a performance measurement**). Evidence: `job-30097169.log` and
`build-gpu/Testing/Temporary/LastTest.log`. Pilot fingerprints and frozen source
checks appear before and after that run.

Each precision runs five shape/batch scenarios with four successive states and
two workspaces using different seeds: 40 batch solves plus individual-block
reference calls. Tests use 112 g-points and 14 bands. Cases include:

- 137 levels, NPROMA 32, K=1.
- 137 levels, NPROMA 32, capacity 3 with 2 active blocks, beta overlap.
- 2 levels, NPROMA 5, capacity 4 with 3 active blocks.
- 11 levels, NPROMA 7, capacity 3 with zero active blocks.
- 137 levels, NPROMA 32, capacity 3 with all 3 blocks active.

Offset/partial columns, an empty active block, nighttime/clear/full-cloud
columns and changing clouds/seeds are exercised within those scenarios. Both
workspaces are enqueued before waiting on GPU. Actual hardware overlap was not
profiled; correctness does not require simultaneous scheduling.

Scratch estimate per DP workspace for B32/K16/137 levels/112 g-points:
**508,809,216 bytes = 485.24 MiB**, excluding input/output buffers, descriptor
metadata and runtime overhead. Total device peak is not yet measured.

## Earlier failures and fixes

- Initial CPU scalar-local cumulative product produced incorrect clear-column
  cover at `-O3`; diagnostic output masked the issue. Per-column array storage,
  matching the reference design, passes with diagnostic output removed. A
  specific compiler cause has not been established.
- GPU job **30095720** passed the initial 137-level cases but failed the
  one-level case on a zero-length overlap-array PRESENT lookup. The initial
  pilot now explicitly requires at least two levels. That failed job is kept.
- Job **30096536** passed the corrected extent cases with identical workloads
  on two queues. The final harness strengthened this to different seeds and
  separate expected results; job **30097169** passed that version.

## Open gates

1. Capture real solver fixtures and validate sampled clouds/RNG final states
   and draw counts directly. Synthetic flux equality does not prove that gate.
2. Measure matched K=1/K>1 timings and compiler/profile resource usage.
3. Add the batch-aware radiation caller and transfer/staging-inclusive B32
   experiment. No end-to-end benefit has yet been demonstrated.
4. Extend supported configurations/diagnostics, then LW and preparation stages
   as justified by the measured gates in the working plan.
