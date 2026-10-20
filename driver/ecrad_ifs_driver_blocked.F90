! ecrad_ifs_driver_blocked.F90 - Driver for offline ECRAD radiation scheme
!
! (C) Copyright 2014- ECMWF.
!
! This software is licensed under the terms of the Apache Licence Version 2.0
! which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
!
! In applying this licence, ECMWF does not waive the privileges and immunities
! granted to it by virtue of its status as an intergovernmental organisation
! nor does it submit to any jurisdiction.
!
! Author:  Robin Hogan
! Email:   r.j.hogan@ecmwf.int
!
! ECRAD is the radiation scheme used in the ECMWF Integrated
! Forecasting System in cycle 43R3 and later. Several solvers are
! available, including McICA, Tripleclouds and SPARTACUS (the Speedy
! Algorithm for Radiative Transfer through Cloud Sides, a modification
! of the two-stream formulation of shortwave and longwave radiative
! transfer to account for 3D radiative effects). Gas optical
! properties are provided by the RRTM-G gas optics scheme.

! This program takes three arguments:
! 1) Namelist file to configure the radiation calculation, but note
!    that only the radiation_config group is read
! 2) Name of a NetCDF file containing one or more atmospheric profiles
! 3) Name of output NetCDF file
!
! This version uses the infrastructure of the IFS, such as computing
! effective radius and cloud overlap from latitude and other
! variables. To configure ecRad in this version you need to edit
! ifs/yoerad.F90 in the ecRad package, but these options can be
! overridden with the "radiation" namelist. This file requires the
! input data to have compatible settings, e.g. the right number of
! aerosol variables, and surface albedo/emissivity bands; a test file
! satisfying this requirement is test/ifs/ecrad_meridian.nc in the
! ecRad package.
!
! Note that the purpose of this file is simply to demonstrate the use
! of the setup_radiation_scheme and radiation_scheme routines as well
! as the use of a blocked memory layout to improve cache efficiency;
! all the rest is using the offline ecRad driver containers to read
! a NetCDF file to memory and pass it into these routines.

program ecrad_ifs_driver

  ! --------------------------------------------------------
  ! Section 1: Declarations
  ! --------------------------------------------------------
  use parkind1,                 only : jprb, jprd, jpim, jpib ! Working/double precision
  use yomhook,                  only : dr_hook_init, lhook, dr_hook, jphook
#if defined(_OPENACC) && defined(ECRAD_NSYS_CAPTURE_API)
  use iso_c_binding,            only : c_int
#endif
#ifdef HAVE_FIAT
  use mpl_module,               only : mpl_init, mpl_end
#endif

  use radiation_io,             only : nulout,radiation_abort
  use radiation_single_level,   only : single_level_type
  use radiation_thermodynamics, only : thermodynamics_type
  use radiation_gas,            only : gas_type, IMassMixingRatio, &
       &   IH2O, ICO2, IO3, IN2O, INO2, ICO, ICH4, IO2, ICFC11, ICFC12, &
       &   IHCFC22, ICCl4
  use radiation_cloud,          only : cloud_type
  use radiation_aerosol,        only : aerosol_type
  use radiation_flux,           only : flux_type
  use radiation_save,           only : save_net_fluxes
  use radiation_setup,          only : tradiation, setup_radiation_scheme
  use radiation_constants,      only : Pi
  use radiation_sw_batch_context_mod, only : sw_batch_context_type, radiation_block_state_type
  use ecrad_driver_config,      only : driver_config_type
  use ecrad_driver_read_input,  only : read_input
  use easy_netcdf
  use ifs_blocking
#ifdef HAVE_NVTX
  use nvtx
#endif

  implicit none

#include "radiation_scheme.intfb.h"

  ! The NetCDF file containing the input profiles
  type(netcdf_file)         :: file

  ! Configuration for the radiation scheme, IFS style
  type(tradiation)          :: yradiation

  ! Derived types for the inputs to the radiation scheme
  type(single_level_type)   :: single_level
  type(thermodynamics_type) :: thermodynamics
  type(gas_type)            :: gas
  type(cloud_type)          :: cloud
  type(aerosol_type)        :: aerosol

  ! Configuration specific to this driver
  type(driver_config_type)  :: driver_config

  ! Derived type containing outputs from the radiation scheme
  type(flux_type)           :: flux

  ! Additional arrays passed to radiation_scheme
  real(jprb), allocatable, dimension(:) :: ccn_land, ccn_sea, sin_latitude, longitude_rad, land_frac
  real(jprb), allocatable, dimension(:,:) :: pressure_fl, temperature_fl
  real(jprb), allocatable, dimension(:) :: flux_sw_direct_normal, flux_uv, flux_par, flux_par_clear, &
       &  emissivity_out
  real(jprb), allocatable, dimension(:,:) :: flux_diffuse_band, flux_direct_band

  ! Bespoke data types to set-up the blocked memory layout
  type(ifs_config_type)        :: ifs_config
  real(kind=jprb), allocatable :: zrgp(:,:,:) ! monolithic IFS data structure
#ifdef BITIDENTITY_TESTING
  integer, allocatable         :: iseed(:,:) ! Seed for random number generator
#endif

  integer :: ncol, nlev         ! Number of columns and levels
  integer :: nproma             ! block size

  ! Name of file names specified on command line
  character(len=512) :: file_name
  integer            :: istatus ! Result of command_argument_count

#ifndef NO_OPENMP
  ! OpenMP functions
  integer, external :: omp_get_thread_num
  real(kind=jprd), external :: omp_get_wtime
  ! Start/stop time in seconds
  real(kind=jprd) :: tstart, tstop
