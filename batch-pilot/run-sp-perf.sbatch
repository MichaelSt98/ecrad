#!/usr/bin/env bash
#SBATCH --job-name=ecrad-sp-batch-perf
#SBATCH --time=00:45:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gpus-per-task=1
#SBATCH --mem=64G
#SBATCH --qos=dg
#SBATCH --output=/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot/perf-job-%j.log
set -euo pipefail
shopt -s inherit_errexit
root=/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/block-batch-sw/batch-pilot
deps=/etc/ecmwf/nfs/dh2_perm_a/nams/ecrad-dev/ci-debug/pr119/deps
module load cmake ninja prgenv/nvidia nvidia/24.5 netcdf4 python3
ulimit -s unlimited
export OMP_NUM_THREADS=1 OMP_DYNAMIC=FALSE ACC_DEVICE_TYPE=nvidia
export DR_HOOK_ASSERT_MPI_INITIALIZED=0 PYTHONDONTWRITEBYTECODE=1
export LD_LIBRARY_PATH="$NETCDF4_DIR/lib:$deps/fiat-install/lib64:${LD_LIBRARY_PATH:-}"
module list
nvidia-smi
nvfortran --version
python3 -B "$root/verify_reference.py"
python3 -B "$root/run_sp_perf.py" --run "$root/perf-sp-$SLURM_JOB_ID"
