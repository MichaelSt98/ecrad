cmake_minimum_required(VERSION 3.25)
project(ecrad_sw_batch_pilot LANGUAGES Fortran)
set(CMAKE_Fortran_FLAGS_RELEASE "")
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
enable_testing()

# Build the immutable five-band reference, rather than alter an old build.
set(ECRAD_BATCH_REFERENCE_SOURCE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../further-1/five-band-20261014-integrated-v1/sources/candidate"
    CACHE PATH "Frozen five-band reference source")
add_subdirectory("${ECRAD_BATCH_REFERENCE_SOURCE}" reference EXCLUDE_FROM_ALL)

foreach(precision IN ITEMS dp sp)
    if(NOT TARGET ecrad.${precision})
        continue()
    endif()
    set(module_dir "${CMAKE_CURRENT_BINARY_DIR}/module-${precision}")
    file(MAKE_DIRECTORY "${module_dir}")
    add_executable(test_sw_batch_${precision}
        ../radiation/radiation_mcica_sw_batch_mod.F90
        reference_sw.F90
        test_sw_batch.F90)
    set_target_properties(test_sw_batch_${precision} PROPERTIES
        Fortran_MODULE_DIRECTORY "${module_dir}")
    target_include_directories(test_sw_batch_${precision} PRIVATE
        "${module_dir}" "${ECRAD_BATCH_REFERENCE_SOURCE}/radiation")
    target_link_libraries(test_sw_batch_${precision} PRIVATE
        ecrad.${precision} ecrad_base.${precision})
    if(ECRAD_ENABLE_ACC OR ENABLE_ACC)
        target_compile_options(test_sw_batch_${precision} PRIVATE
            -O3 -Mrecursive -gpu=cc80,ptxinfo -Minfo=accel)
    else()
        target_compile_options(test_sw_batch_${precision} PRIVATE -O3 -Mrecursive)
    endif()
    add_test(NAME sw_batch_${precision} COMMAND test_sw_batch_${precision})
endforeach()

option(SW_BATCH_PERF "Build SP real-input capture and replay experiment" OFF)
if(SW_BATCH_PERF)
    set(capture_modules "${CMAKE_CURRENT_BINARY_DIR}/module-capture-sp")
    set(replay_modules "${CMAKE_CURRENT_BINARY_DIR}/module-replay-sp")
    file(MAKE_DIRECTORY "${capture_modules}" "${replay_modules}")
    add_executable(capture_sw_sp
        "${ECRAD_BATCH_REFERENCE_SOURCE}/driver/ecrad_ifs_driver_blocked.F90"
        "${ECRAD_BATCH_REFERENCE_SOURCE}/driver/ifs_blocking.F90"
        reference_sw.F90 capture_sw.F90)
    target_link_libraries(capture_sw_sp PRIVATE ifs.sp driver_lib.sp ecrad_base.sp)
    set_target_properties(capture_sw_sp PROPERTIES Fortran_MODULE_DIRECTORY "${capture_modules}")
    target_include_directories(capture_sw_sp BEFORE PRIVATE "${capture_modules}")
    target_include_directories(capture_sw_sp PRIVATE "${ECRAD_BATCH_REFERENCE_SOURCE}/radiation")
    add_executable(bench_sw_replay_sp
        ../radiation/radiation_mcica_sw_batch_mod.F90
        reference_sw.F90 sw_replay_io_mod.F90 sw_replay_bench_mod.F90 bench_sw_replay.F90)
    target_link_libraries(bench_sw_replay_sp PRIVATE ecrad.sp ecrad_base.sp)
    set_target_properties(bench_sw_replay_sp PROPERTIES Fortran_MODULE_DIRECTORY "${replay_modules}")
    target_include_directories(bench_sw_replay_sp BEFORE PRIVATE "${replay_modules}")
    target_include_directories(bench_sw_replay_sp PRIVATE "${ECRAD_BATCH_REFERENCE_SOURCE}/radiation")
    foreach(tgt IN ITEMS capture_sw_sp bench_sw_replay_sp)
        target_compile_options(${tgt} PRIVATE -O3 -Mrecursive -gpu=cc80,ptxinfo -Minfo=accel,inline)
    endforeach()
endif()
