# (C) Copyright 2014- ECMWF.
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
#
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.

include( ecrad_source_file_compile_flags )

set( driver_read_input ecrad_driver_read_input.F90 )
set( driver_standalone ecrad_driver.F90 )
set( driver_ifs ecrad_ifs_driver.F90 )
set( driver_blocked ecrad_ifs_driver_blocked.F90 )
ecrad_select_gpu_sources( driver_read_input )
ecrad_select_gpu_sources( driver_standalone )
ecrad_select_gpu_sources( driver_ifs )
ecrad_select_gpu_sources( driver_blocked )

ecbuild_add_library(
    TARGET driver_lib.${PREC}
    TYPE OBJECT
    SOURCES
        ${driver_read_input}
        ecrad_driver_config.F90
        print_matrix_mod.F90
    PUBLIC_INCLUDES
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}/module_driver_lib>"
    PUBLIC_DEFINITIONS
        $<$<NOT:${HAVE_OMP}>:NO_OPENMP>
    PUBLIC_LIBS
        ecrad.${PREC}
        ecrad_base.${PREC}
        $<${HAVE_OMP}:OpenMP::OpenMP_Fortran>
)
set_target_properties( driver_lib.${PREC}
    PROPERTIES
        Fortran_MODULE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/module_driver_lib"
)

ecbuild_add_executable(
    TARGET ecrad_${PREC}
    SOURCES
        ${driver_standalone}
    LIBS
        driver_lib.${PREC}
    LINKER_LANGUAGE Fortran
)

ecbuild_add_executable(
    TARGET ecrad_ifs_${PREC}
    SOURCES
        ${driver_ifs}
    LIBS
        ifs.${PREC}
        driver_lib.${PREC}
    LINKER_LANGUAGE Fortran
)

ecbuild_add_executable(
    TARGET ecrad_ifs_blocked_${PREC}
    SOURCES
        ${driver_blocked}
    LIBS
        ifs.${PREC}
        driver_lib.${PREC}
    LINKER_LANGUAGE Fortran
)


if( HAVE_FIELD_API AND NOT HAVE_GPU )
    ecbuild_add_executable(
        TARGET ecrad_ifs_field_api_${PREC}
        SOURCES
            ecrad_ifs_driver_field_api.F90
        LIBS
            ifs_field_api.${PREC}
            driver_lib.${PREC}
        LINKER_LANGUAGE Fortran
    )
endif()

# Create a symlink for each driver without the precision suffix.
# The default is to link to the double precision version if it is built,
# and single precision otherwise.
if ( "${PREC}" STREQUAL "dp" OR NOT HAVE_DOUBLE_PRECISION )
    foreach( tgt ecrad ecrad_ifs ecrad_ifs_blocked ecrad_ifs_field_api )
        if( TARGET ${tgt}_${PREC} )
            add_custom_command(
                TARGET ${tgt}_${PREC}
                POST_BUILD
                COMMAND
                    "${CMAKE_COMMAND}" -E create_symlink ${tgt}_${PREC} ${tgt}
                BYPRODUCTS "${CMAKE_BINARY_DIR}/bin/${tgt}"
                WORKING_DIRECTORY
                    "${CMAKE_BINARY_DIR}/bin"
                COMMENT "Creating symbolic link from ${tgt} to ${tgt}_${PREC}"
            )
            install(
                FILES ${CMAKE_BINARY_DIR}/bin/${tgt}
                DESTINATION bin
            )
        endif()
    endforeach()
endif()
