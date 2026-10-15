# สรุปรายงานผลการ Refactor Trimtok 2.0 — Phase 2 (รอบที่ 3)

**วันที่ดำเนินการ:** 15 ตุลาคม 2026  
**ผู้ดำเนินการ:** MIKA (Executive Orchestration)  
**เวอร์ชัน:** v0.7.0 → **v0.8.0** (deploy ขึ้น live plugin แล้ว)  
**เป้าหมาย (ตามแผน Argon 4 Phase 2):** Scoped Search Grouping + Fault-Polarity Rails ขั้นลึก (ปิด canary ทั้ง 2 จุดที่สอบซ้ำแล้วว่า RED)  
**Baseline อ้างอิง:** `trimtok/tests/BASELINE_0.6.2.md` + ตัวเลข Phase 1 (197 checks, SIM 61.2%)

---

## 1. สิ่งที่ลงมือพัฒนาจริง (Completed Actions)

### Step 1: Fault-Polarity Rails รวมศูนย์ — `polarity.py` (ใหม่)
- เดิม core.py และ marker.py ต่างคนต่างถือสำเนา regex error ของตัวเอง (เสี่ยง drift) → ย้ายเป็น **single source** ที่ `polarity.py`; ชื่อ `is_important` ที่ทุก fold ใช้ยังอยู่ที่ marker.py ตามเดิม (core นำเข้า ไม่ถือสำเนา)
- **ปิด canary gap #1 (พิสูจน์แล้วว่า RED ก่อนแก้):** `status=500` ไม่ถูกจับเลย → เพิ่ม rails 3 รูปแบบ: `status=500 / status_code:503`, `HTTP/1.1 502`, `bad gateway`

### Step 2: Fault ต้องไม่ถูกกลืนใน Template Fold — `dedup.py`
- **ปิด canary gap #2:** เคส 5 บรรทัด template เดียวกัน (`item N status=NNN request delta`) ที่มี `status=500` คั่นอยู่ — เดิม 500 ถูกพับกลืนหายไปกับกลุ่ม 200
- แก้: `_fold_intra_templates` ข้ามบรรทัด fault เสมอ (`is_important` skip) → **500 ปรากฏ 100%** ขณะที่บรรทัด 200 ยังพับได้ปกติ (net save ยังเป็นบวก — มีเทสต์ยืนยันทั้งคู่)

### Step 3: Scoped Search Grouping — `scoped_search.py` (ใหม่)
- ผลค้นหา/เกรปหลายไฟล์ → จัดกลุ่มต่อไฟล์: `=== src/mod0.py (100 hits) [defs: handler_0_0@L7 …+98] ===`
- เก็บ first/last + **บรรทัด fault ทุกตัว** ไว้กับตา; ตรงกลางพับเป็น `[@#refxN]` (resolve ได้ทั้ง alias และ sha เดิม)
- **Single-file doc ไม่แตะ** — ปล่อยให้ template fold เดิมจัดการ (นี่คือทางที่ล็อก SIM 61.2% ไว้)
- Grouped doc เป็น **final** — ข้าม fold ทั่วไปที่เหลือ (กัน template fold ช่วงหลังกลืน header กลับเป็น pointer เดียว); `remember()` ยัง index ข้อความดิบ → cross-turn fold + retrieval ทำงานครบ
- กันพลาด: min 12 บรรทัด / ≥10 hits / ≥2 ไฟล์ / hit-density ≥60% / net-save guard / never-raise

### Step 4: เทสต์ + Deploy
- `tests/test_v2_phase2.py` ใหม่ **26 checks** (polarity 7, canary 3, grouping 9, integration 6, SIM-pin 2)
- stress เพิ่มเคส **D14** (grep 8 ไฟล์ × 100 hits) และ **D14b** (canary ฝังกลางกอง)
- Deploy `plugins/trimtok` v0.8.0 (11 ไฟล์ byte-identical) + `hermes plugins doctor` OK (1 tool + 5 hooks)
- Runtime integration test: **workspace + deployed ผ่านทั้งคู่**

---

## 2. ตารางเทียบตัวเลขวัดจริง ก่อนทำ vs หลังทำ