#endif

  ! For demonstration of get_sw_weights later on
  ! Ultraviolet weightings
  !integer    :: nweight_uv
  !integer    :: iband_uv(100)
  !real(jprb) :: weight_uv(100)
  ! Photosynthetically active radiation weightings
  !integer    :: nweight_par
  !integer    :: iband_par(100)
  !real(jprb) :: weight_par(100)

  ! Loop index for repeats (for benchmarking)
  integer :: jrepeat

  ! Loop index
  integer :: jrl, ibeg, iend, il, ib
  type(sw_batch_context_type) :: sw_batch
  type(radiation_block_state_type), allocatable, target :: block_states(:)
  type(radiation_block_state_type), pointer :: block_state
  integer(kind=jpim) :: batch_size,tile_start,tile_span,phase,slot,active
  integer(kind=jpim) :: phase_index,phase_count,phase_order(3),profile_bucket
  integer(kind=jpim) :: full_batch_size,full_width,full_active,jfield,jblock,jcolumn,first_block
  real(kind=jprb), allocatable :: full_fields(:,:)
  integer(kind=jpim), allocatable :: full_seed(:)
  logical :: resident_fields
#if defined(_OPENACC) && defined(ECRAD_NSYS_CAPTURE_API)
  logical :: capture_iteration
  integer(kind=c_int) :: capture_status
  INTERFACE
    FUNCTION CUDA_PROFILER_START() BIND(C,NAME='cudaProfilerStart') RESULT(KSTATUS)
      IMPORT :: C_INT
      INTEGER(KIND=C_INT) :: KSTATUS
    END FUNCTION CUDA_PROFILER_START
    FUNCTION CUDA_PROFILER_STOP() BIND(C,NAME='cudaProfilerStop') RESULT(KSTATUS)
      IMPORT :: C_INT
      INTEGER(KIND=C_INT) :: KSTATUS
    END FUNCTION CUDA_PROFILER_STOP
  END INTERFACE
#endif
  integer(kind=jpib) :: batch_tick0,batch_tick1,batch_rate
  character(len=32) :: batch_argument

  ! If enabled, COPY_ASYNC stages copy in, compute and copy out of ZRGP
  ! in different streams, overlapping the copy-in of the next block with the
  ! compute of the current block, and the copy-out of the processed block
  ! with the compute of the next block. However, this does not seem to
  ! improve overall performance and it is therefore disabled by default.
! #define COPY_ASYNC
#ifdef COPY_ASYNC
  integer :: next_il
#endif

  ! Are any variables out of bounds?
  logical :: is_out_of_bounds

!  integer    :: iband(20), nweights
!  real(jprb) :: weight(20)

  ! Initialise MPI if not done yet
#ifdef HAVE_FIAT
  call mpl_init
#endif

  call dr_hook_init()

  ! --------------------------------------------------------
  ! Section 2: Configure
  ! --------------------------------------------------------

  ! Check program called with correct number of arguments
  if (command_argument_count() < 3) then
    stop 'Usage: ecrad config.nam input_file.nc output_file.nc'
  end if

  ! Use namelist to configure the radiation calculation
  call get_command_argument(1, file_name, status=istatus)
  if (istatus /= 0) then
    stop 'Failed to read name of namelist file as string of length < 512'
  end if

  ! Read "radiation_driver" namelist into radiation driver config type
  call driver_config%read(file_name)
  nproma = driver_config%nblocksize

  if (driver_config%iverbose >= 2) then
    write(nulout,'(a)') '-------------------------- OFFLINE ECRAD RADIATION SCHEME --------------------------'
    write(nulout,'(a)') 'Copyright (C) 2014- ECMWF'
    write(nulout,'(a)') 'Contact: Robin Hogan (r.j.hogan@ecmwf.int)'
#ifdef PARKIND1_SINGLE
    write(nulout,'(a)') 'Floating-point precision: single'
#else
    write(nulout,'(a)') 'Floating-point precision: double'
