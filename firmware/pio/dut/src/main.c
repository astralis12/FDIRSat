/*
 * DUT firmware (STM32F411CE): onboard FDIR code behind a framed UART protocol.
 *
 * Clock: SYSCLK_MHZ (default 16 = HSI, no PLL). Other values use the PLL from HSI,
 * flash wait states per RM0383 Table 6 (2.7-3.6 V: 0 WS <= 30 MHz, 1 <= 64, 2 <= 90,
 * 3 <= 100), voltage scale 1 above 84 MHz, APB1 / 2 above 50 MHz; ART accelerator
 * (prefetch + I/D cache) on unless ART=0. USART1 PA9 TX / PA10 RX, UART_BAUD
 * (default 460800) 8N1. LED PC13 toggles per frame. The firmware drives no other
 * GPIO: it only computes and reports.
 *
 * SEU tolerance (item 5): tree tables in RAM (FDIR_TREE_IN_RAM) scrubbed by the
 * hardware CRC unit every frame and restored from flash on mismatch; IWDG (~1 s);
 * HardFault -> reset; boot frame 'b' with the reset cause.
 * Status byte per spacecraft: bits 0-3 EKF guards, bit 5 state CRC mismatch (EKF /
 * history / charge latch rebuilt, ISS-057), bit 6 tree structural fault, bit 7 tree
 * tables repaired by the scrub in this frame.
 *
 * Frame (both directions, little-endian):
 *   0xA5 0x5A | cmd (1) | len (1) | payload (len) | CRC-16/CCITT-FALSE over cmd..payload (2)
 * Commands:
 *   'P' ping            -> 'p' "fdir-dut <version>"
 *   'I' f32 soc0        -> 'i' (empty)            soc_ekf_init
 *   'S' f32 i_prev, i_now, v, dt
 *                       -> 's' f32 soc, std, innov, innov_var | u8 status | u32 cycles
 *   'T' f32 in[9]       -> 't' u8 S, B, L | u32 cycles                 tree only
 *   'F' f32 in[9]       -> 'f' u8 S, B, L, flags | u32 cycles          tree + SGO v3
 *   'G' f32 in[9], f32 t_bat[3]
 *                       -> 'g' u8 S, B, L, flags, latch | u32 cycles   median-of-3 vote + tree + SGO v3
 *   'X' f32 i_prev, i_now, v, dt, c_rate, t_bat, i_sa_pu, u_bus_pu
 *                       -> 'x' f32 soc, std | u8 S, B, L, flags, ekf_status | u32 cycles
 *                          full 1 Hz loop: EKF step, history features, tree + SGO v3
 *   'J' u8 n, f32 soc0[n]   -> 'j'      init n (<= MULTI) independent EKF instances
 *   'Y' u8 n, n x (8 f32 as 'X')
 *                       -> 'y' n x (f32 soc, std | u8 S, B, L, flags, ekf_status | u32 cycles)
 *                          the 'X' loop for n spacecraft per frame (processor-in-the-loop)
 *   'Z' u8 on            -> 'z'      tree-table scrub on/off (fault-injection ablation)
 *   'C'                  -> 'c' u32 cycles of one scrub (timing sweep)
 *   'b' (DUT -> host at boot) "fdir-dut <version>" | u8 reset cause (RCC CSR bits 24-31)
 * Bad CRC or unknown command -> 'e' u8 code.
 */
#include <stdint.h>
#include <string.h>
#include "stm32f4xx.h"
#include "soc_ekf.h"
#include "fdir_tree.h"
#include "fdir_onboard.h"
#include "fdir_features.h"
#include "fdir_sgo_v3.h"
#include "fdir_config.h"
#include "fdir_state_guard.h"

#define FW_VERSION "fdir-dut 0.9 tree9 sgo3.2 guard multi7 " FDIR_CONFIG_NAME
#define MULTI 7
#ifndef UART_BAUD
#define UART_BAUD 460800U
#endif
#ifndef SYSCLK_MHZ
#define SYSCLK_MHZ 16U
#endif
#ifndef ART
#define ART 1
#endif
#define SYSCLK_HZ (SYSCLK_MHZ * 1000000U)

