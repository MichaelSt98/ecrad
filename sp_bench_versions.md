# SP CPU/GPU lineage and block-batch port milestones

## Latest checkpoint

The full-pipeline device-tile path reaches **0.3313219 s** on one A100 versus
**0.7730250 s** on 32 physical CPU cores in the same quarter-node allocation:
**2.333× faster**. Model NPROMA remains 32; the selected execution tile is K256.
Enable it with `ECRAD_FULL_BATCH_SIZE=256`.

SP/DP full-domain and partial-tile checks have zero saved-output deviation from
the frozen parent GPU build. Ordinary, non-bit-identity SP checks also match
the separately rebuilt GPU parent. The implementation uses a contiguous
compute-tile view; explicit-block leaf interfaces remain a follow-up.

The ledger below records the successive checkpoints, matched experiments,
validation scope and retained artifact locations.

## Protocol

- Same full input: 16,960 columns, 137 levels; SHA256
  `214b418cccfaac72b2f772eb7a8135ac8e8f8a2719fed96764d6caef51d5cb89`.
- NVHPC 24.5, Release `-O3 -Mrecursive`; internal **single precision**.
- **NPROMA=32 for CPU and every GPU version**.
- Quarter of an A100 node: one of four GPUs versus **32 of 128 physical CPU
  cores**. SMT siblings are not counted as additional cores. Verify affinity
  against `lscpu`; CPU uses 32 OpenMP threads, GPU one host thread within the
  same CPU allocation. GPU compute capability 80; ACC enabled, OMP disabled.
- Identical benchmark physics and bit-identity inputs. CPU uses the same
  McICA ACC implementation with ACC disabled; no GPU fallback is timed as CPU.
- Five rotated/reversed process groups, ten iterations per process, one warmup
  excluded. Report median of five process medians and their range.
- Synchronized full radiation including per-iteration transfers; excludes
  configuration/coefficient setup, initial host blocking and NetCDF I/O.
- Every process output checked against the 32-core CPU reference: finite values,
  dimensions, LW tolerance 0.001, SW 0.1 W/m². Record the existing zero-at-night
  SW cover convention separately; daytime cover must agree within 1e-6.
- Raw iteration times, configurations, source hashes and allocation/topology
  evidence retained under the worktree's `batch-pilot/lineage-JOBID/` directory.

## 1. Quarter-node lineage matrix

Runner: `ci-debug/block-batch-sw/batch-pilot/run_lineage.py` and
`run-lineage.sbatch`. Five-process matrix job **30140646** completed in 00:31:47;
it reuses the immutable builds/snapshots from **30135604** and records executable
hashes and its own allocation. Initial job 30135604 stopped at a cross-version
numerical mismatch; see the numerical investigation below.

The fair-share CPU time is **0.699925 s**, versus **3.299987 s** for SW batching.
The CPU remains substantially faster at this resource share. The subsequent LW
milestone reaches **2.779014 s** in its separate matched experiment. These are
wall-time observations with the numerical agreement statuses retained below.
The following gas-optics milestone reaches **1.459260 s**; its own matched
LW+SW-only control is 2.827999 s (a **48.400%** reduction). Milestone runs are
separate experiments, so their separately measured savings are not added.
All GPU versions fail the strict large-input CPU threshold: master-opt misses
the LW threshold narrowly (maximum 0.003815 W/m²), while the original/release
lineages have materially larger discrepancies (~4.012 W/m² LW). A completed
benchmark job does not mean these cross-version numerical checks passed.

| Label | Revision | Meaning |
|---|---|---|
| cpu | `2be60a2` | Native CPU, 32 physical cores, ACC disabled |
| master-acc | `4dad0bd` | Original master-acc |
| master-opt | `7ea1fef` | Optimised ACC on master-omp lineage |
| release-opt | `8414ec3` | General optimisations backported to release 1.7.1 |
| small-np | `2be60a2` | Additional small-NPROMA optimisations |
| batch K16/K32 | `9ee11eb` | Integrated bounded SW batching |