#endif
  end if

  ! Albedo/emissivity intervals may be specified like this
  !call config%define_sw_albedo_intervals(6, &
  !     &  [0.25e-6_jprb, 0.44e-6_jprb, 0.69e-6_jprb, &
  !     &     1.19_jprb, 2.38e-6_jprb], [1,2,3,4,5,6], &
  !     &   do_nearest=.false.)
  !call config%define_lw_emiss_intervals(3, &
  !     &  [8.0e-6_jprb, 13.0e-6_jprb], [1,2,1], &
  !     &   do_nearest=.false.)

  ! If monochromatic aerosol properties are required, then the
  ! wavelengths can be specified (in metres) as follows - these can be
  ! whatever you like for the general aerosol optics, but must match
  ! the monochromatic values in the aerosol input file for the older
  ! aerosol optics
  !call config%set_aerosol_wavelength_mono( &
  !     &  [3.4e-07_jprb, 3.55e-07_jprb, 3.8e-07_jprb, 4.0e-07_jprb, 4.4e-07_jprb, &
  !     &   4.69e-07_jprb, 5.0e-07_jprb, 5.32e-07_jprb, 5.5e-07_jprb, 6.45e-07_jprb, &
  !     &   6.7e-07_jprb, 8.0e-07_jprb, 8.58e-07_jprb, 8.65e-07_jprb, 1.02e-06_jprb, &
  !     &   1.064e-06_jprb, 1.24e-06_jprb, 1.64e-06_jprb, 2.13e-06_jprb, 1.0e-05_jprb])

  call yradiation%rad_config%read(file_name=file_name)

  ! Setup aerosols
  if (yradiation%rad_config%use_aerosols) then
    yradiation%yrerad%naermacc = 1 ! MACC-derived aerosol climatology on a NMCLAT x NMCLON grid
  else
    yradiation%yrerad%naermacc = 0
  endif

  ! Setup the radiation scheme: load the coefficients for gas and
  ! cloud optics, currently from RRTMG
  call setup_radiation_scheme(yradiation, .true., file_name=file_name)
  ! The offline namelist can override the model default during setup. Keep the
  ! caller's output request consistent with the arrays allocated by ecRad.
  yradiation%yrerad%lapproxlwupdate=yradiation%rad_config%do_lw_derivatives
  ! Or call without specifying the namelist filename, in which case
  ! the default settings are from yoerad.F90
  !call setup_radiation_scheme(yradiation, .true.)

  ! Demonstration of how to get weights for UV and PAR fluxes
  !if (config%do_sw) then
  !  call config%get_sw_weights(0.2e-6_jprb, 0.4415e-6_jprb,&
  !       &  nweight_uv, iband_uv, weight_uv,&
  !       &  'ultraviolet')
  !  call config%get_sw_weights(0.4e-6_jprb, 0.7e-6_jprb,&
  !       &  nweight_par, iband_par, weight_par,&
  !       &  'photosynthetically active radiation, PAR')
  !end if

  ! --------------------------------------------------------
  ! Section 3: Read input data file
  ! --------------------------------------------------------

  ! Get NetCDF input file name
  call get_command_argument(2, file_name, status=istatus)
  if (istatus /= 0) then
    stop 'Failed to read name of input NetCDF file as string of length < 512'
  end if

  ! Open the file and configure the way it is read
  call file%open(trim(file_name), iverbose=driver_config%iverbose)

  ! Get NetCDF output file name
  call get_command_argument(3, file_name, status=istatus)
  if (istatus /= 0) then
    stop 'Failed to read name of output NetCDF file as string of length < 512'
  end if

  ! 2D arrays are assumed to be stored in the file with height varying
  ! more rapidly than column index. Specifying "true" here transposes
  ! all 2D arrays so that the column index varies fastest within the
  ! program.
  call file%transpose_matrices(.true.)

  ! Read input variables from NetCDF file, noting that cloud overlap
  ! and effective radius are ignored
  call read_input(file, yradiation%rad_config, driver_config, ncol, nlev, &
       &          single_level, thermodynamics, &
       &          gas, cloud, aerosol)

  ! Latitude is used for cloud overlap and ice effective radius
  if (file%exists('lat')) then
    call file%get('lat', sin_latitude)
    sin_latitude = sin(sin_latitude * Pi/180.0_jprb)
  else
    allocate(sin_latitude(ncol))
    sin_latitude = 0.0_jprb
  end if

  if (file%exists('lon')) then
    call file%get('lon', longitude_rad)
    longitude_rad = longitude_rad * Pi/180.0_jprb
  else
    allocate(longitude_rad(ncol))
    longitude_rad = 0.0_jprb
  end if

  ! Close input file
  call file%close()

  ! Convert gas units to mass-mixing ratio
  call gas%set_units(IMassMixingRatio, lacc=.false.)

  ! Compute seed from skin temperature residual
  !  single_level%iseed = int(1.0e9*(single_level%skin_temperature &
  !       &                            -int(single_level%skin_temperature)))

  ! Set first and last columns to process
  if (driver_config%iendcol < 1 .or. driver_config%iendcol > ncol) then
    driver_config%iendcol = ncol
  end if

  if (driver_config%istartcol > driver_config%iendcol) then
    write(nulout,'(a,i0,a,i0,a,i0,a)') '*** Error: requested column range (', &
         &  driver_config%istartcol, &
         &  ' to ', driver_config%iendcol, ') is out of the range in the data (1 to ', &
         &  ncol, ')'
    stop 1
  end if

  ! --------------------------------------------------------
  ! Section 4: Call radiation scheme
  ! --------------------------------------------------------

  ! Compute saturation with respect to liquid (needed for aerosol
  ! hydration) call
  call thermodynamics%calc_saturation_wrt_liquid(driver_config%istartcol,driver_config%iendcol)

  ! Check inputs are within physical bounds, printing message if not
  is_out_of_bounds =     gas%out_of_physical_bounds(driver_config%istartcol, driver_config%iendcol, &
       &                                            driver_config%do_correct_unphysical_inputs) &
       & .or.   single_level%out_of_physical_bounds(driver_config%istartcol, driver_config%iendcol, &
       &                                            driver_config%do_correct_unphysical_inputs) &
       & .or. thermodynamics%out_of_physical_bounds(driver_config%istartcol, driver_config%iendcol, &
       &                                            driver_config%do_correct_unphysical_inputs) &
       & .or.          cloud%out_of_physical_bounds(driver_config%istartcol, driver_config%iendcol, &
       &                                            driver_config%do_correct_unphysical_inputs) &
       & .or.        aerosol%out_of_physical_bounds(driver_config%istartcol, driver_config%iendcol, &
       &                                            driver_config%do_correct_unphysical_inputs)

  ! Allocate memory for the flux profiles, which may include arrays
  ! of dimension n_bands_sw/n_bands_lw, so must be called after
  ! setup_radiation
  call flux%allocate(yradiation%rad_config, 1, ncol, nlev)

  ! set relevant fluxes to zero
  flux%lw_up(:,:) = 0._jprb
  flux%lw_dn(:,:) = 0._jprb
  flux%sw_up(:,:) = 0._jprb
  flux%sw_dn(:,:) = 0._jprb
  flux%sw_dn_direct(:,:) = 0._jprb
  flux%lw_up_clear(:,:) = 0._jprb
  flux%lw_dn_clear(:,:) = 0._jprb
  flux%sw_up_clear(:,:) = 0._jprb
  flux%sw_dn_clear(:,:) = 0._jprb
  flux%sw_dn_direct_clear(:,:) = 0._jprb

  flux%lw_dn_surf_canopy(:,:) = 0._jprb
  flux%sw_dn_diffuse_surf_canopy(:,:) = 0._jprb
  flux%sw_dn_direct_surf_canopy(:,:) = 0._jprb
  if (allocated(flux%lw_derivatives)) flux%lw_derivatives(:,:) = 0._jprb

  ! Allocate memory for additional arrays
  allocate(ccn_land(ncol))
  allocate(ccn_sea(ncol))
  allocate(land_frac(ncol))
  allocate(pressure_fl(ncol,nlev))
  allocate(temperature_fl(ncol,nlev))
  allocate(flux_sw_direct_normal(ncol))
  allocate(flux_uv(ncol))
  allocate(flux_par(ncol))
  allocate(flux_par_clear(ncol))
  allocate(emissivity_out(ncol))
  allocate(flux_diffuse_band(ncol,yradiation%yrerad%nsw))
  allocate(flux_direct_band(ncol,yradiation%yrerad%nsw))

  ccn_land = yradiation%yrerad%rccnlnd
  ccn_sea = yradiation%yrerad%rccnsea
  pressure_fl = 0.5_jprb * (thermodynamics%pressure_hl(:,1:nlev)+thermodynamics%pressure_hl(:,2:nlev+1))
  temperature_fl = 0.5_jprb * (thermodynamics%temperature_hl(:,1:nlev)+thermodynamics%temperature_hl(:,2:nlev+1))

  ! --------------------------------------------------------
  ! Section 4a: Reshuffle into blocked memory layout
  ! --------------------------------------------------------

  call ifs_setup_indices(driver_config, ifs_config, yradiation, nlev)
  call ifs_copy_inputs_to_blocked(driver_config, ifs_config, yradiation,&
        & ncol, nlev, single_level, thermodynamics, gas, cloud, aerosol,&
        & sin_latitude, longitude_rad, land_frac, pressure_fl, temperature_fl,&
        & zrgp &
#ifdef BITIDENTITY_TESTING
        &, iseed=iseed &
#endif
        & )

  ! --------------------------------------------------------
  ! Section 4b: Call radiation_scheme with blocked memory data
  ! --------------------------------------------------------

  if (driver_config%iverbose >= 2) then
    write(nulout,'(a)')  'Performing radiative transfer calculations'
  end if

  ! Option of repeating calculation multiple time for more accurate
  ! profiling
  batch_size=0
  full_batch_size=0
  call get_environment_variable('ECRAD_SW_BATCH_SIZE',batch_argument,status=istatus)
  if (istatus == 0 .and. len_trim(batch_argument) > 0) read(batch_argument,*) batch_size
  call get_environment_variable('ECRAD_FULL_BATCH_SIZE',batch_argument,status=istatus)
  if (istatus == 0 .and. len_trim(batch_argument) > 0) read(batch_argument,*) full_batch_size
  if (full_batch_size > 0) batch_size=0
