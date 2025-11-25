import struct
import os
import sys

# --- MINI ASSEMBLER ---
class ASM:
    def __init__(self, org=0):
        self.code = bytearray()
        self.labels = {}
        self.patches = [] 
        self.org = org

    def current_addr(self): return self.org + len(self.code)
    def label(self, name): self.labels[name] = self.current_addr()
    def db(self, data):
        if isinstance(data, int): self.code.append(data)
        elif isinstance(data, bytes): self.code.extend(data)
        elif isinstance(data, str): self.code.extend(data.encode('ascii'))
    def dw(self, val): self.code.extend(struct.pack('<H', val & 0xFFFF))

    # Instructions
    def nop(self): self.db(0x90)
    def cli(self): self.db(0xFA)
    def sti(self): self.db(0xFB)
    def hlt(self): self.db(0xF4)
    def ret(self): self.db(0xC3)
    def pusha(self): self.db(0x60)
    def popa(self): self.db(0x61)
    def int_(self, imm): self.db(0xCD); self.db(imm)
    
    def jmp(self, label): self.db(0xE9); self.patches.append((len(self.code), label, 2, True)); self.db(0x00); self.db(0x00)
    def je(self, label): self.db(0x74); self.patches.append((len(self.code), label, 1, True)); self.db(0x00)
    def jne(self, label): self.db(0x75); self.patches.append((len(self.code), label, 1, True)); self.db(0x00)
    def jl(self, label): self.db(0x7C); self.patches.append((len(self.code), label, 1, True)); self.db(0x00)
    def jg(self, label): self.db(0x7F); self.patches.append((len(self.code), label, 1, True)); self.db(0x00)
    def jle(self, label): self.db(0x7E); self.patches.append((len(self.code), label, 1, True)); self.db(0x00)
    def jge(self, label): self.db(0x7D); self.patches.append((len(self.code), label, 1, True)); self.db(0x00)
    def jcxz(self, label): self.db(0xE3); self.patches.append((len(self.code), label, 1, True)); self.db(0x00)
    def jc(self, label): self.db(0x72); self.patches.append((len(self.code), label, 1, True)); self.db(0x00)

    def call(self, label): self.db(0xE8); self.patches.append((len(self.code), label, 2, True)); self.db(0x00); self.db(0x00)

    # MOV
    def mov_al(self, val): self.db(0xB0); self.db(val)
    def mov_ax(self, val): self.db(0xB8); self.dw(val)
    def mov_bx(self, val): self.db(0xBB); self.dw(val)
    def mov_cx(self, val): self.db(0xB9); self.dw(val)
    def mov_dx(self, val): self.db(0xBA); self.dw(val)
    def mov_si(self, val): self.db(0xBE); self.dw(val)
    def mov_di(self, val): self.db(0xBF); self.dw(val)
    def mov_sp(self, val): self.db(0xBC); self.dw(val)
    def mov_bp(self, val): self.db(0xBD); self.dw(val)
    
    def mov_ds_ax(self): self.db(0x8E); self.db(0xD8)
    def mov_es_ax(self): self.db(0x8E); self.db(0xC0)
    def mov_ss_ax(self): self.db(0x8E); self.db(0xD0)

    # Arithmetic
    def xor_ax_ax(self): self.db(0x31); self.db(0xC0)
    def xor_di_di(self): self.db(0x31); self.db(0xFF)
    def inc_di(self): self.db(0x47)
    def inc_si(self): self.db(0x46)
    def inc_ax(self): self.db(0x40)
    def inc_cx(self): self.db(0x41)
    def dec_cx(self): self.db(0x49)
    def dec_dx(self): self.db(0x4A)
    
    def cmp_al(self, val): self.db(0x3C); self.db(val)
    def cmp_ax(self, val): self.db(0x3D); self.dw(val)
    def cmp_cx(self, val): self.db(0x81); self.db(0xF9); self.dw(val)
    def cmp_dx(self, val): self.db(0x81); self.db(0xFA); self.dw(val)
    def cmp_bx(self, val): self.db(0x81); self.db(0xFB); self.dw(val)
    def cmp_bp(self, val): self.db(0x81); self.db(0xFD); self.dw(val)
    def cmp_si(self, val): self.db(0x81); self.db(0xFE); self.dw(val)
    def cmp_di(self, val): self.db(0x81); self.db(0xFF); self.dw(val)

    # String
    def lodsb(self): self.db(0xAC)
    def stosb(self): self.db(0xAA)
    def rep_stosb(self): self.db(0xF3); self.db(0xAA)

    # IO
    def out_dx_al(self): self.db(0xEE)

    def resolve(self):
        for offset, label, size, relative in self.patches:
            if label not in self.labels: print(f"Error: Undefined label '{label}'"); continue
            target = self.labels[label]
            if relative:
                pc = self.org + offset + size
                diff = target - pc
                if size == 1: self.code[offset] = diff & 0xFF
                elif size == 2: struct.pack_into('<h', self.code, offset, diff)
            else: struct.pack_into('<H', self.code, offset, target)
        return self.code

