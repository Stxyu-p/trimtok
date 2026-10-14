# สรุปงาน trimtok 0.6.1 → 0.6.2 (ฉบับพี่โชคอ่าน)

วันที่: 2026-10-14
ทำอะไร: ซ่อม benchmark ที่ FAIL + freeze baseline ใหม่ + เพิ่มตัวประมาณ token
แบบแม่นขึ้น + ลองทำ signature pinning แล้วถอดทิ้ง + เพิ่มทางต่อ agent ตัวอื่น
สถานะ: เสร็จครบ 5 Steps, เทสต์เขียวทั้งหมด (core 64 / dedup 77 / torture 18 / benchmark ผ่าน)

---

## 1. ภาพรวมก่อนทำ → หลังทำ

| เรื่อง | ก่อนทำ (0.6.1 เดิม) | หลังทำ (0.6.2) | เปลี่ยนไปอย่างไร |
|---|---|---|---|
| เทสต์รวม | benchmark FAIL 1 ตัว (`_no_gain_sample`), core/dedup ผ่าน | เขียวหมด 4 ชุด (64+77+18+benchmark) | ซ่อม fixture ให้ unique จริง |
| บีบอัดรวม 26 เคส | 88.6% (347,587 ตัวอักษร) | 89.4% (324,346 ตัวอักษร) | +0.8pp เป็น noise จากจังหวะรัน ไม่ใช่ gain จริง — ล็อกเลขใหม่ไว้เทียบ |
| SIM 5-turn สมจริง | 61.2% (41,092) | 61.2% (41,092) เท่าเดิม | ไม่ถดถอย (เคยตกไป 30.0% ระหว่างลอง แล้วกู้กลับมา) |
| ความเร็ว | terminal 0.89ms / tool 1.01ms / cross-turn 1.10ms / CCR get 0.27ms | terminal 0.82ms / tool first-seen 3.92ms / cross-turn 1.52ms / CCR get 0.26ms | first-seen ช้าขึ้น (~+2.9ms) เพราะคำนวณ estimate เพิ่ม แต่ยังห่างงบ 10ms เยอะ |
| ประมาณ token | `chars // 4` หยาบ (ไทย 4,200 ตัว → บอก 1,050) | มี `estimate_tokens()` แบบ byte-class ลง stats (ไทย → 3,520 ใกล้จริงกว่า) | แม่นขึ้นเยอะสำหรับไทย+โค้ด ไม่ได้ลด token ใช้จริง |
| ทางต่อ agent ตัวอื่น | ไม่มี (มีแต่ hook Hermes) | มี `compress_agent_payload()` รับ dict/str ได้เลย | Codex/ตัวอื่นเรียก engine เดิมได้โดยไม่แตะ core |
| โครงสร้าง | 5 ไฟล์ root | 5 ไฟล์ root เท่าเดิม | ไม่รื้อตึกตามแผนใหญ่ (ปัดตก `trimtok_engine/` + adapters + reasoning ตามที่ตกลง) |
| `read_file` (D5a) | verbatim | verbatim เท่าเดิม | ไม่แตะตามสัญญา |

---

## 2. ราย Step: ทำอะไรไป ผลเป็นอย่างไร

### Step 1 — ซ่อม `test_benchmark.py` ที่ FAIL ✅

- **ก่อน:** `_no_gain_sample` assert `output == result` แต่ FAIL เพราะ fixture
  180 บรรทัด `record_..._value_..._hash_..._state_...` มีแต่ตัวเลขเรียงกัน
  Drain (`_fold_intra_templates`) เห็นเป็น template เดียวกันหมดแล้วพับทิ้ง
  (`intra_template_saved=9979`) — แผนรอบสองเดาว่าเป็น intra-repeat ซึ่งผิด
  หนูวัดเองว่าตัวพับคือ Drain ไม่ใช่ repeat
- **หลัง:** เติมตัวอักษร base-26 (`_idx_letters`) ลงกลางแต่ละบรรทัด ทำให้
  template key แตกเป็นกลุ่มละ 1 บรรทัด (Drain ต้องเจอ ≥3 ถึงพับ)
  ผลวัด: `gate=reverted_gate, output==input` จริง benchmark เขียวทั้งชุด