#ifndef _OPENACC
  if (batch_size /= 0 .or. full_batch_size /= 0) call radiation_abort()
#endif
  if (batch_size < 0 .or. full_batch_size < 0) call radiation_abort()
#ifdef COPY_ASYNC
  if (batch_size > 0 .or. full_batch_size > 0) call radiation_abort()
#endif
  resident_fields=batch_size > 0 .or. full_batch_size > 0
#if defined(_OPENACC) && defined(ECRAD_NSYS_CAPTURE_API)
  capture_iteration=.false.
  call get_environment_variable('ECRAD_NSYS_CAPTURE',batch_argument,status=istatus)
  if (istatus == 0) capture_iteration=trim(batch_argument) == '1'
#endif
  tile_span=ncol
  if (batch_size > 0) then
    tile_span=batch_size*nproma
    allocate(block_states(batch_size))
    call sw_batch%init(nproma,nlev,batch_size,yradiation%rad_config)
    call get_environment_variable('ECRAD_BATCH_PROFILE',batch_argument,status=istatus)
    if (istatus == 0) sw_batch%profile=trim(batch_argument) == '1'
    call get_environment_variable('ECRAD_LW_BATCH',batch_argument,status=istatus)
    if (istatus == 0 .and. trim(batch_argument) == '1') call sw_batch%init_lw(yradiation%rad_config)
    call get_environment_variable('ECRAD_GAS_BATCH',batch_argument,status=istatus)
    if (istatus == 0 .and. trim(batch_argument) == '1') then
      allocate(sw_batch%gas_tile)
      call sw_batch%gas_tile%init(nproma,nlev,batch_size,yradiation%yrerad%nsw, &
        & yradiation%yrerad%nlwemiss,yradiation%rad_config)
      sw_batch%gas_tile%profile=sw_batch%profile
    ENDIF
  ENDIF
  if (full_batch_size > 0) then
    full_width=full_batch_size*nproma
    write(nulout,'(a,3i8)') 'FULL_BATCH_LAYOUT ',nproma,full_width,ncol
    allocate(full_fields(full_width,ifs_config%ifldstot))
    !$acc enter data copyin(ifs_config)
    !$acc enter data create(full_fields)
    allocate(full_seed(full_width))
    !$acc enter data create(full_seed)
    call get_environment_variable('ECRAD_BATCH_PROFILE',batch_argument,status=istatus)
    if (istatus == 0) sw_batch%profile=trim(batch_argument) == '1'
  ENDIF
  !$acc enter data create(zrgp) if(resident_fields)
#ifdef BITIDENTITY_TESTING
  !$acc enter data copyin(iseed) if(resident_fields)
#endif
  do jrepeat = 1,driver_config%nrepeat
    !$acc wait
#if defined(_OPENACC) && defined(ECRAD_NSYS_CAPTURE_API)
    if (capture_iteration .and. jrepeat == 2) then
      capture_status=cuda_profiler_start()
      if (capture_status /= 0) call radiation_abort()
    ENDIF
