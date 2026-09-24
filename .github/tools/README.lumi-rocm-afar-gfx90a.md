# LUMI-G AMD GPU environment

`lumi-rocm-afar-gfx90a-env.sh` configures the current AFAR amdflang installation
for LUMI-G `gfx90a` OpenMP offload. Source it before configuring ecbuild, FIAT,
or ecRad:

```sh
source .github/tools/lumi-rocm-afar-gfx90a-env.sh
```

It intentionally uses the project-specific AFAR and libffi paths currently
validated by ecWAM. Replace them with a shared installation before enabling the
LUMI CI matrix.

The script exports `ECRAD_LUMI_TOOLCHAIN`, which identifies
`cmake/lumi-rocm-afar-gfx90a.cmake`. That toolchain adds
`-fopenmp --offload-arch=gfx90a` and sets `CMAKE_HIP_ARCHITECTURES=gfx90a`.

Configure ecRad with `-DENABLE_GPU=ON -DENABLE_ACC=OFF -DENABLE_OMP=ON` and run
with `OMP_TARGET_OFFLOAD=MANDATORY` so a missing GPU target fails rather than
silently executing on the host.

## Interactive build and test

Run these commands from a LUMI-G GPU allocation. Replace `ECRAD_SOURCE` with
the absolute path to this checkout. The commands build ecbuild and FIAT from
their `develop` branches, matching the HPC workflow.

```sh
salloc --nodes=1 --ntasks-per-node=4 --cpus-per-task=7 --gpus-per-task=1 \
  --partition=dev-g --time=00:30:00 --account=<project>

export ECRAD_SOURCE=/path/to/ecrad
export ECRAD_BUILD_ROOT=${SCRATCH}/ecrad-lumi-gfx90a
mkdir -p "${ECRAD_BUILD_ROOT}"
cd "${ECRAD_BUILD_ROOT}"

source "${ECRAD_SOURCE}/.github/tools/lumi-rocm-afar-gfx90a-env.sh"
export CMAKE_TEST_LAUNCHER="srun;-n;1"
export DR_HOOK_ASSERT_MPI_INITIALIZED=0
export OMP_TARGET_OFFLOAD=MANDATORY

git clone --branch develop https://github.com/ecmwf/ecbuild.git ecbuild
cmake -S ecbuild -B ecbuild-build -DCMAKE_TOOLCHAIN_FILE="${ECRAD_LUMI_TOOLCHAIN}"
cmake --build ecbuild-build
cmake --install ecbuild-build --prefix "${ECRAD_BUILD_ROOT}/ecbuild-install"

git clone --branch develop https://github.com/ecmwf-ifs/fiat.git fiat
cmake -S fiat -B fiat-build \
  -DCMAKE_TOOLCHAIN_FILE="${ECRAD_LUMI_TOOLCHAIN}" \
  -Decbuild_ROOT="${ECRAD_BUILD_ROOT}/ecbuild-install" \
  -DENABLE_TESTS=OFF -DENABLE_MPI=OFF \
  -DENABLE_GPU=ON -DENABLE_ACC=OFF -DENABLE_OMP=ON
cmake --build fiat-build
cmake --install fiat-build --prefix "${ECRAD_BUILD_ROOT}/fiat-install"

cmake -S "${ECRAD_SOURCE}" -B ecrad-build -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="${ECRAD_LUMI_TOOLCHAIN}" \
  -Decbuild_ROOT="${ECRAD_BUILD_ROOT}/ecbuild-install" \
  -Dfiat_ROOT="${ECRAD_BUILD_ROOT}/fiat-install" \
  -DENABLE_BITIDENTITY_TESTING=ON -DENABLE_SINGLE_PRECISION=ON \
  -DENABLE_GPU=ON -DENABLE_ACC=OFF -DENABLE_OMP=ON
cmake --build ecrad-build

ctest --test-dir ecrad-build -R mcica_acc --output-on-failure
```

The generated `ecrad-build/compile_commands.json` must contain
`-fopenmp --offload-arch=gfx90a`. The successful GPU configuration prints
`GPU_OFFLOAD=OMP` in the ecbuild summary.