# --- BUILDER ---

def build_bootloader():
    asm = ASM(org=0x7C00)
    asm.xor_ax_ax(); asm.mov_ds_ax(); asm.mov_es_ax(); asm.mov_ss_ax(); asm.mov_sp(0x7C00)
    asm.mov_bx(0x0007); asm.mov_ax(0x0E42); asm.int_(0x10) # 'B'
    asm.xor_ax_ax(); asm.int_(0x13)
    asm.mov_bx(0x1000); asm.mov_ax(0x020A); asm.mov_cx(0x0002); asm.db(0xB6); asm.db(0x00); asm.int_(0x13)
    asm.jc('disk_error')
    asm.db(0xEA); asm.dw(0x1000); asm.dw(0x0000) # JMP FAR
    asm.label('disk_error'); asm.mov_ax(0x0E45); asm.int_(0x10); asm.hlt(); asm.jmp('disk_error')
    code = asm.resolve()
    code += b'\x00' * (510 - len(code)) + b'\x55\xAA'
    return code

def build_kernel():
    asm = ASM(org=0x1000)
    
    # --- CONSTANTS ---
    COLOR_BG = 0x01   
    COLOR_PREY = 0x2F 
    COLOR_PRED = 0x28 
    COLOR_FOOD = 0x2C 
    COLOR_WATER = 0x33 # Light Blue
    
    VGA_SEG = 0xA000
    SCREEN_W = 320; SCREEN_H = 200
    
    NUM_PREY = 30
    NUM_PRED = 4
    NUM_FOOD = 50
    
    # Entity Struct: [X(2), Y(2), Energy(2), GeneSpeed(2), GeneSense(2), State(2)] = 12 bytes
    ENT_SIZE = 12
    
    DATA_BASE = 0x9000
    # DB-2: PRNG, DB-4: Time, DB-6: PreyScore, DB-8: PredScore
    # DB-10: TempTarget
    
    asm.label('start')
    asm.xor_ax_ax(); asm.mov_ds_ax(); asm.mov_es_ax(); asm.mov_ss_ax(); asm.mov_sp(0x7C00) 
    asm.mov_ax(0x0013); asm.int_(0x10) # Mode 13h 
    # Init Variables
    asm.mov_bx(DATA_BASE - 4); asm.xor_ax_ax(); asm.db(0x89); asm.db(0x07) # Time = 0
    
    # Reset ES to 0 for data operations
    asm.xor_ax_ax(); asm.mov_es_ax()
    
    asm.call('init_entities')
    
    # --- MAIN LOOP ---
    asm.label('main_loop')
    
    # 0. Time & Day/Night Cycle
    asm.mov_bx(DATA_BASE - 4)
    asm.db(0x8B); asm.db(0x07) # MOV AX, [BX]
    asm.inc_ax()
    asm.db(0x89); asm.db(0x07) # MOV [BX], AX
    
    # Palette Shifting (Simulate Day/Night)
    # Check if Time bit 8 is set (Cycle speed)
    asm.db(0xA9); asm.dw(0x0100) # TEST AX, 0x100
    asm.je('is_day')
    # Night: Darken Palette (Simple hack: Use different BG color?)
    # Real palette shifting is complex. Let's just set a flag in DX.
    asm.mov_dx(1) # Night
    asm.jmp('time_done')
    asm.label('is_day')
    asm.mov_dx(0) # Day
    asm.label('time_done')
    
    # 1. Input
    asm.mov_ax(0x0100); asm.int_(0x16); asm.je('no_key')
    asm.mov_ax(0x0000); asm.int_(0x16)
    asm.cmp_al(114); asm.je('key_restart') # r
    asm.cmp_al(112); asm.je('key_pause') # p
    asm.jmp('no_key')
    asm.label('key_restart'); asm.jmp('start')
    asm.label('key_pause'); asm.mov_ax(0); asm.int_(0x16); asm.cmp_al(112); asm.je('no_key'); asm.jmp('key_pause')
    asm.label('no_key')
    
    # 2. Draw BG
    asm.mov_ax(VGA_SEG); asm.mov_es_ax()
    asm.xor_di_di(); asm.mov_cx(32000)
    # If Night (DX=1), use Dark Blue (0x10), else Blue (0x01)
    asm.cmp_dx(1); asm.je('bg_night')
    asm.mov_ax(0x0101); asm.jmp('bg_draw')
    asm.label('bg_night'); asm.mov_ax(0x1010)
    asm.label('bg_draw'); asm.db(0xF3); asm.db(0xAB)
    
    # Draw Water (Bottom 30 pixels)
    asm.mov_di(320 * 170)
    asm.mov_cx(320 * 30 // 2)
    asm.mov_ax(0x3333) # Water Color
    asm.db(0xF3); asm.db(0xAB)
    
    # Draw Fireflies (If Night)
    asm.cmp_dx(1); asm.jne('skip_fireflies')
    asm.mov_cx(20) # 20 Fireflies
    asm.label('firefly_loop')
    asm.call('rand'); asm.mov_di(0); asm.db(0x89); asm.db(0xC7) # Random Pos
    asm.cmp_di(64000); asm.jge('ff_next') # Safety
    asm.mov_ax(0x2C2C); asm.db(0xAA) # Yellow pixel
    asm.label('ff_next')
    asm.dec_cx(); asm.jcxz('skip_fireflies'); asm.jmp('firefly_loop')
    asm.label('skip_fireflies')
    
    # Draw UI (Sun/Moon)
    asm.mov_di(320*5 + 300) # Top Right
    asm.cmp_dx(1); asm.je('draw_moon')
    asm.mov_ax(0x2C2C); asm.jmp('draw_celest') # Sun (Yellow)
    asm.label('draw_moon'); asm.mov_ax(0x0F0F) # Moon (White)
    asm.label('draw_celest'); asm.db(0xAA); asm.db(0xAA); asm.db(0xAA); asm.db(0xAA) # 4 pixels
    
    # 3. Update Entities
    # Food
    asm.mov_si(DATA_BASE); asm.mov_cx(NUM_FOOD); asm.call('update_food')
    # Prey
    asm.mov_si(DATA_BASE + (NUM_FOOD * 6)); asm.mov_cx(NUM_PREY); asm.call('update_prey')
    # Pred
    asm.mov_si(DATA_BASE + (NUM_FOOD * 6) + (NUM_PREY * ENT_SIZE)); asm.mov_cx(NUM_PRED); asm.call('update_pred')
    
    asm.call('delay')
    asm.jmp('main_loop')
    
    # --- FUNCTIONS ---
    
    # PRNG
    asm.label('rand')
    asm.db(0x53); asm.mov_bx(DATA_BASE - 2); asm.db(0x8B); asm.db(0x07)
    asm.inc_ax(); asm.db(0x05); asm.dw(13); asm.db(0x89); asm.db(0x07); asm.db(0x5B); asm.ret()

    # Init
    asm.label('init_entities')
    asm.mov_di(DATA_BASE)
    
    # Food (Simple X,Y)
    asm.mov_cx(NUM_FOOD)
    asm.label('init_food')
    asm.call('rand'); asm.db(0x25); asm.dw(300); asm.db(0x05); asm.dw(10); asm.db(0xAA); asm.db(0x00) # X
    asm.call('rand'); asm.db(0x25); asm.dw(180); asm.db(0x05); asm.dw(10); asm.db(0xAA); asm.db(0x00) # Y
    asm.mov_ax(0); asm.db(0xAB) # Padding
    asm.dec_cx(); asm.jcxz('init_prey'); asm.jmp('init_food')
    
    # Prey (Advanced Struct)
    asm.label('init_prey')
    asm.mov_cx(NUM_PREY)
    asm.label('init_prey_loop')
    asm.call('rand'); asm.db(0x25); asm.dw(300); asm.db(0x05); asm.dw(10); asm.db(0xAA); asm.db(0x00) # X
    asm.call('rand'); asm.db(0x25); asm.dw(180); asm.db(0x05); asm.dw(10); asm.db(0xAA); asm.db(0x00) # Y
    asm.mov_ax(100); asm.db(0xAB) # Energy
    asm.mov_ax(2); asm.db(0xAB) # GeneSpeed (2)
    asm.mov_ax(50); asm.db(0xAB) # GeneSense (50)
    asm.mov_ax(0); asm.db(0xAB) # State (0=Idle)
    asm.dec_cx(); asm.jcxz('init_pred'); asm.jmp('init_prey_loop')
    
    # Pred
    asm.label('init_pred')
    asm.mov_cx(NUM_PRED)
    asm.label('init_pred_loop')
    asm.call('rand'); asm.db(0x25); asm.dw(300); asm.db(0x05); asm.dw(10); asm.db(0xAB)
    asm.call('rand'); asm.db(0x25); asm.dw(180); asm.db(0x05); asm.dw(10); asm.db(0xAB)
    asm.mov_ax(100); asm.db(0xAB)
    asm.mov_ax(3); asm.db(0xAB) # Faster
    asm.mov_ax(100); asm.db(0xAB) # Better Vision
    asm.mov_ax(0); asm.db(0xAB)
    asm.dec_cx(); asm.jcxz('init_done'); asm.jmp('init_pred_loop')
    asm.label('init_done'); asm.ret()

    # Draw Pixel
    asm.label('draw_pixel')
    asm.pusha()
    asm.db(0x06) # PUSH ES
    asm.cmp_cx(318); asm.jg('draw_skip'); asm.cmp_dx(198); asm.jg('draw_skip')
    asm.mov_ax(0xA000); asm.mov_es_ax()
    asm.db(0x89); asm.db(0xD0); asm.db(0xC1); asm.db(0xE0); asm.db(0x08)
    asm.mov_di(0); asm.db(0x89); asm.db(0xD7); asm.db(0xC1); asm.db(0xE7); asm.db(0x06)
    asm.db(0x01); asm.db(0xF8); asm.db(0x01); asm.db(0xC8)
    asm.mov_di(0); asm.db(0x89); asm.db(0xC7)
    asm.mov_ax(0); asm.db(0x88); asm.db(0xD8)
    asm.db(0xAA); asm.db(0xAA); asm.db(0x81); asm.db(0xC7); asm.dw(318); asm.db(0xAA); asm.db(0xAA)
    asm.label('draw_skip'); asm.db(0x07); asm.popa(); asm.ret() # POP ES

    # Update Food
    asm.label('update_food')
    asm.label('food_loop')
    asm.pusha()
    asm.db(0xAD); asm.mov_bx(0); asm.db(0x89); asm.db(0xC3) 
    asm.db(0xAD); asm.mov_dx(0); asm.db(0x89); asm.db(0xC2)
    asm.mov_cx(0); asm.db(0x89); asm.db(0xD9); asm.mov_bx(COLOR_FOOD); asm.call('draw_pixel')
    asm.popa()
    asm.db(0x83); asm.db(0xC6); asm.db(0x06) # Food is 6 bytes
    asm.dec_cx(); asm.jcxz('food_done'); asm.jmp('food_loop')
    asm.label('food_done'); asm.ret()

    # Update Prey (Genetics + FSM)
    asm.label('update_prey')
    asm.label('prey_loop')
    asm.pusha()
    
    # Load Genes
    # SI points to X. 
    # [SI+6] = GeneSpeed, [SI+8] = GeneSense
    
    # FSM Check: Sleep?
    # Check Night (Global DX from main loop? No, DX clobbered. Check Time again)
    asm.pusha()
    asm.mov_bx(DATA_BASE - 4); asm.db(0x8B); asm.db(0x07); asm.db(0xA9); asm.dw(0x0100)
    asm.popa()
    asm.je('prey_awake') # Day
    # Night: Sleep (Don't move, Heal)
    asm.db(0x83); asm.db(0x44); asm.db(0x04); asm.db(0x01) # ADD [SI+4], 1 (Energy)
    asm.jmp('prey_draw')
    
    asm.label('prey_awake')
    # Flee Logic (Using GeneSense)
    asm.mov_di(DATA_BASE + (NUM_FOOD * 6) + (NUM_PREY * ENT_SIZE))
    asm.mov_dx(NUM_PRED)
    asm.label('flee_scan')
    # Calc Dist
    asm.db(0x8B); asm.db(0x04); asm.db(0x2B); asm.db(0x05); asm.mov_bx(0); asm.db(0x89); asm.db(0xC3) # X diff
    asm.db(0x8B); asm.db(0x44); asm.db(0x02); asm.db(0x2B); asm.db(0x45); asm.db(0x02); asm.mov_cx(0); asm.db(0x89); asm.db(0xC1) # Y diff
    asm.cmp_bx(0); asm.jge('bx_p'); asm.db(0xF7); asm.db(0xDB); asm.label('bx_p')
    asm.cmp_cx(0); asm.jge('cx_p'); asm.db(0xF7); asm.db(0xD9); asm.label('cx_p')
    asm.db(0x01); asm.db(0xCB) # Dist
    
    # Compare with GeneSense [SI+8]
    asm.db(0x3B); asm.db(0x5C); asm.db(0x08) # CMP BX, [SI+8]
    asm.jl('flee_now')
    
    asm.db(0x83); asm.db(0xC7); asm.db(0x0C) # Next Pred (12 bytes)
    asm.dec_dx(); asm.cmp_dx(0); asm.je('flee_none'); asm.jmp('flee_scan')
    
    asm.label('flee_now')
    # Move Away (Using GeneSpeed [SI+6])
    asm.db(0x8B); asm.db(0x5C); asm.db(0x06) # MOV BX, [SI+6] (Speed)
    # Direction logic (Simplified)
    asm.db(0x8B); asm.db(0x04); asm.db(0x2B); asm.db(0x05); asm.cmp_ax(0); asm.jge('f_r'); asm.db(0x01); asm.db(0x1C); asm.jmp('f_y'); asm.label('f_r'); asm.db(0x29); asm.db(0x1C)
    asm.label('f_y')
    asm.db(0x8B); asm.db(0x44); asm.db(0x02); asm.db(0x2B); asm.db(0x45); asm.db(0x02); asm.cmp_ax(0); asm.jge('f_d'); asm.db(0x01); asm.db(0x5C); asm.db(0x02); asm.jmp('prey_move_done'); asm.label('f_d'); asm.db(0x29); asm.db(0x5C); asm.db(0x02)
    asm.jmp('prey_move_done')
    
    asm.label('flee_none')
    # Random Move (Speed)
    asm.call('rand'); asm.db(0x25); asm.db(0x03); asm.db(0x00); asm.db(0x2D); asm.db(0x01); asm.db(0x00) # -1 to 1
    # Multiply by Speed? Hard. Just add Speed.
    asm.db(0x03); asm.db(0x44); asm.db(0x06) # ADD AX, [SI+6]
    asm.db(0x01); asm.db(0x04) # Add to X
    
    asm.call('rand'); asm.db(0x25); asm.db(0x03); asm.db(0x00); asm.db(0x2D); asm.db(0x01); asm.db(0x00)
    asm.db(0x03); asm.db(0x44); asm.db(0x06)
    asm.db(0x01); asm.db(0x44); asm.db(0x02) # Add to Y
    
    asm.label('prey_move_done')
    
    # Water Physics (Drag)
    # If Y > 170, Speed = Speed / 2 (Simulated by skipping move every other frame? Too complex. Just subtract 1 from pos)
    asm.db(0x83); asm.db(0x7C); asm.db(0x02); asm.db(0xAA) # CMP [SI+2], 170
    asm.jl('not_water')
    asm.db(0xFF); asm.db(0x4C); asm.db(0x02) # DEC [SI+2] (Push back)
    asm.label('not_water')
    
    # Eat Food
    asm.mov_di(DATA_BASE); asm.mov_dx(NUM_FOOD)
    asm.label('eat_loop')
    # Collision Check (Inline)
    asm.db(0x8B); asm.db(0x04); asm.db(0x2B); asm.db(0x05); asm.cmp_ax(0); asm.jge('dx_p'); asm.db(0xF7); asm.db(0xD8); asm.label('dx_p'); asm.cmp_ax(4); asm.jg('no_eat')
    asm.db(0x8B); asm.db(0x44); asm.db(0x02); asm.db(0x2B); asm.db(0x45); asm.db(0x02); asm.cmp_ax(0); asm.jge('dy_p'); asm.db(0xF7); asm.db(0xD8); asm.label('dy_p'); asm.cmp_ax(4); asm.jg('no_eat')
    # Ate
    asm.call('rand'); asm.db(0x25); asm.dw(300); asm.db(0x05); asm.dw(10); asm.db(0x89); asm.db(0x05) # Respawn Food
    asm.call('rand'); asm.db(0x25); asm.dw(180); asm.db(0x05); asm.dw(10); asm.db(0x89); asm.db(0x45); asm.db(0x02)
    asm.db(0x83); asm.db(0x44); asm.db(0x04); asm.db(0x14) # Add 20 Energy
    asm.mov_bx(0x0F); asm.jmp('prey_draw_c')
    asm.label('no_eat')
    asm.db(0x83); asm.db(0xC7); asm.db(0x06); asm.dec_dx(); asm.cmp_dx(0); asm.je('eat_done'); asm.jmp('eat_loop')
    asm.label('eat_done')
    
    asm.mov_bx(COLOR_PREY)
    asm.label('prey_draw')
    asm.label('prey_draw_c')
    asm.db(0x8B); asm.db(0x0C); asm.db(0x8B); asm.db(0x54); asm.db(0x02); asm.call('draw_pixel')
    
    asm.popa()
    asm.db(0x83); asm.db(0xC6); asm.db(0x0C) # Next Prey (12 bytes)
    asm.dec_cx(); asm.jcxz('prey_done'); asm.jmp('prey_loop')
    asm.label('prey_done'); asm.ret()

    # Update Pred (Chase)
    asm.label('update_pred')
    asm.label('pred_loop')
    asm.pusha()
    
    # Chase Logic (Find closest Prey)
    asm.mov_di(DATA_BASE + (NUM_FOOD * 6))
    asm.mov_dx(NUM_PREY)
    asm.mov_bp(0x7FFF) # Min Dist
    asm.mov_bx(0) # Target
    
    asm.label('chase_scan')
    asm.db(0x8B); asm.db(0x04); asm.db(0x2B); asm.db(0x05); asm.cmp_ax(0); asm.jge('pdx_p'); asm.db(0xF7); asm.db(0xD8); asm.label('pdx_p'); asm.pusha(); asm.mov_bx(0); asm.db(0x89); asm.db(0xC3)
    asm.db(0x8B); asm.db(0x44); asm.db(0x02); asm.db(0x2B); asm.db(0x45); asm.db(0x02); asm.cmp_ax(0); asm.jge('pdy_p'); asm.db(0xF7); asm.db(0xD8); asm.label('pdy_p')
    asm.db(0x01); asm.db(0xD8) # Dist
    asm.db(0x39); asm.db(0xE8) # CMP AX, BP
    asm.jge('not_closer')
    asm.db(0x89); asm.db(0xC5) # MOV BP, AX
    asm.db(0x89); asm.db(0x3E); asm.dw(DATA_BASE - 10) # Save Target DI
    asm.label('not_closer')
    asm.popa()
    asm.db(0x83); asm.db(0xC7); asm.db(0x0C); asm.dec_dx(); asm.cmp_dx(0); asm.je('chase_done'); asm.jmp('chase_scan')
    
    asm.label('chase_done')
    # Check Vision [SI+8]
    asm.db(0x3B); asm.db(0x6C); asm.db(0x08) # CMP BP, [SI+8]
    asm.jg('chase_none')
    
    # Move to Target
    asm.db(0x8B); asm.db(0x3E); asm.dw(DATA_BASE - 10) # MOV DI, Target
    # X
    asm.db(0x8B); asm.db(0x04); asm.db(0x2B); asm.db(0x05); asm.cmp_ax(0); asm.je('c_y'); asm.jg('c_l'); asm.db(0xFF); asm.db(0x04); asm.jmp('c_y'); asm.label('c_l'); asm.db(0xFF); asm.db(0x0C)
    asm.label('c_y')
    asm.db(0x8B); asm.db(0x44); asm.db(0x02); asm.db(0x2B); asm.db(0x45); asm.db(0x02); asm.cmp_ax(0); asm.je('c_done'); asm.jg('c_u'); asm.db(0xFF); asm.db(0x44); asm.db(0x02); asm.jmp('c_done'); asm.label('c_u'); asm.db(0xFF); asm.db(0x4C); asm.db(0x02)
    asm.jmp('c_done')
    
    asm.label('chase_none')
    asm.call('rand'); asm.db(0x25); asm.db(0x03); asm.db(0x00); asm.db(0x2D); asm.db(0x01); asm.db(0x00); asm.db(0x01); asm.db(0x04)
    asm.call('rand'); asm.db(0x25); asm.db(0x03); asm.db(0x00); asm.db(0x2D); asm.db(0x01); asm.db(0x00); asm.db(0x01); asm.db(0x44); asm.db(0x02)
    
    asm.label('c_done')
    
    # Eat Prey
    asm.mov_di(DATA_BASE + (NUM_FOOD * 6)); asm.mov_dx(NUM_PREY)
    asm.label('kill_loop')
    asm.db(0x8B); asm.db(0x04); asm.db(0x2B); asm.db(0x05); asm.cmp_ax(0); asm.jge('kdx_p'); asm.db(0xF7); asm.db(0xD8); asm.label('kdx_p'); asm.cmp_ax(4); asm.jg('no_kill')
    asm.db(0x8B); asm.db(0x44); asm.db(0x02); asm.db(0x2B); asm.db(0x45); asm.db(0x02); asm.cmp_ax(0); asm.jge('kdy_p'); asm.db(0xF7); asm.db(0xD8); asm.label('kdy_p'); asm.cmp_ax(4); asm.jg('no_kill')
    # Kill
    asm.call('rand'); asm.db(0x25); asm.dw(300); asm.db(0x05); asm.dw(10); asm.db(0x89); asm.db(0x05)
    asm.call('rand'); asm.db(0x25); asm.dw(180); asm.db(0x05); asm.dw(10); asm.db(0x89); asm.db(0x45); asm.db(0x02)
    asm.mov_bx(0x28); asm.jmp('pred_draw_c')
    asm.label('no_kill')
    asm.db(0x83); asm.db(0xC7); asm.db(0x0C); asm.dec_dx(); asm.cmp_dx(0); asm.je('kill_done'); asm.jmp('kill_loop')
    asm.label('kill_done')
    
    asm.mov_bx(COLOR_PRED)
    asm.label('pred_draw_c')
    asm.db(0x8B); asm.db(0x0C); asm.db(0x8B); asm.db(0x54); asm.db(0x02); asm.call('draw_pixel')
    
    asm.popa()
    asm.db(0x83); asm.db(0xC6); asm.db(0x0C)
    asm.dec_cx(); asm.jcxz('pred_done'); asm.jmp('pred_loop')
    asm.label('pred_done'); asm.ret()

    # Delay
    asm.label('delay')
    asm.mov_cx(5000); asm.label('d1'); asm.pusha(); asm.mov_cx(100); asm.label('d2'); asm.nop(); asm.dec_cx(); asm.jcxz('d2_d'); asm.jmp('d2'); asm.label('d2_d'); asm.popa(); asm.dec_cx(); asm.jcxz('d1_d'); asm.jmp('d1'); asm.label('d1_d'); asm.ret()

    return asm.resolve()

def create_floppy_img(boot_bin, kernel_bin, floppy_path):
    FLOPPY_SIZE = 1474560
    image = bytearray(FLOPPY_SIZE)
    image[:512] = boot_bin
    k_len = len(kernel_bin)
    image[512:512+k_len] = kernel_bin
    with open(floppy_path, 'wb') as f: f.write(image)
    return True

def create_iso(floppy_img_path, iso_path):
    SECTOR_SIZE = 2048
    try:
        with open(floppy_img_path, 'rb') as f: floppy_data = f.read()
    except FileNotFoundError: return
    floppy_sectors_count = (len(floppy_data) + SECTOR_SIZE - 1) // SECTOR_SIZE
    sectors = [b'\x00' * SECTOR_SIZE] * 16 
    pvd = bytearray(b'\x00' * SECTOR_SIZE)
    pvd[0] = 1; pvd[1:6] = b'CD001'; pvd[6] = 1; pvd[40:72] = b'EVOSIM_RETRO'.ljust(32, b' ')
    boot_catalog_lba = 19
    boot_image_lba = 20
    next_free_lba = boot_image_lba + floppy_sectors_count
    root_lba = next_free_lba
    root_record = bytearray(34)
    root_record[0] = 34; root_record[1] = 0
    struct.pack_into('<I', root_record, 2, root_lba)
    struct.pack_into('>I', root_record, 6, root_lba)
    struct.pack_into('<I', root_record, 10, SECTOR_SIZE)
    struct.pack_into('>I', root_record, 14, SECTOR_SIZE)
    root_record[25] = 2; root_record[32] = 1; root_record[33] = 0
    pvd[156:190] = root_record
    sectors.append(pvd) 
    brvd = bytearray(b'\x00' * SECTOR_SIZE)
    brvd[0] = 0; brvd[1:6] = b'CD001'; brvd[6] = 1
    brvd[7:39] = b'EL TORITO SPECIFICATION'.ljust(32, b'\x00')
    brvd[71:75] = struct.pack('<I', boot_catalog_lba) 
    sectors.append(brvd) 
    term = bytearray(b'\x00' * SECTOR_SIZE)
    term[0] = 255; term[1:6] = b'CD001'; term[6] = 1
    sectors.append(term) 
    catalog = bytearray(b'\x00' * SECTOR_SIZE)
    catalog[0] = 1; catalog[1] = 0
    catalog[30] = 0x55; catalog[31] = 0xAA
    checksum = 0
    for i in range(0, 32, 2):
        if i == 28: continue
        val = struct.unpack_from('<H', catalog, i)[0]
        checksum = (checksum + val) & 0xFFFF
    checksum = (-checksum) & 0xFFFF
    struct.pack_into('<H', catalog, 28, checksum)
    catalog[32] = 0x88; catalog[33] = 0x02 
    catalog[34:36] = b'\x00\x00'; catalog[36] = 0; catalog[37] = 0
    catalog[38:40] = struct.pack('<H', 1)
    catalog[40:44] = struct.pack('<I', boot_image_lba)
    sectors.append(catalog) 
    for i in range(0, len(floppy_data), SECTOR_SIZE):
        chunk = floppy_data[i:i+SECTOR_SIZE]
        if len(chunk) < SECTOR_SIZE: chunk = chunk.ljust(SECTOR_SIZE, b'\x00')
        sectors.append(chunk)
    root_dir = bytearray(b'\x00' * SECTOR_SIZE)
    root_dir[0] = 34
    struct.pack_into('<I', root_dir, 2, root_lba)
    struct.pack_into('>I', root_dir, 6, root_lba)
    struct.pack_into('<I', root_dir, 10, SECTOR_SIZE)
    struct.pack_into('>I', root_dir, 14, SECTOR_SIZE)
    root_dir[25] = 2; root_dir[32] = 1; root_dir[33] = 0
    offset = 34
    root_dir[offset] = 34
    struct.pack_into('<I', root_dir, offset+2, root_lba)
    struct.pack_into('>I', root_dir, offset+6, root_lba)
    struct.pack_into('<I', root_dir, offset+10, SECTOR_SIZE)
    struct.pack_into('>I', root_dir, offset+14, SECTOR_SIZE)
    root_dir[offset+25] = 2; root_dir[offset+32] = 1; root_dir[offset+33] = 1
    sectors.append(root_dir)
    with open(iso_path, 'wb') as f:
        for sector in sectors: f.write(sector)
    print(f"Successfully created {iso_path}")

if __name__ == "__main__":
    print("Building Bootloader...")
    boot_bin = build_bootloader()
    print("Building Kernel...")
    kernel_bin = build_kernel()
    print(f"Kernel Size: {len(kernel_bin)} bytes")
    print("Creating Floppy Image...")
    create_floppy_img(boot_bin, kernel_bin, "floppy.img")
    print("Creating ISO...")
    create_iso("floppy.img", "os.iso")