The table is a comparison of explicitly identified versions, not one linear
ancestry chain. The uncommitted five-band candidate is not in the batching
revision and its previous DP savings must not be added to these SP results.

<!-- lineage-results:start -->
### Quarter-node SP lineage matrix

Job 30140646; ac6-300.bullx.
NPROMA=32; one GPU / 32 physical CPU cores; five processes, nine measured iterations each.

| Version | K | Seconds | Process range | CPU / version | CPU agreement |
|---|---:|---:|---|---:|---|
| cpu | 0 | 0.6999249 | 0.6992609–0.7005849 | 1.000 | pass |
| master-acc | 0 | 12.0187901 | 12.0158660–12.0196760 | 0.058 | FAIL |
| master-opt | 0 | 4.7993939 | 4.7970429–4.8098180 | 0.146 | FAIL |
| release-opt | 0 | 5.3864980 | 5.3855178–5.3967199 | 0.130 | FAIL |
| small-np | 0 | 4.1254060 | 4.1194410–4.1318989 | 0.170 | FAIL |
| batch | 16 | 3.3098080 | 3.3051949–3.3103349 | 0.211 | FAIL |
| batch | 32 | 3.2999868 | 3.2915408–3.3106730 | 0.212 | FAIL |

CPU / version below one means GPU is slower than the 32-core CPU run.
**FAIL** rows are timing context, not validated equivalent-output speedups.
All outputs repeat within their version; SW batching matches its GPU parent.

| Version | Maximum CPU-reference LW error | Maximum SW error |
|---|---:|---:|
| master-acc | 4.0117188 | 2.4433346 |
| master-opt | 0.0038146973 | 0.0088381767 |
| release-opt | 4.0116882 | 2.4433346 |
| small-np | 4.0116882 | 2.4433346 |
| batch | 4.0116882 | 2.4433346 |

Artifacts: `/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot/lineage-30140646`; raw times, source/binary hashes, allocation/topology,
per-process configurations/logs/outputs, numerical failures and CTest logs.
<!-- lineage-results:end -->

## 2. Batched versus not-yet-ported cost

Completed diagnostic job **30136159**, frozen SW-only snapshot based on
`9ee11eb` plus opt-in phase timers. Three processes per K/mode, five iterations
each, first excluded. These synchronized diagnostic runs are separate from the
headline matrix. Times below are medians of per-process phase medians; these
independently aggregated medians need not sum exactly to the total.

| Phase | K16 seconds | K32 seconds | K32 share |
|---|---:|---:|---:|
| SW device staging | 0.0458856 | 0.0468505 | 1.39% |
| Batched SW solve | 0.1491035 | 0.1160883 | 3.44% |
| SW device scatter | 0.0279232 | 0.0283939 | 0.84% |
| Gas/cloud/aerosol optics and albedos, per block | 1.6424978 | 1.6573280 | 49.13% |
| LW solve, per block | 0.6650214 | 0.6665097 | 19.76% |
| Other preparation, per block | 0.4443589 | 0.4493429 | 13.32% |
| Other finishing/diagnostics, per block | 0.3719584 | 0.3742060 | 11.09% |
| Blocked-field transfers | 0.0345469 | 0.0344954 | 1.02% |
| Remaining orchestration | 0.0001219 | 0.0000636 | <0.01% |

K32: **about 0.1913 s (5.7%)** in SW staging + batched solve + scatter;
**about 3.1474 s (93.3%)** still in per-block preparation/optics/LW/finishing;
about 1% in field transfers. Optimising SW solving alone now has little
full-call headroom. Optics are the largest remaining target. LW is a bounded
next continuation milestone using the existing validated scalar/g-point core;
its current ~0.667 s places an upper bound on the benefit before staging costs.

Instrumented versus uninstrumented controls:

- K16: 3.3811970 versus 3.3627795 s (~0.55% instrumentation overhead).
- K32: 3.3734486 versus 3.3602276 s (~0.39% overhead).

