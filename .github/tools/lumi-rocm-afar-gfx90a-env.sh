#!/usr/bin/env bash
# Source this file on LUMI before configuring ecRad or its dependencies.

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "Source this file instead of executing it: source $0" >&2
  exit 1
fi

_ecrad_lumi_env_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export ECRAD_LUMI_TOOLCHAIN="${_ecrad_lumi_env_dir}/../../cmake/lumi-rocm-afar-gfx90a.cmake"
unset _ecrad_lumi_env_dir

module reset
module load LUMI/25.09
module load partition/G
module load cray-mpich/9.0.1
module load craype-network-ofi
module load buildtools/25.09
module load cray-python/3.11.7
module load cray-hdf5
module load cray-netcdf

export ECRAD_ROCM_AFAR_ROOT=/pfs/lustrep4/scratch/project_465000527/nawabahm/therock-afar-23.2.1-gfx90a-7.13.0-7357b5084b
export PATH="${ECRAD_ROCM_AFAR_ROOT}/lib/llvm/bin:${ECRAD_ROCM_AFAR_ROOT}/bin:${PATH}"
export LD_LIBRARY_PATH="${ECRAD_ROCM_AFAR_ROOT}/lib:${ECRAD_ROCM_AFAR_ROOT}/lib/llvm/lib:/pfs/lustrep4/scratch/project_465000527/nawabahm/local/libffi-3.2.1/lib:${LD_LIBRARY_PATH:-}"

export CC=amdclang
export CXX=amdclang++
export FC=amdflang

ulimit -s unlimited
ulimit -l unlimited
