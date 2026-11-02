/* The installer preserves bytes 3..61 from the FAT16 VBR. */
.intel_syntax noprefix
.code16
.section .text
.global _start
_start:
    jmp short start
    nop
.org 0x3e
start:
    cli
    xor ax, ax
    mov ds, ax
    mov ss, ax
    mov sp, 0x7c00
    sti
    cld
    mov si, offset message
print:
    lodsb
    test al, al
    jz halt
    mov ah, 0x0e
    xor bx, bx
    push si
    int 0x10
    pop si
    jmp print
halt:
    cli
    hlt
    jmp halt
message: .asciz "VBR OK\r\n"
.org 510
.word 0xaa55
