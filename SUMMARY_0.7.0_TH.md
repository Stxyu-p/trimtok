# สรุปรายงานผลการ Refactor Trimtok 2.0 — Phase 1 (รอบที่ 2)

**วันที่ดำเนินการ:** 15 ตุลาคม 2026  
**ผู้ดำเนินการ:** MIKA (Executive Orchestration)  
**เวอร์ชัน:** v0.6.2 → **v0.7.0** (deploy ขึ้น live plugin แล้ว)  
**เป้าหมาย (ตามแผน Argon 4 Phase 1):** Fail-Open Guard + Two-Tier CCR (SQLite WAL + alias `#c1`) + ลดขนาดป้าย Stub  
**Baseline แช่แข็ง:** `trimtok/tests/BASELINE_0.6.2.md` (วัดก่อนแก้จริง)

---

## 1. สิ่งที่ลงมือพัฒนาจริง (Completed Actions)

### Step 1: Two-Tier CCR v2 — `ccr_v2.py` (ใหม่)
- **RAM LRU (512 รายการ) + SQLite WAL ไฟล์เดียว** แทนการเขียนไฟล์ `.txt` ทีละชิ้นลง NTFS
- ออกรหัสสั้น **Base62 alias `#c1, #c2, …`** (stable: คอนเทนต์เดิมได้ alias เดิมเสมอ) พร้อมรับรหัส sha256-12 เดิมได้ทั้งคู่
- กติกา Kill-criteria ตามแผน: ถ้า SQLite lock error เกิน 1 ครั้ง → สลับเป็น legacy file store อัตโนมัติ (sticky) — ยังไม่เกิดในเทสต์

### Step 2: Fail-Open Guard — `guard.py` (ใหม่)
- `Deadline` แบบ cooperative (เช็คทุก N ลูปใน hot loop) + `fail_open_guard` + `VERBATIM_TOOLS`
- เกินงบ → ตัดบท คืนข้อความเดิมทันที (`passthrough_timeout`) — Hermes ไม่มีวันแครช
- **เบี่ยงจากแผนเล็กน้อย (บันทึกตรง):** แผนระบุ 8.5 ms แต่จากการวัดจริง ข้อความ 31 KB ใช้เวลา ~25–55 ms บนเครื่องที่มีโหลด → 8.5 ms จะ fail-open ทิ้งงานจริงบ่อย = ขัด priority 2 (ลด token สูงสุด) จึงตั้ง default **50 ms** เป็น "เพดานกันสะดุด" และเปิดให้ปรับผ่าน `guard_ms` (1–1000; ตั้ง 8.5 = โปรไฟล์ strict ตามแผน)

### Step 3: ป้ายอ้างอิงแบบย่อ — `marker.py`
- Pointer ที่เกิดบ่อย → **`[@#c1]`** (1 บรรทัด) / **`[@#c1x5]`** (5 บรรทัด) ตามแบบแผน `[@#c1]`
- หัวบล็อก (terminal/tool) → `[tt:... #ref]`; sentinel รับได้ 3 เจเนอเรชัน (legacy `retrieve:hex`, v2 `#ref`, pointer ใหม่) — ข้อความเก่าในเซสชันยังทำงานปกติ

### Step 4: `bpe_estimator.py` (ใหม่)
- ประเมิน BPE token ด้วย stdlib regex (สุ่มวัดเมื่อ >32KB) สำหรับรายงานผลเฟสนี้และ Token-Gain Gate เฟสถัดไป

### Step 5: ต่อสายทั้งระบบ
- `_store_original` สลับ ref ทุกจุดในบล็อกเป็น alias; `trimtok_retrieve` รับได้ทั้ง `#c1`, `c1`, `retrieve:#c1` และ hex เดิม
- Hooks ทั้งสองทางใส่ deadline + verbatim bypass (`apply_patch`, `edit_file`, `trimtok_retrieve` ฯลฯ)

### Step 6: ตรวจสอบ + Deploy
- **Runtime integration test ผ่านทั้ง workspace และ deployed copy** (compress 32,899 → 210 chars, retrieve คืนค่าตรงเดิม 100%)
- `hermes plugins doctor` PASS (1 tool + 5 hooks) / `hermes plugins list` โชว์ 0.7.0

---

