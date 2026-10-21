# Trimtok v4.2.2 — แก้ lane `read_file` ให้ทำงานจริงใน Hermes (ภาษาไทย)

วันที่: 2026-10-21 · ผู้ทำ: MIKA · ผู้รีวิว/ออกแบบ: Argon 4 (Thinking: Max)
รีวิวเต็ม: `TRIMTOK_READFILE_LANE_ARGON_REVIEW.md` (44,658 chars) · diff สำรอง: `trimtok_v422_readfile_lane.diff`
สถานะ repo: แก้ใน working tree ยังไม่ commit (`trimtok/{__init__,dedup,pruners,signals}.py` + test ใหม่)

---

## 1. ปัญหา: lane `read_file` ไม่เคยทำงานเลยใน Hermes

จาก telemetry จริง: `read_file` 1,078 ครั้ง / 5.83M chars → **ประหยัด 0** (v4.1 era) · v4.2 era 57 ครั้ง / 359,591 chars → **ยัง 0** · วันนี้ 16 ครั้ง / 134,595 chars → **0**

Argon วิเคราะห์โค้ดลึกแล้วเจอ **5 จุดที่ทำให้ lane นี้ตายพร้อมกัน** (ตัวแรกคือที่หนูเจอเอง):

| # | จุดตาย | พิกัด | ผลกระทบ |
|---|---|---|---|
| 1 | Rule 0 ตีกลับ default ของ Hermes (`offset=1, limit=2000` ทุก call) | `dedup.py:801` | ทุก call จบที่ passthrough |
| 2 | payload รูปแบบ `LINE|CONTENT` ทำให้ `ast.parse()` พัง / outline มองไม่เห็น / เลขบรรทัดเลื่อนทำให้ diff พัง | `pruners.py:479`, `dedup.py:726,747,774` | 3 tier พังหมด |
| 3 | `patch` สั่ง `.pop(mut_path)` ทิ้ง cache | `dedup.py:944` | Tier 2 (file_delta) เป็นไปไม่ได้ในโลกจริง |
| 4 | `expire_old_turns(keep_turns=2)` ฆ่า cache ไฟล์เร็วเกินจริง | `__init__.py:476` | agent loop หลาย step อ่านไฟล์เดิมแล้วไม่เจอ cache |
| 5 | `read_file_extra` ถูกตัดออกเพราะเช็กชื่อ tool แบบตายตัว | `dedup.py:947` | 172,180 chars ไม่เคยถูกบีบ |

## 2. สิ่งที่แก้ (4 ไฟล์, +311/-126)

- **`trimtok/dedup.py`**
  - `_unwrap_numbered_lines()` — แกะ prefix `LINE|` ออกเป็น raw source + เก็บ prefix/เลขบรรทัดเดิมไว้
  - `_is_targeted_slice(offset, limit, first_line_no, n_lines)` — แยก "อ่านทั้งไฟล์" กับ "อ่านช่วง" **โดยดูจาก payload + args ไม่ต้องเปิด filesystem** (offset>1 / first_line_no>1 / limit<500 และตัดจริง = targeted)
  - `_handle_read_file_v42()` เขียนใหม่: unwrap → Rule 0 ใหม่ → Case A (outline stub) → Case B (contextual delta) → Case C (AST skeleton พร้อมเลขบรรทัดจริง) + **anti-loop rail** (ถ้าเคยได้ stub/skeleton แล้วอ่านซ้ำ → คืนไฟล์เต็ม verbatim ทันที กัน agent วนกับ stub)
  - `patch` ไม่ลบ cache แล้ว (แต่ทำเครื่องหมาย `mutated`) → file_delta กลับมาได้
  - `expire_old_turns` → cache ไฟล์อยู่ได้ 8 turns (แทน 2) ตามจริงของ agent loop
  - `_READ_FILE_TOOLS` รวม `read_file_extra`
  - `_tok_pair()` — ประหยัดเวลา: ใช้สัดส่วนตัวอักษรแทน BPE pass เมื่อผลลัพธ์สั้นลงชัดเจน (BPE จริงใช้เมื่อขนาดใกล้เคียงกัน)
  - fail-open: ถ้า guard budget หมด → ส่งไฟล์ verbatim แทนที่จะเกิน deadline
- **`trimtok/pruners.py`** — `smart_skeletonize_code()` รับ `line_prefixes`/`line_nos` แล้วติด prefix เดิมกลับทุกบรรทัดที่เก็บไว้ + marker ชี้เลขบรรทัดจริงของไฟล์
- **`trimtok/signals.py` / `__init__.py`** — `read_file_extra` ไม่ถูกมองเป็น verbatim-only และรับ args จาก `**kw` (tool_args/arguments/input) ด้วย

## 3. หลักฐาน (รันจริง)

| รายการ | ผล |
|---|---|
| test ใหม่ `tests/test_v422_hermes_read_lane.py` | **4 passed** (จำลอง Hermes จริง: offset=1/limit=2000 + LINE\|CONTENT + patch คั่น) |
| test suite ทั้งหมด | **31 passed in 8.18s** (27 เดิม + 4 ใหม่) |
| benchmark band 3 (read_file cold reads) | 240,202 → 115,155 chars = **ประหยัด 52.1%** (ก่อนแก้: passthrough 100%) |
| acceptance gates | **RESULT: PASS — all applicable gates met** |
| latency band 3 | p50 6.49 ms · p95 74.83 ms (ไฟล์ 43KB; fail-open เป็น verbatim ถ้าเกินงบ 50 ms) |
| ตัวอย่างจริง payload 999 chars | passthrough verbatim ถูกต้อง (เล็กกว่า min_chars → patch safety) |

**ผลกระทบคาดการณ์** (บนปริมาณ read_file แบบเดิม 5.83M chars): ประหยัดเพิ่ม ~3.0M chars ≈ **~760,000 tokens**; วันนี้เฉพาะวันนี้ 134,595 chars → ~70k chars ≈ ~17,500 tokens

## 4. ต้องรู้ / ยังไม่ได้ทำ

1. **ต้อง restart Hermes 1 ครั้ง** ให้โค้ดใหม่มีผลกับ plugin ที่กำลังรัน (ตอนนี้ process เก่ายังถือโค้ดเก่าใน memory) — telemetry จึงยังไม่เปลี่ยน
2. **ยังไม่ commit** (รอคำสั่งพี่โชค) · สำรอง diff ไว้ที่ `trimtok_v422_readfile_lane.diff`
3. **Lane อื่นที่ Argon ยังไม่แนะนำให้ทำ**: `skill_view` (151,874 chars, 0%) → ทำได้แค่ dedupe ซ้ำในเซสชันเดียว (ห้ามตัด SKILL.md กลางไฟล์ เพราะเป็นคำสั่งขั้นตอน) · `patch` (1.23M chars, 0.7%) → ปล่อย payload เล็ก (<2KB) เพราะเป็นข้อความยืนยัน/Error ที่ต้องครบ
4. **Rollback**: เพิ่ม `"read_file","read_file_extra"` ใน `exclude_tools` ของ config → กลับเป็น verbatim 100% โดยไม่ต้องแก้โค้ด

## 5. ไฟล์ที่เกี่ยวข้อง

- รีวิว Argon: `TRIMTOK_READFILE_LANE_ARGON_REVIEW.md`
- ไฟล์ที่แก้: `trimtok/{dedup,pruners,__init__,signals}.py` + `tests/test_v422_hermes_read_lane.py`
- diff สำรอง: `trimtok_v422_readfile_lane.diff`
- benchmark: `.venv/Scripts/python tests/benchmark_v42.py`
