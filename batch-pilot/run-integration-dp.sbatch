#!/usr/bin/env bash
#SBATCH --job-name=ecrad-batch-dp-check
#SBATCH --time=00:15:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gpus-per-task=1
#SBATCH --mem=96G
#SBATCH --qos=dg
#SBATCH --output=/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot/integration-dp-job-%j.log
set -euo pipefail
shopt -s inherit_errexit
module load cmake ninja prgenv/nvidia nvidia/24.5 netcdf4 python3
ulimit -s unlimited
export OMP_NUM_THREADS=1 ACC_DEVICE_TYPE=nvidia DR_HOOK_ASSERT_MPI_INITIALIZED=0 PYTHONDONTWRITEBYTECODE=1
export ECRAD_INTEGRATION_PRECISION=dp ECRAD_INTEGRATION_VALIDATE_ONLY=1
export LD_LIBRARY_PATH="$NETCDF4_DIR/lib:/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/pr119/deps/fiat-install/lib64:${LD_LIBRARY_PATH:-}"
python3 -B /etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot/run_integration.py
