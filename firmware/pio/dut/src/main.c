/*
 * DUT firmware (STM32F411CE): onboard FDIR code behind a framed UART protocol.
 *
 * Clock: HSI 16 MHz, no PLL, 0 flash wait states (cycle counts are for this
 * configuration). USART1 PA9 TX / PA10 RX, UART_BAUD (default 460800) 8N1. LED PC13 toggles per frame.
 * The firmware drives no other GPIO: it only computes and reports.
 *
 * Frame (both directions, little-endian):
 *   0xA5 0x5A | cmd (1) | len (1) | payload (len) | CRC-16/CCITT-FALSE over cmd..payload (2)
 * Commands:
 *   'P' ping            -> 'p' "fdir-dut <version>"
 *   'I' f32 soc0        -> 'i' (empty)            soc_ekf_init
 *   'S' f32 i_prev, i_now, v, dt
 *                       -> 's' f32 soc, std, innov, innov_var | u8 status | u32 cycles
 *   'T' f32 in[6]       -> 't' u8 S, B, L | u32 cycles                 tree only
 *   'F' f32 in[6]       -> 'f' u8 S, B, L, flags | u32 cycles          tree + SGO v2
 *   'X' f32 i_prev, i_now, v, dt, c_rate, t_bat, i_sa_pu, u_bus_pu
 *                       -> 'x' f32 soc, std | u8 S, B, L, flags, ekf_status | u32 cycles
 *                          full 1 Hz loop: EKF step, then tree + SGO with the EKF SoC
 *   'J' u8 n, f32 soc0[n]   -> 'j'      init n (<= MULTI) independent EKF instances
 *   'Y' u8 n, n x (8 f32 as 'X')
 *                       -> 'y' n x (f32 soc, std | u8 S, B, L, flags, ekf_status | u32 cycles)
 *                          the 'X' loop for n spacecraft per frame (processor-in-the-loop)
 * Bad CRC or unknown command -> 'e' u8 code.
 */
#include <stdint.h>
#include <string.h>
#include "stm32f4xx.h"
#include "soc_ekf.h"
#include "fdir_tree.h"
#include "fdir_onboard.h"

#define FW_VERSION "fdir-dut 0.3 ekf+tree+sgo multi7"
#define MULTI 7
#ifndef UART_BAUD
#define UART_BAUD 460800U
#endif

static SocEkf ekf;
SocEkf ekfs[MULTI];   /* not static: fault injection targets it by symbol (hil/run_case.py C3) */

/* ---------------------------------------------------------------- hardware */
/* one full 1 Hz loop on EKF instance e: in = 8 floats as 'X', out = 17 bytes */
static void full_step(SocEkf *e, const uint8_t *in, uint8_t *out)
{
    float f[8], x[6], soc, sd;
    FDIRResult r;
    uint32_t t0, cycles;
    uint8_t status;
    memcpy(f, in, 32);
    t0 = DWT->CYCCNT;
    status = soc_ekf_step(e, f[0], f[1], f[2], f[3], NULL);
    soc = soc_ekf_soc(e);
    sd = soc_ekf_std(e);
    x[0] = f[2];
    x[1] = f[4];
    x[2] = f[5];
    x[3] = soc;
    x[4] = f[6];
    x[5] = f[7];
    r = fdir_onboard_step(x);
    cycles = DWT->CYCCNT - t0;
    memcpy(out, &soc, 4);
    memcpy(&out[4], &sd, 4);
    out[8] = r.solar_reconfig_action;
    out[9] = r.battery_limit_action;
    out[10] = r.load_shed_action;
    out[11] = r.fault_flags;
    out[12] = status;
    memcpy(&out[13], &cycles, 4);
}

static void hw_init(void)
{
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

    /* USART1, oversampling 16: BRR = round(16 MHz / baud)
     * 115200 -> 139 (0.08 % error), 460800 -> 35 (0.8 % error) */
    USART1->BRR = (16000000U + UART_BAUD / 2U) / UART_BAUD;
    USART1->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;

    /* DWT cycle counter */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

static uint8_t uart_get(void)
{
    while (!(USART1->SR & USART_SR_RXNE)) {
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
    soc_ekf_init(&ekf, 0.5f);

    for (;;) {
        int r = recv_frame(&cmd, in, &len);
        GPIOC->ODR ^= GPIO_ODR_OD13;
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
        } else if (cmd == 'T' && len == 24) {
            float x[6];
            uint8_t a[3];
            uint32_t t0, cycles;
            memcpy(x, in, 24);
            t0 = DWT->CYCCNT;
            fdir_tree_eval(x, a);
            cycles = DWT->CYCCNT - t0;
            memcpy(out, a, 3);
            memcpy(&out[3], &cycles, 4);
            send_frame('t', out, 7);
        } else if (cmd == 'F' && len == 24) {
            float x[6];
            FDIRResult r;
            uint32_t t0, cycles;
            memcpy(x, in, 24);
            t0 = DWT->CYCCNT;
            r = fdir_onboard_step(x);
            cycles = DWT->CYCCNT - t0;
            out[0] = r.solar_reconfig_action;
            out[1] = r.battery_limit_action;
            out[2] = r.load_shed_action;
            out[3] = r.fault_flags;
            memcpy(&out[4], &cycles, 4);
            send_frame('f', out, 8);
        } else if (cmd == 'X' && len == 32) {
            full_step(&ekf, in, out);
            send_frame('x', out, 17);
        } else if (cmd == 'J' && len >= 1 && in[0] >= 1U && in[0] <= MULTI && len == 1U + 4U * in[0]) {
            for (uint32_t k = 0; k < in[0]; k++) {
                float soc0;
                memcpy(&soc0, &in[1 + 4 * k], 4);
                soc_ekf_init(&ekfs[k], soc0);
            }
            send_frame('j', out, 0);
        } else if (cmd == 'Y' && len >= 1 && in[0] >= 1U && in[0] <= MULTI && len == 1U + 32U * in[0]) {
            static uint8_t big[MULTI * 17];
            for (uint32_t k = 0; k < in[0]; k++) {
                full_step(&ekfs[k], &in[1 + 32 * k], &big[17 * k]);
            }
            send_frame('y', big, (uint8_t)(17U * in[0]));
        } else {
            uint8_t code = 3U;                                  /* unknown command / length */
            send_frame('e', &code, 1);
        }
    }
}
