! (C) Copyright 2026- ECMWF.
! Licensed under the Apache Licence Version 2.0; see COPYING.
program acc_rng_compatibility
  use openacc, only : acc_on_device, acc_device_host
  use parkind1, only : jpib, jprb
  use radiation_random_numbers, only : uniform_distribution_acc, uniform_distribution_acc_gpoint
  implicit none
  integer(jpib), parameter :: modulus = 2147483647_jpib
  integer(jpib) :: i, samples, seed, old_state, new_state, errors
  integer(jpib) :: fallback(6)
  real(jprb) :: old_random, new_random
  character(len=32) :: argument
  logical :: on_device

  !$acc serial copyout(on_device)
  on_device = .not. acc_on_device(acc_device_host)
  !$acc end serial
  if (.not. on_device) stop 'RNG compatibility test requires GPU execution'

  samples = 1000000_jpib
  if (command_argument_count() > 0) then
    call get_command_argument(1, argument)
    if (trim(argument) /= '--exhaustive') stop 'Unknown RNG test argument'
    samples = modulus
  end if
  errors = 0_jpib
  ! In exhaustive mode this visits every state 0..M, including endpoints.
  ! The smaller CTest mode spans the same range at evenly spaced states.
  !$acc parallel loop gang vector copy(errors) reduction(+:errors) &
  !$acc& private(seed, old_state, new_state, old_random, new_random)
  do i = 0_jpib, samples
    seed = i * modulus / samples
    old_state = seed
    new_state = seed
    old_random = uniform_distribution_acc(old_state)
    call uniform_distribution_acc_gpoint(new_state, new_random)
    if (old_state /= new_state .or. old_random /= new_random) errors = errors + 1_jpib
  end do
  fallback = [-1_jpib, -modulus, -modulus-1_jpib, modulus+1_jpib, &
              & 2_jpib*modulus, huge(modulus)/48272_jpib]
  !$acc parallel loop gang vector copy(errors) copyin(fallback) reduction(+:errors) &
  !$acc& private(old_state, new_state, old_random, new_random)
  do i = 1_jpib, int(size(fallback),jpib)
    old_state = fallback(i)
    new_state = fallback(i)
    old_random = uniform_distribution_acc(old_state)
    call uniform_distribution_acc_gpoint(new_state, new_random)
    if (old_state /= new_state .or. old_random /= new_random) errors = errors + 1_jpib
  end do
  write(*,*) 'RNG_COMPATIBILITY_STATES ', samples+1_jpib+int(size(fallback),jpib)
  write(*,*) 'RNG_COMPATIBILITY_ERRORS ', errors
  if (errors /= 0_jpib) stop 1
end program acc_rng_compatibility
