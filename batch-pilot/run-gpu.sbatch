#!/usr/bin/env bash
#SBATCH --job-name=ecrad-sw-block-batch
#SBATCH --time=00:30:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gpus-per-task=1
#SBATCH --mem=64G
#SBATCH --qos=dg
#SBATCH --output=/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot/job-%j.log
set -euo pipefail
shopt -s inherit_errexit
root=/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot
deps=/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/pr119/deps
module load cmake ninja prgenv/nvidia nvidia/24.5 netcdf4 python3
ulimit -s unlimited
export OMP_NUM_THREADS=1 ACC_DEVICE_TYPE=nvidia DR_HOOK_ASSERT_MPI_INITIALIZED=0
export LD_LIBRARY_PATH="$NETCDF4_DIR/lib:$deps/fiat-install/lib64:${LD_LIBRARY_PATH:-}"
module list
nvidia-smi
nvfortran --version
python3 -B "$root/verify_reference.py"
cmake -S "$root" -B "$root/build-gpu" -G Ninja \
  -Decbuild_ROOT="$deps/ecbuild" -Dfiat_ROOT="$deps/fiat-install" \
  -DCMAKE_Fortran_COMPILER=nvfortran -DCMAKE_BUILD_TYPE=Release \
  -DECRAD_Fortran_FLAGS='-O3 -Mrecursive -gpu=cc80,ptxinfo -Minfo=accel,inline' \
  -DENABLE_FIAT=ON -DENABLE_OMP=OFF -DENABLE_ACC=ON -DENABLE_NVTX=OFF \
  -DENABLE_SINGLE_PRECISION=ON -DENABLE_DOUBLE_PRECISION=ON \
  -DBUILD_SHARED_LIBS=OFF -DENABLE_TESTS=OFF
cmake --build "$root/build-gpu" --target test_sw_batch_dp test_sw_batch_sp -j 6
ctest --test-dir "$root/build-gpu" -V
python3 -B "$root/verify_reference.py"
