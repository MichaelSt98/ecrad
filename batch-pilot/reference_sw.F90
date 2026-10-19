! Test-only access to the unmodified numerical reference core. Renaming the
! module avoids replacing any production symbol in the reference library.
#define private public
#define radiation_mcica_acc_gpoint_sw radiation_mcica_sw_batch_reference
#include "../radiation/radiation_mcica_acc_gpoint_sw.F90"