Raw bucket 6 includes preparation and buckets 1/4/5; bucket 7 includes finishing
and bucket 3. Subtract nested buckets before summing. No CPU wait/API time is
added to GPU kernel durations. Enable with `ECRAD_BATCH_PROFILE=1`.

Validation: genuinely truncated **65-column** input plus full domain, K0/1/16/32,
all saved-output errors zero; SP GPU CTest 13/13 passed. Artifacts:
`ci-debug/block-batch-sw/batch-pilot/milestone-phases-30136159/`.

## 3. Subsequent batch-port milestones

### Milestone: bounded LW continuation — validated and measured

Opt-in `ECRAD_LW_BATCH=1` with SW batch K16/K32. Stage LW optical properties,
Planck functions, cloud fields and original seeds device-to-device; call the
existing intrinsic-array LW solver across a contiguous tile view, then scatter
LW outputs before ordinary per-block diagnostics. No spectral host packing.

This is an intermediate tile bridge: logical NPROMA stays 32, while the LW
backend sees up to K×32 columns. Its internal scratch is still allocated per
tile; an explicit block-dimensional persistent-workspace backend remains a
subsequent step. Initial support requires cloud scattering, no aerosol LW
scattering, clear diagnostics and McICA ACC. Legacy paths remain available.

First run **30139239** failed its real 65-column derivative check. Run
**30139835** tested dedicated derivative storage; the owner also needed its
optional LW scalar flags updated on device after late initialization.
Run **30140239** includes that explicit device update. Failed snapshots/logs
are preserved. Job 30140239 passed validation and completed the matched run.

The milestone runner covers SP/DP full and real partial inputs, K1/16/32,
both GPU CTest suites, five-process matched SW-only versus LW+SW timings and
separate phase diagnostics. Both tiles win all five matched process pairs.
At K32, **3.3023550 -> 2.7790141 s (15.848% reduction)**. These are matched
SW-only versus LW+SW measurements, not an additive combination of earlier
separately measured savings. GPU uses one host thread within the quarter-node
allocation; this milestone did not explicitly pin that thread to one core.
Validation here is against the legacy GPU path. The large-input CPU-reference
discrepancy described below also applies to the batching lineage.

Diagnostic LW staging + solving + scatter at K32 costs about **0.16764 s**,
versus the old per-block LW solve's ~0.66660 s. Optics still costs ~1.65 s,
so preparation/optics is the next main block-port target. The diagnostic total
2.85548 s is ~2.75% above the uninstrumented 2.77901 s; diagnostic runs have
extra waits and are not used for headline speedup claims. Direct RNG-state
instrumentation, runtime peak memory and broader configuration coverage remain
open; saved-output agreement alone is not claimed to close those checks.

<!-- lw-results:start -->
### LW tile milestone SP

| K | LW batched | Seconds | Process range | Reduction |
|---:|---:|---:|---|---:|
| 16 | 0 | 3.3124621 | 3.3042249–3.3159280 | 0.000% |
| 16 | 1 | 2.8169861 | 2.8107930–2.8817342 | 14.958% |
| 32 | 0 | 3.3023550 | 3.2963770–3.3674619 | 0.000% |
| 32 | 1 | 2.7790141 | 2.7765438–2.8373389 | 15.848% |

Maximum saved-output difference over validation/timing/phase processes: **0**.
SP/DP full-domain and actual 65-column inputs, K1/16/32 versus legacy K0.
GPU CTest 13/13 per precision; rebuilt native CPU DP+SP CTest **182/182**.

Separate diagnostic runs, one warmup plus four measured iterations:

| K | LW batching | Total s | Optics s | Per-block LW s | LW stage s | LW solve s | LW scatter s |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 16 | 0 | 3.3730950 | 1.6382879 | 0.6645694 | 0.0000000 | 0.0000000 | 0.0000000 |
| 16 | 1 | 2.8913755 | 1.6390338 | 0.0016450 | 0.0403052 | 0.1205864 | 0.0266435 |
| 32 | 0 | 3.3670330 | 1.6550224 | 0.6666024 | 0.0000000 | 0.0000000 | 0.0000000 |
| 32 | 1 | 2.8554755 | 1.6521993 | 0.0016394 | 0.0405851 | 0.0999551 | 0.0271017 |

