/* CHS-read-only MBR diagnostic. Linked at physical address 0x7c00. */
.intel_syntax noprefix
.code16
.section .text
.global _start
_start:
    cli
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7c00
    cld
    mov si, 0x7c00
    mov di, 0x0600
    mov cx, 256
    rep movsw
    ljmp 0, start
start:
    cli
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7c00
    sti
    cld
    mov byte ptr [drive], dl
    mov si, offset msg_start
    call print
    mov si, 0x07be         /* relocated partition table */
    xor bx, bx
    mov cx, 4
find_active:
    cmp byte ptr [si], 0
    je next_entry
    cmp byte ptr [si], 0x80
    jne bad_entry
    test bx, bx
    jnz bad_entry
    mov bx, si
next_entry:
    add si, 16
    loop find_active
    test bx, bx
    jz bad_entry
    cmp byte ptr [bx+4], 0
    je bad_entry
    cmp byte ptr [bx+1], 0xff
    je bad_entry
    test byte ptr [bx+2], 0x3f
    jz bad_entry
    cmp dword ptr [bx+8], 0
    je bad_entry
    cmp dword ptr [bx+12], 0
    je bad_entry
    mov si, offset msg_reading
    push bx
    call print
    pop bx
    mov dh, byte ptr [bx+1]
    mov cl, byte ptr [bx+2]
    mov ch, byte ptr [bx+3]
    mov dl, byte ptr [drive]
    xor ax, ax
    mov es, ax
    mov bx, 0x7c00         /* MBR has relocated to 0x0600 */
    mov ax, 0x0201
    stc
    int 0x13
    jc read_error
    mov si, offset msg_ok
    call print
    mov si, offset msg_jump
    call print
    mov dl, byte ptr [drive]
    ljmp 0, 0x7c00
read_error:
    mov byte ptr [status], ah
    mov si, offset msg_error
    call print
    mov al, byte ptr [status]
    shr al, 4
    call hex_digit
    mov al, byte ptr [status]
    and al, 15
    call hex_digit
    jmp halt
bad_entry:
    mov si, offset msg_bad
    call print
halt:
    cli
    hlt
    jmp halt
hex_digit:
    and al, 15
    cmp al, 10
    jb decimal
    add al, 7
decimal:
    add al, '0'
    mov ah, 0x0e
    xor bx, bx
    int 0x10
    ret
print:
    lodsb
    test al, al
    jz printed
    mov ah, 0x0e
    xor bx, bx
    push si
    int 0x10
    pop si
    jmp print
printed:
    ret
drive: .byte 0
status: .byte 0
msg_start: .asciz "MBR START\r\n"
msg_reading: .asciz "READING...\r\n"
msg_ok: .asciz "DISK READ OK\r\n"
msg_jump: .asciz "JUMPING TO VBR\r\n"
msg_error: .asciz "DISK READ ERROR AH="
msg_bad: .asciz "INVALID PARTITION\r\n"
.org 440
