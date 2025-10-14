.section .vectors, "ax"
.global _start
_start:
    b   reset
    b   .           /* Undefined */
    b   .           /* SVC */
    b   .           /* Prefetch abort */
    b   .           /* Data abort */
    b   .           /* Reserved */
    b   .           /* IRQ */
    b   .           /* FIQ */

reset:
    ldr sp, =0x00100000    /* 1MB stack in OCM */
    bl  main
    b   .