#endif
    call system_clock(batch_tick0,batch_rate)
    sw_batch%times(:)=0.0_jprd
    if (allocated(sw_batch%gas_tile)) sw_batch%gas_tile%times(:)=0.0_jprd
    if (sw_batch%profile) call sw_batch%profile_mark(8,.true.)
    if (resident_fields) then
      !$acc update device(zrgp(:,ifs_config%iinbeg:ifs_config%iinend,:), &
      !$acc& zrgp(:,ifs_config%ioutend+1:ifs_config%ifldstot,:)) async(1)
#ifdef BITIDENTITY_TESTING
      !$acc update device(iseed) async(1)
#endif
    ENDIF
#ifdef HAVE_NVTX
     call nvtxStartRange("ecrad_it")
#endif
    if (sw_batch%profile) call sw_batch%profile_mark(8,.false.)

#ifdef COPY_ASYNC
    !$acc enter data create(zrgp(:,:,1)) &
#ifdef BITIDENTITY_TESTING
    !$acc&     copyin(iseed(:,1)) &
#endif
    !$acc&     async(2)

    next_il = min(nproma,ncol)
    !$acc update device(zrgp(:,ifs_config%iinbeg:ifs_config%iinend,1), &
    !$acc&              zrgp(:,ifs_config%ioutend+1:ifs_config%ifldstot,1)) async(2)
#endif

#ifndef NO_OPENMP
    if (jrepeat == driver_config%nwarmup + 1) then
      tstart = omp_get_wtime()
    end if
#endif

!    if (driver_config%do_parallel) then
      ! Run radiation scheme over blocks of columns in parallel

      if (full_batch_size > 0) then
        do tile_start=1,ncol,full_width
          full_active=min(full_width,ncol-tile_start+1)
          active=(full_active+nproma-1)/nproma
          first_block=(tile_start-1)/nproma+1
          if (sw_batch%profile) call sw_batch%profile_mark(1,.true.)
          ! Preserve the model's (NPROMA,field,block) layout. A bounded device
          ! transpose exposes all active blocks to every physics stage.
          !$acc parallel loop collapse(3) gang vector default(present) async(1)
          do jblock=1,active
            do jfield=1,ifs_config%ifldstot
              do jcolumn=1,nproma
                if ((jblock-1)*nproma+jcolumn <= full_active) then
                  if (jfield <= ifs_config%iinend .or. jfield > ifs_config%ioutend) then
                    full_fields((jblock-1)*nproma+jcolumn,jfield)=zrgp(jcolumn,jfield,first_block+jblock-1)
                  else
                    full_fields((jblock-1)*nproma+jcolumn,jfield)=0.0_jprb
                  ENDIF
                ENDIF
              ENDDO
            ENDDO
          ENDDO
          !$acc parallel loop collapse(2) gang vector default(present) async(1)
          do jblock=1,active
            do jcolumn=1,nproma
#ifdef BITIDENTITY_TESTING
              if ((jblock-1)*nproma+jcolumn <= full_active) &
                & full_seed((jblock-1)*nproma+jcolumn)=iseed(jcolumn,first_block+jblock-1)
#else
              ! Match init_seed_simple in each original logical block.
              if ((jblock-1)*nproma+jcolumn <= full_active) &
                & full_seed((jblock-1)*nproma+jcolumn)=jcolumn
#endif
            ENDDO
          ENDDO
          if (sw_batch%profile) call sw_batch%profile_mark(1,.false.)
          if (sw_batch%profile) call sw_batch%profile_mark(2,.true.)
          call run_radiation_view(full_width,full_active,full_fields,full_seed)
          if (sw_batch%profile) call sw_batch%profile_mark(2,.false.)
          if (sw_batch%profile) call sw_batch%profile_mark(3,.true.)
          !$acc parallel loop collapse(3) gang vector default(present) async(1)
          do jblock=1,active
            do jfield=ifs_config%ioutbeg,ifs_config%ioutend
              do jcolumn=1,nproma
                if ((jblock-1)*nproma+jcolumn <= full_active) &
                  & zrgp(jcolumn,jfield,first_block+jblock-1)=full_fields((jblock-1)*nproma+jcolumn,jfield)
              ENDDO
            ENDDO
          ENDDO
          if (sw_batch%profile) call sw_batch%profile_mark(3,.false.)
        ENDDO
      else
      do tile_start=1,ncol,tile_span
      phase_order(:)=0
      phase_count=1
      if (batch_size > 0) then
        active=min(batch_size,(ncol-tile_start+nproma)/nproma)
        call sw_batch%begin_tile(active)
        phase_order(1:2)=[1,2]
        phase_count=2
        if (allocated(sw_batch%gas_tile)) then
          phase_order(:)=[3,4,2]
          phase_count=3
        ENDIF
      ENDIF
      do phase_index=1,phase_count
       phase=phase_order(phase_index)
       if (phase == 4) call sw_batch%gas_tile%solve(yradiation%rad_config)
       if (phase == 2) call sw_batch%solve(yradiation%rad_config)
       profile_bucket=6
       if (phase == 2) profile_bucket=7
       if (sw_batch%profile) call sw_batch%profile_mark(profile_bucket,.true.)
      !$OMP PARALLEL DO SCHEDULE(DYNAMIC,1) IF(BATCH_SIZE == 0)&
      !$OMP&PRIVATE(JRL,IBEG,IEND,IL,IB,SLOT,BLOCK_STATE)
      do jrl=tile_start,min(ncol,tile_start+tile_span-1),nproma
        ibeg=jrl
        iend=min(ibeg+nproma-1,ncol)
        il=iend-ibeg+1
        ib=(jrl-1)/nproma+1
        slot=1
        nullify(block_state)
        if (batch_size > 0) then
          slot=(jrl-tile_start)/nproma+1
          block_state=>block_states(slot)
        ENDIF

        if (driver_config%iverbose >= 3) then
#ifndef NO_OPENMP
          write(nulout,'(a,i0,a,i0,a,i0)')  'Thread ', omp_get_thread_num(), &
               &  ' processing columns ', ibeg, '-', iend
