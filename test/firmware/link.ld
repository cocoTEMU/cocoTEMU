MEMORY {
    OCM (rwx) : ORIGIN = 0x00000000, LENGTH = 256K
}
SECTIONS {
    .text : {
        *(.vectors)
        *(.text*)
    } > OCM
    .rodata : { *(.rodata*) } > OCM
    .data   : { *(.data*)   } > OCM
    .bss    : { *(.bss*)    } > OCM
}