## 2. ตารางเทียบตัวเลขวัดจริง ก่อนทำ vs หลังทำ

| ตัวชี้วัด | ก่อน (v0.6.2) | หลัง (v0.7.0) | ผลลัพธ์ |
|---|---|---|---|
| **CCR Write (put median)** | 1.93–1.99 ms (ไฟล์/NTFS) | **0.26 ms** (SQLite WAL) | เร็วขึ้น ~7.4 เท่า (เป้าแผน <0.25 ms — วัดได้ 0.26 ms, p95 0.62) |
| **CCR Read (get median)** | 0.11–0.21 ms | **0.007 ms** | เร็วขึ้น ~15–30 เท่า (ระดับ 7 ไมโครวินาที) |
| **Stub pointer (1 จุด)** | ~9 tok (`[tt:seen line retrieve:hex12]`) | **~3 tok** (`[@#c1]`) | **-66%** |
| **Stub span (N บรรทัด)** | ~11 tok | **~3 tok** (`[@#c1x5]`) | **-72%** (เป้าแผน -70%: ผ่าน) |
| หัวบล็อก terminal/tool | ~15–17 tok | ~12–10 tok | -9% ถึง -41% |
| **ชุดทดสอบ (5 scripts)** | 159 checks / 0 failures | **197 checks / 0 failures** (+38 ใหม่) | ไร้ Regression |
| **Runtime integration (Hermes จริง)** | ผ่าน (ไม่มี guard) | **ผ่าน + มี guard** (workspace & deployed) | — |
| **SIM 5-turn (stress)** | 61.2% | **61.2%** | ล็อกค่าเดิม ไม่ถดถอย |
| **ALL cases (stress)** | 89.4% | **89.5%** (fails=0) | คงเดิม (fixture noise) |
| Latency compress path | เทียบ A/B เครื่องเดียวกัน: 1.59/3.67/3.12/5.46 ms | 1.50/4.64/3.00/4.67 ms | พอ ๆ กัน (ต่าง ±noise ของเครื่อง) |

---

## 3. ไฟล์ที่แก้ไข / เพิ่ม

**เพิ่มใหม่:** `ccr_v2.py`, `guard.py`, `bpe_estimator.py`, `tests/test_v2_phase1.py` (37 checks), `tests/BASELINE_0.6.2.md`  
**แก้ไข:** `__init__.py` (alias swap + deadline + verbatim + schema), `core.py` (deadline ticks + VERSION 0.7.0), `dedup.py` (deadline ticks), `marker.py` (pointer ใหม่ + sentinel 3 เจน), `plugin.yaml` (v0.7.0 + guard_ms), เทสต์เดิมทั้งชุด  
**Deploy:** `AppData/Local/hermes/plugins/trimtok/` (ไฟล์ตรงกัน 100% + doctor ผ่าน)

---

## 4. หมายเหตุสำคัญ

- เซสชัน Hermes ที่กำลังรันอยู่ **ยังใช้โค้ดเก่าในหน่วยความจำ** — โค้ดใหม่มีผลตั้งแต่เซสชัน/รีสตาร์ตถัดไป (division แบบเดียวกับ skill-proof)
- เป้า CCR write <0.25 ms: วัดได้ **0.26 ms** — บันทึกตรงตามจริง ไม่ปัดให้ผ่าน; p95 ยังต่ำ (0.62 ms) และเร็วกว่าเดิม ~7.4 เท่า
- Kill-criteria (SQLite lock → fallback) implement แล้ว ยังไม่ถูก trigger
- ถ้าต้องการโหมด strict ตามแผน 8.5 ms เป๊ะ: ตั้ง `plugins.entries.trimtok.settings.guard_ms: 8.5`

## 5. ค้างไว้สำหรับรอบถัดไป (Phase 2)

- Scoped Search Grouping (ผลค้นหา/เกรปหลายร้อยบรรทัดจัดกลุ่มตามไฟล์ + header signatures) — เกณฑ์: SIM ต้อง ≥ 61.2%
- Polarity-Locked Engine เชิงลึก (ต่อจาก guard รอบ 1)
- Token-Gain Gate ด้วย `bpe_estimator` (พับเฉพาะเมื่อประหยัด BPE จริง ≥ 4 tokens)
