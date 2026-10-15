# Trimtok 2.0 — Phase 4 Summary (v0.10.0)

**Stateful Command Delta Engine (Cross-Turn)** · วันที่ 2026-10-15 · สถานะ: เสร็จ + deploy แล้ว (เฟสสุดท้ายของแผน Argon)

## เป้าหมาย (ตามแผน)
- คำสั่งเดิมรันซ้ำข้ามเทิร์น (pytest / git status / git diff) → ถ้า output ซ้ำเดิม ≥ 65% ส่งเฉพาะส่วนต่าง
  **Added / Removed / STILL FAILING**
- เป้า: loop เทสต์/ดีบั๊กลด token ≥ 82% · cross-turn latency < 1.2 ms
- Canary (Regression Blindness): เทสต์พังเทิร์น 1 ยังพังเทิร์น 2 ⇒ error ต้องอยู่ใน STILL FAILING เสมอ ห้ามหาย

## สิ่งที่ทำ
1. **`delta_engine.py` (ใหม่)** — `StatefulDeltaEngine` ต่อ (session, command):
   - snapshot เก็บ raw text + lines + fault-lines (คำนวณ fault ครั้งเดียวตอน ingest), LRU + cap ต่อ command/session (32 cmds / 6,000 บรรทัด / 400KB ต่อ cmd)
   - เทียบ set บรรทัด: `sim = overlap/len(current)`; ผ่าน threshold (default 0.65) → สร้าง delta view
   - รูปแบบ: `[tt:delta cmd="pytest -q" sim=97% +#2 -#1 !#1 #c8 #c7]` + legend + 3 หมวดเรียง faults-first
   - **STILL FAILING** = fault line (`is_important`) ที่อยู่ในทั้ง two รอบ — canary การันตี 100%
   - refs (cur/prev) materialize **ตอนยิงจริงเท่านั้น** — เทิร์นไม่ยิงไม่แตะ store; fallback เป็น hex digest ได้
   - เคลียร์ state ตาม session (ต่อเข้ากับสนาม on_session_start/end เดิม)
2. **Wiring**: `core.py` รับ `delta_enabled`/`delta_threshold`/`session_id` (opt-in — pure callers เดิมไม่เปลี่ยน) · `__init__.py` hook เปิดใช้จาก config + ส่ง session · `marker.py` เพิ่ม `[tt:delta]` + legend + sentinel · `plugin.yaml` config ใหม่ 2 ตัว (เปิด default, threshold 0.65 + kill-criteria note)
3. **Fault rails เร็วขึ้น**: `polarity.is_fault_line` memoize ด้วย LRU (16,384 entries, บรรทัด ≤240 chars) — เพราะทุกเทิร์นต้องสแกน line ซ้ำ
4. **เทสต์**: `tests/test_v2_phase4.py` 28 checks (canary / threshold / caps / chain / isolation / integration / determinism / latency) · stress `D16` (loop 5 เทิร์น) · runtime test เพิ่ม delta live regression 2 โหมด

## ตัวเลขวัดจริง (ก่อน → หลัง)
| รายการ | ก่อน (0.9.0) | หลัง (0.10.0) |
|---|---|---|
| ชุดทดสอบรวม (7 suites + stress) | 246 checks | **290 PASS / 0 FAIL** (phase4 +28) |
| D16 loop 5 เทิร์น (pytest ซ้ำ 50KB×5) | fold-only ลด 99.0% | **delta ลด 98.5%** (210,725→3,136 chars) ≥82% ✓ |
| Re-run เดิม (ท้าย loop) | — | **452 chars** (saved 41,693 chars) + STILL FAILING ครบ |
| SIM 5-turn | 62.3% | 62.3% (ไม่ถอย) |
| Latency delta-step 200 บรรทัด | — | **0.61 ms** median → ผ่านเป้า 1.2 ms ✓ |
| Latency delta-step 2000 บรรทัด | — | ~5.2 ms steady (เป้า 1.2 ms ✗) — ดูบันทึกตรง |
| Fault scan 2000 บรรทัด | ~10.4 ms ทุกครั้ง | **1.3 ms** (warm; cold แรก ~10 ms) |
| Deploy | 12 ไฟล์ | **13 ไฟล์ byte-identical** + doctor 0.10.0 (1 tool, 5 hooks) |

## จุดบันทึกตรง
- **Latency 1.2 ms**: ผ่านที่ 200 บรรทัด (0.61 ms); 2000 บรรทัด ~5 ms steady — ต้นทุนหลัก = CCR puts ตอนยิง (เพื่อให้ refs resolve ได้จริง) + set ops; cold แรกจ่าย fault scan ~10 ms ครั้งเดียว
- **บั๊กที่เจอ+แก้ระหว่างทาง**: `_ref_for` เดิม import `ccr_v2` แบบ absolute → ใน packaged plugin สร้าง module instance ที่ 2 (sqlite connection ซ้อน → transient lock ทำ alias ตกเป็น hex + handle ค้าง) → เปลี่ยนเป็น dual-import ใช้ instance เดียวกับปลั๊กอิน + เพิ่ม assert ใน runtime test ว่า refs ต้องเป็น alias
- **D16 vs fold-only**: fold-only ลด 99.0% มากกว่า 0.5pp แต่ delta ให้ "โครง" Added/Removed/STILL FAILING ที่ LLM ใช้ต่อได้ทันที — เลือกตามเจตนาแผน
- **Determinism**: `compress_terminal` ยัง pure/byte-identical เมื่อไม่เปิด delta — เทสต์เดิม 100% ไม่แตะ
- **Kill-criteria (แผน)**: ถ้า retrieve เกิน ~15% ของเทิร์น → ปรับ `delta_threshold` 0.65 → 0.80 (มี config พร้อม)

## หลักฐาน
- commit: "feat(v2-phase4)" → push แล้วที่ Stxyu-p/trimtok
- deploy: `plugins/trimtok` v0.10.0 (13 ไฟล์ byte-identical), doctor OK
- runtime: workspace + deployed PASS ×5 (compressed+retrieve / **delta+canary+alias** / tool / lifecycle / CCR isolation)
- stress: D16 rows, SIM TOTAL 62.3%, ALL CASES 88.1% (3,559,894→422,408 chars ~784k tokens saved)

**หมายเหตุ: แผน Argon ครบทั้ง 4 เฟสแล้ว** (0.7.0 fail-open+CCR+alias → 0.8.0 fault rails+scoped search → 0.9.0 TOON → 0.10.0 delta engine)
