# Trimtok — บันทึกงาน/ผลวัดจริง (living changelog)

> ไฟล์นี้คือ "บันทึกเดียว" ของงาน trimtok — อัปเดตต่อท้าย/ต่อหัวไปเรื่อยๆ ไม่ต้องสร้างไฟล์รายงานใหม่ทุกครั้ง
> หลักฐานดิบ (รีวิว Argon แบบเต็ม, แผน, เทสต์ log) เก็บที่ `reports/raw/` — ลิงก์จากแต่ละหัวข้อ
> ไม่ใช่ release notes (ของพวกนั้นอยู่ที่ `CHANGELOG.md` ของรูท)

---

## 2026-10-21 — v4.2.2 · lane `read_file` ทำงานจริงใน Hermes แล้ว

**ปัญหา:** lane `read_file` **ไม่เคยทำงานเลย** ใน Hermes — `read_file` 1,078 ครั้ง / 5.83M chars → ประหยัด **0** (v4.1 era) · v4.2 era 57 ครั้ง / 359,591 chars → ยัง 0 · วันนี้ 16 ครั้ง / 134,595 chars → 0
สาเหตุ (Argon 4 deep review, 44,658 chars, verdict **[CONFIRMED ROOT CAUSE]** — เจอ 5 จุดที่ทำให้ lane ตายพร้อมกัน):

| # | จุดตาย | พิกัด |
|---|---|---|
| 1 | Rule 0 ตีกลับ default ของ Hermes (`offset=1, limit=2000` ทุก call) | `dedup.py:801` |
| 2 | payload `LINE\|CONTENT` ทำให้ `ast.parse()` พัง / outline มองไม่เห็น / เลขบรรทัดเลื่อนทำให้ diff พัง | `pruners.py:479` |
| 3 | `patch` สั่ง `.pop(mut_path)` ทิ้ง cache → file_delta เป็นไปไม่ได้ในโลกจริง | `dedup.py:944` |
| 4 | `expire_old_turns(keep_turns=2)` ฆ่า cache ไฟล์เร็วเกินจริงใน agent loop | `__init__.py:476` |
| 5 | `read_file_extra` ถูกตัดเพราะเช็กชื่อ tool แบบตายตัว | `dedup.py:947` |

**แก้ (4 ไฟล์, +311/−126):** `_unwrap_numbered_lines` (แกะ prefix `LINE|` แล้วเก็บเลขบรรทัดเดิมไว้) · `_is_targeted_slice` (แยกอ่านทั้งไฟล์/อ่านช่วง จาก payload+args เท่านั้น ไม่ต้องเปิด filesystem) · `_handle_read_file_v42` เขียนใหม่ + anti-loop rail (อ่านซ้ำหลังได้ stub → คืนไฟล์เต็มทันที) · `smart_skeletonize_code` ติด prefix เดิมกลับทุกบรรทัด · `patch` ไม่ลบ cache แล้ว · cache ไฟล์อยู่ได้ 8 turns · `read_file_extra` เข้า engine · `_tok_pair` (BPE เฉพาะกรณีขนาดใกล้กัน) · fail-open เมื่องบ guard หมด

**หลักฐาน:** test ใหม่จำลอง Hermes จริง (offset/limit + `LINE|CONTENT` + patch คั่น) **4 passed** · suite **31 passed** · benchmark band read_file **240,202 → 115,155 chars = 52.1%** (ก่อนแก้ passthrough 100%) · **RESULT: PASS ทุก gate** · latency p50 6.49 ms / p95 74.83 ms (ไฟล์ 43KB)

**ผลกระทบคาดการณ์:** บนปริมาณ read_file แบบเดิม (5.83M chars) เพิ่ม ~3.0M chars ≈ **~760,000 tokens**

**ยังค้าง:** ต้อง restart Hermes 1 ครั้งให้โค้ดใหม่มีผล (process เก่าถือโค้ดเก่าใน memory) · ยังไม่ commit/push ของ v4.2.2 (รอคำสั่ง) · `skill_view` lane ทำได้แค่ dedupe ซ้ำในเซสชันเดียว (ห้ามตัด SKILL.md กลางไฟล์) · `patch` lane ปล่อย payload <2KB (เป็นข้อความยืนยัน/Error) · rollback = ใส่ `read_file`/`read_file_extra` ใน `exclude_tools`
→ ฉบับเต็ม: `reports/raw/TRIMTOK_V422_READFILE_LANE_TH.md` · `reports/raw/TRIMTOK_READFILE_LANE_ARGON_REVIEW.md`

