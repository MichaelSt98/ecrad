! (C) Copyright 2026- ECMWF.
! Licensed under the Apache Licence Version 2.0; see COPYING.
program acc_flux_ranges
  use parkind1, only : jprb
  use openacc, only : acc_on_device, acc_device_host
  use radiation_flux, only : flux_type, sum_surf_band_omp, canopy_nearest_gpu, sum_surf_lw_band_gpu
  implicit none
  logical :: on_host

  !$acc serial copyout(on_host)
  on_host = acc_on_device(acc_device_host)
  !$acc end serial
  if (on_host) error stop 'Flux aggregation test requires device execution'

  call test_case(1, 8, 5, 8)
  call test_case(5, 12, 9, 10)

contains

  subroutine test_case(col_lo, col_hi, first, last)
    integer, intent(in) :: col_lo, col_hi, first, last
    real(jprb), parameter :: sentinel = -9999.0_jprb
    integer :: jcol
    integer :: map_g(4), map_canopy(2)
    real(jprb), target :: direct(4,col_lo:col_hi), diffuse(4,col_lo:col_hi)
    real(jprb), target :: band_direct(2,col_lo:col_hi), band_total(2,col_lo:col_hi)
    real(jprb), target :: canopy(2,col_lo:col_hi)
    real(jprb) :: lw_band(2,col_lo:col_hi)
    type(flux_type) :: flux
    real(jprb) :: expected_direct(2,col_lo:col_hi), expected_total(2,col_lo:col_hi)
    real(jprb) :: expected_canopy(2,col_lo:col_hi)

    map_g = [2,1,2,1]
    map_canopy = [2,1]
    do jcol = col_lo,col_hi
      direct(:,jcol) = real(jcol,jprb)*[1.0_jprb,2.0_jprb,3.0_jprb,4.0_jprb]
      diffuse(:,jcol) = 2.0_jprb*direct(:,jcol)
    end do
    band_direct = sentinel
    band_total = sentinel
    canopy = sentinel
    lw_band = sentinel
    expected_direct = sentinel
    expected_total = sentinel
    expected_canopy = sentinel
    do jcol = first,last
      expected_direct(:,jcol) = real(jcol,jprb)*[6.0_jprb,4.0_jprb]
      expected_total(:,jcol) = 3.0_jprb*expected_direct(:,jcol)
      expected_canopy(:,jcol) = real(jcol,jprb)*[4.0_jprb,6.0_jprb]
    end do

    flux%sw_dn_direct_surf_g => direct
    flux%sw_dn_diffuse_surf_g => diffuse
    flux%sw_dn_direct_surf_band => band_direct
    flux%sw_dn_surf_band => band_total
    flux%sw_dn_direct_surf_canopy => canopy
    !$acc data copyin(direct,diffuse,map_g,map_canopy) copy(band_direct,band_total,canopy,lw_band)
    call sum_surf_band_omp(first,last,4,2,col_lo,col_hi,map_g, &
         &  flux%sw_dn_direct_surf_g, flux%sw_dn_diffuse_surf_g, &
         &  flux%sw_dn_direct_surf_band, flux%sw_dn_surf_band)
    call canopy_nearest_gpu(first,last,4,2,2,col_lo,col_hi,map_canopy,map_g, &
         &  flux%sw_dn_direct_surf_g, flux%sw_dn_direct_surf_canopy)
    call sum_surf_lw_band_gpu(first,last,4,2,col_lo,map_g,direct,lw_band(:,first:last))
    !$acc wait(1)
    !$acc end data

    ! Entire-array comparisons also verify that inactive columns stay untouched.
    if (any(band_direct /= expected_direct)) error stop 'Incorrect direct band fluxes'
    if (any(band_total /= expected_total)) error stop 'Incorrect total band fluxes'
    if (any(canopy /= expected_canopy)) error stop 'Incorrect canopy fluxes'
    if (any(lw_band /= expected_direct)) error stop 'Incorrect longwave band fluxes'
  end subroutine test_case

end program acc_flux_ranges