After the LW milestone, synchronized diagnostic wall-time split:

| K | SW batched chain s | LW batched chain s | Remaining per-block s | Remaining share |
|---:|---:|---:|---:|---:|
| 16 | 0.2135698 | 0.1875053 | 2.4558837 | 84.94% |
| 32 | 0.1815341 | 0.1676681 | 2.4715442 | 86.55% |

Raw buckets 9/10/11 are LW stage/solve/scatter. Staging is nested in
preparation and bucket 5; solving is outside preparation; scatter is nested
in finishing. Subtract nested buckets before summing.

Artifacts: `/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot/milestone-lw-30140239`; source manifests, builds/tests, validation, raw timings and phase arrays.
<!-- lw-results:end -->

### LW configuration coverage and caller consistency

Accepted job **30150529**: all **24 SP/DP comparisons** pass for default,
derivatives-off and beta-overlap configurations, using a real 65-column tile
with alternating day/night and a final one-column daylight block. Full-input
SP/DP controls also pass against the previous accepted legacy GPU outputs;
both GPU CTest suites pass 13/13. Source snapshot, fixture modification/hash,
per-variable errors and full-input control timings are in
`ci-debug/block-batch-sw/batch-pilot/milestone-lw-coverage-30150529/`.

The derivatives-off checks exposed existing blocked-caller inconsistencies:
the namelist disabled ecRad's allocation while the model output flag still
requested derivatives; disabling that flag then exposed an invalid mandatory
derivative argument because its field had no backing storage. The offline
driver now synchronizes the output flag, reserves storage for the mandatory
argument and only initializes/copies an allocated derivative array. Default
benchmark settings are unaffected. Failed jobs 30145664 and 30149441 are
retained. Rebuilt native CPU DP/SP regression suite passes 182/182 after these
changes and the subsequent gas-continuation integration.

### Milestone: RRTMG gas-optics continuation — validated and measured

Job **30153332**, completed 00:15:10, tests `ECRAD_GAS_BATCH=1` in combination with the existing
LW/SW continuations. Prepared gas, pressure/temperature and surface fields are
staged device-to-device into a bounded contiguous tile. Surface albedos and
RRTMG gas optics/Planck functions are computed once per tile, then their results
are loaded into each block's cloud/aerosol optics calculation. No host optical
property packing or changes to the gas algorithm are introduced.

The caller now has a prepare-inputs phase (3), a cloud/aerosol/solver-staging
phase (4), then solve/finish (2); the established phases 0/1/2 remain available.
Logical NPROMA stays 32. This still uses a contiguous compute-tile view, not an
explicit block-dimension RRTMG leaf-call tree or persistent internal RRTMG
scratch. Initial support requires RRTMG for LW/SW, simple surfaces, half-level
thermodynamics and no spectral solar scaling.

SP/DP full-input and mixed-day/night partial-tile checks pass with zero saved
output differences. Both GPU suites pass 13/13; native CPU DP/SP passes 182/182.
K16/K32 both win all five matched processes. K32 improves **2.8279991 ->
1.4592600 s (48.400%)**, K16 improves by 45.961%. These comparisons are against
the LW+SW-only control in the same job, not against a CPU-equivalent-output
reference; the historical large-input CPU discrepancy remains open.

For K32, separate diagnostics put gas staging + albedos/gas solving + loading
at ~**0.15946 s**, versus the old combined optics bucket of ~1.647 s. The
remaining per-block cloud/aerosol optics cost is ~**0.11775 s**. Preparation
and finishing now dominate: ~**0.46028 + 0.37051 = 0.83079 s**, about **56%**
of the 1.48992 s instrumented total. Including remaining optics, about **64%**
is still per-block. The next port target is therefore resident/prepared block
state and preparation/finishing, followed by the remaining cloud/aerosol stage.
Diagnostic overhead/variation is ~2.1% relative to the uninstrumented total.

