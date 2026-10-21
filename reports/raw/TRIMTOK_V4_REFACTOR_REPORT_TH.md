# Trimtok v4.2 — รายงานการปฏิรูปตามแผน Argon 4

**วันที่:** 20 ตุลาคม 2026
**ผู้บัญชาการ:** MIKA · **ผู้ปฏิบัติงาน:** SORA (โค้ด), MILIM (เอกสาร) · **ผู้ตรวจ:** NUA (test/benchmark), ALTIMA (adversarial review), Gemini 4 Argon (architectural review)
**แผนต้นทาง:** `C:\Users\BlankScreen\Workspace\TRIMTOK_V4_ARGON_PLAN.md` (963 บรรทัด / 58,870 ตัวอักษร)

---

## 1. บริบท — ทำไมต้องปฏิรูป

หนูเจาะ telemetry จริงจาก `~/.trimtok/usage.jsonl` + `plugin-data/trimtok/usage.jsonl*` (รวมไฟล์ที่หมุนเวียน) = **9,303 event** จากการใช้งานจริงของพี่โชค

| ตัวชี้วัด | ค่า |
|---|---|
| Event ทั้งหมด | 9,303 |
| ตัวอักษรดิบรวม | 8,766,415 |
| ตัวอักษรหลังบีบอัด | 7,379,214 |
| **ประหยัดได้** | **1,387,201 ตัวอักษร (~385,334 โทเคน)** |
| Latency p50 / p90 / p99 | 7.16 / 34.62 / 116.75 ms |

### 3 คอขวดที่พบจากข้อมูลจริง

| # | ปัญหา | หลักฐาน | ผลกระทบ |
|---|---|---|---|
| **A** | `read_file` **ไม่ถูกบีบอัดเลย** | 1,107 calls · 5,966,692 chars · saved **0** (0.0%) | **35% ของข้อมูลทั้งหมดสูญหายไปกับ context** |
| **B** | `reverted_gate` ล้มเหลว 558 ครั้ง | 6.0% ของ event ทั้งหมด | บีบอัดไม่สำเร็จ → ส่งข้อความดิบกลับไปเต็ม ๆ |
| **C** | ช่วง 1K–2K ผ่านไปโดยไม่ถูกแตะ | 1,306 events (24.1%) | สูญเสียโอกาส ~10–15% ของ output ขนาดกลาง |

---

## 2. ก่อนทำ — Baseline ที่วัดได้จริง

รันด้วย harness ที่หนูเขียน (`tests/benchmark_v42.py`) บนโค้ด v4.1.0 ปัจจุบัน — เก็บไว้ที่ `trimtok_v42_BASELINE_bench.txt`

```
[band 1] terminal outputs ใน band ที่เกิด reverted_gate: n=40 ขนาด 2,281 chars
   gates            : {'reverted_gate': 40}          <-- ล้มเหลว 100%
   chars before/after: 91,240 -> 91,240  (saved 0)
   latency p50/p95/max: 0.85 / 1.62 / 2.05 ms

[band 2] pretty JSON ในช่วง 1K-2K: n=20 ขนาด 1,123-1,941
   gates            : {'passthrough_small': 20}       <-- ผ่านไป 100%
   chars before/after: 30,207 -> 30,207  (saved 0)
   latency p50/p95/max: 0.003 / 0.013 / 0.013 ms

[band 3] read_file cold reads: n=10 ขนาด 4,936-43,138
   gates            : {'passthrough_reference': 10}   <-- ผ่านไป 100%
   chars before/after: 240,202 -> 240,202  (saved 0)
   latency p50/p95/max: 5.82 / 10.09 / 10.09 ms

ACCEPTANCE GATES (BASELINE v4.1.0)
reverted_gate rate (2K-4K band)      < 0.8 %        100.0 %  FAIL
terminal band net saving             > 0 %           0.0 %  FAIL
1K-2K band net saving                >= 20 %          0.0 %  FAIL
micro-minify latency (p95)           < 2.0 ms     0.013 ms  PASS
read_file cold-read handling         no growth        0.0 %  FAIL
no lane grows a payload              0 cases       0 cases  PASS
smart_skeletonize_code present       present      absent   N/A
```

---

## 3. หลังทำ — วัดจริงหลัง integration gate ผ่าน (v4.2.0)

โค้ด: SORA เขียนโมดูลใหม่ 5 ไฟล์ · หนู wire เข้ากับ `core.py` / `__init__.py` + แก้ regression 4 กลุ่มที่เจอจากการรัน baseline v4.1.0 เทียบ

