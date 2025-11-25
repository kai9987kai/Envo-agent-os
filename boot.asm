; boot.asm
BITS 16
ORG 0x7C00

start:
    ; Set up data segments
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7C00

    ; Print welcome message
    mov si, msg_hello
    call print_string

    ; Infinite loop to hang execution
    jmp $

print_string:
    mov ah, 0x0E        ; BIOS teletype output
.loop:
    lodsb               ; Load next byte from DS:SI to AL
    cmp al, 0           ; Check for null terminator
    je .done
    int 0x10            ; Call BIOS video interrupt
    jmp .loop
.done:
    ret

msg_hello db 'Hello World from your custom OS!', 0x0D, 0x0A, 0

times 510-($-$$) db 0   ; Pad to 510 bytes
dw 0xAA55               ; Boot signature