<!-- gas-results:start -->
### RRTMG tile milestone SP

| K | Gas batching | Seconds | Process range | Reduction |
|---:|---:|---:|---|---:|
| 16 | 0 | 2.8663390 | 2.8594931–2.8682158 | 0.000% |
| 16 | 1 | 1.5489480 | 1.5449312–1.5575981 | 45.961% |
| 32 | 0 | 2.8279991 | 2.8232331–2.8401048 | 0.000% |
| 32 | 1 | 1.4592600 | 1.4584000–1.4636040 | 48.400% |

Maximum saved-output difference over SP/DP validation, SP timing and profiles: **0**.
GPU CTest 13/13 per precision; native CPU DP/SP CTest 182/182.

Separate synchronized phase diagnostics (median of four warmed iterations):

| K | Gas batching | Total s | Gas stage s | Gas solve/albedos s | Gas load s | Remaining optics s | Other preparation s | Finishing s |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 16 | 0 | 2.8780366 | 0.0000000 | 0.0000000 | 0.0000000 | 1.6339737 | 0.4390647 | 0.3701458 |
| 16 | 1 | 1.5782415 | 0.0338799 | 0.1344491 | 0.0326436 | 0.1163836 | 0.4594656 | 0.3686013 |
| 32 | 0 | 2.8413261 | 0.0000000 | 0.0000000 | 0.0000000 | 1.6470033 | 0.4409690 | 0.3692494 |
| 32 | 1 | 1.4899241 | 0.0336485 | 0.0929745 | 0.0328386 | 0.1177541 | 0.4602827 | 0.3705065 |

K16: faster in 5/5 paired processes.

K32: faster in 5/5 paired processes.

Gas staging is nested in preparation; gas load is nested in raw optics bucket 4.
Gas solve/albedos is outside the per-block preparation buckets. The table uses
exclusive buckets; do not add the nested raw buckets a second time.

Artifacts: `/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot/milestone-gas-30153332`; immutable source hashes, SP/DP validation, timing and phase JSON.
<!-- gas-results:end -->

<!-- gas-coverage:start -->
Extended gas+LW+SW coverage: **milestone-lw-coverage-30159578**.
24 SP/DP runs: six legacy references and 18 candidate comparisons covering
default, derivatives-off and beta-overlap, K1/16/32, on mixed day/night
65-column input with a final single daylight column.
Maximum candidate saved-output difference: **0**.
Full-domain SP control: 1.4287380 s over two warmed iterations; maximum saved error 0. Validation timing only, not a new performance claim.
Full-domain DP control: 1.6829145 s over two warmed iterations; maximum saved error 0. Validation timing only, not a new performance claim.
GPU CTest 13/13 per precision. Frozen reused source/binary hashes and fixture
modification/hash retained in `/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot/milestone-lw-coverage-30159578`.
<!-- gas-coverage:end -->

### Milestone: full-pipeline device execution tile

Current acceptance reference: the **frozen parent GPU binaries from job
30153332**, with K32 gas+LW+SW continuation. Per user instruction, further
acceptance checks concern deviation from that GPU parent; the CPU timing is
only a resource-matched performance target in this phase of work.

`ECRAD_FULL_BATCH_SIZE=K` now gathers the model's `(32,field,block)` data on
device into a bounded compute tile, runs the complete radiation scheme over
that tile, and scatters outputs back on device. This includes thermodynamic
and cloud preparation, gas/cloud/aerosol optics, LW/SW, spectral diagnostics,
output assembly and cleanup. Model input/output layout stays NPROMA=32. The
existing native scalar/g-point solver algorithms are used over the tile.

This milestone is explicitly a **contiguous compute-tile view**: the physics
call has execution width K×32. It is not an isolated 32-column speedup or a
completed explicit-block-dimension/device-callable leaf-interface rewrite.
Host orchestration and allocations now occur per execution tile, rather than
per logical model block. Internal workspace allocation remains a tuning target.