```
[band 1] terminal outputs ใน band ที่เกิด reverted_gate: n=40 ขนาด 2,281 chars
   gates            : {'ok': 40}                    <-- ผ่าน 100%
   chars before/after: 91,240 -> 86,360  (saved 4,880)
   latency p50/p95/max: 1.19 / 4.09 / 4.09 ms

[band 2] pretty JSON ในช่วง 1K-2K: n=20 ขนาด 1,123-1,941
   gates            : {'ok': 20}                    <-- ผ่าน 100%
   chars before/after: 30,207 -> 13,789  (saved 16,418)
   latency p50/p95/max: 0.979 / 1.808 / 1.808 ms

[band 3] read_file cold reads: n=10 ขนาด 4,936-43,138
   gates            : {'passthrough_reference': 1, 'ok': 9}
   chars before/after: 240,202 -> 56,002  (saved 184,200)
   latency p50/p95/max: 21.65 / 32.09 / 32.09 ms

ACCEPTANCE GATES (v4.2.0)  ->  RESULT: PASS (ทุก gate)
reverted_gate rate (2K-4K band)      < 0.8 %          0.0 %  PASS
terminal band net saving             > 0 %            5.3 %  PASS
1K-2K band net saving                >= 20 %         54.4 %  PASS
micro-minify latency (p50)           < 0.25 ms       0.084 ms  PASS
read_file cold-read handling         no growth       76.7 %  PASS
no lane grows a payload              0 cases         0 cases  PASS
skeleton stays valid Python           parses          parses   PASS
```

### เทียบก่อน/หลัง (ตัวเลขเดียวกัน วัดจาก harness เดียวกัน)

| Gate | v4.1.0 (ก่อน) | v4.2.0 (หลัง) |
|---|---|---|
| reverted_gate rate (2K-4K) | 100.0% FAIL | **0.0% PASS** |
| terminal band net saving | 0.0% FAIL | **5.3% PASS** |
| 1K-2K band net saving | 0.0% FAIL | **54.4% PASS** |
| read_file cold-read | 0.0% FAIL (ทิ้ง 240,202 chars) | **76.7% PASS (ประหยัด 184,200 chars)** |
| micro-minify latency | 0.013 ms (ไม่ได้ทำอะไร) | **0.084 ms p50 (ทำงานจริงแล้ว)** |
| lanes ที่ทำให้ payload โต | 0 | **0** |
| เทสต์ | — | **19/19 ผ่าน** |

### Regression ที่เจอและแก้ (เทียบกับ baseline v4.1.0 จริง)

1. **`NameError: raw_hashes`** — SORA ทิ้ง `.add()` เก่าไว้ 2 บรรทัด → **tool result ขนาดใหญ่ทุกตัวได้ `passthrough_exception` = การบีบอัดตายเงียบ ๆ** (นี่คือบั๊กที่อันดับผลตรวจของ SORA ไม่เจอ เพราะเกตเมืองใหม่ไม่ได้แตะเส้นทางนี้)
2. **`passthrough_exception` ไม่บอกสาเหตุ** — เพิ่ม `error` ใน stats เพื่อไม่ให้บั๊กประเภทนี้ซ่อนได้อีก
3. **stub ของ file_cache_hit ยาวเกิน** (79.6% เป้า 80%) → ตัด hint ให้สั้น
4. **`micro_minify_tool` เรียก BPE 2 ครั้ง** (0.39 ms) → เปลี่ยนเป็นประมาณจากจำนวนตัวอักษร (0.084 ms p50)
5. **สัญญาเดิมของ exact-repeat** เปลี่ยน (เดิมส่ง raw ซ้ำ = เสีย ~1.3K tokens) → อัปเดต test ให้ตรง "เล็กลงเสมอ"

### Argon 4 review (thread เดิม `b3b293e15fa29ad8`) → **[REQUEST CHANGES]** 5 blockers

ส่ง diff 81,920 chars เข้า thread เดิม (ที่วางแผน v4.2) · Argon อ่านแล้วตอบ 22,085 chars — **เจอของจริงที่เทสต์เดิมไม่เจอ**

