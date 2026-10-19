# Block-aware SW McICA pilot

Working implementation of stages 1–3 in
`../../../SMALL_NPROMA_BLOCK_BATCH_PORT_PLAN.md`.

## Source and reference

The isolated detached worktree is `ci-debug/block-batch-sw`, at parent
`2be60a28f8ea9a9761c14b31b7119d0f8d727278`. New code is uncommitted.
The standalone CMake project explicitly builds the **frozen five-band candidate**
from `ci-debug/further-1/five-band-20261014-integrated-v1/sources/candidate` into
a new binary directory; it does not reuse or modify previous build directories.
The worktree's original SW solver, cloud generator, adding and two-stream
sources are byte-identical to that candidate. `verify_reference.py` checks all
569 frozen source files and these four dependencies and prints pilot hashes.

`reference_sw.F90` exposes the original scalar core for tests by changing only
module visibility/name through the preprocessor. The numerical source is
included unchanged. It has a distinct module name and does not replace a symbol
in the frozen reference library.

## Implemented API

```fortran
type(sw_batch_workspace_type) :: workspace
call workspace%init(nlev, nproma, batch_capacity, ng, queue)
call solve_sw_batch(workspace, config, active_blocks, first, last, seeds, mu, &
  & fraction, fractional_std, overlap, od, ssa, asymmetry, &
  & cloud_od, cloud_ssa, cloud_asymmetry, albedo_direct, albedo_diffuse, &
  & incoming, cover, surface, broadband)
! Caller waits for queue before reading outputs/changing input or configuration.
call workspace%release()
```

- `INIT` allocates/maps persistent scratch. Matching shape/queue reuses it;
  changing either waits/releases/reallocates.
- `SOLVE_SW_BATCH` queues kernels and returns asynchronously. All input/output
  arrays, PDF values, spectral band mapping and bounds must already be present
  on the device. Host/device bounds must agree. Input production and output
  consumption must be ordered on the same queue or explicitly synchronised.
- Config scalars are extracted on the host; computational kernels receive
  explicit-shape arrays, not the workspace descriptor. Config coefficient
  tables must remain alive/unchanged while either queue uses them.
- Mutable scratch belongs to one workspace and one queue. Multiple independent
  callers need separate workspaces and output storage. Reusing a workspace on
  its queue is ordered, but callers must not concurrently mutate the host owner.
- `RELEASE` waits for that workspace's queue, unmaps buffers and deallocates.
  It is idempotent. Callers must release explicitly before the owner goes out of
  scope; intrinsic copying/assignment of a mapped owner is unsupported.
- Active blocks are `1:active_blocks`; capacity beyond that is untouched.
  Columns outside `first(b):last(b)` are untouched. Empty blocks may use
  `first=1,last=0`; an active-block count of zero is a host-side no-op.
- Spectral profiles are `(ng,nlev,nproma,batch_capacity)`; cloud profiles use
  bands instead of g-points. Column/layer inputs are `(nproma,nlev,batch_capacity)`.
- Surface output is `(ng,nproma,batch_capacity,4)`: clear diffuse, clear direct,
  total diffuse, total direct.
- Broadband output is `(nproma,nlev+1,batch_capacity,6)`: clear up, clear down,
  total up, total down, total direct, clear direct.

The pilot supports clear diagnostics with
`do_sw_delta_scaling_with_gases=.false.`, positive cloud threshold and
decorrelation scaling, at least two vertical levels and at least two PDF table
points in each dimension. Other modes are rejected explicitly. Always returning
direct diagnostics simplifies this initial interface; optional diagnostic and
full-configuration integration remain follow-up work.

## Computational stages

Six launches per nonempty batch:

1. Cloud-input transpose and independent pair-cloud-cover preparation.
2. Ordered cumulative cloud cover and cloudy-layer bounds per `(block,column)`.
3. Cloud sampling over `(block,column,g-point)` using the validated NVIDIA
   generator's inlined algorithm, original seed derivation and draw traversal.
4. Clear-sky transfer over `(block,column,g-point)`.
5. Cloudy transfer and spectral surface mixing over the same dimensions.
6. Ordered spectral sums and broadband mixing over `(block,column,half-level)`.

Vertical sweeps stay inside each problem. G-point accumulation stays sequential.
The clear/cloudy transfer kernels call the existing validated device helpers.
Sample and two-stream calculations are not moved to device-side host wrappers.

The cloud-cover product retains per-column storage as in the reference. A
scalar-local version failed the NVHPC 24.5 CPU reference comparison at `-O3`
(clear-column cover became one); adding diagnostic output masked the failure.
The array-storage version passes without diagnostic output. No compiler-cause
claim is established beyond this observation.

## Build and test

GPU build/device checks:

```bash
sbatch ci-debug/block-batch-sw/batch-pilot/run-gpu.sbatch
```

The batch script uses NVHPC 24.5 and A100 `cc80`, builds both precisions in
separate module directories, and runs verbose CTest. Source fingerprints appear
before and after the run. Do not edit sources during a submitted run.

CPU configuration from the repository root:

