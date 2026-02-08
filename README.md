# Envo-agent-os

A **minimal custom OS project** containing a **bootloader**, a **graphical kernel simulation**, and a **Python build tool** that generates **bootable media** (floppy image + ISO). :contentReference[oaicite:0]{index=0}

This repo includes both **source** (`boot.asm`, `create_iso.py`) and **prebuilt artifacts** (`boot.bin`, `floppy.img`, `os.iso`) so you can boot it immediately in an emulator. :contentReference[oaicite:1]{index=1}

---

## Quick start (run the prebuilt ISO)

### QEMU (recommended)
```bash
qemu-system-i386 -cdrom os.iso