#else
          write(nulout,'(a,i0,a,i0)')  'Processing columns ', ibeg, '-', iend
#endif
        end if

#ifdef COPY_ASYNC
        if (iend < ncol) then
          !$acc enter data create(zrgp(:,:,ib+1)) &
#ifdef BITIDENTITY_TESTING
          !$acc&     copyin(iseed(:,ib+1)) &
#endif
          !$acc&     async(2) wait(2)

          next_il = min(iend+nproma,ncol) - (ibeg+nproma) + 1
          !$acc update device(zrgp(:,ifs_config%iinbeg:ifs_config%iinend,ib+1), &
          !$acc&              zrgp(:,ifs_config%ioutend+1:ifs_config%ifldstot,ib+1)) async(2)
        endif

#else  /* COPY_ASYNC */

        !$acc enter data create(zrgp(:,:,ib)) &
#ifdef BITIDENTITY_TESTING
        !$acc&     copyin(iseed(:,ib)) &
#endif
        !$acc& if(batch_size == 0)

        if (batch_size == 0) then
        !$acc update device(zrgp(1:il,ifs_config%iinbeg:ifs_config%iinend,ib), &
        !$acc&            zrgp(1:il,ifs_config%ioutend+1:ifs_config%ifldstot,ib)) &
        !$acc& async(1)
        ENDIF
#endif /* COPY_ASYNC */

        ! Call the ECRAD radiation scheme
        call radiation_scheme &
             & (yradiation, &
             &  1, il, nproma, &                       ! startcol, endcol, ncol
             &  nlev, size(aerosol%mixing_ratio,3), &    ! nlev, naerosols
             &  single_level%solar_irradiance, &                               ! solar_irrad
             ! array inputs
             &  zrgp(:,ifs_config%iamu0,ib), zrgp(:,ifs_config%its,ib), &    ! mu0, skintemp
             &  zrgp(:,ifs_config%iald,ib) , zrgp(:,ifs_config%ialp,ib), &    ! albedo_dif, albedo_dir
             &  zrgp(:,ifs_config%iemiss,ib), &                   ! spectral emissivity
             &  zrgp(:,ifs_config%iccnl,ib), zrgp(:,ifs_config%iccno,ib) ,&  ! CCN concentration, land and sea
             &  zrgp(:,ifs_config%igelam,ib),zrgp(:,ifs_config%igemu,ib), &  ! longitude, sine of latitude
             &  zrgp(:,ifs_config%islm,ib), &                     ! land sea mask
             &  zrgp(:,ifs_config%ipr,ib),   zrgp(:,ifs_config%iti,ib),  &   ! full level pressure and temperature
             &  zrgp(:,ifs_config%iaprs,ib), zrgp(:,ifs_config%ihti,ib), &   ! half-level pressure and temperature
             &  zrgp(:,ifs_config%iwv,ib),   zrgp(:,ifs_config%iico2,ib), &
             &  zrgp(:,ifs_config%iich4,ib), zrgp(:,ifs_config%iin2o,ib), &
             &  zrgp(:,ifs_config%ino2,ib),  zrgp(:,ifs_config%ic11,ib), &
             &  zrgp(:,ifs_config%ic12,ib),  zrgp(:,ifs_config%ic22,ib), &
             &  zrgp(:,ifs_config%icl4,ib),  zrgp(:,ifs_config%ioz,ib), &
             &  zrgp(:,ifs_config%iclc,ib),  zrgp(:,ifs_config%ilwa,ib), &
             &  zrgp(:,ifs_config%iiwa,ib),  zrgp(:,ifs_config%irwa,ib), &
             &  zrgp(:,ifs_config%iswa,ib), &
             &  zrgp(:,ifs_config%iaer,ib),  zrgp(:,ifs_config%iaero,ib), &
             ! flux outputs
             &  zrgp(:,ifs_config%ifrso,ib), zrgp(:,ifs_config%ifrth,ib), &
             &  zrgp(:,ifs_config%iswfc,ib), zrgp(:,ifs_config%ilwfc,ib),&
             &  zrgp(:,ifs_config%ifrsod,ib),zrgp(:,ifs_config%ifrted,ib), &
             &  zrgp(:,ifs_config%ifrsodc,ib),zrgp(:,ifs_config%ifrtedc,ib),&
             &  zrgp(:,ifs_config%ifdir,ib), zrgp(:,ifs_config%icdir,ib), &
             &  zrgp(:,ifs_config%isudu,ib), &
             &  zrgp(:,ifs_config%iuvdf,ib), zrgp(:,ifs_config%iparf,ib), &
             &  zrgp(:,ifs_config%iparcf,ib),zrgp(:,ifs_config%itincf,ib), &
             &  zrgp(:,ifs_config%iemit,ib) ,zrgp(:,ifs_config%ilwderivative,ib), &
             &  zrgp(:,ifs_config%iswdiffuseband,ib), zrgp(:,ifs_config%iswdirectband,ib)&
#ifdef BITIDENTITY_TESTING
            ! To validate results against standalone ecrad, we overwrite effective
            ! radii, cloud overlap and seed with input values
             &  ,pre_liq=zrgp(:,ifs_config%ire_liq,ib), &
             &  pre_ice=zrgp(:,ifs_config%ire_ice,ib), &
             &  pcloud_overlap=zrgp(:,ifs_config%ioverlap,ib), &
             &  iseed=iseed(:,ib) &
#endif
             & ,kphase=phase,ydblockstate=block_state,ydbatch=sw_batch,kslot=slot)
#ifdef COPY_ASYNC
          !$acc update host(zrgp(:,ifs_config%ioutbeg:ifs_config%ioutend,ib)) async(3) wait(1)
          !$acc exit data delete(zrgp(:,:,ib)) async(3)