static uint8_t scrub_flag;   /* set when the scrub repaired the tree tables in this frame */
static uint8_t scrub_on = 1U;

static SocEkf ekf;            /* 'S' command only (bare EKF) */
/* Per-spacecraft state behind a CRC-16 guard (ISS-057): EKF, history, charge latch.
 * Not static: fault injection targets it by symbol (verify/fault_injection.py, hil/run_case.py C3). */
FdirGuardedState gstate[MULTI];
static FdirGuardedState xstate;   /* the single-spacecraft 'X' / 'G' path */

/* ---------------------------------------------------------------- hardware */
/* one full 1 Hz loop on EKF instance e: in = 8 floats as 'X', out = 17 bytes */
static void full_step(FdirGuardedState *g, const uint8_t *in, uint8_t *out)
{
    float f[8], x[FDIR_TREE_INPUTS], soc, sd;
    FDIRResult r;
    uint32_t t0, cycles;
    uint8_t status;
    int repaired;
    memcpy(f, in, 32);
    t0 = DWT->CYCCNT;
    repaired = fdir_state_check(g, f[2]);          /* SEU between steps -> rebuilt state */
    status = soc_ekf_step(&g->ekf, f[0], f[1], f[2], f[3], NULL);
    soc = soc_ekf_soc(&g->ekf);
    sd = soc_ekf_std(&g->ekf);
    x[0] = f[2];
    x[1] = f[4];
    x[2] = f[5];
    x[3] = soc;
    x[4] = f[6];
    x[5] = f[7];
    fdir_features_step(&g->feat, x[0], x[3], x[4], &x[6]);
    fdir_sgo_v3_state = g->sgo;
    r = fdir_onboard_step(x);
    g->sgo = fdir_sgo_v3_state;
    fdir_state_seal(g);
    cycles = DWT->CYCCNT - t0;
    if (fdir_tree_last_status != 0) {
        status |= 0x40U;
    }
    status |= scrub_flag;
    if (repaired) {
        status |= FDIR_GUARD_ST_REPAIRED;
    }
    memcpy(out, &soc, 4);
    memcpy(&out[4], &sd, 4);
    out[8] = r.solar_reconfig_action;
    out[9] = r.battery_limit_action;
    out[10] = r.load_shed_action;
    out[11] = r.fault_flags;
    out[12] = status;
    memcpy(&out[13], &cycles, 4);
}

/* Hardware CRC unit (CRC-32/MPEG-2 polynomial, word input) for the tree scrub */
uint32_t fdir_tree_crc32(const void *data, uint32_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    uint32_t k, w;
    CRC->CR = CRC_CR_RESET;
    for (k = 0; k + 4U <= len; k += 4U) {
        memcpy(&w, &p[k], 4);
        CRC->DR = w;
    }
    for (; k < len; k++) {
        CRC->DR = (uint32_t)p[k];
    }
    return CRC->DR;
}

void HardFault_Handler(void)
{
    NVIC_SystemReset();                                  /* reported by the boot frame */
}