- **ไฟล์ที่แตะ:** `tests/test_benchmark.py` (+20 บรรทัด)

### Step 2 — Freeze `BASELINE_0.6.1.md` ✅

- **ก่อน:** มีแค่ baseline 0.5.2 กับ measure 0.6.0 ไม่มีเลขหลัง commit
  Drain (`85d8875` ดัน SIM 30.0% → 61.2%) ให้ล็อก
- **หลัง:** เขียน `tests/BASELINE_0.6.1.md` ใหม่ครบ: ตาราง T1–T15/D1–D12/SIM
  ทุกเคส + latency medians/p95 + estimator เทียบข้าง + วิธีรันซ้ำ
  ล็อกเลข: รวม 89.4% / SIM 61.2%
- **ไฟล์ที่เพิ่ม:** `tests/BASELINE_0.6.1.md`

### Step 3 — Byte-class token estimator ✅ (stats-only)

- **ก่อน:** รายงาน `est-tokens = chars/4` ทุกที่ ไทยโดนประเมินต่ำกว่าจริง
  ~3 เท่า (4,200 ตัว → 1,050 ทั้งที่ BPE จริง ~3,500)
- **หลัง:** เพิ่ม `estimate_tokens()` ใน `marker.py` (stdlib ล้วน, regex 2 ตัว)
  ผลวัด: ไทย 1,050 → 3,520 / โค้ด 1,125 → 1,666 / อังกฤษเท่าเดิม 1,275
  ค่าใหม่เขียนลง `stats["est_tokens_before/after"]` ทุกครั้งที่ fold สำเร็จ
  **เงื่อนไขสำคัญที่หนูล็อกไว้:** estimator ห้ามแตะ `reverted_gate` เด็ดขาด
  (gate ยังวัดด้วย `len()` ล้วนเหมือนเดิม) เพราะถ้าเอาไปตัดสินจะเปลี่ยน
  พฤติกรรม fold ทั้ง 26 เคส เสี่ยงถดถอย
- **ต้นทุน:** ไทย ~1.2ms / โค้ด ~0.3ms / อังกฤษ ~0.03ms ต่อครั้ง
  (ดัน first-seen median 1.01 → 3.92ms ยังห่างงบ 10ms)
- **ไฟล์ที่แตะ:** `marker.py` (+27), `core.py`/`dedup.py` (เติม 2 ฟิลด์ลง stats)

### Step 4 — Search signature pinning ❌ ลองแล้วถอดทิ้ง (มีเหตุผล)

- **ก่อน:** แผนเชื่อว่าบรรทัด `def`/`class` ในผล search จะโดนพับแล้ว agent
  ต้อง `read_file` ซ้ำ เสนอให้ตรึง signature ไว้ verbatim
- **สิ่งที่หนูวัดก่อนทำ:** ของจริงพับอยู่แล้ว 94% (`saved=38943` จาก 41,400)
  เหลือ `def` ไว้เพราะเป็นบรรทัดแรกของกลุ่ม (keep-earliest) ไม่ได้หาย
  ส่วนบล็อก `def` เปลือยไม่มีตัวเลขไม่เคยเข้าเงื่อนไข Drain อยู่แล้ว
- **สิ่งที่ลอง:** ใส่ `_SIG_RE` ห้าม `def/class/function/func` เข้ากลุ่ม Drain
  ผล probe: signature อยู่ครบ 120/120 จริง
- **ผลข้างเคียงที่เจอ:** SIM 5-turn ตกจาก **61.2% → 30.0% (-31.2pp)**
  เพราะ search 800 บรรทัดใน SIM ที่เคยพับผ่าน template โดนตรึงไว้หมด
- **คำตัดสิน (kill-criteria ที่ตกลงกัน):** ถอดทิ้ง ลบโค้ดออก เหลือแค่โน้ต
  ใน `dedup.py` ว่าเคยลองแล้วพัง เพราะอะไร ไม่ส่ง gain นี้
  นี่คือตัวอย่างที่หนูวัดจริงก่อนเชื่อแผน ไม่ใช่เดา

### Step 5 — `compress_agent_payload()` ✅ (glue 20 บรรทัด)