Original input seeds are gathered with the model fields in bit-identity mode.
Without bit-identity mode, original `init_seed_simple` block-local seeds are
preserved as column-within-logical-block, rather than replacing them with wide
tile column indices. There is no host optical-property packing.

GPU-parent SP/DP checks cover full input and actual 65-column mixed-day/night
input, including single-column partial blocks and partial execution tiles.
K1 is an orchestration/packing control; K16/32/64/128 expose 512–4096 columns.
Five matched process groups compare all full-tile variants to the immutable
parent on the same quarter-node allocation. Captures use Nsight Systems
2024.1.1 with CUDA Profiler API boundaries around **iteration 2 only**. Setup,
warmup and NetCDF I/O are excluded from the captured radiation interval.
Profiles and synchronized phase runs remain separate from headline timings.

First job **30163743** stopped at a missing `IFS_CONFIG` PRESENT association in
the new device gather. The corrected caller maps this immutable index metadata
for the full-tile lifetime. Its failed artifacts are retained. Job **30164046**
contains the correction and completed validation, timing and profiling in
00:09:04, exit 0. All checks against the parent GPU passed with zero saved
output differences. The K128 median is 0.342256 s versus 1.431343 s for the
parent, a 76.088% reduction (4.182× faster), winning all five pairs.

<!-- full-results:start -->
### Full pipeline device-tile SP milestone

| Version | K | Seconds | Process range |
|---|---:|---:|---|
| parent | 0 | 1.4313431 | 1.4276769–1.4341901 |
| candidate | 16 | 0.5485520 | 0.5480249–0.5488150 |
| candidate | 32 | 0.4325328 | 0.4322300–0.4330359 |
| candidate | 64 | 0.3730221 | 0.3727331–0.3731242 |
| candidate | 128 | 0.3422560 | 0.3419070–0.3423479 |

Maximum saved-output difference versus frozen GPU parent: **0**.
SP/DP full-domain and mixed day/night partial-input validation passes; GPU CTest
13/13 per precision and rebuilt native CPU DP/SP regression suite 182/182.

K16: 61.676% reduction, 2.609× GPU-parent speedup, 5/5 faster pairs.
K32: 69.781% reduction, 3.309× GPU-parent speedup, 5/5 faster pairs.
K64: 73.939% reduction, 3.837× GPU-parent speedup, 5/5 faster pairs.
K128: 76.088% reduction, 4.182× GPU-parent speedup, 5/5 faster pairs.

Separate synchronized diagnostic wall times:

| K | Total s | Device gather s | Full physics/finishing s | Scatter s | Field transfers s |
|---:|---:|---:|---:|---:|---:|
| 32 | 0.4351575 | 0.0014931 | 0.4012268 | 0.0003419 | 0.0320343 |
| 128 | 0.3428435 | 0.0013227 | 0.3097553 | 0.0001790 | 0.0315670 |

Nsight steady-iteration captures (setup and warmup excluded):

| K | Launches | GPU kernel total s | SW clear/cloudy solve kernels s |
|---:|---:|---:|---:|
| 32 | 3281 | 0.3513216 | 0.1292356 |
| 128 | 965 | 0.2964710 | 0.1191140 |

GPU kernel totals and CPU CUDA API/wait durations overlap and are not added.
At K128 the SW solve kernels consume about 40% of GPU kernel time; this motivates
the following launch-vector sweep. The gather/scatter itself is only about 1.5 ms.

Artifacts: `/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot/milestone-full-30164046`; sources/hashes, validation, raw times, phase arrays, `trace.nsys-rep`,
`trace.sqlite`, CSV statistics and parsed `analysis.json` for each captured configuration.
<!-- full-results:end -->

### Nsight-guided launch tuning and fresh same-allocation CPU comparison

Job **30166891** follows the Nsight evidence: the clear/cloudy SW solve kernels
are ~40% of GPU kernel time, while the device gather/scatter is only ~1.5 ms.
It tests SW vector lengths 32/64/256 against the existing large-tile 128-lane
setting, plus K256 (8192 execution columns). The unmodified full-pipeline
reference is the immutable GPU binary from job 30164046. GPU outputs are
checked against the frozen gas+LW+SW parent; CPU outputs are checked only for
repeatability, per the current acceptance scope.