static void clock_init(void)
{
#if SYSCLK_MHZ > 16U
    uint32_t ws = (SYSCLK_MHZ <= 30U) ? 0U : (SYSCLK_MHZ <= 64U) ? 1U : (SYSCLK_MHZ <= 90U) ? 2U : 3U;
    uint32_t pllp = (SYSCLK_MHZ >= 50U) ? 2U : 4U;      /* VCO = SYSCLK * pllp stays in 100..432 MHz */
    uint32_t plln = SYSCLK_MHZ * pllp / 2U;             /* PLL input 2 MHz (HSI / 8) */
    RCC->APB1ENR |= RCC_APB1ENR_PWREN;
    (void)RCC->APB1ENR;
    PWR->CR = (PWR->CR & ~PWR_CR_VOS) | ((SYSCLK_MHZ > 84U) ? PWR_CR_VOS : PWR_CR_VOS_1);
    FLASH->ACR = (FLASH->ACR & ~FLASH_ACR_LATENCY) | ws;
    RCC->PLLCFGR = 8U | (plln << RCC_PLLCFGR_PLLN_Pos) | (((pllp / 2U) - 1U) << RCC_PLLCFGR_PLLP_Pos)
                 | (4U << RCC_PLLCFGR_PLLQ_Pos);         /* PLLSRC = HSI (bit 22 = 0) */
    RCC->CR |= RCC_CR_PLLON;
    while (!(RCC->CR & RCC_CR_PLLRDY)) {
    }
    RCC->CFGR = (RCC->CFGR & ~(RCC_CFGR_PPRE1 | RCC_CFGR_PPRE2)) | ((SYSCLK_MHZ > 50U) ? RCC_CFGR_PPRE1_DIV2 : 0U);
    RCC->CFGR = (RCC->CFGR & ~RCC_CFGR_SW) | RCC_CFGR_SW_PLL;
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL) {
    }
#endif
#if ART
    FLASH->ACR |= FLASH_ACR_PRFTEN | FLASH_ACR_ICEN | FLASH_ACR_DCEN;
#else
    FLASH->ACR &= ~(FLASH_ACR_PRFTEN | FLASH_ACR_ICEN | FLASH_ACR_DCEN);
#endif
}

static void iwdg_init(void)
{
    IWDG->KR = 0x5555U;                                  /* unlock PR / RLR */
    IWDG->PR = 3U;                                       /* LSI 32 kHz / 32 = 1 kHz */
    IWDG->RLR = 1000U;                                   /* ~1 s */
    IWDG->KR = 0xAAAAU;
    IWDG->KR = 0xCCCCU;                                  /* start */
}

