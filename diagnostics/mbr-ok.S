/* GNU assembler equivalent, used when NASM is unavailable. */
.intel_syntax noprefix
.code16
.section .text
.global _start
_start:
    ljmp 0x0000, start
start:
    cli
    xor ax, ax
    mov ds, ax
    mov ss, ax
    mov sp, 0x7c00
    sti
    cld
    mov ax, 0x0003
    int 0x10
    mov si, offset message
next_char:
    lodsb
    test al, al
    jz halt
    mov ah, 0x0e
    xor bx, bx
    int 0x10
    jmp next_char
halt:
    cli
    hlt
    jmp halt
message: .asciz "MBR OK"
.org 510
.word 0xaa55
