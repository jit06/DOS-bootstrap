; BIOS loads LBA 0 at physical address 0x7c00. No disk I/O is performed.
bits 16
org 0x7c00

    jmp 0x0000:start
start:
    cli
    xor ax, ax
    mov ds, ax
    mov ss, ax
    mov sp, 0x7c00
    sti
    cld
    mov ax, 0x0003          ; standard color text mode, active page 0
    int 0x10
    mov si, message
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
message: db 'MBR OK', 0

times 510-($-$$) db 0
dw 0xaa55