static void hw_init(void)
{
    clock_init();
    RCC->AHB1ENR |= RCC_AHB1ENR_CRCEN;
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_GPIOCEN;
    RCC->APB2ENR |= RCC_APB2ENR_USART1EN;
    (void)RCC->APB2ENR;

    /* PA9, PA10: alternate function 7 (USART1) */
    GPIOA->MODER &= ~(GPIO_MODER_MODER9 | GPIO_MODER_MODER10);
    GPIOA->MODER |= (2U << GPIO_MODER_MODER9_Pos) | (2U << GPIO_MODER_MODER10_Pos);
    GPIOA->AFR[1] &= ~((0xFU << 4) | (0xFU << 8));
    GPIOA->AFR[1] |= (7U << 4) | (7U << 8);
    GPIOA->PUPDR &= ~GPIO_PUPDR_PUPDR10;
    GPIOA->PUPDR |= (1U << GPIO_PUPDR_PUPD10_Pos);          /* RX pull-up */

    /* PC13 LED, output */
    GPIOC->MODER &= ~GPIO_MODER_MODER13;
    GPIOC->MODER |= (1U << GPIO_MODER_MODER13_Pos);

    /* USART1 on APB2 = SYSCLK, oversampling 16: BRR = round(SYSCLK / baud)
     * 16 MHz: 460800 -> 35 (0.8 % error); 100 MHz -> 217 (0.03 %) */
    USART1->BRR = (SYSCLK_HZ + UART_BAUD / 2U) / UART_BAUD;
    USART1->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;

    /* DWT cycle counter */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

static uint8_t uart_get(void)
{
    while (!(USART1->SR & USART_SR_RXNE)) {
        IWDG->KR = 0xAAAAU;                               /* idle host is not a hang */
        if (USART1->SR & USART_SR_ORE) {
            (void)USART1->DR;                                   /* clear overrun */
        }
    }
    return (uint8_t)USART1->DR;
}

static void uart_put(uint8_t b)
{
    while (!(USART1->SR & USART_SR_TXE)) {
    }
    USART1->DR = b;
}

/* ---------------------------------------------------------------- framing */
static uint16_t crc16(const uint8_t *p, uint32_t n)
{
    uint16_t c = 0xFFFFU;
    while (n--) {
        c ^= (uint16_t)(*p++) << 8;
        for (int k = 0; k < 8; k++) {
            c = (c & 0x8000U) ? (uint16_t)((c << 1) ^ 0x1021U) : (uint16_t)(c << 1);
        }
    }
    return c;
}

static void send_frame(uint8_t cmd, const uint8_t *payload, uint8_t len)
{
    uint8_t buf[2 + 255];
    uint16_t c;
    buf[0] = cmd;
    buf[1] = len;
    memcpy(&buf[2], payload, len);
    c = crc16(buf, (uint32_t)len + 2U);
    uart_put(0xA5);
    uart_put(0x5A);
    for (uint32_t k = 0; k < (uint32_t)len + 2U; k++) {
        uart_put(buf[k]);
    }
    uart_put((uint8_t)(c & 0xFFU));
    uart_put((uint8_t)(c >> 8));
}

static int recv_frame(uint8_t *cmd, uint8_t *payload, uint8_t *len)
{
    uint8_t buf[2 + 255];
    uint16_t c;
    while (uart_get() != 0xA5) {
    }
    if (uart_get() != 0x5A) {
        return 0;
    }
    buf[0] = uart_get();
    buf[1] = uart_get();
    for (uint32_t k = 0; k < buf[1]; k++) {
        buf[2 + k] = uart_get();
    }
    c = (uint16_t)uart_get();
    c |= (uint16_t)((uint16_t)uart_get() << 8);
    if (c != crc16(buf, (uint32_t)buf[1] + 2U)) {
        return -1;
    }
    *cmd = buf[0];
    *len = buf[1];
    memcpy(payload, &buf[2], buf[1]);
    return 1;
}

/* ---------------------------------------------------------------- main */
int main(void)
{
    uint8_t cmd = 0U, len = 0U, in[255], out[32];   /* set by recv_frame; init for GCC 7 -Wmaybe-uninitialized */
    hw_init();
    fdir_tree_init();
    {
        uint8_t boot[48];
        uint32_t n = (uint32_t)strlen(FW_VERSION);
        memcpy(boot, FW_VERSION, n);
        boot[n] = (uint8_t)(RCC->CSR >> 24);             /* reset cause: IWDG, software, POR, pin ... */
        RCC->CSR |= RCC_CSR_RMVF;
        send_frame('b', boot, (uint8_t)(n + 1U));
    }
    iwdg_init();
    soc_ekf_init(&ekf, 0.5f);
    fdir_state_init(&xstate, 0.5f);
    for (uint32_t k = 0; k < MULTI; k++) {
        fdir_state_init(&gstate[k], 0.5f);
    }

    for (;;) {
        int r = recv_frame(&cmd, in, &len);
        GPIOC->ODR ^= GPIO_ODR_OD13;
        IWDG->KR = 0xAAAAU;
        scrub_flag = (uint8_t)((scrub_on && fdir_tree_scrub()) ? 0x80U : 0x00U);   /* check tables once per frame */
        if (r <= 0) {
            uint8_t code = (r < 0) ? 1U : 2U;                  /* 1 = CRC, 2 = sync */
            send_frame('e', &code, 1);
            continue;
        }
        if (cmd == 'P') {
            send_frame('p', (const uint8_t *)FW_VERSION, (uint8_t)strlen(FW_VERSION));
        } else if (cmd == 'I' && len == 4) {
            float soc0;
            memcpy(&soc0, in, 4);
            soc_ekf_init(&ekf, soc0);
            fdir_state_init(&xstate, soc0);
            send_frame('i', out, 0);
        } else if (cmd == 'S' && len == 16) {
            float f[4];
            SocEkfStep st;
            uint32_t t0, cycles;
            uint8_t status;
            memcpy(f, in, 16);
            t0 = DWT->CYCCNT;
            status = soc_ekf_step(&ekf, f[0], f[1], f[2], f[3], &st);
            cycles = DWT->CYCCNT - t0;
            f[0] = soc_ekf_soc(&ekf);
            f[1] = soc_ekf_std(&ekf);
            f[2] = st.innov;
            f[3] = st.innov_var;
            memcpy(out, f, 16);
            out[16] = status;
            memcpy(&out[17], &cycles, 4);
            send_frame('s', out, 21);
        } else if (cmd == 'T' && len == 36) {
            float x[FDIR_TREE_INPUTS];
            uint8_t a[3];
            uint32_t t0, cycles;
            memcpy(x, in, 36);
            t0 = DWT->CYCCNT;
            fdir_tree_eval(x, a);
            cycles = DWT->CYCCNT - t0;
            memcpy(out, a, 3);
            memcpy(&out[3], &cycles, 4);
            send_frame('t', out, 7);
        } else if (cmd == 'F' && len == 36) {
            float x[FDIR_TREE_INPUTS];
            FDIRResult r;
            uint32_t t0, cycles;
            memcpy(x, in, 36);
            t0 = DWT->CYCCNT;
            r = fdir_onboard_step(x);
            cycles = DWT->CYCCNT - t0;
            out[0] = r.solar_reconfig_action;
            out[1] = r.battery_limit_action;
            out[2] = r.load_shed_action;
            out[3] = r.fault_flags;
            memcpy(&out[4], &cycles, 4);
            send_frame('f', out, 8);
        } else if (cmd == 'G' && len == 48) {
            float x[FDIR_TREE_INPUTS], tb[3];
            FDIRResult r;
            uint32_t t0, cycles;
            memcpy(x, in, 36);
            memcpy(tb, &in[36], 12);
            t0 = DWT->CYCCNT;
            fdir_sgo_v3_state = xstate.sgo;
            r = fdir_onboard_step3(x, tb);
            xstate.sgo = fdir_sgo_v3_state;
            fdir_state_seal(&xstate);
            cycles = DWT->CYCCNT - t0;
            out[0] = r.solar_reconfig_action;
            out[1] = r.battery_limit_action;
            out[2] = r.load_shed_action;
            out[3] = r.fault_flags;
            out[4] = fdir_sgo_v3_charge_inhibit;
            memcpy(&out[5], &cycles, 4);
            send_frame('g', out, 9);
        } else if (cmd == 'X' && len == 32) {
            full_step(&xstate, in, out);
            send_frame('x', out, 17);
        } else if (cmd == 'J' && len >= 1 && in[0] >= 1U && in[0] <= MULTI && len == 1U + 4U * in[0]) {
            for (uint32_t k = 0; k < in[0]; k++) {
                float soc0;
                memcpy(&soc0, &in[1 + 4 * k], 4);
                fdir_state_init(&gstate[k], soc0);
            }
            send_frame('j', out, 0);
        } else if (cmd == 'Y' && len >= 1 && in[0] >= 1U && in[0] <= MULTI && len == 1U + 32U * in[0]) {
            static uint8_t big[MULTI * 17];
            for (uint32_t k = 0; k < in[0]; k++) {
                full_step(&gstate[k], &in[1 + 32 * k], &big[17 * k]);
            }
            send_frame('y', big, (uint8_t)(17U * in[0]));
        } else if (cmd == 'Z' && len == 1) {
            scrub_on = in[0] ? 1U : 0U;
            send_frame('z', out, 0);
        } else if (cmd == 'C' && len == 0) {
            uint32_t t0 = DWT->CYCCNT, c;
            (void)fdir_tree_scrub();
            c = DWT->CYCCNT - t0;
            memcpy(out, &c, 4);
            send_frame('c', out, 4);
        } else {
            uint8_t code = 3U;                                  /* unknown command / length */
            send_frame('e', &code, 1);
        }
    }
}