The job remeasures CPU NPROMA=32 on the same node/allocation with 32 physical
cores, verified from affinity and `lscpu`. It also validates selected DP inputs,
ordinary (non-bit-identity) SP inputs with preserved logical-block seeds, and
captures a separate steady-iteration Nsight profile of the selected variant.

Completed **30166891**, 00:12:21, exit 0. **Native vector policy + K256** wins:
**0.3313219 s GPU versus 0.7730250 s CPU**, a same-allocation wall-time ratio of
**2.333× in favour of GPU**. Using the earlier 0.699925 s CPU measurement as
a separate contextual reference still gives ~2.11×. The fresh CPU measurement
is the controlled comparison for this tuning run.

K256 improves the same-job default K128 median **0.3451009 -> 0.3313219 s**,
about **4.0%**. Forcing SW vector lengths 32/64/256 does not improve the native
large-tile policy and is not enabled by default. The optional
`ECRAD_SW_VECTOR_LENGTH` build macro is retained only as an explicit tuning
control for reproduction/hardware experiments. The recommendation for this
A100/SP workload is `ECRAD_FULL_BATCH_SIZE=256` (8192 execution columns,
model NPROMA=32). DP full/partial checks at the selected K also pass.

Selected Nsight capture has **579 launches**, ~**0.28877 s** total GPU kernel
time and ~**0.11769 s** in the two SW solve kernels. The earlier K128 capture
had 965 launches. These captured device durations are separate from the
unprofiled wall-time comparison, and no CUDA graphs or multi-queue overlap
were used. Direct cloud-sample/RNG-final-state instrumentation and a complete
explicit-block leaf-call-tree rewrite remain architectural/validation follow-ups.

<!-- full-tuning:start -->
### Full-pipeline launch tuning and fresh quarter-node CPU baseline

| Version | K | Seconds | Process range | CPU / version |
|---|---:|---:|---|---:|
| cpu | 0 | 0.7730250 | 0.7716608–0.7872291 | 1.000 |
| default | 128 | 0.3451009 | 0.3449300–0.3456080 | 2.240 |
| default | 256 | 0.3313219 | 0.3310840–0.3318400 | 2.333 |
| vl32 | 128 | 0.3452658 | 0.3451199–0.3460710 | 2.239 |
| vl64 | 128 | 0.3455209 | 0.3452020–0.3458109 | 2.237 |
| vl256 | 128 | 0.3458281 | 0.3455491–0.3462090 | 2.235 |
| vl256 | 256 | 0.3323400 | 0.3318191–0.3325601 | 2.326 |

Allocation: `ac6-300.bullx`, verified **32 physical CPU cores**, one A100.
Selected configuration: **default, K256**, 0.3313219 s.
Fresh CPU time **0.7730250 s**; selected GPU is **2.333× faster**.
Maximum saved-output deviation from GPU reference (or CPU repeatability control): **0**.

The initial SW vector sweep used 32/64/256 lanes versus the native 128-lane
large-tile setting. All arithmetic and spectral order are unchanged. Five matched
process groups confirm the table; the CPU has NPROMA=32 and 32 threads, GPU one host thread.

Validation includes selected DP full/partial inputs and ordinary non-bit-identity SP
full/partial inputs against a separately built immutable parent GPU source. This checks
that ordinary block-local seeds are preserved, not replaced by compute-tile indices.

Selected steady-iteration Nsight trace: **579 launches**, **0.2887744 s** GPU kernel total, **0.1176861 s** in the two SW solve kernels.
Profiles are separate from headline runs; CPU API waits are not added to device time.

Artifacts: `/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot/milestone-tuning-30166891`; allocation, binary/source hashes, raw process times, DP/normal-mode
validation, CTest logs and Nsight report/SQLite/CSV/parsed analysis.
<!-- full-tuning:end -->

### Numerical investigation for the historical SP matrix

