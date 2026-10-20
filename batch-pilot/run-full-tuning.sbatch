#!/usr/bin/env bash
#SBATCH --job-name=ecrad-sp-full-tuning
#SBATCH --time=00:45:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --hint=nomultithread
#SBATCH --gpus-per-task=1
#SBATCH --mem=120000M
#SBATCH --qos=dg
#SBATCH --output=/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot/milestone-job-%j.log
set -euo pipefail
module load cmake/3.28.3 ninja prgenv/nvidia nvidia/24.5 netcdf4 python3
ulimit -s unlimited
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export ACC_DEVICE_TYPE=nvidia DR_HOOK_ASSERT_MPI_INITIALIZED=0 PYTHONDONTWRITEBYTECODE=1
export LD_LIBRARY_PATH="$NETCDF4_DIR/lib:/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/pr119/deps/fiat-install/lib64:${LD_LIBRARY_PATH:-}"
srun --cpu-bind=cores python3 -B /etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot/run_full_tuning.py