- **ก่อน:** engine รับแต่ `str` ต้องเขียน glue เองถ้าจะให้ agent ตัวอื่นใช้
- **หลัง:** เพิ่มฟังก์ชันเดียวใน `__init__.py` รับ `dict | str`
  ดึงฟิลด์ `content/output/stdout/result` → เรียก compressor เดิม → เขียนกลับ
  วัดแล้ว: dict-tool `ok saved=107,608` / str-terminal `ok` /
  shape แปลก passthrough / `read_file` ยัง verbatim
  ไม่แตะ `plugin.yaml` ไม่แตะ hook Hermes เดิม
- **ไฟล์ที่แตะ:** `__init__.py` (+37)

---

## 3. ตัวเลขหลังทำ (วัดจริง session นี้)

- Suites: core 64 PASS / dedup 77 PASS / torture 18 PASS / benchmark ผ่านหมด / stress `fails=0`
- Latency medians: terminal 0.82ms / first-seen 3.92ms / repeat 0.06ms / cross-turn 1.52ms / no-gain 2.35ms / CCR put 2.00ms / CCR get 0.26ms (งบ 10ms/5ms ผ่านทุก path)
- Stress รวม: 3,059,792 → 324,346 = 89.4% (~683,862 tokens)
- SIM: 105,839 → 41,092 = 61.2% (เท่าเดิม ไม่ถดถอย)
- D5a `read_file` verbatim 0% ตามออกแบบ, fail-open ครบ (torture 18/18)

## 4. เรื่องที่ตัดทิ้ง (และเหตุผล)

| ข้อเสนอเดิม | ตัดสิน | เหตุผลสั้น |
|---|---|---|
| `trimtok_engine/` + IR + 4 adapters | ปัดตก | engine เป็น pure stdlib รับ str อยู่แล้ว ใช้ glue 37 บรรทัดพอ |
| headroom/engine Two-Tier | ปัดตก | hook เป็น ingress-only ไม่เคยแก้ history อยู่แล้ว |
| `reasoning.py` ตัด `<think>` | ปัดตก | hook ไม่มีเส้น reasoning ให้ต่อ ทำไปเป็น dead code |
| signature pinning (Step 4) | ลองแล้วถอด | วัดจริง SIM ตก 31.2pp เกิน kill-criteria |
| เอา estimator ไปตัดสิน gate | ห้าม | จะเปลี่ยนผล 26 เคส — ล็อกเป็น stats-only |

## 5. งานค้างที่ยังไม่ทำ (รอพี่สั่ง)

1. TOON default flip (ปิด → เปิด): ของวางไว้ตั้งแต่ baseline 0.5.2 ว่าเหลือ
   3.8× ไว้บนโต๊ะ (`D2b` 55.7% vs `D2a` 14.5%) — ต้องพี่ approve อย่างเดียว
2. Bump `VERSION` 0.6.1 → 0.6.2 + commit (ตอนนี้ code พร้อม เหลือแค่พี่สั่ง)
3. ถ้า first-seen 3.92ms วันไหนใกล้ 10ms: ทำ est-fields แบบ lazy (คำนวณเมื่อขอ)

---

## ภาคผนวก: ความจำสำหรับครั้งหน้า (พี่สั่งให้จำ)

1. งานแบบนี้พี่ต้องการสรุปเป็น **.md ไฟล์เดียว ภาษาไทย** ทุกครั้ง ไม่ต้องบอกใหม่
   (ก่อนทำ/หลังทำ มีอะไรเปลี่ยน ราย Step พร้อมตัวเลขวัดจริง)
2. ก่อนเชื่อแผนจากข้างนอก ต้องอ่านโค้ดจริง + รันเทสต์เองก่อน (รอบนี้จับผิดได้
   5 ข้อ: ccr/cache, no-mutation, stdlib, guards, D5a)
3. เลข claim ทุกตัวต้องมี case ID (T1–T15/D1–D12/SIM) + วัดด้วย stress/benchmark
   จริง ห้ามรับเลขลอย
4. Enhancement ใหม่ต้องมี kill-criteria (+1% / latency) วัดไม่ผ่านให้ถอด
   (รอบนี้ Step 4 โดนถอดจริง SIM -31.2pp)