| ตัวชี้วัด | ก่อน (v0.7.0) | หลัง (v0.8.0) | ผลลัพธ์ |
|---|---|---|---|
| **ชุดทดสอบ** | 197 checks / 0 failures | **223 checks / 0 failures** (+26) | ไร้ Regression |
| **SIM 5-turn (stress)** | 61.2% (41,066 chars) | **61.2% (41,066 chars)** | **ล็อกค่าเดิมเป๊ะ** (เกณฑ์แผน ≥61.2%: ผ่าน) |
| **ALL cases (stress, +2 เคสใหม่)** | 89.5% | **89.7%** (fails=0) | คงเดิม/ดีขึ้นเล็กน้อย |
| **D14: grep 800 hits / 8 ไฟล์** | (ไม่มีเคส) | **42,783 → 1,639 chars (−96.2%)** | ใหม่ — ได้ file headers + defs + pointer |
| **D14b: เดียวกัน + canary status=500** | (ไม่มีเคส) | **42,824 → 1,700 (−96.0%) + canary ปรากฏ** | ใหม่ — fault ไม่หาย |
| **Canary unit (template ผสม fault)** | 500 ถูกกลืน (RED) | **500 ปรากฏ 100% + ยังพับ 200 ได้** | ปิด gap #2 |
| **Rails: status=5xx / HTTP 5xx / bad gateway** | ไม่จับ (RED) | **จับครบทั้ง 3 รูปแบบ** | ปิด gap #1 |
| **CCR v2 put / get (bench)** | 0.26 / 0.007 ms | **0.207 / 0.014 ms** | ระดับเดิม ไม่ถดถอย (put ผ่านเป้า <0.25 ms รอบนี้) |
| **Benchmark ทั้งชุด** | ผ่านทุก assertion | **ผ่านทุก assertion** (compress_terminal median 0.657 ms) | ไม่ถดถอย |
| **Runtime integration (Hermes จริง)** | ผ่าน (2 ที่) | **ผ่าน (2 ที่: workspace & deployed)** | terminal hook 32,899→210; tool 3,080→2,146; retrieve 100% |
| **doctor** | 0.7.0 OK | **0.8.0 OK (1 tool + 5 hooks)** | — |

---

## 3. ไฟล์ที่แก้ไข / เพิ่ม

**เพิ่มใหม่:** `polarity.py` (single source of rails), `scoped_search.py` (grouping engine), `tests/test_v2_phase2.py` (26 checks)  
**แก้ไข:** `marker.py` (delegate rails → polarity), `core.py` (ถอดสำเนา rails + VERSION 0.8.0), `dedup.py` (skip fault ใน templates + ต่อสาย grouping), `plugin.yaml` (v0.8.0 + description ตรงจริง), `tests/stress_heavy.py` (+D14/D14b)  
**Deploy:** `AppData/Local/hermes/plugins/trimtok/` (11 ไฟล์ตรงกัน 100% + doctor ผ่าน)

---

## 4. หมายเหตุสำคัญ

- **เทียบ % การพับกับ path เก่า:** path เก่าพับ grep 8 ไฟล์ได้ 99.7% เพราะยุบ **ทุกไฟล์เป็น pointer เดียว** (ไม่เห็นชื่อไฟล์เลย); ทางใหม่ 96.2% แต่ได้โครงสร้างครบ (ชื่อไฟล์ + จำนวน + defs) — เป็นการแลกที่ตั้งใจตามเจตนา Phase 2
- **เบี่ยงจากแผนเล็กน้อย (บันทึกตรง 2 จุด):** (1) ไม่ใช้ `is_exempt` เป็น keep-rule ใน scoped (เลขบรรทัดใน hit list คือ metadata ไม่ใช่ content — ไม่งั้นทุกบรรทัดถูก exempt แล้วไม่พับเลย พบตอนเทสต์รอบแรก) (2) grouped doc เป็น final ไม่ผ่าน fold ถัดไป (กัน header ถูกกลืน — พบตอนตรวจ output จริงเช่นกัน)
- live session ปัจจุบันยังใช้โค้ดเก่าในหน่วยความจำ — โค้ด 0.8.0 มีผลเมื่อเปิด session ถัดไป
- เกณฑ์ผ่านตามแผนครบ: SIM ไม่ถดถอย ✓ / canary ห้าม Error หาย ✓ / stdlib-only ไม่เพิ่ม dependency ✓ / fail-open (gate=ok ทุกเคส stress) ✓

## 5. ค้างไว้สำหรับรอบถัดไป (Phase 3)

- **TOON Encoder** — ลด token เพิ่ม 65–80% กับ structured payload (JSON/ตาราง)
- **Token-Gain Gate** — พับเฉพาะเมื่อประหยัด BPE จริง ≥ 4 tokens (ใช้ `bpe_estimator.py`)
- Polarity-Locked ต่อยอดใน TOON (KEEP rules / canary ชุดเดิมต้องยังผ่าน)
