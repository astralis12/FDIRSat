#ifndef FDIR_SGO_H
#define FDIR_SGO_H

#include <stdint.h>

/* --- ECSS-E-ST-20C Reference Operational Bounds --- */
#define ECSS_VBAT_CRITICAL_MIN   9.0f   /* Emergency Deep Discharge Boundary [ECSS-20199] */
#define ECSS_VBAT_NOMINAL_MIN    10.8f  /* Lower operating limit (30% DoD standard) */
#define ECSS_IBAT_CRITICAL_MAX   12.0f  /* Maximum continuous overcurrent limit */
#define ECSS_TBAT_CRITICAL_MAX   60.0f  /* Maximum cell qualification thermal envelope [ECSS-20140] */
#define ECSS_TBAT_NOMINAL_MAX    45.0f  /* Maximum nominal operational temperature */

/* --- Diagnostic Mask Allocations --- */
#define FAULT_BIT_UNDERVOLTAGE   (1U << 0)
#define FAULT_BIT_OVERCURRENT    (1U << 1)
#define FAULT_BIT_OVERTEMPERATURE (1U << 2)
#define FAULT_BIT_DEEP_DISCHARGE (1U << 3)
#define FAULT_BIT_SENSOR_DROPOUT (1U << 4)

/* --- Actuation Vector States --- */
#define ACT_NONE                 0x00U
#define ACT_SHED_SECONDARY       0x01U
#define ACT_SHED_PRIMARY         0x02U
#define ACT_SHED_ALL             0x03U
#define ACT_ISOLATE_ARRAYS       0x02U
#define ACT_ENFORCE_LIMITS       0x02U

typedef struct {
    float solar_voltage;
    float battery_current;
    float system_temperature;
    float battery_soc;
} HealthTelemetry;

typedef struct {
    uint8_t fault_flags;
    uint8_t solar_reconfig_action;
    uint8_t battery_limit_action;
    uint8_t load_shed_action;
} FDIRResult;

/* Intercept Prototype */
FDIRResult process_fdir_with_sgo(HealthTelemetry* telemetry);

/*Raw fdir policy*/
FDIRResult execute_onboard_fdir(HealthTelemetry* telemetry);

#endif /* FDIR_SGO_H */
