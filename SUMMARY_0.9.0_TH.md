# สรุปรายงานผลการ Refactor Trimtok 2.0 — Phase 3 (รอบที่ 4)

**วันที่ดำเนินการ:** 15 ตุลาคม 2026  
**ผู้ดำเนินการ:** MIKA (Executive Orchestration)  
**เวอร์ชัน:** v0.8.0 → **v0.9.0** (deploy ขึ้น live plugin แล้ว)  
**เป้าหมาย (ตามแผน Argon 4 Phase 3):** Lossless JSON-to-TOON Transpiler + Token-Gain Gate (พับเมื่อประหยัดจริง ≥ 4 BPE tokens)  
**Baseline อ้างอิง:** ตัวเลข Phase 2 (223 checks, SIM 61.2%, D14)

---

## 1. สิ่งที่ลงมือพัฒนาจริง (Completed Actions)

### Step 1: Lossless JSON→TOON Columnar Codec — `toon_codec.py` (ใหม่)
- แปลง JSON ทั้งก้อนที่โครงสร้างซ้ำซากเป็นตารางคอลัมน์: **bare array** หรือ **dict ที่ห่อ array ไว้** (เช่น `{"total":...,"results":[...]}`) → `@meta:{...}` + `@results[N]{f1,f2}:` + แถวข้อมูล
- **Path-prefix dictionary:** สกัด prefix เส้นทางร่วม (เช่น `C:\work\proj\src\`) ขึ้นหัวตารางเป็น `@prefix(~/)=...`, แถวใช้ `~/mod0123.py` — ทุกค่าอยู่ครบ ไม่มีการตัดเนื้อหา
- **Lossless เป็นเงื่อนไขบังคับ:** ก่อนใช้ผลลัพธ์ต้องผ่าน **round-trip decode แบบ type-strict** (`toon_decode` แล้วเทียบชนิด+ค่าทุกตัว) — ไม่ผ่าน = คืนข้อความเดิมทันที
- Quoting อัตโนมัติเมื่อกำกวม (`|`, ขึ้นบรรทัดใหม่, สตริงหน้าตาเลข, `~` , ช่องว่างหัวท้าย, สตริงว่าง) — ภาษาไทย/เครื่องหมายคำพูดรอบทดสอบผ่านทั้งหมด
- **v1 ตั้งใจแคบตาม kill-criteria ของแผน:** เฉพาะ flat + uniform keys (nested/ไม่สม่ำเสมอ → ปล่อยผ่านให้ minify ตามเดิม) — ขยายต่อได้เมื่อมี round-trip gate ตัวเดิม

### Step 2: Token-Gain Gate (≥ 4 BPE tokens)
- พับเฉพาะเมื่อ `estimate_bpe_gain` (หน่วย BPE จริงจาก `bpe_estimator.py`) ≥ 4 tokens **สุทธิรวม legend** และไบต์เล็กลงจริง — ไม่งั้นคืนข้อความเดิม byte-identical
- ตรวจทับ + กันพลาด: ≥3 แถว, ≤24 ฟิลด์, ฟิลด์ชื่อปลอดภัย, net-save guard, never-raise

### Step 3: ต่อสายทั้งระบบ
- `dedup.py`: แทน TOON path เก่าด้วย codec ใหม่ (ลบ `toon_encode` legacy ทิ้ง — เหลือทางเดียว) โดยรัน "หลัง fold" เป็น final step เหมือนเดิม
- `plugin.yaml`: `toon_enabled` default **true** (ปิดได้ผ่าน config) + description อัปเดต; `VERSION` → 0.9.0
- Legend บรรยายรูปแบบใหม่ให้ LLM อ่านตารางได้ทันที (ย่อให้สั้นลงเพื่อประหยัดโทเคน)

### Step 4: เทสต์ + Deploy
- `tests/test_v2_phase3.py` ใหม่ **22 checks**: รูปแบบ/prefix/meta, refusal cases, **Q&A Equivalence 50 เคส** (round-trip type-strict 100%), canary status=500, gate, integration default-on/off, latency
- Fuzz เพิ่มระหว่างพัฒนา: **200/200 เอกสารสุ่ม round-trip ผ่านเป๊ะ** (รวมสตริงพิษ `|`/quote/`\n`/ไทย/เลขปลอม)
- stress เพิ่ม **D15** (JSON 1,000 แถว + canary ซ่อน 999999/500 + เช็ค `@prefix`) และอัปเดต D2
- Deploy `plugins/trimtok` v0.9.0 (12 ไฟล์ byte-identical) + doctor OK + **runtime PASS ทั้ง workspace และ deployed**

---

## 2. ตารางเทียบตัวเลขวัดจริง ก่อนทำ vs หลังทำ

| ตัวชี้วัด | ก่อน (v0.8.0) | หลัง (v0.9.0) | ผลลัพธ์ |
|---|---|---|---|
| **ชุดทดสอบ** | 223 checks / 0 failures | **246 checks / 0 failures** (+22 phase3, +1 dedup) | ไร้ Regression |
| **SIM 5-turn (stress)** | 61.2% (41,066 chars) | **62.3% (39,904 chars)** | **ดีขึ้น 1.1pp** (เกณฑ์ ≥61.2%: ผ่าน) |
| **SIM turn "json-toon"** | (legacy) 5,369 chars | **4,207 chars (65.2%)** | −1,162 chars |
| **D2: JSON 500 แถว (on vs off)** | (approx. 55% ตัวคั่นเดิม) | **on 65.4%** (20,280→7,008) vs off 14.6% | ชนะ minify 4.5 เท่า |
| **D15: JSON 1,000 แถว (ใหม่)** | — | **off 29.4% → on 74.3%** (101,885→26,147) | **toon_saved 45,785 chars** |
| **Canary D15 (999999/500 + @prefix)** | — | **ปรากฏครบ 100%** | Zero-Loss ยืนยัน |
| **Q&A Equivalence 50 เคส + fuzz 200** | — | **50/50 + 200/200 (type-strict)** | ไม่ลดความฉลาด |
| **Latency 138 KB JSON** | — | **median ~21 ms (min 15.3); quiet ~14 ms** | ⚠️ **ไม่ผ่านเป้า 3.0ms ของแผน** — บันทึกตรง (เหตุผลข้อ 4) |
| **Benchmark first-seen pretty JSON** | 3.24 ms | **5.19 ms** (budget ปรับ 10→25 ms โดยเจตนา) | ยังห่างเพดาน guard 50 ms |
| **CCR v2 put / get (bench)** | 0.26 / 0.007 ms | **0.164 / 0.008 ms** | ไม่ถดถอย |
| **ALL cases (stress)** | เคสเดิม 89.7% | **เคสเดิม 89.8% / รวมเคสใหม่ 87.5%** (fails=0) | เคสเดิมดีขึ้น |
| **Runtime integration (Hermes จริง)** | ผ่าน (2 ที่) | **ผ่าน (2 ที่: workspace & deployed)** | — |
| **doctor** | 0.8.0 OK | **0.9.0 OK (1 tool + 5 hooks)** | — |

---

## 3. ไฟล์ที่แก้ไข / เพิ่ม

**เพิ่มใหม่:** `toon_codec.py` (codec + decode + round-trip gate), `tests/test_v2_phase3.py` (22 checks)  
**แก้ไข:** `dedup.py` (ลบ legacy toon_encode + ต่อ codec ใหม่ + default on), `marker.py` (legend TOON ใหม่), `__init__.py` (config default true), `core.py` (VERSION 0.9.0), `plugin.yaml` (0.9.0 + toon_enabled default true + description), `tests/test_dedup.py` (rewired section 11), `tests/stress_heavy.py` (+D15, D2 update), `tests/test_benchmark.py` (budget first-seen 10→25 ms พร้อมคอมเมนต์เหตุผล)  
**Deploy:** `AppData/Local/hermes/plugins/trimtok/` (12 ไฟล์ตรงกัน 100% + doctor ผ่าน)

---

## 4. หมายเหตุสำคัญ (บันทึกตรงทุกจุด)

- **ตัวคั่นแถวใช้ `|` แนบ (ไม่มีช่องว่าง)** แทน `" | "` ในแผน — เหตุผล: ตัวคั่นเดิมกิน char เกินจน SIM ตกลง 0.1pp; แบบแนบปลอดภัยเท่าเดิม (ทุกค่าที่มี `|` ถูก quote เสมอ จึงแยกขอบเขตได้ไม่กำกวม) — ผลพลอยได้: SIM ขึ้นเป็น 62.3%
- **Latency เป้า 3.0 ms / 100 KB: ไม่ผ่าน** — วัดได้ median ~21 ms (min 15) ที่ 138 KB; เหตุผลเชิงวิศวกรรม: `json.loads` อย่างเดียว ~2 ms + round-trip verify เต็มรูป (ราคาของ Zero-Loss) บน pure-Python — ยังต่ำกว่าเพดาน fail-open guard 50 ms และเครื่องนี้แกว่งตามโหลด ~4 เท่า (บันทึกเป็น documented miss; ทางต่อยอด: native/C accelerator)
- **v1 แคบตั้งใจ:** flat + uniform เท่านั้นตาม kill-criteria ในแผน (nested/non-uniform → ไม่พับ; ยังได้ minify ตามปกติ)
- **Benchmark budget first-seen: 10 → 25 ms** โดยเจตนา เพราะ default-on TOON เพิ่ม parse+verify (5.19 ms วัดจริง ยังห่าง budget 3 เท่า)
- live session ปัจจุบันยังใช้โค้ดเก่าในหน่วยความจำ — โค้ด 0.9.0 มีผลเมื่อเปิด session ถัดไป

## 5. ค้างไว้สำหรับรอบถัดไป (Phase 4)

- **Stateful Command Delta Engine** — เทียบ output คำสั่งซ้ำข้ามเทิร์น (pytest/git status) คง `ADDED`/`REMOVED`/`STILL FAILING` เป้า: ลดโทเคน loop เทสต์/ดีบั๊ก 82–92%
- ขยาย TOON ต่อยอดได้ (nested 1-level, array of scalars) ด้วย round-trip gate เดิม — เมื่อมีข้อมูลใช้งานจริงชี้ว่าคุ้ม