---

## 2026-10-20/21 — v4.2.0 → v4.2.1 · ปฏิรูปตามแผน Argon 4 (ปล่อยแล้ว)

**บริบท:** จาก telemetry จริง 9,303 events เจอคอขวด 3 ตัว — `read_file` 1,107 ครั้ง / 5.97M chars ที่ 0.0% · `reverted_gate` 558 ครั้ง (6.0% fail) · band 1K–2K 1,306 events (24.1%) ที่ไม่ถูกบีบ
**แผน:** Argon 4 (Thinking: Max) ในเธรด `b3b293e15fa29ad8` — 7 waves ผ่าน 3.8 Flash Thinking:Min (160,111 chars) → 1 query Argon → แผน 963 บรรทัด / 58,870 bytes → ขีดหลัก 3 ตัว: 3-Tier Stateful File Engine · 4-Tier Adaptive Headers · Micro-minification

**ผลวัดจริง (harness เดียวกันก่อน/หลัง)**

| Gate | v4.1.0 | v4.2.0/1 |
|---|---|---|
| reverted_gate rate (2K–4K) | 100.0% FAIL | **0.0% PASS** |
| terminal band net saving | 0.0% FAIL | **5.3% PASS** |
| 1K–2K band net saving | 0.0% FAIL | **54.4% PASS** |
| read_file cold-read | 0.0% FAIL | **52.1% PASS** (ค่าจริงหลังแก้ delta ให้จำกัด ≤1,500 บรรทัด) |
| micro-minify latency | 0.013 ms (ไม่ทำอะไร) | **0.069–0.075 ms p50** |
| lane ที่ทำให้ payload โต | 0 | **0** |
| เทสต์ | — | **27/27** |

**Argon review รอบ 1 (diff 81,920 chars เข้าเธรดเดิม) → [REQUEST CHANGES] 5 blockers** — จับได้ของจริงที่เทสต์เดิมไม่เจอ: `NameError: raw_hashes` (การบีบอัดตายเงียบๆ), skeletonizer อ่าน indent ผิดทำ IndentationError, `hygiene()` กิน context line ของ git diff, `adaptive_tool_head` ไม่ถูก wire, `invalidate_shell_mutations` เป็น dead code
**รอบ 2 (diff 34,911 chars) → [APPROVE]** (2,487 chars) — "Production-Ready, merge ได้ทันที"

**ปล่อย:** v4.2.0 (commit `feeb78a`, PyPI run 39510982166) · v4.2.1 (commit `1d8c611`, แก้ 5 blockers + 8 regression tests, PyPI run 39515664911) — ทั้งคู่ LIVE บน PyPI + GitHub release
→ ฉบับเต็ม: `reports/raw/TRIMTOK_V4_REFACTOR_REPORT_TH.md` · รีวิว: `reports/raw/TRIMTOK_V4_ARGON_REVIEW.md` · `reports/raw/TRIMTOK_V4_ARGON_REVIEW_V421.md` · แผน: `reports/raw/TRIMTOK_V4_ARGON_PLAN.md`

**บันทึกกระบวนการ (ไม่ให้เกิดซ้ำ):** root cause ที่แท้จริงของ "agent ค้าง 47 นาที" = venv stamp แฮชไบต์ทุกไฟล์ในโปรเจกต์ปลั๊กอิน → ทุก launch rebuild venv → lease 300s หมดอายุ → ถูกฆ่า → แก้ด้วย `HERMES_DISABLE_LAZY_INSTALLS=1` (HKCU env) + `security.allow_lazy_installs: false` → `hermes --version` 60+วิ → 2.99วิ
→ รายละเอียด: `reports/raw/TRIMTOK_V4_REFACTOR_REPORT_TH.md` หัวข้อ 4.4

---

## 2026-10-15 — v4.1.0 · เพิ่ม PyPI release workflow + entry point hardening (LIVE)
→ ดูรายละเอียดใน `CHANGELOG.md` ของรูท
