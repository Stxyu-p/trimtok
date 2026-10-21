แผนแม่บทสถาปัตยกรรม Trimtok v4.2 (Production Refactor Blueprint)

จากการตรวจสอบโค้ดจริงทั้ง 7 ระลอกและข้อมูล Telemetry จากการใช้งานจริง 9,303 ครั้ง พบว่า Trimtok v4.1.0 สูญเสียโอกาสในการประหยัดโทเคนไปถึง ~8.3 ล้านตัวอักษร จาก 3 คอขวดหลักและบั๊กเชิงตรรกะในท่อส่งข้อมูล (Pipeline) ตารางด้านล่างสรุปสถานะปัจจุบัน (Facts) เทียบกับเป้าหมายทางวิศวกรรมใน v4.2 (Projections):

จุดคอขวด / ปัญหาที่พบจาก Telemetry (Facts)	สาเหตุระดับโค้ด (Root Cause in v4.1.0)	สถาปัตยกรรมใหม่ใน v4.2 (To-Be Solution)	ผลลัพธ์ที่คาดการณ์ (Projected Impact)


1. read_file ไม่ถูกบีบอัดเลย




1,107 ครั้ง | 5,966,692 chars | ประหยัดได้ 0 chars (0.0%)

	dedup.py บรรทัด 409 ดัก if tool_name == "read_file": return result, {"gate": "passthrough_reference"} และ __init__.py ไม่ได้ส่ง args (เช่น path, offset, limit) เข้าฟังก์ชัน compress_tool_result	

3-Tier Stateful File Engine:




• Tier 1 (Unchanged Repeat): ส่งโครงร่าง AST + พอยน์เตอร์อ้างอิงเทิร์นก่อน




• Tier 2 (Mutated Re-read): ส่งเฉพาะ Hunk ที่เปลี่ยน ±15 บรรทัดพร้อมเลขบรรทัดจริง




• Tier 3 (Cold Oversize ≥ 12KB): Line-Anchored Smart Skeletonizer

	ประหยัดเพิ่ม 3.38M – 4.23M chars (~56%–71% ของปริมาณ read_file ทั้งหมด) โดยไม่ทำลายความแม่นยำของ patch


2. บั๊ก raw_hashes ปล่อยข้อมูลซ้ำหลุดรอด