| # | Blocker | ระดับ | แก้ที่ | เทสต์จับบั๊ก |
|---|---|---|---|---|
| 1 | skeletonizer อ่าน indent จากบรรทัดว่างหลัง docstring → `pass` ผิดระดับ (IndentationError) + `@decorator` ค้างเหนือ `pass` (SyntaxError) | P1 | `pruners.py` | ✅ พิสูจน์แล้วว่า fail ก่อนแก้ |
| 2 | อ่านซ้ำหลัง cold skeleton แล้วตอบ "identical to prior read" ทั้งที่ agent เห็นแค่โครงร่าง + แคชไฟล์ไม่หมดอายุ + `#prev_ref` ดึงไม่ได้ | P1 | `dedup.py` + `__init__.py` | ✅ พิสูจน์แล้ว |
| 3 | `hygiene()` ลบช่องว่างหน้า context line ของ git diff + เงื่อนไข `hidden_small == 0` ผิดทาง | P1/P2 | `core.py` + `dedup.py` | ✅ พิสูจน์แล้ว |
| 4 | `adaptive_tool_head` ไม่ถูก wire ใน `compress_tool_result` + ไม่มี BPE token gate + ไม่มี cascade rescue + Tier 2 ยาวกว่า Tier 3 | P2 | `dedup.py` + `signals.py` | contract test |
| 5 | `invalidate_shell_mutations` เขียนไว้แต่ไม่เคยถูกเรียก (dead code) | P1 | `core.py` | contract test |

**วิธีพิสูจน์ว่าเทสต์จับบั๊กได้จริง:** เอา fix ออกด้วย `git stash` → รันเทสต์ → ต้อง FAIL → ใส่ fix กลับ → ต้อง PASS (4/8 เป็น regression guard ที่พิสูจน์แล้ว, อีก 4 เป็น contract test)

### Argon 4 re-review (round 2) → **[APPROVE]** ✅

ส่ง diff v4.2.1 (34,911 chars) กลับเข้า thread เดิม → Argon ตอบ **2,487 chars**:

> **A) VERDICT: [APPROVE]** — 5 Blockers ร้ายแรงถูกแก้ไขอย่างสมบูรณ์ โค้ดเสถียรและบรรลุทั้ง 3 Priorities อย่างแท้จริง
> **B) BLOCKERS: ไม่มี (None)** — การแก้ไขครอบคลุม edge cases ทั้งหมดที่เคยทำให้ข้อมูลสูญหาย หรือเกิด Syntax Error อย่างสมบูรณ์
> **C) ZERO-LOSS AUDIT:** ทั้ง 6 กลไก PASS (file_unchanged / file_delta / smart_skeletonize_code / adaptive headers + cascade / micro-minify / state expiry + shell invalidation)
> **E) PERFORMANCE:** micro-minify 0.5ms → <0.15ms ตามเป้า · SequenceMatcher cap เป็น fail-open ที่สมบูรณ์
> **F)** "สถาปัตยกรรม 3-Tier Read File และ 4-Tier Adaptive Headers ขณะนี้สมบูรณ์ระดับ Production-Ready ... อนุญาตให้ Merge เข้า Main Branch ทันที"

ฉบับเต็ม: `TRIMTOK_V4_ARGON_REVIEW_V421.md`

### ปล่อยแล้ว

- **v4.2.0** — commit `feeb78a` + `e3c6556` · tag push · GitHub release (wheel 73,427 B · sdist 124,158 B) · `twine check` PASSED · PyPI run `39510982166` success → **4.2.0 LIVE**
- **v4.2.1** — commit `1d8c611` (แก้ 5 blockers + 8 regression tests) · tag push · GitHub release + assets · `twine check` PASSED · PyPI run `39515664911` → **4.2.1 LIVE**
- editable install รีเฟรชแล้ว → `VERSION 4.2.1`
- โปรเจกต์ปลั๊กอินใน Hermes เป็น junction → ใช้โค้ดล่าสุดทันที (หลัง restart Hermes)
- เทสต์สุดท้าย **27/27 ผ่าน** · acceptance gates **PASS ทั้ง 8 ข้อ**

> ⚠️ **ตัวเลข read_file เปลี่ยนจาก 76.7% → 52.1%** — ไม่ใช่ของแย่ลง แต่เพราะเดิมวัดบน "อ่านไฟล์ 10 ไฟล์คนละเนื้อหาแต่ path เดียวกัน" ซึ่งเดิมตกไปทาง delta (ตัวเลขสวยเกินจริง) พอแก้ delta ให้จำกัด ≤1,500 บรรทัดตามที่ Argon แนะ ไฟล์ใหญ่เลยตกไปใช้ skeleton → **ตัวเลขตอนนี้คือค่าจริง** (ไม่มีการโกหก agent ว่า "ไม่เปลี่ยน" เมื่อมันเห็นแค่ skeleton)

---