The initial CPU-vs-original-GPU check failed: maximum LW net flux difference
**2.41025 W/m²**, above 0.001. Do not silently widen tolerances or treat this
as a validated equivalent-work speedup chain.

Diagnostic job **30138346** establishes:

- Native CPU McICA ACC output is identical with 1 and 32 threads (and in the
  earlier ten-iteration run). This is not evidence of a threading race.
- CPU `McICA` and `McICA ACC` have different stochastic cloudy results; changing
  the CPU solver is not a numerical fix. The matrix keeps `McICA ACC` throughout.
- Optimised master-derived GPU clear-sky results agree closely with CPU; the
  original master-acc and release small-NPROMA GPU outputs differ from that CPU
  in clear-sky fluxes too (up to ~4.01 W/m² LW and ~2.44 W/m² direct SW).
- Batched SW outputs match the small-NPROMA GPU parent in the diagnostic run.

The matrix preserves per-variable raw differences and a strict CPU-agreement
status, while checking repeatability and batch-vs-parent agreement separately.
Any timing row that fails the CPU numerical threshold is labelled **FAIL**.
Resolving the cross-version SP discrepancy is a follow-up correctness task,
not an assumed consequence of the batching changes.

Infrastructure: jobs 30128506/30132989 were cancelled after CMake stalled with
SIGCHLD blocked in its inherited signal mask. Child launch now unblocks SIGCHLD
explicitly; job 30135604 successfully built all six configurations. CMake is
pinned to 3.28.3, NVHPC to 24.5.

Validation correction: earlier integration jobs' `istartcol=1,iendcol=65`
configuration did not truncate the blocked driver's domain. Those results prove
full-domain output agreement, not a 65-column partial-block test. Future partial
tests must truncate the input NetCDF and assert the actual processed extent.

## Reproduction and ongoing updates

From `ci-debug/block-batch-sw/`, with the ECMWF batch environment:

The batch wrappers retain the original ECMWF development paths, dependency
installations and input/snapshot locations. These local run artifacts are not
part of the Git branch; the committed ledger records their hashes and locations.

```bash
sbatch batch-pilot/run-lineage.sbatch
sbatch batch-pilot/run-phases.sbatch
sbatch batch-pilot/run-lw-milestone.sbatch
sbatch batch-pilot/run-gas-milestone.sbatch
sbatch batch-pilot/run-full-milestone.sbatch
sbatch batch-pilot/run-full-tuning.sbatch
```

Each run keeps source snapshots before compiling. The existing matrix reused
the already built immutable job 30135604 tree using the explicitly exported
`ECRAD_LINEAGE_REUSE` path; its own allocation and binary hashes are recorded.

Refresh result blocks from completed raw artifacts with the pure-Python helper:

```bash
python3 -B batch-pilot/report_sp_bench.py \
  --lineage batch-pilot/lineage-30140646 \
  --lw batch-pilot/milestone-lw-30140239
```

Additional LW coverage uses the accepted immutable binaries and a 65-column
fixture alternating 33 day/32 night columns, with the last single-column partial
block in daylight. It checks default, derivatives-off and beta-overlap
configurations in SP/DP against legacy GPU controls. First job **30142683**
stopped constructing the fixture: the source input contains only sunlit columns.
Replacement job **30145664** explicitly sets cosine of solar zenith angle to
zero in alternating columns; original indices and the modification are recorded
with the fixture hash. The accepted caller-consistency/coverage result is
job **30150529**, documented above.

Gas results can be refreshed independently with
`python3 -B batch-pilot/report_sp_bench.py --gas batch-pilot/milestone-gas-30153332`.

Full pipeline and tuning reports can be refreshed with
`python3 -B batch-pilot/report_sp_bench.py --full batch-pilot/milestone-full-30164046 --tuning batch-pilot/milestone-tuning-30166891`.

Source checks: changed Fortran quick check reports zero findings. The fortls
launcher still exits 1 without diagnostic text in this nested worktree; actual
NVHPC SP/DP compilation, GPU execution and CPU regression results are retained.
