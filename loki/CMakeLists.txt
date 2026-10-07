# (C) Copyright 2014- ECMWF.
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
#
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.

include(ecrad_fypp_macros)

set( CMAKE_Fortran_MODULE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}" )

set( ecrad_loki_SOURCE_DIRS "" )
set( target_list
    ecrad_ifsaux.${PREC}
    ecrad_utilities.${PREC}
    ifsrrtm.${PREC}
    ecrad.${PREC}
    ifs.${PREC}
)

foreach( tgt IN LISTS target_list )
    get_target_property( ${tgt}_SOURCE_DIR ${tgt} SOURCE_DIR )
    list( APPEND ecrad_loki_SOURCE_DIRS "${${tgt}_SOURCE_DIR}" )
endforeach()

# Generate radintg with fypp (if enabled)
# ecrad_ifs_process_fypp( "${PROJECT_SOURCE_DIR}/ifs/radintg_zrgp_mod" )
# Generate radiation_scheme_layer with fypp (if enabled)
# ecrad_ifs_process_fypp( "${PROJECT_SOURCE_DIR}/ifs/radiation_scheme_layer_mod" )

if( HAVE_LOKI )
    ecbuild_add_library(
        TARGET
            ecrad_loki.${PREC}
        SOURCES
            ${ifsaux_SOURCES}
            ${utilities_SOURCES}
            ${ifsrrtm_SOURCES}
            ${radiation_SOURCES}
            ${ifs_SOURCES}
            "${PROJECT_SOURCE_DIR}/ifs/radintg_zrgp_mod.F90"
            "${PROJECT_SOURCE_DIR}/ifs/radiation_scheme_layer_mod.F90"
            "${PROJECT_SOURCE_DIR}/ifs/radiation_field_type_mod.F90"
        DEFINITIONS
            $<${HAVE_FIELD_API}:HAVE_FIELD_API>
            $<${HAVE_LOKI}:HAVE_LOKI>
            $<${HAVE_ACC}:HAVE_ACC>
        PRIVATE_INCLUDES
            ${ecrad_loki_SOURCE_DIRS}
        PUBLIC_INCLUDES
            "$<BUILD_INTERFACE:${PROJECT_SOURCE_DIR}/include>"
            "$<BUILD_INTERFACE:${CMAKE_Fortran_MODULE_DIRECTORY}>"
            "$<INSTALL_INTERFACE:${ecrad_${PREC}_INSTALL_INCLUDEDIR}>"
        PRIVATE_LIBS
            ecrad_base.${PREC}
            $<${HAVE_FIELD_API}:field_api_${PREC}>
            NetCDF::NetCDF_Fortran
        PUBLIC_LIBS
            $<${HAVE_OMP}:OpenMP::OpenMP_Fortran>
            $<${HAVE_ACC}:ecrad_openacc>
    )
endif()

if( HAVE_ACC AND CMAKE_Fortran_COMPILER_ID MATCHES "PGI|NVHPC" AND CMAKE_BUILD_TYPE STREQUAL "Release" )
    # Lower optimisation level to O2 for Tripleclouds OpenACC kernels, when compilng
    # with NVHPC, to work around compiler bug in O3 builds.
    set_source_files_properties(
        "${PROJECT_SOURCE_DIR}/radiation/radiation_tripleclouds_lw.F90"
        "${PROJECT_SOURCE_DIR}/radiation/radiation_tripleclouds_sw.F90"
        PROPERTIES
            COMPILE_FLAGS "-O2"
    )
endif()

if( HAVE_BITIDENTITY_TESTING )
    set( LOKI_DEFINITIONS "DEFINITIONS;BITIDENTITY_TESTING" )
else()
    set( LOKI_DEFINITIONS )
endif()

if( "${PREC}" STREQUAL "sp" )
    list( APPEND LOKI_DEFINITIONS "DEFINITIONS;PARKIND1_SINGLE" )
endif()

if( HAVE_ACC )
    list( APPEND LOKI_DEFINITIONS "DEFINITIONS;HAVE_ACC=ON" )
endif()

# The standalone target owns all transformed sources in its aggregate library.
set(ECRAD_LOKI_IFS_ARPIFS_LIB "ecrad_loki.${PREC}")
set(ECRAD_LOKI_IFS_ECRAD_LIB "ecrad_loki.${PREC}")
set(ECRAD_LOKI_IFS_UTILITIES_LIB "ecrad_loki.${PREC}")
configure_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/ecrad_loki.config
    ${CMAKE_CURRENT_BINARY_DIR}/ecrad_loki.config
    @ONLY
)

if( HAVE_LOKI )
    loki_transform_target(
        TARGET ecrad_loki.${PREC}
        MODE ${LOKI_MODE}
        CONFIG ${CMAKE_CURRENT_BINARY_DIR}/ecrad_loki.config
        ${LOKI_DEFINITIONS}
        PLAN ${CMAKE_CURRENT_BINARY_DIR}/ecrad_loki_plan.${PREC}.cmake
        SOURCES ${ecrad_loki_SOURCE_DIRS}
        NO_PLAN_SOURCEDIR
        DEFINITIONS
            HAVE_FIELD_API=ON
    )
endif()