```bash
module load cmake ninja prgenv/nvidia nvidia/24.5 netcdf4 python3
cmake -S ci-debug/block-batch-sw/batch-pilot \
  -B ci-debug/block-batch-sw/batch-pilot/build-cpu -G Ninja \
  -Decbuild_ROOT="$PWD/ci-debug/pr119/deps/ecbuild" \
  -Dfiat_ROOT="$PWD/ci-debug/pr119/deps/fiat-install" \
  -DCMAKE_Fortran_COMPILER=nvfortran -DCMAKE_BUILD_TYPE=Release \
  -DECRAD_Fortran_FLAGS='-O3 -Mrecursive' \
  -DENABLE_FIAT=ON -DENABLE_OMP=OFF -DENABLE_ACC=OFF -DENABLE_NVTX=OFF \
  -DENABLE_SINGLE_PRECISION=ON -DENABLE_DOUBLE_PRECISION=ON \
  -DBUILD_SHARED_LIBS=OFF -DENABLE_TESTS=OFF
cmake --build ci-debug/block-batch-sw/batch-pilot/build-cpu \
  --target test_sw_batch_dp test_sw_batch_sp -j 6
ulimit -s unlimited
export LD_LIBRARY_PATH="$NETCDF4_DIR/lib:$PWD/ci-debug/pr119/deps/fiat-install/lib64:${LD_LIBRARY_PATH:-}"
ctest --test-dir ci-debug/block-batch-sw/batch-pilot/build-cpu -V
```

Synthetic comparisons exercise 112 g-points, 14 bands, 137- and short-level
profiles; K=1, partial/full batches, offset/partial and empty blocks, zero active
blocks, night columns, beta/alpha overlap, full cloud and cloud transitions,
changing seeds/thresholds, reuse/resize/release and separate queues. GPU tests
enqueue both independent workspaces with **different seeds** before waiting, so
overlap is permitted and each result is checked against its own reference.
CPU tests establish numerical fallback correctness, not GPU queue isolation.

Cloud cover and inactive/night conventions are checked exactly. Surface and
broadband flux errors use 0.001 W/m² in DP and 0.1 W/m² in SP. These synthetic tests do **not** yet prove
exact RNG final states/draw counts or real-input/full-application equivalence.

## Remaining gates

1. Complete device correctness, including any compiler-sensitive bounds.
2. Capture real solver inputs and directly compare cloud samples/RNG traversal.
3. Establish matched K=1/K>1 solver timings and memory/register/launch profiles.
4. Integrate the batched caller and include preparation/staging/transfers in a
   B32 full-input comparison before making any end-to-end performance claim.
5. Support the other delta-scaling mode and optional diagnostics, then extend
   to LW and other preparation kernels after the performance gates pass.

This is an isolated experimental pilot, not yet selected by a production driver.

## Focused SP performance experiment

Run with `sbatch ci-debug/block-batch-sw/batch-pilot/run-sp-perf.sbatch`.
The runner creates a unique `perf-sp-JOBID/` artifact directory and freezes the
capture/replay sources there before compiling. It verifies the immutable
five-band source and the established input checksum. Compilation uses SP only,
NVHPC 24.5, A100 `cc80`, `-O3 -Mrecursive`, bit-identity driver inputs and a
single fixed CPU affinity for capture/replay. Synthetic checks run first.

`capture_sw.F90` is a test-only link interposer with the public solver's ABI.
It invokes the unchanged original numerical core and writes inputs and expected
outputs after device-to-host updates. No frozen reference source is edited.
Capture runs exactly one full-input radiation iteration at NPROMA 32 and produces
530 self-contained native SP stream records. Fixture loading, model preparation
and capture are never included in replay timing.

The replay reconstructs the same 530 blocks in their original order. Two metrics
are measured in separate process executions:

- **Resident:** sum of synchronized wall times around each batch's solver call.
  Per-batch packing/upload precedes its timer; download/scatter follows it.
  This does not require retaining the entire domain on device.
- **Staging-inclusive:** sum of wall times around host packing, upload,
  synchronized solve, download and scattering to a common full-domain host
  output layout. Both old and new paths use the same host-resident input and
  output layout. This is a host-staging stress test, not the integrated
  radiation pipeline, which normally produces optics on the device.

All K values process exactly the same columns/seeds. Original solver uses K=1;
the pilot sweeps K=1/2/4/8/16/32. Full allocated buffer capacity is transferred
even in the final partial batch, and that cost is included. Setup/mapping is
reported separately. Every iteration is checked against captured cover and
surface/broadband fluxes after timing, with a 0.1 W/m² SP tolerance and finite
output checks. These checks are not direct RNG-state/draw-count instrumentation.

The initial sweep uses one warmup plus two measurements per configuration.
It selects the best K>1 independently for the two timing boundaries. The existing
solver, pilot K=1 and both selected K values then receive five matched process
runs per boundary, each with one warmup plus nine measurements. Execution order
and boundary order rotate/reverse across process groups. Unprofiled measurements
are recorded before separate Nsight Systems profiles of existing, pilot K=1 and
the resident winner. No CUDA graphs or multi-queue overlap are used.

Evidence includes configuration/build logs, source and input hashes, the fixture
hash, compiler commands, CPU affinity, raw iteration times, validation errors,
per-process medians, selection and analysis JSON, and separate profile reports.

### Completed experiment

Job **30101373** completed successfully. Read
[perf-sp-30101373/INTERPRETATION.md](perf-sp-30101373/INTERPRETATION.md) for the
results and integration recommendation. Resident solver replay improved from
0.779595 s to 0.139473 s at K=32; host-staging-inclusive replay improved from
1.571862 s to 0.763569 s at K=16. All 400 confirmation iterations had zero flux
error. Full-radiation timing and direct RNG-state/draw-count validation remain
open. Reproduce the audit with:

```bash
python3 -B ci-debug/block-batch-sw/batch-pilot/analyse_sp_perf.py \
  ci-debug/block-batch-sw/batch-pilot/perf-sp-30101373
```