## 4. บันทึกกระบวนการ (สำคัญ — เพื่อไม่ให้เกิดซ้ำ)

### 4.1 บั๊กในแผนของ Argon ที่ต้องแก้ (จับได้ก่อนลงมือ)
| # | บั๊ก | ผลกระทบ | วิธีแก้ |
|---|---|---|---|
| 1 | marker ของ body-fold เป็น comment เปล่า | `IndentationError` — `ast.parse()` พัง | เปลี่ยนเป็น `pass  # [tt:body ...]` |
| 2 | fixture ของ test_problem_c ใช้ 18 items = 2,489 chars | assert `< 2048` พังทันที | ลดเป็น 12 items = 1,667 chars (วัดแล้ว) |
| 3 | `expire_old_turns` ทำให้ file cache หมดอายุทั้งก้อน | ฆ่า Tier 1/2 ในเซสชันยาว | ปรับเป็น expire ต่อ digest ต่อ turn |
| 4 | แผนไม่มีการ invalidate file cache เมื่อ shell เปลี่ยนไฟล์ | `git checkout` → Tier-2 delta ผิด | เพิ่ม `invalidate_shell_mutations()` |

### 4.2 เหตุการณ์ระหว่างดำเนินการ: Agent ค้าง 47 นาที (หาที่ต้นเหตุแล้ว — ดูหัวข้อ 4.4)

### 4.3 ปัญหาสุขภาพเครื่องที่ค้างอยู่ (ยังไม่แก้ — ไม่กระทบงานนี้)
`hermes pm install` ล้มด้วย:
```
ValueError: Dependency inputs changed while preparing publication; retry.
  (pm/install.py:775 _commit_selection)
```
คือ dependency inputs เปลี่ยนไประหว่างที่กำลังเตรียม publication → self-update ของ Hermes จบไม่ได้ → ทุก launch จึงต้องพยายาม sync ใหม่และเตือน `install out of sync`
- **ผลกระทบ:** ไม่กระทบงาน v4.2 (ใช้ `HERMES_DISABLE_LAZY_INSTALLS=1` แล้ว) แต่ทำให้ทุก `hermes` CLI ที่พี่โชคเรียกใช้ช้าขึ้น และถ้าเรียกหลาย process พร้อมกันจะแย่ง lock กัน
- **ยังไม่แก้ เพราะ:** ต้องรู้ก่อนว่าอะไรคือ "dependency inputs" ที่เปลี่ยนระหว่าง publication (ต้องการการตรวจสอบเพิ่ม) — พี่โชคสั่งให้โฟกัสที่ v4.2 ก่อน หนูจะกลับมาจัดการเป็นงานแยกถ้าพี่โชคต้องการ

---

## 4.4 🔍 ROOT CAUSE ที่แท้จริงของ "agent ค้างบ่อย" (พิสูจน์แล้ว ไม่ใช่การเดา)

พี่โชคตั้งข้อสังเกตว่า **agent ค้างหลายรอบจนงานไม่เสร็จ และหนูต้องทำเองทุกที** — หนูไล่หาที่ต้นเหตุจริง โดยอ่านโค้ด Hermes ทีละชั้น ไม่ใช่เดาจากอาการ

### สาเหตุ: **"venv stamp" แฮชไบต์ของทุกไฟล์ในโปรเจกต์ปลั๊กอิน**

| # | ชั้น | ที่มาในโค้ด | สิ่งที่เกิด |
|---|---|---|---|
| 1 | `pm/packages.py:413-433` `expected_stamp()` | แฮช `uv.lock` + extras + python + **`members_stamp(enabled_member_dirs())`** | stamp ผูกกับเนื้อหาไฟล์ของปลั๊กอิน |
| 2 | `pm/workspace.py:42-62` `members_stamp()` | `os.walk` ทุกไฟล์ในไดเรกทอรีปลั๊กอิน → `path.read_bytes()` | **ไฟล์ 1 ไฟล์ที่เพิ่ม/แก้ = stamp เปลี่ยน** |
| 3 | `pm/install.py:774-775` | เทียบ stamp ก่อน/หลัง build venv → `raise ValueError("Dependency inputs changed while preparing publication; retry.")` | install พังถ้ามีอะไรแก้ไฟล์ระหว่าง build |
| 4 | `hermes_cli/venv_sync.py:338` `prepare_launch()` | ทำงานทุกครั้งที่ CLI สตาร์ท | เมื่อ venv "ไม่ current" → **rebuild venv ทั้งก้อน (หลายนาที)** ใต้ `_venv_install_lock` (`pm/install.py:831`) |
| 5 | `agent/session_persistence.py:298` | turn lease TTL = **300 วินาที** | process ที่ค้างในข้อ 4 → lease หมดอายุ → **`interrupted_during_api_call(session_turn_lease_lost)`** |