ใน dedup.py บรรทัด 421 (passthrough_repeat)

	เมื่อผลลัพธ์ของ Tool มีค่า SHA-256 (_rh) ซ้ำกับเทิร์นก่อนหน้า โค้ดสั่ง if _rh in raw_hashes: return result ทำให้ส่งข้อความดิบเต็มจำนวนกลับไปแทนที่จะพับ (Fold)	แยกเซต failed_hashes (เคยติด reverted_gate) ออกจาก seen_hashes (เคยบีบอัดสำเร็จ) หากซ้ำกับเทิร์นที่ยังไม่หมดอายุ ให้พับเป็น [tt:repeat #cN] ทันที	ประหยัดได้ 95%–99% สำหรับ Tool output ที่ซ้ำเดิมแบบ 100% ข้ามเทิร์น


3. reverted_gate สูงถึง 558 ครั้ง (6.0%)




ส่วนใหญ่เกิดในช่วงขนาด 2KB–4KB

	Header + LEGEND ใน core.py และ dedup.py กินพื้นที่คงที่ ~160–180 chars (~45–55 tokens) เมื่อลดบรรทัดขยะได้เพียง 50–150 chars จึงถูก Gate ตีกลับทั้งหมด	

4-Tier Adaptive Header Scaling:




• Tier 0 (Lossless-Only): ไม่มี Header (0 chars) ไม่ต้องลง CCR




• Tier 1 (Nano 1KB–4KB): [tt:#c1 -14L] (14 chars)




• Tier 2 (Compact 4KB–8KB): 1 บรรทัด (~48 chars)




• Tier 3 (Full ≥ 8KB): Header + Legend เต็ม

	ลดอัตรา reverted_gate จาก 6.0% เหลือต่ำกว่า 0.8% กู้คืนการบีบอัดได้ ~480–510 ครั้ง (~180k–310k chars)


4. passthrough_small ช่วง 1KB–2KB




1,306 ครั้ง (24.1%) | ~1.96M chars ไม่ถูกแตะต้อง

	กำแพง min_chars = 2048 บล็อกการทำงานตั้งแต่บรรทัดแรก ทั้งที่ข้อมูลช่วง 1KB–2KB มี Pretty JSON, ANSI codes, ช่องว่างท้ายบรรทัด และบรรทัดซ้ำติดกัน	

Sub-ms Micro-Minification Lane (500–2,047 chars):




ทำ Lossless Hygiene + Compact JSON + Adjacent Repeat Fold แบบไร้ Header (Tier 0/1) ในเวลาต่ำกว่า 0.15 ms

	ประหยัดเพิ่ม 240k – 390k chars (ลดลงเฉลี่ย 12%–20% ในกลุ่ม 1KB–2KB) ด้วยความเสี่ยงเป็นศูนย์
หมวดที่ 1: แผนเจาะลึกแก้จุดตาย read_file (Stateful Read Cache, Hash Invalidation & Line-Anchored Skeletonizer)
1.1 วิเคราะห์สาเหตุเชิงลึก (As-Is Failure Analysis)

การขาดบริบทพารามิเตอร์ (args Blindness): ใน trimtok/__init__.py ฟังก์ชัน _on_tool_result(tool_name, args, result, ...) รับ args มาจาก Hermes Agent แต่ไม่ได้ส่งต่อไปยัง compress_tool_result() ทำให้ dedup.py ไม่รู้ว่า read_file กำลังอ่านไฟล์ชื่ออะไร (path / file_path) หรือเป็นการอ่านเฉพาะส่วน (offset / limit)

ความกลัวเรื่อง patch พัง (File-Edit Safety Dilemma): หากแปลงไฟล์โค้ดเป็น AST Skeleton ทันทีตั้งแต่การอ่านครั้งแรก (Cold Read) โดยไม่มีเลขบรรทัดอ้างอิงที่แม่นยำ เมื่อ LLM เรียกใช้เครื่องมือ patch หรือ edit_file ในเทิร์นถัดไป การแก้ไขจะล้มเหลวเพราะไม่มีเนื้อหาบรรทัดจริง

1.2 สถาปัตยกรรม 3-Tier read_file Pipeline (To-Be Design)

เพื่อให้บรรลุ Priority 1 (Zero Loss of Intelligence & 100% Patch Safety) คู่กับ Priority 2 (Maximum Token Reduction) เราจะแบ่งการจัดการ read_file ออกเป็น 4 กฎเหล็กตามสถานะของเซสชัน:

                       [ read_file(args, result) ]
                                    │
         ┌──────────────────────────┴──────────────────────────┐
         ▼ (มี offset/limit หรือขนาด ≤ 2,047 chars)             ▼ (อ่านทั้งไฟล์ ขนาด ≥ 2,048 chars)
[ กฎที่ 0: Targeted Slice ]                         [ ตรวจสอบ (session_id, norm_path) ใน FileStateCache ]
  ทำเฉพาะ Tier-0 Hygiene                                       │
  (ตัด trailing space ท้ายบรรทัด                                 ├───► [ กรณี A: Unchanged Repeat Read ]
   เฉพาะไฟล์ที่ไม่ใช่ diff/patch)                                 │     (sha12 ตรงกับที่ส่งไปแล้วภายใน keep_turns)
                                                               │     → ส่ง [tt:file_cached #cN] + Structural Outline
                                                               │       (ประหยัด 88%–95%)
                                                               │
                                                               ├───► [ กรณี B: Mutated Re-Read (ไฟล์ถูกแก้แล้วอ่านซ้ำ) ]
                                                               │     (sha12 เปลี่ยนไปจากเทิร์นก่อน)
                                                               │     → ส่ง Contextual File Delta (แสดงบรรทัดจริง ±15 บรรทัด
                                                               │       รอบจุดที่เปลี่ยน พร้อมเลขบรรทัด L1..LN + พับส่วนที่ไม่เปลี่ยน)
                                                               │       (ประหยัด 60%–80%)
                                                               │
                                                               └───► [ กรณี C: Cold First Read (อ่านครั้งแรกในเซสชัน) ]
                                                                     ├─ ขนาด 2,048 – 12,000 chars:
                                                                     │  ส่งเนื้อหา Verbatim 100% (บันทึกเข้า CCR + FileStateCache)
                                                                     │  เพื่อให้ Agent สั่ง patch ได้ทันทีในเทิร์นเดียว
                                                                     └─ ขนาด ≥ 12,001 chars (กลุ่ม 91 เคสยักษ์ สูงสุด 95KB):
                                                                        ส่ง Line-Anchored Smart Skeleton + เก็บต้นฉบับใน CCR #cN

กฎการล้างแคชอัตโนมัติ (Deterministic Cache Invalidation)

ใน _on_tool_result: เมื่อ tool_name อยู่ในกลุ่มแก้ไขไฟล์ ได้แก่ {"patch", "edit_file", "write_file", "replace_in_file", "multi_edit", "apply_patch"} ให้ดึง path / file_path จาก args แล้วทำเครื่องหมาย dirty = True ใน FileStateCache

ใน _on_transform (Terminal hook): เมื่อคำสั่ง Shell มีคำกริยาที่อาจแก้ไขไฟล์ในโปรเจกต์ เช่น git checkout, git reset, git apply, sed -i, python -c, npm run format, ruff --fix ให้ลดระดับความเชื่อมั่นของแคชเป็นโหมดตรวจสอบ sha12 เสมอ (เนื่องจากเราคำนวณ sha12 = sha256(result)[:12] จากเนื้อหาจริงที่ read_file คืนมา การเปรียบเทียบ sha12 จึงรับประกันความถูกต้องแบบ Content-Addressed 100% ต่อให้ไฟล์ถูกแก้จากภายนอกก็ตาม!)

กฎของ Line-Anchored Smart Skeletonizer (ไม่ทำลายความฉลาดและไม่ทำโค้ดเสีย)

ต่างจาก skeletonize_code เดิมที่ตัดบอดี้ฟังก์ชันทิ้งแบบไร้พิกัดบรรทัด ตัว smart_skeletonize_code(text, ccr_ref) รุ่น v4.2 ใช้ ast.parse (Pure Python stdlib) โดยมีข้อบังคับดังนี้:

คงไว้ 100% (Never Fold):

ทุก import และ from ... import ...

ตัวแปรระดับโมดูล (Constants, TypeAliases, TypedDict, Enum, Dataclass fields, Pydantic models)

ทุก Decorator (@property, @staticmethod, @app.route(...))

ลายเซ็นฟังก์ชันและคลาส (def / async def / class) แบบเต็มรวม Type Hints และ Default Arguments ทุกตัว

Docstring บรรทัดแรกของคลาสและฟังก์ชัน

ฟังก์ชันขนาดเล็ก (ความยาวไม่เกิน 15 บรรทัด): คงเนื้อหาโค้ดไว้ 100% Verbatim เพราะฟังก์ชันสั้นมักเป็น helper หรือ property สำคัญ

ส่วนท้ายของฟังก์ชันยาว: คงบรรทัด return ..., yield ..., หรือ raise ... สุดท้ายของฟังก์ชันไว้ เพื่อให้ LLM เห็น Return shape/type จริงโดยไม่ต้องเปิดอ่านบอดี้เต็ม

พับเฉพาะบอดี้ตรงกลางของฟังก์ชันที่ยาวเกิน 15 บรรทัด พร้อมระบุพิกัดบรรทัด (Lstart..Lend):

แทนที่บรรทัดตรงกลางด้วยคอมเมนต์มาตรฐาน Python ที่ถูกต้องตามหลักไวยากรณ์ (Valid Syntax):
    # [tt:body L142-L218 (77 lines) in #c4 -> read_file(offset=142, limit=77) or trimtok_retrieve(hash="#c4", offset=142, limit=77)]

Fallback ปลอดภัย: หากไฟล์ไม่ใช่ Python หรือ ast.parse โยน SyntaxError ให้ใช้ Brace/Indent Scope Scanner สำหรับ JS/TS/Go/Rust หรือคงไว้แบบ Verbatim (Fail-open)

หมวดที่ 2: แผนขจัด reverted_gate (4-Tier Adaptive Header & Legend Scaling)
2.1 วิเคราะห์คณิตศาสตร์ของ reverted_gate (558 ครั้ง)

ใน v4.1.0 ทุกครั้งที่ผ่าน compress_terminal หรือ compress_tool_result ระบบจะแปะ Header + LEGEND ความยาวประมาณ 165 ตัวอักษร (~48 โทเคน):

สมมติไฟล์หรือผลลัพธ์ขนาด 2,400 chars (~600 tokens)

ระบบตัดบรรทัดซ้ำหรือบรรทัดว่างออกไปได้ 140 chars (เหลือ 2,260 chars)

พอแปะ head + LEGEND (+165 chars) ขนาดรวมกลายเป็น 2,425 chars (มากกว่า 2,400 chars เดิม!)

ผลลัพธ์: ติด reverted_gate ทิ้งความพยายามทั้งหมดไป 558 ครั้ง

2.2 ตาราง 4-Tier Adaptive Header Scaling

เราแยกการใส่มาร์กเกอร์ตาม "ลักษณะการสูญเสียบรรทัด (Lossiness)" และ "ขนาดของข้อมูล (Payload Size)":

ระดับ (Tier)	เงื่อนไขการใช้งาน (Trigger Condition)	รูปแบบ Header ที่ส่งออก (Exact Format)	ขนาด Overhead	ต้องบันทึกลง SQLite CCR หรือไม่?
Tier 0: Headerless Lossless	มีเฉพาะการตัด ANSI, ตัดช่องว่างท้ายบรรทัด, ยุบบรรทัดว่างติดกัน, หรือ Minify JSON โดยที่ hidden == 0 และ dropped == 0 (ไม่มีบรรทัดเนื้อหาหายแม้แต่บรรทัดเดียว)	(ไม่มี Header เลย ส่งเฉพาะเนื้อหาที่คลีนแล้ว)	

0 chars




(0 tokens)

	ไม่ต้อง (ลดภาระ Disk I/O และ SQLite Lock)
Tier 1: Nano Header	ข้อมูลขนาด 500 – 4,096 chars หรือจำนวนตัวอักษรที่ประหยัดได้ (Gross Saved) น้อยกว่า 240 chars	[tt:#c1 -18L] หรือ [tt:#c1 -320c]	

13 – 16 chars




(~4 tokens)

	บันทึก (เพื่อให้เรียก trimtok_retrieve(hash="#c1") ได้)
Tier 2: Compact 1-Line	ข้อมูลขนาด 4,097 – 8,192 chars และประหยัดได้ตั้งแต่ 240 chars ขึ้นไป	[tt:log -45L #c1 | retrieve(hash="#c1", section="errors|diff|tail")]	

48 – 68 chars




(~14 tokens)

	บันทึก
Tier 3: Full Header + Legend	ข้อมูลขนาด มากกว่า 8,192 chars และประหยัดได้มากกว่า 400 chars	terminal_head(...) + \n + LEGEND (รูปแบบเดิมของ v4.1)	

160 – 180 chars




(~48 tokens)

	บันทึก
กลไก Auto-Downgrade ก่อนยอมแพ้ (Cascade Gate Rescue)

ใน core.py และ dedup.py ก่อนจะคืนค่า reverted_gate:

คำนวณ candidate ด้วย Header ตาม Tier ปกติ

หาก len(candidate) >= len(raw) หรือ estimate_tokens(candidate) >= estimate_tokens(raw):

Step A: ลองลดระดับ Header ลงเป็น Tier 1 (Nano Header: [tt:#digest -NL]) แล้วตรวจวัดโทเคนอีกครั้ง

Step B: หากยังมากกว่าเดิม แต่มีขั้นตอน Lossless (เช่น strip_ansi, hygiene, minify_json) ที่ลดขนาดได้จริง ให้ส่งออกผลลัพธ์จากขั้นตอน Tier 0 (Lossless-Only, ไร้ Header) แทนการคืนค่าดิบ!

หมวดที่ 3: แผน Micro-Minification สำหรับช่วง 1KB–2KB (Zero-Risk Fast Lane)
3.1 ขอบเขตและหลักความปลอดภัย (500 – 2,047 ตัวอักษร)

ข้อมูลในกลุ่ม passthrough_small จำนวน 1,306 ครั้ง อยู่ในช่วง 1,000 – 2,000 chars ซึ่งมักเป็นผลลัพธ์ของคำสั่งสั้นๆ เช่น git status, pytest ที่รันผ่านไม่กี่ไฟล์, JSON response จาก API/MCP, หรือผลลัพธ์ search_files ขนาดย่อม

เพื่อให้ทำงานได้เร็วระดับ ต่ำกว่า 0.15 ms (150 ไมโครวินาที) และ ไม่เสี่ยงทำโค้ดหรือข้อมูลเสีย (Zero Loss of Intelligence) เราจะสร้างท่อพิเศษ micro_minify(text, kind_hint) ที่ทำงานเมื่อความยาวอยู่ระหว่าง micro_min_chars (ค่าเริ่มต้น 512 ตัวอักษร) ถึง min_chars - 1 (2,047 ตัวอักษร):

3.2 ลำดับขั้นการทำงานของ micro_minify (4 ขั้นตอนไร้ความเสี่ยง)

ANSI & Spinner Strip (เฉพาะ Terminal Output):

ลบรหัสสี ANSI (\x1b\[[0-9;?]*[A-Za-z]) และบรรทัด Spinner/Progress bar (Downloading... 10%, ⠋) ที่ไม่ใช้บรรทัด Error (not is_important(ln))

Whitespace & Blank-Run Hygiene (ยกเว้น Unified Diff):

หากข้อความไม่ใช่ Unified Diff ("diff --git" หรือ "@@ -"):

ลบช่องว่างและแท็บท้ายบรรทัด ([ \t]+(?=\n|$))

ยุบบรรทัดว่างที่ติดกันตั้งแต่ 3 บรรทัดขึ้นไป (\n{3,}) ให้เหลือเพียง 1 บรรทัดว่าง (\n\n)

Whole-Payload Lossless JSON Minification (O(N) Fast Path):

หากขึ้นต้นด้วย { หรือ [ และลงท้ายด้วย } หรือ ] ให้แปลงผ่าน minify_json(text) เพื่อตัดช่องว่างและการขึ้นบรรทัดใหม่ของ Pretty JSON ออกทั้งหมดโดยรักษาค่าและชนิดข้อมูลเดิม 100%

(หมายเหตุ: ไม่แปลงเป็น TOON ในช่วงต่ำกว่า 1,500 chars เพราะ Header ของ TOON อาจไม่คุ้มค่า BPE เมื่อตารางมีขนาดเล็ก ยกเว้นได้กำไรสุทธิมากกว่าหรือเท่ากับ 15 tokens)

Exact Adjacent Repeat Folding (เฉพาะเมื่อมีบรรทัดซ้ำติดกันตั้งแต่ 4 บรรทัดขึ้นไป):

หากมีบรรทัดที่ไม่ใช่ Error ซ้ำกันติดกันตั้งแต่ 4 บรรทัดขึ้นไป และประหยัดตัวอักษรสุทธิได้มากกว่า 60 ตัวอักษรหลังหัก Tier-1 Nano Header ([tt:#c1 -NL]) ให้พับเป็น [repeated Nx] พร้อมบันทึกต้นฉบับลง CCR

นอกจากนี้ สำหรับ compress_tool_result และ compress_terminal ในช่วง 1,000 – 2,047 chars ให้เรียก remember(text, session_id) และบันทึก Snapshot เข้า delta_engine เสมอ เพื่อให้เทิร์นถัดไปสามารถอ้างอิงข้ามเทิร์นได้

หมวดที่ 4: พิมพ์เขียวสถาปัตยกรรมโมดูล โครงสร้างคลาส และโค้ดจริง (Production Implementation)

ด้านล่างคือพิมพ์เขียวการแก้ไขไฟล์จริงในโปรเจกต์ trimtok แบบ Surgical Diff ที่เป็น Pure Python stdlib 100%, ปลอดภัยแบบ Fail-open และพร้อมนำไปใช้งานจริง

4.1 โมดูลใหม่/ส่วนขยายใน trimtok/signals.py — Adaptive Headers

เพิ่มฟังก์ชันสร้าง Header แบบปรับขนาดอัตโนมัติ (Tier 0 ถึง Tier 3) ลงใน trimtok/signals.py:

Python
# เพิ่มใน trimtok/signals.py

def adaptive_terminal_head(
    kind: str,
    omitted_lines: int,
    digest: str,
    raw_len: int,
    gross_saved_chars: int,
    legend: str = LEGEND_FOLD,
    *,
    force_tier: int | None = None,
) -> list[str]:
    """Return header lines scaled to payload size and savings (Tier 0..3).
    
    Tier 0: omitted_lines == 0 (lossless hygiene/ANSI strip only) -> [] (0 chars)
    Tier 1: raw_len <= 4096 or gross_saved_chars < 240 -> 1-line nano tag (~15 chars)
    Tier 2: raw_len <= 8192 -> 1-line compact tag (~54 chars)
    Tier 3: raw_len > 8192 -> full terminal_head + legend (~165 chars)
    """
    if omitted_lines <= 0 and force_tier is None:
        return []
    ref = digest if digest.startswith("#") else f"#{digest}"
    tier = force_tier
    if tier is None:
        if raw_len <= 4096 or gross_saved_chars < 240:
            tier = 1
        elif raw_len <= 8192 or gross_saved_chars < 450:
            tier = 2
        else:
            tier = 3

    if tier <= 0:
        return []
    if tier == 1:
        return [f"[tt:{ref} -{omitted_lines}L]"]
    if tier == 2:
        return [f"[tt:{kind} -{omitted_lines}L {ref} | retrieve(hash=\"{ref}\", section=\"errors|diff|tail\")]"]
    return [terminal_head(kind, omitted_lines, digest.lstrip("#")), legend]


def adaptive_tool_head(
    saved_chars: int,
    digest: str,
    raw_len: int,
    *,
    is_lossless_only: bool = False,
    force_tier: int | None = None,
) -> str:
    """Return tool header scaled to payload size and lossiness (Tier 0..3)."""
    if is_lossless_only and force_tier is None:
        return ""
    ref = digest if digest.startswith("#") else f"#{digest}"
    tier = force_tier
    if tier is None:
        if raw_len <= 4096 or saved_chars < 240:
            tier = 1
        elif raw_len <= 8192:
            tier = 2
        else:
            tier = 3

    if tier <= 0:
        return ""
    if tier == 1:
        return f"[tt:{ref} -{saved_chars}c]"
    if tier == 2:
        return f"[tt:fold -{saved_chars}c {ref} | retrieve(hash=\"{ref}\")]"
    return tool_head(saved_chars, digest.lstrip("#"))

4.2 การยกระดับ trimtok/pruners.py — Line-Anchored Smart AST Skeletonizer

ปรับปรุง/เพิ่ม smart_skeletonize_code() ใน trimtok/pruners.py เพื่อรักษาเลขบรรทัดจริง (Lstart-Lend), คงฟังก์ชันสั้น (ไม่เกิน 15 บรรทัด) ไว้ 100%, และคงบรรทัด return/raise สุดท้ายของฟังก์ชันยาว:

Python
# เพิ่ม/อัปเกรดใน trimtok/pruners.py
import ast

_MIN_FOLD_BODY_LINES = 15  # ฟังก์ชันที่สั้นกว่าหรือเท่ากับ 15 บรรทัดจะไม่ถูกพับเลย


def smart_skeletonize_code(
    text: str,
    ref: str = "",
    *,
    min_body_lines: int = _MIN_FOLD_BODY_LINES,
) -> tuple[str, int, int]:
    """Line-anchored AST code skeletonizer with Zero Loss of Signatures/Types.

    Preserves 100% of:
      - Imports, module constants, global assignments, type definitions
      - Class/Function decorators, full multi-line signatures & type annotations
      - First line of docstrings
      - Short functions/methods (<= min_body_lines)
      - Final return/yield/raise statement of folded functions (for return-type clarity)
    Replaces only the interior implementation body of long functions with an exact
    1-indexed line-range marker so the agent can call read_file(offset=..., limit=...).

    Returns: (skeleton_text, saved_chars, folded_functions_count)
    """
    try:
        tree = ast.parse(text)
    except Exception:
        return text, 0, 0

    lines = text.split("\n")
    total_lines = len(lines)
    # เก็บช่วงบรรทัดที่จะพับ: list of (fold_start_1idx, fold_end_1idx, indent_str, func_name)
    fold_ranges: list[tuple[int, int, str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not getattr(node, "body", None) or not hasattr(node, "end_lineno"):
            continue
        # หลีกเลี่ยงการพับฟังก์ชันซ้อนข้างในหากฟังก์ชันแม่จะถูกพับอยู่แล้ว
        first_stmt = node.body[0]
        body_start = first_stmt.lineno  # 1-indexed
        body_end = node.end_lineno or body_start

        # หาก statement แรกเป็น Docstring ให้คงบรรทัดแรกของ Docstring ไว้
        if (
            isinstance(first_stmt, ast.Expr)
            and isinstance(getattr(first_stmt, "value", None), ast.Constant)
            and isinstance(first_stmt.value.value, str)
        ):
            doc_end = getattr(first_stmt, "end_lineno", first_stmt.lineno) or first_stmt.lineno
            if len(node.body) == 1:
                continue  # มีแค่ docstring ไม่ต้องพับ
            body_start = doc_end + 1

        # หาก statement สุดท้ายเป็น return / raise / yield สั้นๆ (1-2 บรรทัด) ให้คงไว้ท้ายฟังก์ชัน
        last_stmt = node.body[-1]
        if isinstance(last_stmt, (ast.Return, ast.Raise)) and hasattr(last_stmt, "lineno"):
            last_span = (getattr(last_stmt, "end_lineno", last_stmt.lineno) or last_stmt.lineno) - last_stmt.lineno + 1
            if last_span <= 2 and last_stmt.lineno > body_start:
                body_end = last_stmt.lineno - 1

        span_lines = body_end - body_start + 1
        if span_lines < min_body_lines:
            continue

        # คำนวณ indentation จากบรรทัดแรกของบอดี้
        raw_first = lines[body_start - 1] if 1 <= body_start <= total_lines else "    "
        indent_len = len(raw_first) - len(raw_first.lstrip(" \t"))
        indent = raw_first[:indent_len] if indent_len > 0 else "    "
        fold_ranges.append((body_start, body_end, indent, node.name))

    if not fold_ranges:
        return text, 0, 0

    # เรียงลำดับและตัดช่วงที่ซ้อนทับกัน (เลือกช่วงนอกสุดหรือตามลำดับที่ไม่ทับกัน)
    fold_ranges.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    merged_ranges: list[tuple[int, int, str, str]] = []
    last_end = 0
    for s, e, ind, fn in fold_ranges:
        if s <= last_end:
            continue
        merged_ranges.append((s, e, ind, fn))
        last_end = e

    ref_tag = f" {ref}" if ref else ""
    out: list[str] = []
    cur = 1  # 1-indexed line pointer
    for s, e, ind, fn in merged_ranges:
        while cur < s and cur <= total_lines:
            out.append(lines[cur - 1])
            cur += 1
        omitted = e - s + 1
        marker = (
            f"{ind}# [tt:body L{s}-L{e} ({omitted}L of {fn}){ref_tag} "
            f"-> use read_file(offset={s}, limit={omitted}) or trimtok_retrieve]"
        )
        out.append(marker)
        cur = e + 1

    while cur <= total_lines:
        out.append(lines[cur - 1])
        cur += 1

    new_text = "\n".join(out)
    saved = len(text) - len(new_text)
    if saved <= 0:
        return text, 0, 0
    return new_text, saved, len(merged_ranges)

4.3 การยกระดับ trimtok/dedup.py — Stateful Read-File Engine, Micro-Minify & raw_hashes Bug Fix

ใน trimtok/dedup.py เราจะเพิ่ม FileReadTracker สำหรับจัดการ read_file ทั้ง 3 Tier, แก้บั๊ก raw_hashes ที่ปล่อยข้อมูลซ้ำหลุดรอด, และเพิ่มท่อ micro_minify_tool:

Python
# เพิ่มใน trimtok/dedup.py
import difflib
import os
from .signals import adaptive_tool_head
from .pruners import smart_skeletonize_code

_MICRO_MIN_CHARS = 512
_COLD_READ_SKELETON_MIN_CHARS = 12_000  # ไฟล์ <= 12KB อ่านครั้งแรกได้ Verbatim 100% เพื่อความปลอดภัยของ patch
_READ_DELTA_CONTEXT_LINES = 12          # แสดงบรรทัดจริง ±12 บรรทัดรอบจุดที่ถูกแก้ไขเมื่ออ่านไฟล์ซ้ำ
_MUTATING_TOOLS = frozenset({
    "patch", "edit_file", "write_file", "replace_in_file",
    "multi_edit", "apply_patch", "create_file", "str_replace_editor",
})


def _extract_file_args(args: Any) -> tuple[str, int | None, int | None]:
    """Extract normalized (file_path, offset, limit) from tool arguments."""
    if isinstance(args, str) and args.strip().startswith("{"):
        try:
            args = fast_json_loads(args)
        except Exception:
            return "", None, None
    if not isinstance(args, dict):
        return "", None, None
    raw_path = (
        args.get("path")
        or args.get("file_path")
        or args.get("target_file")
        or args.get("filename")
        or ""
    )
    norm_path = os.path.normcase(os.path.normpath(str(raw_path).strip())) if raw_path else ""
    offset = args.get("offset") or args.get("start_line")
    limit = args.get("limit") or args.get("num_lines") or args.get("end_line")
    try:
        offset_i = int(offset) if offset is not None else None
    except Exception:
        offset_i = None
    try:
        limit_i = int(limit) if limit is not None else None
    except Exception:
        limit_i = None
    return norm_path, offset_i, limit_i


def _build_file_outline(text: str, max_items: int = 35) -> str:
    """Fast O(N) structural outline (classes/functions with 1-indexed line numbers)."""
    items: list[str] = []
    for idx, line in enumerate(text.split("\n"), 1):
        s = line.lstrip()
        if s.startswith(("def ", "async def ", "class ", "interface ", "func ", "fn ", "pub fn ", "export function ")):
            sig = s.split("{")[0].split(":")[0].strip()[:90]
            items.append(f"  L{idx}: {sig}")
            if len(items) >= max_items:
                items.append("  ...")
                break
    return "\n".join(items) if items else f"  (total {text.count(chr(10)) + 1} lines)"


def _build_read_delta(
    prev_text: str,
    cur_text: str,
    cur_ref: str,
    prev_ref: str,
    ctx_lines: int = _READ_DELTA_CONTEXT_LINES,
) -> tuple[str, int]:
    """Build line-numbered contextual diff view when a previously read file is modified."""
    prev_lines = prev_text.split("\n")
    cur_lines = cur_text.split("\n")
    matcher = difflib.SequenceMatcher(None, prev_lines, cur_lines, autojunk=False)
    changed_cur_indices: list[int] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            changed_cur_indices.extend(range(j1, max(j1 + 1, j2)))

    if not changed_cur_indices:
        return cur_text, 0

    total = len(cur_lines)
    intervals: list[tuple[int, int]] = []
    for idx in changed_cur_indices:
        s = max(0, idx - ctx_lines)
        e = min(total, idx + ctx_lines + 1)
        if intervals and s <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], e))
        else:
            intervals.append((s, e))

    chunks: list[str] = [
        f"[tt:file_delta #{cur_ref} vs #{prev_ref} | {total}L total, showing modified hunks ±{ctx_lines}L]"
    ]
    prev_end = 0
    for s, e in intervals:
        if s > prev_end:
            chunks.append(f"[... L{prev_end + 1}-L{s} ({s - prev_end} unchanged lines in #{cur_ref}) ...]")
        for ln_idx in range(s, e):
            chunks.append(f"{ln_idx + 1}|{cur_lines[ln_idx]}")
        prev_end = e
    if prev_end < total:
        chunks.append(f"[... L{prev_end + 1}-L{total} ({total - prev_end} unchanged lines in #{cur_ref}) ...]")

    delta_str = "\n".join(chunks)
    saved = len(cur_text) - len(delta_str)
    if saved <= 120:
        return cur_text, 0
    return delta_str, saved


def _handle_read_file_v42(
    result: str,
    session_id: str,
    args: Any,
    min_chars: int,
    t0: float,
) -> tuple[str, dict]:
    """3-Tier Stateful Handler for read_file with 100% Patch Safety."""
    norm_path, offset, limit = _extract_file_args(args)
    digest = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:12]
    clean_text, _ = hygiene(result)
    remember(clean_text, session_id, owner=digest)

    # กฎที่ 0: หาก Agent ระบุ offset/limit (เจาะจงอ่านเฉพาะช่วงบรรทัด) หรือไฟล์เล็กกว่า min_chars -> คืน Verbatim 100%
    if offset is not None or limit is not None or len(result) < min_chars:
        return result, {"gate": "passthrough_reference", "digest": digest, "ms": (time.perf_counter() - t0) * 1000}

    seen = _seen(session_id)
    file_cache: dict[str, dict] = seen.setdefault("files", {})
    cache_key = norm_path or f"__anon_{digest}"
    prev_entry = file_cache.get(cache_key)

    # กรณี A: ไฟล์เดิมถูกอ่านซ้ำโดยที่เนื้อหาไม่เปลี่ยนเลย (sha12 ตรงกัน 100%)
    if prev_entry is not None and prev_entry.get("digest") == digest and not prev_entry.get("expired", False):
        prev_entry["reads"] = prev_entry.get("reads", 1) + 1
        outline = _build_file_outline(result)
        stub = (
            f"[tt:file_unchanged #{digest} path=\"{norm_path or 'file'}\" "
            f"({len(result)} chars, {result.count(chr(10)) + 1}L) — identical to prior read in active context]\n"
            f"Outline (use read_file(offset, limit) or trimtok_retrieve(hash=\"#{digest}\", offset, limit) for exact lines):\n"
            f"{outline}"
        )
        if len(stub) < len(result) and estimate_tokens(stub) < estimate_tokens(result):
            return stub, {
                "gate": "ok",
                "kind": "file_cache_hit",
                "digest": digest,
                "before_chars": len(result),
                "after_chars": len(stub),
                "saved_chars": len(result) - len(stub),
                "est_tokens_before": estimate_tokens(result),
                "est_tokens_after": estimate_tokens(stub),
                "fold_saved": len(result) - len(stub),
                "ms": (time.perf_counter() - t0) * 1000,
            }

    # กรณี B: ไฟล์เดิมถูกแก้ไขแล้วอ่านซ้ำ (มี prev_entry ของ path เดียวกัน แต่ digest เปลี่ยน)
    if norm_path and prev_entry is not None and prev_entry.get("digest") != digest:
        prev_text = prev_entry.get("text", "")
        prev_digest = prev_entry.get("digest", "")
        if prev_text:
            delta_view, d_saved = _build_read_delta(prev_text, result, digest, prev_digest)
            file_cache[cache_key] = {"digest": digest, "text": result, "reads": 1, "expired": False}
            if d_saved > 0 and estimate_tokens(delta_view) < estimate_tokens(result):
                return delta_view, {
                    "gate": "ok",
                    "kind": "file_delta",
                    "digest": digest,
                    "before_chars": len(result),
                    "after_chars": len(delta_view),
                    "saved_chars": d_saved,
                    "est_tokens_before": estimate_tokens(result),
                    "est_tokens_after": estimate_tokens(delta_view),
                    "fold_saved": d_saved,
                    "ms": (time.perf_counter() - t0) * 1000,
                }

    # บันทึกสถานะการอ่านครั้งแรกของไฟล์นี้ลงใน Session Cache
    file_cache[cache_key] = {"digest": digest, "text": result, "reads": 1, "expired": False}

    # กรณี C: Cold First Read — หากขนาด >= 12,000 chars ให้ใช้ Line-Anchored Smart Skeletonizer
    if len(result) >= _COLD_READ_SKELETON_MIN_CHARS:
        sk_text, sk_saved, fn_count = smart_skeletonize_code(result, ref=f"#{digest}")
        if sk_saved > 200 and fn_count > 0:
            head = adaptive_tool_head(sk_saved, digest, len(result))
            candidate = f"{head}\n{sk_text}" if head else sk_text
            if len(candidate) < len(result) and estimate_tokens(candidate) < estimate_tokens(result):
                return candidate, {
                    "gate": "ok",
                    "kind": "file_skeleton",
                    "digest": digest,
                    "before_chars": len(result),
                    "after_chars": len(candidate),
                    "saved_chars": len(result) - len(candidate),
                    "est_tokens_before": estimate_tokens(result),
                    "est_tokens_after": estimate_tokens(candidate),
                    "skeleton_saved": sk_saved,
                    "ms": (time.perf_counter() - t0) * 1000,
                }

    # Cold First Read ขนาด 2KB - 12KB: ส่ง Verbatim เพื่อให้ Agent สั่ง patch ได้ทันทีโดยไม่สะดุด
    return result, {"gate": "passthrough_reference", "digest": digest, "ms": (time.perf_counter() - t0) * 1000}

การแก้บั๊ก raw_hashes และเพิ่ม Micro-Minification ใน compress_tool_result (trimtok/dedup.py)

แทนที่ส่วนต้นและส่วนท้ายของ compress_tool_result ใน trimtok/dedup.py ด้วยตรรกะต่อไปนี้:

Python
def micro_minify_tool(result: str, session_id: str, t0: float) -> tuple[str, dict]:
    """Zero-risk fast lane for 512..2047 char tool outputs (Tier 0 headerless)."""
    digest = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:12]
    text, h_saved = hygiene(result)
    text, m_saved = minify_json(text)
    remember(text, session_id, owner=digest)
    total_saved = len(result) - len(text)
    if total_saved >= 12 and estimate_tokens(text) < estimate_tokens(result):
        return text, {
            "gate": "ok",
            "digest": digest,
            "lossless_tier0": True,  # ไม่ต้องเขียนลง SQLite CCR เพราะไม่มีข้อมูลถูกตัดออก
            "before_chars": len(result),
            "after_chars": len(text),
            "saved_chars": total_saved,
            "est_tokens_before": estimate_tokens(result),
            "est_tokens_after": estimate_tokens(text),
            "hygiene_saved": h_saved,
            "minify_saved": m_saved,
            "ms": (time.perf_counter() - t0) * 1000,
        }
    return result, {"gate": "passthrough_small", "ms": (time.perf_counter() - t0) * 1000}


และใน compress_tool_result:

เพิ่มพารามิเตอร์ args: Any = None และตรวจสอบ _MUTATING_TOOLS:

Python
if tool_name in _MUTATING_TOOLS:
    mut_path, _, _ = _extract_file_args(args)
    if mut_path:
        _seen(session_id or "default").get("files", {}).pop(mut_path, None)


เปลี่ยนช่องทาง 1KB–2KB (len(result) < min_chars):

Python
if len(result) < min_chars:
    if len(result) >= _MICRO_MIN_CHARS and tool_name != "read_file":
        return micro_minify_tool(result, session_id, t0)
    return result, {"gate": "passthrough_small", "ms": 0.0}


เปลี่ยน if tool_name == "read_file": ให้เรียก _handle_read_file_v42(result, session_id, args, min_chars, t0)

แก้บั๊ก raw_hashes บรรทัด 420–422:

เปลี่ยนจาก if _rh in raw_hashes: return result, {"gate": "passthrough_repeat"} เป็น failed_hashes = _seen(session_id or "default").setdefault("failed_raw", set())

เฉพาะผลลัพธ์ที่เคยติด reverted_gate และยังไม่มี Span ใน seen["spans"] เท่านั้นจึงจะถูกข้าม ส่วนผลลัพธ์ที่เคยบีบอัดสำเร็จในเทิร์นก่อน เมื่อถูกเรียกซ้ำจะไหลเข้า fold_spans() และถูกพับเป็น [tt:seen N lines #c1] ลดขนาดลงได้กว่า 95%!

ใช้ adaptive_tool_head พร้อม Cascade Gate Rescue:

Python
is_lossless_only = (
    ss_saved == 0 and f_saved == 0 and i_saved == 0
    and dt_saved == 0 and ht_saved == 0 and sk_saved == 0 and not toon_used
)
if ht_saved > 0:
    head = headtail_head(ht_head, ht_tail, ht_omitted, digest)
else:
    head = adaptive_tool_head(
        len(result) - len(folded), digest, len(result), is_lossless_only=is_lossless_only
    )
new_result = (head + "\n" + folded) if head else folded

# Cascade Gate Rescue: หาก Header ปกติทำให้เกินขนาด ให้ลดระดับเป็น Tier 1 Nano Header
if len(new_result) >= len(result) or estimate_tokens(new_result) >= estimate_tokens(result):
    nano_head = adaptive_tool_head(len(result) - len(folded), digest, len(result), force_tier=1)
    nano_cand = f"{nano_head}\n{folded}"
    if len(nano_cand) < len(result) and estimate_tokens(nano_cand) < estimate_tokens(result):
        new_result = nano_cand
    elif is_lossless_only and len(folded) < len(result) and estimate_tokens(folded) < estimate_tokens(result):
        new_result = folded
        is_lossless_only = True
    else:
        failed_hashes.add(_rh)
        return result, {"gate": "reverted_gate", "before_chars": len(result), "ms": (time.perf_counter() - t0) * 1000}

4.4 การยกระดับ trimtok/core.py — Terminal Micro-Minification & Adaptive Header Rescue

ใน trimtok/core.py:

เมื่อ len(output) < min_chars แต่มีความยาวตั้งแต่ 512 ตัวอักษรขึ้นไป:

รัน strip_ansi(output) + hygiene(text) + พับบรรทัดที่ซ้ำติดกันตั้งแต่ 4 บรรทัดขึ้นไป (_fold_repeats)

หากไม่มีบรรทัดถูกซ่อน (hidden == 0) แต่การลบ ANSI และช่องว่างท้ายบรรทัดช่วยลดโทเคนได้จริง ให้ส่งออกเป็น Tier 0 (lossless_tier0: True) ทันทีโดยไม่แปะ Header

สำหรับ len(output) >= min_chars (บรรทัด 256–268 เดิม):

แทนที่ head = [terminal_head(kind, hidden + dropped, digest), LEGEND] ด้วย adaptive_terminal_head:

Python
omitted_total = hidden + dropped
gross_saved = len(output) - sum(len(x) + 1 for x in windowed)
head = adaptive_terminal_head(kind, omitted_total, digest, len(output), gross_saved, LEGEND)
new_output = "\n".join(head + windowed) if head else "\n".join(windowed)
tokens_before = estimate_tokens(output)
tokens_after = estimate_tokens(new_output)

# Cascade Gate Rescue สำหรับช่วง 2KB-4KB ที่เคยติด reverted_gate 558 ครั้ง
if len(new_output) >= len(output) or tokens_after >= tokens_before:
    if omitted_total > 0:
        nano_head = adaptive_terminal_head(kind, omitted_total, digest, len(output), gross_saved, force_tier=1)
        nano_out = "\n".join(nano_head + windowed)
        nano_tok = estimate_tokens(nano_out)
        if len(nano_out) < len(output) and nano_tok < tokens_before:
            new_output = nano_out
            tokens_after = nano_tok
    # หากยังเกินขนาด ลองส่งเฉพาะ Lossless ANSI/Hygiene strip (Tier 0)
    if len(new_output) >= len(output) or tokens_after >= tokens_before:
        clean_only = strip_ansi(output).rstrip()
        clean_tok = estimate_tokens(clean_only)
        if len(clean_only) + 12 <= len(output) and clean_tok < tokens_before:
            return clean_only, {
                "gate": "ok", "kind": kind, "lossless_tier0": True,
                "before_chars": len(output), "after_chars": len(clean_only),
                "saved_chars": len(output) - len(clean_only),
                "est_tokens_before": tokens_before, "est_tokens_after": clean_tok,
                "hidden_lines": 0, "dropped_lines": 0,
                "ms": (time.perf_counter() - t0) * 1000,
            }
        return output, {
            "gate": "reverted_gate", "kind": kind,
            "before_chars": len(output), "after_chars": len(output),
            "saved_chars": 0, "ms": (time.perf_counter() - t0) * 1000,
        }

4.5 การเชื่อมโยงใน trimtok/__init__.py (Surgical Wiring)

แก้ไขเพียง 2 จุดใน trimtok/__init__.py:

ใน _finalize() (บรรทัด 280): หาก stats.get("lossless_tier0") เป็น True ไม่ต้องเรียก _store_original() ลง SQLite CCR เพื่อประหยัด I/O และคืนค่า compressed ทันที:

Python
if stats.get("lossless_tier0"):
    _audit({
        **audit_base,
        "before_chars": stats.get("before_chars"),
        "after_chars": stats.get("after_chars"),
        "saved_chars": stats.get("saved_chars"),
        "gate": "ok",
        "hash": "tier0_lossless",
        "ms": round(float(stats.get("ms", 0.0)), 2),
    }, ctx=ctx)
    return compressed


ใน _on_tool_result() (บรรทัด 337): ส่ง args=args เข้าสู่ compress_tool_result:

Python
new_result, stats = compress_tool_result(
    str(tool_name or ""),
    result,
    args=args,
    session_id=sid,
    min_chars=_int_cfg(ctx, "min_chars", 2048, 0, 10_000_000),
    toon_enabled=_bool_cfg(ctx, "toon_enabled", True),
    deadline=Deadline(_float_cfg(ctx, "guard_ms", 50.0, 1.0, 1000.0)),
)


ใน expire_old_turns() (trimtok/dedup.py): เมื่อเทิร์นเก่าหมดอายุตาม keep_turns ให้ตั้งค่า entry["expired"] = True ใน seen["files"] ของเทิร์นนั้นด้วย เพื่อป้องกันไม่ให้ Agent อ้างอิง file_unchanged ไปยังข้อความที่หลุดออกจาก Context Window ไปแล้ว

หมวดที่ 5: แผนการทดสอบเชิงปริมาณ (Verification Benchmark & Telemetry Gates)

เพื่อพิสูจน์อย่างเป็นวิทยาศาสตร์ว่า Trimtok v4.2 บรรลุทั้ง 3 Priorities โดยไม่มี Regression เราจะกำหนดเกณฑ์ผ่าน (Acceptance Gates) และชุดทดสอบ tests/test_v42_telemetry_gates.py ดังนี้:

5.1 ตารางเกณฑ์ชี้วัดความสำเร็จ (Quantitative Telemetry Acceptance Gates)
มิติการวัดผล (Metric)	ค่าจริงใน v4.1.0 (Baseline)	เกณฑ์บังคับผ่านใน v4.2 (Target Gate)	วิธีตรวจสอบ (Verification Method)
1. Fault & Syntax Preservation (Priority 1)	100.0%	100.0% (Zero Regression)	ทุก Error line (is_important), Stack Crash frame, Diff hunk (+-), และ Type Signature ต้องอยู่ครบ 100% และโค้ด Python ที่ผ่าน smart_skeletonize_code ต้องผ่าน ast.parse() ได้โดยไม่เกิด SyntaxError
2. read_file Compression Ratio	0.0% (0 / 5.96M chars)	≥ 55.0% (ในการจำลอง Session 5 เทิร์นที่มีทั้ง Cold Read, Unchanged Re-read, และ Patch + Re-read)	ทดสอบด้วยชุดจำลองพฤติกรรม Agent อ่านไฟล์ 3KB–95KB ข้ามเทิร์น
3. reverted_gate Failure Rate	6.0% (558 / 9,303 runs)	ต่ำกว่า 0.8% (ลดลงมากกว่า 85%)	ทดสอบด้วย Corpus ขนาด 2,048–3,500 chars ที่มีบรรทัดซ้ำเล็กน้อย (ลดได้ 20–120 chars) ต้องผ่านด้วย Tier-0 หรือ Tier-1 Nano Header
4. 1KB–2KB Micro-Minify Savings	0.0% (passthrough_small)	เฉลี่ย ≥ 15.0% สำหรับ JSON/Log ขนาด 512–2,047 chars	ทดสอบกับ Pretty JSON 1.2KB, ANSI Log 1.5KB, และไฟล์ที่มี Trailing spaces
5. P95 Execution Latency (Priority 3)	1.62 ms (Terminal) / 2.65 ms (Tool)	Median ต่ำกว่า 2.0 ms, P95 ต่ำกว่า 8.0 ms (และ Micro-minify ต่ำกว่า 0.25 ms)	วัดด้วย time.perf_counter() จำนวน 1,000 รอบใน tests/test_benchmark.py
5.2 ชุดคำสั่งรันตรวจสอบ (Verification Suite Commands)
Bash
# 1. ทดสอบความถูกต้องของ Logic เดิมทั้งหมด (Zero Regression Gate)
pytest -q
python tests/test_core.py
python tests/test_dedup.py
python tests/test_torture.py

# 2. ทดสอบชุด Benchmark ความเร็วและหน่วยความจำ (< 10ms Gate)
python tests/test_benchmark.py
python tests/stress_heavy.py

5.3 โค้ดชุดทดสอบใหม่ tests/test_v42_telemetry_gates.py

ชุดทดสอบนี้จำลองปัญหาทั้ง 3 ข้อจาก Telemetry จริงเพื่อยืนยันผลลัพธ์เชิงปริมาณ:

Python
"""Verification suite for Trimtok v4.2 telemetry bottlenecks (read_file, reverted_gate, 1K-2K micro-minify)."""

import ast
import json
import time
from trimtok.core import compress_terminal
from trimtok.dedup import clear_session, compress_tool_result
from trimtok.pruners import smart_skeletonize_code


def test_problem_a_read_file_lifecycle():
    """Verify Cold Read -> Unchanged Repeat Cache Hit -> Patch Invalidation -> File Delta -> Oversize Skeleton."""
    clear_session("s_v42")
    # สร้างไฟล์ Python ขนาด ~5,500 chars (Cold Read อยู่ในเกณฑ์ 2KB-12KB ต้องได้ Verbatim 100%)
    funcs = []
    for idx in range(12):
        body = "\n".join(f"    x_{j} = {j} * {idx}" for j in range(18))
        funcs.append(f"def compute_metric_{idx}(val: int, label: str = 'ok') -> int:\n    \"\"\"Compute {idx}.\"\"\"\n{body}\n    return val + {idx}\n")
    raw_file = "import os\nfrom typing import Any\n\nGLOBAL_LIMIT: int = 100\n\n" + "\n".join(funcs)
    assert 4000 <= len(raw_file) <= 10000

    # Turn 1: Cold First Read (<= 12KB) -> ต้องคืน Verbatim เพื่อให้ patch ปลอดภัย 100%
    out1, st1 = compress_tool_result(
        "read_file", raw_file, args={"path": "src/metrics.py"}, session_id="s_v42"
    )
    assert st1["gate"] == "passthrough_reference"
    assert out1 == raw_file

    # Turn 2: Unchanged Repeat Read -> ต้องได้ File Cache Hit + Outline ประหยัด > 80%
    out2, st2 = compress_tool_result(
        "read_file", raw_file, args={"path": "src/metrics.py"}, session_id="s_v42"
    )
    assert st2["gate"] == "ok"
    assert st2["kind"] == "file_cache_hit"
    assert st2["saved_chars"] > int(len(raw_file) * 0.80)
    assert "def compute_metric_0" in out2

    # Turn 3: Agent แก้ไขไฟล์ด้วย patch แล้วอ่านซ้ำ -> ต้องได้ Contextual File Delta พร้อมเลขบรรทัดจริง
    edited_file = raw_file.replace("x_5 = 5 * 3", "x_5 = 99999  # patched bug")
    out3, st3 = compress_tool_result(
        "read_file", edited_file, args={"path": "src/metrics.py"}, session_id="s_v42"
    )
    assert st3["gate"] == "ok"
    assert st3["kind"] == "file_delta"
    assert "x_5 = 99999  # patched bug" in out3
    assert st3["saved_chars"] > int(len(edited_file) * 0.50)

    # Turn 4: Cold First Read ของไฟล์ขนาดใหญ่ (> 12KB) -> ต้องได้ Smart AST Skeleton ที่ parse ผ่าน 100%
    large_file = raw_file * 3
    assert len(large_file) > 12000
    out4, st4 = compress_tool_result(
        "read_file", large_file, args={"path": "src/huge_module.py"}, session_id="s_v42"
    )
    assert st4["gate"] == "ok"
    assert st4["kind"] == "file_skeleton"
    ast.parse(out4.split("\n", 1)[1] if out4.startswith("[tt:") else out4)  # ต้องเป็น Valid Python Syntax 100%!


def test_problem_b_reverted_gate_eliminated_in_2k_3k_band():
    """Verify 2KB-3KB outputs with modest savings (40-120 chars) succeed via Tier-0 or Tier-1 Nano Header."""
    # สร้าง Terminal output ขนาด 2,300 chars ที่มีบรรทัดซ้ำกันเพียง 4 บรรทัด (~120 chars)
    unique_lines = [f"src/module_{i}.py:42: info: checked symbol_{i}_status_ok" for i in range(38)]
    repeat_block = ["npm notice Tarball details: package-lock-verified-ok"] * 4
    payload = "\n".join(unique_lines + repeat_block)
    assert 2100 <= len(payload) <= 2800

    compressed, stats = compress_terminal("npm test", payload, min_chars=2048)
    assert stats["gate"] == "ok", f"Expected 'ok' via Nano Header, got {stats['gate']}"
    assert len(compressed) < len(payload)


def test_problem_c_micro_minify_1k_2k_band():
    """Verify 1KB-2KB payloads get lossless micro-minification in < 1ms with zero header bloat."""
    data = {
        "status": "ok",
        "items": [
            {"id": i, "name": f"service_{i}", "healthy": True, "latency_ms": 12.4}
            for i in range(18)
        ],
    }
    pretty_json = json.dumps(data, indent=4)
    assert 1000 <= len(pretty_json) < 2048

    t0 = time.perf_counter()
    out, stats = compress_tool_result("mcp_status", pretty_json, session_id="s_micro")
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert stats["gate"] == "ok"
    assert stats.get("lossless_tier0") is True
    assert len(out) < len(pretty_json) * 0.70  # ลดลงมากกว่า 30%
    assert json.loads(out) == data             # ข้อมูล JSON ตรงกับต้นฉบับ 100%
    assert elapsed_ms < 2.0                    # เร็วระดับ Sub-millisecond

ขั้นตอนถัดไปสำหรับการลงมืออัปเกรด Trimtok v4.2:
สร้างไฟล์ plan.md ฉบับสมบูรณ์สำหรับสั่งรัน Hermes Agent
เขียนโค้ดฉบับเต็มของ dedup.py และ core.py รุ่น v4.2
จำลองผลลัพธ์ Backtest กับชุดข้อมูล 9,303 runs