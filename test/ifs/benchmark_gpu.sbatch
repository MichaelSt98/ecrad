#!/usr/bin/env bash
#SBATCH --job-name=ecrad-sp-half-node
#SBATCH --qos=dg
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --hint=nomultithread
#SBATCH --gpus-per-task=2
#SBATCH --mem=200000M
#SBATCH --time=00:20:00
#SBATCH --output=build/perf-sp/job-%j.log
set -euo pipefail
module load cmake prgenv/nvidia nvidia/24.5 netcdf4 python3
ulimit -s unlimited
export PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
root=${SLURM_SUBMIT_DIR:?Submit from the ecRad worktree root}
python=${ECRAD_BENCHMARK_PYTHON:-python3}
"$python" "$root/test/ifs/benchmark_gpu.py" run "$@"
