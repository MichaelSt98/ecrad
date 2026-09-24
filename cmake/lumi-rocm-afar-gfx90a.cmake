# LUMI-G AFAR OpenMP-offload toolchain.
# The compiler and AFAR runtime paths are set by
# .github/tools/lumi-rocm-afar-gfx90a-env.sh.

set( OpenMP_Fortran_FLAGS "-fopenmp --offload-arch=gfx90a" CACHE STRING "" )

if( NOT DEFINED CMAKE_HIP_ARCHITECTURES )
    set( CMAKE_HIP_ARCHITECTURES gfx90a )
endif()

set( ECBUILD_Fortran_FLAGS_BIT "-g -O2" )