**ปลั๊กอินที่ถูกแฮช = ไดเรกทอรีที่ deploy เป็น junction เข้า workspace ของพี่โชค** → พอ agent เขียนโค้ดใน `Workspace/trimtok/` หรือ `Workspace/skill-proof/` **ทุก `hermes` launch หลังจากนั้นจะพยายาม rebuild venv**

### วงจรที่ทำให้ "ค้างบ่อยมาก"

```
agent แตะโค้ดปลั๊กอิน  →  stamp เปลี่ยน  →  venv out of sync
   →  ทุก launch พยายาม rebuild (หลายนาที, lock ตัวเดียวทั้งเครื่อง)
   →  agent คนอื่นแย่ง lock → รอเกิน 300s → lease หมดอายุ → ถูกฆ่า
   →  ระหว่าง rebuild มีอะไรแก้ไฟล์อีก → stamp เปลี่ยน → install พัง "retry"
   →  ไม่มี backoff (marker ถูก arm เฉพาะ tail path) → launch ถัดไปลองใหม่ → วนซ้ำ
```

### หลักฐานการพิสูจน์ (รันได้ใน 5 วินาที)
```
ENABLED PLUGIN MEMBER DIRS (ถูกแฮชลงใน stamp):
    AppData/Local/hermes/plugins/skill-proof
    AppData/Local/hermes/plugins/trimtok
    profiles/nua/plugins/homeassistant
    profiles/sora/plugins/homeassistant

stamp BEFORE probe file  : 8e635a303a7e592aa4516450e4159ba1
stamp AFTER  probe file  : 7cac4a8cc8c740b03d868475904ce267   <-- เปลี่ยนทันที
stamp AFTER  file removed: 8e635a303a7e592aa4516450e4159ba1   <-- กลับมาเดิม
```

### สิ่งที่แก้แล้ววันนี้

| # | การแก้ | ที่ไหน | ผลวัดจริง |
|---|---|---|---|
| 1 | **`HERMES_DISABLE_LAZY_INSTALLS=1` แบบทั้งเครื่อง** | `HKCU\Environment` (winreg) + broadcast `WM_SETTINGCHANGE` | `hermes --version` **60+ วิ (timeout) → 2.99 วิ** · `hermes plugins list` **90+ วิ → 22.8 วิ** |
| 2 | **`security.allow_lazy_installs: false`** | `config.yaml` (user-facing knob ตาม `pm/install.py:164-193`) | ปิด lazy-install path (ชั้นที่สอง) |
| 3 | **ระเบียบการ dispatch** | บรีฟ agent | ห้าม `uv` / `find` ทั้ง home dir / คำสั่ง >60 วิ · ใช้ venv python ของ repo |
| 4 | **ตั้ง `pytest` ใน venv ของ repo** | `trimtok/.venv` | agent รันเทสต์ได้โดยไม่ต้องแตะ uv/network |

> ⚠️ **`.env` ใช้ไม่ได้** — ถูกโหลดที่ `agent/agent_init.py` ซึ่งรัน**หลัง** bootstrap → ช้าเกินไป ต้องเป็น env ระดับ process

### กฎปฏิบัติต่อไป
1. **ห้ามแก้ไฟล์ในปลั๊กอินขณะที่มี `hermes` คำสั่งกำลังรัน** — นี่คือต้นเหตุที่ทำให้ install พัง
2. ถ้าจะรัน `hermes pm install` ให้รอให้ workspace นิ่งก่อน (ไม่มี agent เขียนไฟล์) — แล้ว stamp จะถูกบันทึกตรง
3. Hermes จะไม่อัปเดต dependency อัตโนมัติอีก → ถ้าอยากอัปเดต ใช้ `hermes update` หรือ `hermes pm install` เองอย่างตั้งใจ (ซึ่งเป็นเรื่องดีกว่า: อัปเดตแบบตั้งใจ ไม่ใช่แบบหลุดเองระหว่าง agent ทำงาน)

---

**Confidence: High** — ทุกตัวเลขและทุกข้อความ error ในเอกสารนี้มาจากการรันจริงบนเครื่อง และ root cause มาจากการอ่านโค้ด Hermes พร้อมพิสูจน์ด้วยการรันสคริปต์ที่แตะไฟล์จริง ไม่มีการคาดเดา