#else
          if (batch_size == 0) then
            !$acc update host(zrgp(1:il,ifs_config%ioutbeg:ifs_config%ioutend,ib)) async(1)
            !$acc exit data delete(zrgp(:,:,ib)) &
#ifdef BITIDENTITY_TESTING
            !$acc& delete(iseed(:,ib)) &
#endif
            !$acc& async(1)
          ENDIF
#endif
      end do
      !$OMP END PARALLEL DO
      if (sw_batch%profile) call sw_batch%profile_mark(profile_bucket,.false.)
      ENDDO ! phases
      ENDDO ! tiles
      ENDIF ! full tile or staged continuation
      if (resident_fields) then
        if (sw_batch%profile) call sw_batch%profile_mark(8,.true.)
        !$acc update host(zrgp(:,ifs_config%ioutbeg:ifs_config%ioutend,:)) async(1)
        if (sw_batch%profile) call sw_batch%profile_mark(8,.false.)
      ENDIF
      !$acc wait
      call system_clock(batch_tick1)
      write(nulout,'(a,2i6,es24.16)') 'BATCH_ITER ',batch_size,jrepeat, &
        & real(batch_tick1-batch_tick0,jprd)/real(batch_rate,jprd)
      if (full_batch_size > 0 .and. sw_batch%profile) &
        & write(nulout,'(a,i6,4es24.16)') 'FULL_PHASE ',jrepeat,sw_batch%times(1:3),sw_batch%times(8)
      if (allocated(sw_batch%gas_tile)) sw_batch%times(12:14)=sw_batch%gas_tile%times(:)
      if (sw_batch%profile) write(nulout,'(a,i6,14es24.16)') 'BATCH_PHASE ',jrepeat,sw_batch%times
#if defined(_OPENACC) && defined(ECRAD_NSYS_CAPTURE_API)
      if (capture_iteration .and. jrepeat == 2) then
        capture_status=cuda_profiler_stop()
        if (capture_status /= 0) call radiation_abort()
      ENDIF
#endif

!    else
      ! Run radiation scheme serially
!      if (driver_config%iverbose >= 3) then
!        write(nulout,'(a,i0,a)')  'Processing ', ncol, ' columns'
!      end if

      ! Call the ECRAD radiation scheme
!      call radiation_scheme(ncol, nlev, driver_config%istartcol, driver_config%iendcol, &
!           &  config, single_level, thermodynamics, gas, cloud, aerosol, flux)

!    end if
#ifdef HAVE_NVTX
     call nvtxEndRange
#endif

  end do

!$acc wait
  call sw_batch%release()
  !$acc exit data delete(zrgp) if(resident_fields)
#ifdef BITIDENTITY_TESTING
  !$acc exit data delete(iseed) if(resident_fields)
#endif
  if (full_batch_size > 0) then
    !$acc exit data delete(ifs_config)
    !$acc exit data delete(full_fields)
    deallocate(full_fields)
    !$acc exit data delete(full_seed)
    deallocate(full_seed)
  ENDIF

#ifndef NO_OPENMP
  if (driver_config%nrepeat > driver_config%nwarmup) then
    tstop = omp_get_wtime()
    write(nulout, '(a,g12.5,a)') 'Total time elapsed in radiative transfer: ', tstop-tstart, ' seconds'
    write(nulout, '(a,g12.5,a)') 'Average time elapsed in radiative transfer: ', &
      &                         (tstop-tstart)/(driver_config%nrepeat-driver_config%nwarmup), ' seconds'
    write(nulout, '(a,i0)') 'Columns/s : ', int((ncol*(driver_config%nrepeat-driver_config%nwarmup))/(tstop-tstart))
  end if
#endif

  ! --------------------------------------------------------
  ! Section 4c: Copy fluxes from blocked memory data
  ! --------------------------------------------------------

  call ifs_copy_fluxes_from_blocked(driver_config, ifs_config, yradiation, ncol, nlev,&
          & zrgp, flux, flux_sw_direct_normal, flux_uv, flux_par, flux_par_clear, &
          & emissivity_out, flux_diffuse_band, flux_direct_band)

  ! "up" fluxes are actually net fluxes at this point - we modify the
  ! upwelling flux so that net=dn-up, while the TOA and surface
  ! downwelling fluxes are correct.
  flux%sw_up = -flux%sw_up
  flux%sw_up(:,1) = flux%sw_up(:,1)+flux%sw_dn(:,1)
  flux%sw_up(:,nlev+1) = flux%sw_up(:,nlev+1)+flux%sw_dn(:,nlev+1)

  flux%lw_up = -flux%lw_up
  flux%lw_up(:,1) = flux%lw_up(:,1)+flux%lw_dn(:,1)
  flux%lw_up(:,nlev+1) = flux%lw_up(:,nlev+1)+flux%lw_dn(:,nlev+1)

  flux%sw_up_clear = -flux%sw_up_clear
  flux%sw_up_clear(:,1) = flux%sw_up_clear(:,1)+flux%sw_dn_clear(:,1)
  flux%sw_up_clear(:,nlev+1) = flux%sw_up_clear(:,nlev+1)+flux%sw_dn_clear(:,nlev+1)

  flux%lw_up_clear = -flux%lw_up_clear
  flux%lw_up_clear(:,1) = flux%lw_up_clear(:,1)+flux%lw_dn_clear(:,1)
  flux%lw_up_clear(:,nlev+1) = flux%lw_up_clear(:,nlev+1)+flux%lw_dn_clear(:,nlev+1)

  ! --------------------------------------------------------
  ! Section 5: Check and save output
  ! --------------------------------------------------------

  ! This is unreliable because only the net fluxes are valid:
  !is_out_of_bounds = flux%out_of_physical_bounds(driver_config%istartcol, driver_config%iendcol)

  ! Store the fluxes in the output file
  yradiation%rad_config%do_surface_sw_spectral_flux = .false.
  yradiation%rad_config%do_canopy_fluxes_sw = .false.
  yradiation%rad_config%do_canopy_fluxes_lw = .false.

  call save_net_fluxes(file_name, yradiation%rad_config, thermodynamics, flux, &
       &   iverbose=driver_config%iverbose, is_hdf5_file=driver_config%do_write_hdf5, &
       &   experiment_name=driver_config%experiment_name, &
       &   is_double_precision=driver_config%do_write_double_precision)

  if (driver_config%iverbose >= 2) then
    write(nulout,'(a)') '------------------------------------------------------------------------------------'
  end if

  ! Finalise MPI if not done yet
