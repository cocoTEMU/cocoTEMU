/*
 * Minimal bare-metal firmware for Zynq that writes/reads PL registers
 * via the mmio-stub at 0x43C00000.
 */
#include <stdint.h>

#define PL_BASE   ((volatile uint32_t *)0x43C00000)

/* Cadence UART0 at 0xE0000000 (serial0 — attached to stdio by -nographic) */
#define UART_BASE ((volatile uint32_t *)0xE0000000)
#define UART_CR   (UART_BASE[0])   /* Control Register        offset 0x00 */
#define UART_MR   (UART_BASE[1])   /* Mode Register           offset 0x04 */
#define UART_BRGEN (UART_BASE[6])  /* Baud Rate Generator     offset 0x18 */
#define UART_SR   (UART_BASE[11])  /* Channel Status Register offset 0x2C */
#define UART_FIFO (UART_BASE[12])  /* TX/RX FIFO              offset 0x30 */
#define UART_BDIV (UART_BASE[13])  /* Baud Rate Divider       offset 0x34 */

static void uart_init(void) {
    /* Reset TX + RX */
    UART_CR = (1 << 1) | (1 << 0);  /* TXRST | RXRST */
    /* Set baud rate generator (values don't matter for QEMU, just non-zero) */
    UART_BRGEN = 62;
    UART_BDIV  = 6;
    /* Normal mode, 1 stop bit, no parity, 8-bit */
    UART_MR = 0x00000020;
    /* Enable TX + RX */
    UART_CR = (1 << 4) | (1 << 2);  /* TX_EN | RX_EN */
}

static void uart_putc(char c) {
    while (UART_SR & (1 << 4));  /* wait until TX FIFO not full */
    UART_FIFO = c;
}

static void uart_puts(const char *s) {
    while (*s) uart_putc(*s++);
}

static void uart_puthex(uint32_t v) {
    const char hex[] = "0123456789ABCDEF";
    uart_puts("0x");
    for (int i = 28; i >= 0; i -= 4)
        uart_putc(hex[(v >> i) & 0xF]);
}

int main(void) {
    uart_init();

    uart_puts("\r\n=== cocoTEMU test firmware ===\r\n");

    /* Write test values to PL registers */
    uart_puts("Writing PL regs...\r\n");
    PL_BASE[0] = 0xDEADBEEF;
    PL_BASE[1] = 0xCAFEBABE;
    PL_BASE[2] = 0x12345678;
    PL_BASE[3] = 0xA5A5A5A5;

    /* Read them back */
    uart_puts("Reading PL regs...\r\n");
    for (int i = 0; i < 4; i++) {
        uart_puts("  REG[");
        uart_putc('0' + i);
        uart_puts("] = ");
        uart_puthex(PL_BASE[i]);
        uart_puts("\r\n");
    }

    uart_puts("=== DONE ===\r\n");

    /* Spin */
    while (1) __asm__("wfi");
}
