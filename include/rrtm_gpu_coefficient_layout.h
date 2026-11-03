/* Accelerator kernels read the shared g-point-leading CPU coefficient tables.
 * These access permutations change addresses only, not arithmetic order.
 * Include only in accelerator taumol units, never table initialisation.
 * ECRAD_RRTM_BAND selects the rank of minor-gas coefficient tables.
 */
#define ABSA(i,g) ABSA(g,i)
#define absa(i,g) absa(g,i)
#define ABSB(i,g) ABSB(g,i)
#define absb(i,g) absb(g,i)
#define SELFREF(i,g) SELFREF(g,i)
#define selfref(i,g) selfref(g,i)
#define FORREF(i,g) FORREF(g,i)
#define forref(i,g) forref(g,i)
#define SELFREFC(i,g) SELFREFC(g,i)
#define selfrefc(i,g) selfrefc(g,i)
#define FORREFC(i,g) FORREFC(g,i)
#define forrefc(i,g) forrefc(g,i)
#if ECRAD_RRTM_BAND == 1
#define KA_MN2(i,g) KA_MN2(g,i)
#define ka_mn2(i,g) ka_mn2(g,i)
#else
#define KA_MN2(i,j,g) KA_MN2(g,i,j)
#define ka_mn2(i,j,g) ka_mn2(g,i,j)
#endif
#define KB_MN2(i,g) KB_MN2(g,i)
#define kb_mn2(i,g) kb_mn2(g,i)
#if ECRAD_RRTM_BAND == 3 || ECRAD_RRTM_BAND == 9
#define KA_MN2O(i,j,g) KA_MN2O(g,i,j)
#define ka_mn2o(i,j,g) ka_mn2o(g,i,j)
#else
#define KA_MN2O(i,g) KA_MN2O(g,i)
#define ka_mn2o(i,g) ka_mn2o(g,i)
#endif
#if ECRAD_RRTM_BAND == 3
#define KB_MN2O(i,j,g) KB_MN2O(g,i,j)
#define kb_mn2o(i,j,g) kb_mn2o(g,i,j)
#else
#define KB_MN2O(i,g) KB_MN2O(g,i)
#define kb_mn2o(i,g) kb_mn2o(g,i)
#endif
#if ECRAD_RRTM_BAND == 7 || ECRAD_RRTM_BAND == 13
#define KA_MCO2(i,j,g) KA_MCO2(g,i,j)
#define ka_mco2(i,j,g) ka_mco2(g,i,j)
#else
#define KA_MCO2(i,g) KA_MCO2(g,i)
#define ka_mco2(i,g) ka_mco2(g,i)
#endif
#define KB_MCO2(i,g) KB_MCO2(g,i)
#define kb_mco2(i,g) kb_mco2(g,i)
#if ECRAD_RRTM_BAND == 5
#define KA_MO3(i,j,g) KA_MO3(g,i,j)
#define ka_mo3(i,j,g) ka_mo3(g,i,j)
#else
#define KA_MO3(i,g) KA_MO3(g,i)
#define ka_mo3(i,g) ka_mo3(g,i)
#endif
#define KB_MO3(i,g) KB_MO3(g,i)
#define kb_mo3(i,g) kb_mo3(g,i)
#define KA_MCO(i,j,g) KA_MCO(g,i,j)
#define ka_mco(i,j,g) ka_mco(g,i,j)
#define KA_MO2(i,g) KA_MO2(g,i)
#define ka_mo2(i,g) ka_mo2(g,i)
#define KB_MO2(i,g) KB_MO2(g,i)
#define kb_mo2(i,g) kb_mo2(g,i)