#ifdef HAVE_FIAT
  call mpl_end(ldmeminfo=.false.)
#endif

CONTAINS
SUBROUTINE RUN_RADIATION_VIEW(KCOL,KEND,PFIELDS,KSEED)
  INTEGER(KIND=JPIM), INTENT(IN) :: KCOL,KEND
  REAL(KIND=JPRB), INTENT(INOUT) :: PFIELDS(KCOL,IFS_CONFIG%IFLDSTOT)
  INTEGER(KIND=JPIM), INTENT(IN) :: KSEED(KCOL)
  REAL(KIND=JPHOOK) :: ZHOOK_HANDLE
  IF (LHOOK) CALL DR_HOOK('ECRAD_IFS_DRIVER%RUN_RADIATION_VIEW',0,ZHOOK_HANDLE)
  CALL RADIATION_SCHEME(YRADIATION,1,KEND,KCOL,NLEV,SIZE(AEROSOL%MIXING_RATIO,3), &
    & SINGLE_LEVEL%SOLAR_IRRADIANCE, &
    & PFIELDS(:,IFS_CONFIG%IAMU0),PFIELDS(:,IFS_CONFIG%ITS), &
    & PFIELDS(:,IFS_CONFIG%IALD),PFIELDS(:,IFS_CONFIG%IALP),PFIELDS(:,IFS_CONFIG%IEMISS), &
    & PFIELDS(:,IFS_CONFIG%ICCNL),PFIELDS(:,IFS_CONFIG%ICCNO), &
    & PFIELDS(:,IFS_CONFIG%IGELAM),PFIELDS(:,IFS_CONFIG%IGEMU),PFIELDS(:,IFS_CONFIG%ISLM), &
    & PFIELDS(:,IFS_CONFIG%IPR),PFIELDS(:,IFS_CONFIG%ITI), &
    & PFIELDS(:,IFS_CONFIG%IAPRS),PFIELDS(:,IFS_CONFIG%IHTI), &
    & PFIELDS(:,IFS_CONFIG%IWV),PFIELDS(:,IFS_CONFIG%IICO2), &
    & PFIELDS(:,IFS_CONFIG%IICH4),PFIELDS(:,IFS_CONFIG%IIN2O), &
    & PFIELDS(:,IFS_CONFIG%INO2),PFIELDS(:,IFS_CONFIG%IC11), &
    & PFIELDS(:,IFS_CONFIG%IC12),PFIELDS(:,IFS_CONFIG%IC22), &
    & PFIELDS(:,IFS_CONFIG%ICL4),PFIELDS(:,IFS_CONFIG%IOZ), &
    & PFIELDS(:,IFS_CONFIG%ICLC),PFIELDS(:,IFS_CONFIG%ILWA), &
    & PFIELDS(:,IFS_CONFIG%IIWA),PFIELDS(:,IFS_CONFIG%IRWA),PFIELDS(:,IFS_CONFIG%ISWA), &
    & PFIELDS(:,IFS_CONFIG%IAER),PFIELDS(:,IFS_CONFIG%IAERO), &
    & PFIELDS(:,IFS_CONFIG%IFRSO),PFIELDS(:,IFS_CONFIG%IFRTH), &
    & PFIELDS(:,IFS_CONFIG%ISWFC),PFIELDS(:,IFS_CONFIG%ILWFC), &
    & PFIELDS(:,IFS_CONFIG%IFRSOD),PFIELDS(:,IFS_CONFIG%IFRTED), &
    & PFIELDS(:,IFS_CONFIG%IFRSODC),PFIELDS(:,IFS_CONFIG%IFRTEDC), &
    & PFIELDS(:,IFS_CONFIG%IFDIR),PFIELDS(:,IFS_CONFIG%ICDIR),PFIELDS(:,IFS_CONFIG%ISUDU), &
    & PFIELDS(:,IFS_CONFIG%IUVDF),PFIELDS(:,IFS_CONFIG%IPARF), &
    & PFIELDS(:,IFS_CONFIG%IPARCF),PFIELDS(:,IFS_CONFIG%ITINCF), &
    & PFIELDS(:,IFS_CONFIG%IEMIT),PFIELDS(:,IFS_CONFIG%ILWDERIVATIVE), &
    & PFIELDS(:,IFS_CONFIG%ISWDIFFUSEBAND),PFIELDS(:,IFS_CONFIG%ISWDIRECTBAND) &
#ifdef BITIDENTITY_TESTING
    & ,PRE_LIQ=PFIELDS(:,IFS_CONFIG%IRE_LIQ),PRE_ICE=PFIELDS(:,IFS_CONFIG%IRE_ICE), &
    & PCLOUD_OVERLAP=PFIELDS(:,IFS_CONFIG%IOVERLAP) &
#endif
    & ,ISEED=KSEED)
  IF (LHOOK) CALL DR_HOOK('ECRAD_IFS_DRIVER%RUN_RADIATION_VIEW',1,ZHOOK_HANDLE)
END SUBROUTINE RUN_RADIATION_VIEW
END PROGRAM ECRAD_IFS_DRIVER
