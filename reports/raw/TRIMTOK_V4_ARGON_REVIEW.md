A) VERDICT: [REQUEST CHANGES] — โครงสร้างหลักของ v4.2 ถูกวางตามแผนแล้ว แต่พบ 5 Blockers ร้ายแรง (บั๊ก IndentationError ใน smart_skeletonize_code เมื่อพับเมธอดในคลาสที่มี docstring, การสูญเสียเนื้อหาไฟล์เมื่ออ่านซ้ำหลัง Cold Skeleton, การลืมเชื่อม adaptive_tool_head / Cascade Rescue ใน compress_tool_result, การเรียก hygiene() ทำลาย git diff ช่วง 1K–2K และฟังก์ชัน invalidate_shell_mutations / expire_old_turns ที่เขียนไว้แต่ไม่ได้ถูกเรียกใช้งานจริง)

B) BLOCKERS (ร้ายแรง — ต้องแก้ไขก่อน Merge)

[BLOCKER-1] (Priority 1 Violation — Syntax Corruption): smart_skeletonize_code สร้าง IndentationError และ SyntaxError ในโค้ด Python มาตรฐาน

พิกัด: trimtok/pruners.py:494-525

สาเหตุที่ 1 (Class Method + Docstring + Blank Line): เมื่อเมธอดในคลาส (Signature indent = 4 spaces, Body indent = 8 spaces) มี Docstring ตามด้วยบรรทัดว่าง 1 บรรทัด (ตามมาตรฐาน PEP 257 / Black / Ruff) บรรทัดที่ 507 จะเลื่อน body_start = doc_end + 1 ไปชี้ที่บรรทัดว่าง ("") ส่งผลให้บรรทัดที่ 521-523 คำนวณ indent_len = 0 และใช้ค่า Fallback "    " (4 spaces แทนที่จะเป็น 8 spaces) เมื่อเมธอดนั้นมี return ปิดท้ายที่ 8 spaces โค้ดที่ส่งออกจะวาง     pass ที่ 4 spaces (Dedent ออกจากเมธอด) แล้วตามด้วย         return ... ที่ 8 spaces ทำให้ ast.parse() พังด้วย IndentationError: unexpected indent ทันที

สาเหตุที่ 2 (Decorated Inner Function/Class): ใน Python AST ค่า stmt.lineno ของ FunctionDef/ClassDef คือบรรทัดของคำว่า def/class ไม่ใช่บรรทัดของ @decorator ตัวแรก หาก statement แรกที่ถูกพับมี @decorator บรรทัด @decorator จะไม่ถูกพับและค้างอยู่เหนือ pass ทำให้เกิด SyntaxError: invalid syntax

[BLOCKER-2] (Priority 1 Violation — Context Loss on Re-Read): การอ่านไฟล์ขนาดใหญ่ (>= 12KB) ซ้ำในเทิร์นถัดไปทำให้ Agent ไม่มีวันได้เห็นโค้ดจริง + แคชไฟล์ไม่เคยหมดอายุตาม mask_keep_turns + #prev_ref ใน file_delta ดึงจาก CCR ไม่ได้

พิกัด: trimtok/dedup.py:122-155, trimtok/dedup.py:761, trimtok/dedup.py:807-875, และ trimtok/__init__.py:364-380, 451-465

สาเหตุที่ 1 (Case C -> Case A Trap): ในการอ่านไฟล์ขนาด >= 12KB ครั้งแรก (Case C, บรรทัด 855-865) ระบบบันทึก file_cache[cache_key] = {"digest": digest, "text": result, ...} ก่อนส่งออกเฉพาะโครงร่าง file_skeleton เมื่อ Agent เรียก read_file ซ้ำในเทิร์นที่ 2 เพื่อดูเนื้อหา บรรทัดที่ 807 จะเข้าเงื่อนไข Case A (file_cache_hit) และส่งกลับเพียงรายชื่อฟังก์ชันย่อ (_build_file_outline) โดยอ้างว่า "identical to prior read in active context" ทั้งที่ในเทิร์นแรก Agent เพิ่งได้เห็นแค่ Skeleton เท่านั้น

สาเหตุที่ 2 (Stale Context Forever): แม้ CHANGELOG.md:36-37 จะระบุว่าอัปเดต expire_old_turns ให้ทำเครื่องหมาย expired กับไฟล์ที่เก่าเกิน mask_keep_turns แล้ว แต่ใน Diff จริง expire_old_turns ไม่ได้ถูกแก้ไขเลย และใน trimtok/__init__.py:462 การอ่านไฟล์ที่ได้ passthrough_reference คืนค่า out = None ทำให้ไม่เคยเรียก note_turn() ส่งผลให้ prev_entry["expired"] มีค่าเป็น False ตลอดกาลแม้ผ่านไปแล้ว 30 เทิร์นและเนื้อหาไฟล์หลุดจาก Context Window ไปนานแล้ว

สาเหตุที่ 3 (Broken #prev_ref in CCR): เมื่อ Cold Read เทิร์นแรกได้ passthrough_reference ฟังก์ชัน _finalize() ใน trimtok/__init__.py:365 จะไม่บันทึกเนื้อหาเทิร์นแรกลง SQLite CCR พอเทิร์นที่ 2 ไฟล์ถูกแก้แล้วเข้า Case B (file_delta บรรทัด 761) Header จะแสดง [tt:file_delta #cur_ref vs #prev_ref] ซึ่งหาก Agent เรียก trimtok_retrieve(hash="#<prev_ref>") จะเกิด Error "Not found or expired" ทันที

[BLOCKER-3] (Priority 1 & 2 Violation — Diff Corruption & Broken Repeat Fold in 1K–2K Terminal Band): compress_terminal ช่วง 512–2,047 chars ทำลาย Unified Diff และทิ้งผลลัพธ์ที่มีบรรทัดซ้ำ

พิกัด: trimtok/core.py:292-312

สาเหตุที่ 1 (Diff Corruption): บรรทัดที่ 298 เรียก hygiene(strip_ansi(output).rstrip()) โดยไม่ตรวจว่าเป็น Unified Diff หรือไม่ (kind == "diff") ทำให้ _WS_TRAIL_RE ลบช่องว่างเดี่ยว (" ") บนบรรทัด Context ว่างของ git diff ทิ้งทั้งหมด (ขัดกับ README.md:320 และ Priority 1)

สาเหตุที่ 2 (Inverted Repeat Condition): บรรทัดที่ 299-300 รัน folded_small, hidden_small = _fold_repeats(...) แล้วเช็ก if hidden_small == 0: นั่นแปลว่าถ้า Terminal log ขนาด 1.5KB มีทั้ง ANSI codes และบรรทัดซ้ำ 4 บรรทัด (hidden_small == 3) เงื่อนไข hidden_small == 0 จะเป็น เท็จ และโค้ดจะกระโดดออกไปคืนค่า passthrough_small ทันที (ไม่ได้ทั้งการพับบรรทัดซ้ำและไม่ได้ทั้งการล้าง ANSI/Whitespace)

[BLOCKER-4] (Priority 2 Violation — Unwired Adaptive Tool Header & Missing BPE Token Gate): compress_tool_result ไม่ได้เรียกใช้ adaptive_tool_head และไม่มี Cascade Gate Rescue

พิกัด: trimtok/dedup.py:1010-1035 และ trimtok/signals.py:274-307

สาเหตุที่ 1: ใน trimtok/dedup.py มีการ import adaptive_tool_head เข้ามาแต่ในท่อหลักของ compress_tool_result (บรรทัด 1021) ยังคงเรียก head = tool_head(...) แบบคงที่เหมือน v4.1.0, ไม่มีการทำ Cascade Gate Rescue (Tier 1 -> Tier 0), ไม่ได้บันทึก _rh ลง failed_hashes เมื่อติด reverted_gate หลังแปะ Header (บรรทัด 1024), และไม่ได้เช็ก estimate_tokens(new_result) < estimate_tokens(result) ก่อนส่งออก

สาเหตุที่ 2: ใน trimtok/signals.py:302-306 ความยาวของ adaptive_tool_head ระดับ Tier 2 ([tt:fold -... | retrieve(hash="...")] ยาว ~60 chars) ยาวกว่า Tier 3 (tool_head ยาว ~45 chars) ซึ่งกลับด้านกัน

[BLOCKER-5] (Priority 1 Violation — Dead Code): invalidate_shell_mutations ไม่ถูกเรียกใช้งานเลยในทั้งโปรเจกต์

พิกัด: trimtok/dedup.py:664-682, trimtok/core.py:286, trimtok/__init__.py:390

สาเหตุ: นิยามฟังก์ชัน invalidate_shell_mutations(command, session_id) ไว้ใน dedup.py และเขียนเคลมใน CHANGELOG.md:13 และ README.md:300 แต่ไม่มีการเรียกใช้ใน compress_terminal หรือ _on_transform เลย ทำให้เมื่อ Agent รันคำสั่ง Shell แก้ไขหรือ Revert ไฟล์ (เช่น git checkout -- file.py หรือ sed -i) แคชไฟล์ใน seen["files"] จะไม่ถูกล้าง

C) ZERO-LOSS AUDIT (ตารางตรวจสอบความสมบูรณ์ของข้อมูลรายกลไก)
กลไกใน v4.2	สิ่งที่รักษาไว้ครบถ้วน (Preserved 100%)	ความเสี่ยงที่ข้อมูลจะหายหรือผิดพลาดใน Diff ปัจจุบัน (Identified Failure Modes)	สถานะ


1. file_unchanged Outline




(dedup.py:807-827)

	ชื่อคลาส/ฟังก์ชัน 35 ตัวแรกพร้อมเลขบรรทัด L1..LN และคำแนะนำการดึงบรรทัดจริงผ่าน offset/limit	

1. เกิด False Hit กับไฟล์ >= 12KB ที่เทิร์นแรกส่งไปแค่ Skeleton ([BLOCKER-2] ข้อ 1)




2. แคชไม่หมดอายุตาม mask_keep_turns ทำให้ซ่อนไฟล์ที่หลุดจาก Context ไปแล้ว ([BLOCKER-2] ข้อ 2)




3. _build_file_outline ตัดบรรทัดด้วย s.split("{")[0] และ [:90] หาก Signature ยาวหลายบรรทัดจะเห็นเฉพาะบรรทัดแรก

	FAIL


2. file_delta (Mutated Re-Read)




(dedup.py:731-777, 829-849)

	ทุกบรรทัดที่ถูกเพิ่ม/แก้ไข และบรรทัดข้างเคียง ±12 บรรทัด พร้อมเลขบรรทัดจริง 1-indexed (ln_idx + 1|...)	

1. #prev_ref ใน Header ชี้ไปยัง Hash ของเทิร์นแรกที่ไม่ได้ถูกเซฟลง SQLite CCR ([BLOCKER-2] ข้อ 3)




2. ใน README.md และ CHANGELOG.md ระบุว่าแสดง Context ±15 บรรทัด แต่โค้ดตั้ง _READ_DELTA_CONTEXT_LINES = 12

	FAIL


3. smart_skeletonize_code




(pruners.py:459-564)

	Imports, Global constants, Decorators ของฟังก์ชันนอก, Docstring, ฟังก์ชันที่สั้นกว่า 15 บรรทัด, และ return/raise สุดท้าย (ไม่เกิน 2 บรรทัด)	

1. พังด้วย IndentationError ทันทีเมื่อเมธอดในคลาสมี Docstring + บรรทัดว่าง + return ([BLOCKER-1] ข้อ 1)




2. พังด้วย SyntaxError เมื่อ statement แรกในบอดี้มี @decorator ([BLOCKER-1] ข้อ 2)




3. Docstring เก็บไว้ ทั้งก้อน (body_start = doc_end + 1) ไม่ใช่เฉพาะบรรทัดแรกตามที่เขียนใน Docstring/README (ซึ่งปลอดภัยต่อ Syntax แต่ลดอัตราการบีบอัดหาก Docstring ยาว)

	FAIL


4. Adaptive Headers & Cascade Rescue




(signals.py:224-307, core.py:352-389)

	ข้อมูล Error (is_important) และ Quality Gate ใน core.py ยังทำงานครบถ้วน	1. ใน core.py:373 เมื่อตกไปยัง Tier-0 Rescue ใช้ clean_only = strip_ansi(output).rstrip() ซึ่งข้ามทั้ง prune_traceback และ prune_unified_diff ไปใช้ output ดิบที่ล้างแค่ ANSI (ปลอดภัยแต่ได้ผลน้อย) และใน dedup.py ยังไม่ได้เชื่อม adaptive_tool_head เข้ากับ compress_tool_result ([BLOCKER-4])	PARTIAL


5. Micro-Minify (512–2,047 chars)




(dedup.py:881-908, core.py:289-312)

	ข้อมูล JSON คงค่าและ Type เดิม 100%, ลบเฉพาะ Trailing spaces และบรรทัดว่างเกิน 2 บรรทัด	

1. ใน core.py:298 และ dedup.py:891 รัน hygiene() บน Unified Diff โดยไม่ยกเว้น ทำให้ช่องว่างนำหน้าบรรทัดว่างใน Diff หาย ([BLOCKER-3] ข้อ 1)




2. ใน core.py:300 เงื่อนไข if hidden_small == 0: ทำให้ Log ที่มีบรรทัดซ้ำไม่ถูกบีบอัดเลย ([BLOCKER-3] ข้อ 2)

	FAIL


6. raw_hashes -> failed_raw Fix




(dedup.py:948-955, 1013)

	ผลลัพธ์ Tool ที่ซ้ำเดิมข้ามเทิร์นไม่ถูกปล่อยหลุดแบบ Raw อีกต่อไป แต่ไหลเข้า fold_spans() เพื่อพับเป็น [@#refxN]	บรรทัดที่ 1024 เมื่อ len(new_result) >= len(result) (ติด Gate หลังแปะ Header) ลืมสั่ง failed_hashes.add(_rh) ทำให้ Hash ที่พังเพราะความยาว Header ต้องเสียเวลารัน Pipeline ซ้ำทุกครั้ง	PARTIAL
D) CORRECTNESS & STATE MANAGEMENT ISSUES

Thread-Safety / Race Condition บน _SEEN (trimtok/dedup.py:801-865, 948-955, 1013):

ฟังก์ชัน _handle_read_file_v42 และตัวแปร failed_hashes ใน compress_tool_result อ่านและเขียน seen["files"] และ seen["failed_raw"] (รวมถึงการวนลูป for _old_key in list(file_cache.keys()): file_cache.pop(...) ที่บรรทัด 852) โดยไม่ถือ _SEEN_LOCK ในขณะที่ _MUTATING_TOOLS (บรรทัด 937) และ invalidate_shell_mutations (บรรทัด 674) ถือ _SEEN_LOCK หาก Agent เรียก Parallel Tool Calls (read_file คู่กับ patch) ในเซสชันเดียวกันอาจเกิด Race Condition ได้

การเรียกผ่าน MCP Server (trimtok/mcp_server.py:155-160, 180-198) ไม่สอดคล้องกับ Hermes Hook (trimtok/__init__.py):

ใน mcp_server.py ฟังก์ชัน _tool_compress_terminal และ _tool_compress_tool ไม่ได้เช็ก if not stats.get("lossless_tier0"): ก่อนเรียก ccr.put() ทำให้ทุก Payload ในช่วง 512–2,047 chars ที่ผ่าน micro_minify_tool (lossless_tier0: True) ยังคงถูกเขียนลง SQLite CCR ทุกครั้งเมื่อรันผ่าน MCP

ใน _tool_compress_tool (mcp_server.py:187) ไม่ได้ส่ง args={"file_path": source_file} เข้าไปยัง compress_tool_result และตั้ง default_tool = f"Read:{os.path.basename(source_file)}" ซึ่งไม่ตรงกับ tool_name == "read_file" ทำให้ฟีเจอร์ _handle_read_file_v42 ไม่ทำงานเลยเมื่อเรียกผ่าน MCP trimtok_compress_tool(file_path=...)

เงื่อนไข Tier Selection ใน adaptive_terminal_head (trimtok/signals.py:258-263):

โค้ดใช้ if raw_len <= 4096 and gross_saved_chars < 240: tier = 1 หากมี Terminal Output ขนาด 4,500 chars (> 4096) ที่พับบรรทัดซ้ำได้เพียง 120 chars (gross_saved_chars < 240) เงื่อนไขแรกจะเป็นเท็จและได้ Tier 2 หรือหากขนาด 9,000 chars ที่พับได้ 120 chars จะได้ Tier 3 (~165 chars) ซึ่งใหญ่กว่า gross_saved_chars เสมอ ทำให้ต้องเสียเวลารัน estimate_tokens ซ้ำใน Cascade Rescue แทนที่จะเลือก tier = 1 ตั้งแต่แรกเมื่อ gross_saved_chars < 200

E) PERFORMANCE AUDIT (Priority 3: < 10ms & Sub-ms Fast Lane)

difflib.SequenceMatcher ใน _build_read_delta (trimtok/dedup.py:741) เสี่ยงทะลุ 50ms บนไฟล์ใหญ่และไม่มี Deadline Guard:

_handle_read_file_v42 ถูกเรียกตั้งแต่บรรทัดที่ 942 ก่อน if deadline is not None: deadline.check() และไม่ได้รับพารามิเตอร์ deadline เข้าไปเลย

หาก Agent แก้ไขและอ่านไฟล์ขนาด 60KB–95KB (2,500+ บรรทัด) ซ้ำ difflib.SequenceMatcher(None, prev_lines, cur_lines, autojunk=False) มีความซับซ้อนระดับ 
𝑂
(
𝑁
2
)
O(N
2
) ซึ่งอาจใช้เวลา 40–200 ms โดยไม่มี deadline คอยตัดการทำงาน ต้องเพิ่มเพดานขนาดไฟล์สำหรับ Delta (เช่น len(cur_lines) <= 1500) และส่ง deadline เข้าไปตรวจเช็ก

เรียก estimate_tokens() ซ้ำซ้อน 4 ครั้งต่อ 1 Call ใน _handle_read_file_v42 (trimtok/dedup.py:816-825, 837-846, 862-871):

ในทั้ง Case A, Case B และ Case C โค้ดเรียก estimate_tokens(result) และ estimate_tokens(stub/delta_view/candidate) ในบรรทัด if แล้วเรียกซ้ำอีกครั้งใน return { "est_tokens_before": estimate_tokens(result), "est_tokens_after": ... } ทำให้เสียเวลาคำนวณ BPE ซ้ำ 2 เท่าโดยไม่จำเป็น (~0.5–1.2 ms ฟรีๆ ต่อครั้ง)

micro_minify_tool (trimtok/dedup.py:892-894) แปลง JSON ซ้ำ 2 รอบใน 1 Call:

บรรทัดที่ 893 เรียก minify_json(text) ซึ่งรัน fast_json_loads + json.dumps

บรรทัดที่ 894 เรียก remember(text, session_id, owner=digest) ทันที ซึ่งข้างในเรียก _index_json_inners() ที่รัน fast_json_loads(text) ซ้ำเป็นรอบที่ 2 และเดิน Tree ทั้งก้อนเพื่อเก็บลง seen["lines"] (ทั้งที่ total_saved อาจเป็น 0 และไม่ได้บีบอัดอะไรเลย) ส่งผลให้ micro_minify_tool ช้าลงกว่าที่ควรเป็น 2–3 เท่าบน JSON

F) VERDICT RATIONALE & SURGICAL FIXES (โค้ดที่ต้องแก้แบบระบุพิกัดบรรทัด)

เพื่อให้ผ่านเกณฑ์ [APPROVE] อย่างสมบูรณ์โดยไม่ละเมิด Priority 1, 2, 3 ให้ดำเนินการแก้ไข 5 จุดต่อไปนี้:

Fix 1: แก้ IndentationError และ @decorator boundary ใน trimtok/pruners.py:488-526

ใช้ statement แรกที่จะถูกพับจริง (first_fold_stmt) ในการหาทั้งเลขบรรทัดเริ่มต้น (รวม @decorator ถ้ามี) และหา Indentation จาก col_offset หรือบรรทัดของ Statement นั้น (แทนที่จะอ่านบรรทัดว่างหลัง Docstring):

Python
# trimtok/pruners.py (แทนที่บรรทัด 488-526)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not getattr(node, "body", None) or not hasattr(node, "end_lineno"):
            continue

        first_stmt = node.body[0]
        first_fold_stmt = first_stmt

        # If the first statement is a docstring, keep it and start folding at body[1].
        if (
            isinstance(first_stmt, ast.Expr)
            and isinstance(getattr(first_stmt, "value", None), ast.Constant)
            and isinstance(first_stmt.value.value, str)
        ):
            if len(node.body) <= 1:
                continue  # docstring only -> nothing to fold
            first_fold_stmt = node.body[1]

        # Include decorators of the first folded statement so @decorator is never left above pass.
        decs = getattr(first_fold_stmt, "decorator_list", None)
        if decs:
            body_start = min(getattr(d, "lineno", first_fold_stmt.lineno) for d in decs)
        else:
            body_start = first_fold_stmt.lineno

        body_end = node.end_lineno or body_start

        # If the last statement is a short return/raise, keep it (return-type clarity).
        last_stmt = node.body[-1]
        if (
            last_stmt is not first_fold_stmt
            and isinstance(last_stmt, (ast.Return, ast.Raise))
            and hasattr(last_stmt, "lineno")
        ):
            last_span = (getattr(last_stmt, "end_lineno", last_stmt.lineno) or last_stmt.lineno) - last_stmt.lineno + 1
            if last_span <= 2 and last_stmt.lineno > body_start:
                body_end = last_stmt.lineno - 1

        span_lines = body_end - body_start + 1
        if span_lines < min_body_lines:
            continue

        # Derive exact indentation from first_fold_stmt line (never from a blank line after docstring).
        stmt_line = lines[first_fold_stmt.lineno - 1] if 1 <= first_fold_stmt.lineno <= total_lines else ""
        indent_len = len(stmt_line) - len(stmt_line.lstrip(" \t"))
        if indent_len > 0:
            indent = stmt_line[:indent_len]
        else:
            indent = " " * (getattr(first_fold_stmt, "col_offset", getattr(node, "col_offset", 0) + 4))
        fold_ranges.append((body_start, body_end, indent, node.name))

Fix 2: แก้ core.py (ป้องกัน git diff พังในช่วง 512–2,047 chars, แก้เงื่อนไข hidden_small, และเรียก invalidate_shell_mutations)
Python
# trimtok/core.py (แทนที่บรรทัด 286-314)
        if not isinstance(output, str):
            return output, {"gate": "passthrough_non_str", "ms": 0.0}
        if session_id and command:
            try:
                from .dedup import invalidate_shell_mutations
                invalidate_shell_mutations(command, session_id)
            except Exception:
                pass
        if len(output) < min_chars:
            if len(output) >= 512 and not SENTINEL_RE.search(output[:512]):
                try:
                    kind_small = detect_kind(output)
                    clean = strip_ansi(output)
                    if kind_small != "diff":
                        try:
                            from .dedup import hygiene
                        except (ImportError, ValueError):
                            from trimtok.dedup import hygiene
                        clean, _ = hygiene(clean.rstrip())
                    folded_small, hidden_small = _fold_repeats(_classify(clean.split("\n")), deadline)
                    if hidden_small >= 3:
                        digest_s = hashlib.sha256(output.encode("utf-8", "replace")).hexdigest()[:12]
                        body_s = "\n".join(ln for ln, _ in folded_small)
                        head_s = adaptive_terminal_head(kind_small, hidden_small, digest_s, len(output), len(output) - len(body_s), force_tier=1)
                        cand_s = "\n".join(head_s + [body_s])
                        tb, ta = estimate_tokens(output), estimate_tokens(cand_s)
                        if len(cand_s) + 40 <= len(output) and ta < tb:
                            return cand_s, {
                                "gate": "ok", "kind": kind_small, "digest": digest_s,
                                "before_chars": len(output), "after_chars": len(cand_s),
                                "saved_chars": len(output) - len(cand_s),
                                "est_tokens_before": tb, "est_tokens_after": ta,
                                "hidden_lines": hidden_small, "dropped_lines": 0,
                                "ms": (time.perf_counter() - t0) * 1000,
                            }
                    if len(clean) < len(output):
                        tb, ta = estimate_tokens(output), estimate_tokens(clean)
                        if ta < tb:
                            return clean, {
                                "gate": "ok", "kind": kind_small, "lossless_tier0": True,
                                "before_chars": len(output), "after_chars": len(clean),
                                "saved_chars": len(output) - len(clean),
                                "est_tokens_before": tb, "est_tokens_after": ta,
                                "hidden_lines": 0, "dropped_lines": 0,
                                "ms": (time.perf_counter() - t0) * 1000,
                            }
                except Exception:
                    pass
            return output, {"gate": "passthrough_small", "ms": 0.0}

Fix 3: แก้ _handle_read_file_v42, expire_old_turns, micro_minify_tool, และเชื่อม adaptive_tool_head ใน trimtok/dedup.py

ใน expire_old_turns (trimtok/dedup.py:145): เพิ่มการหมดอายุของ seen.get("files", {}) เมื่อ digest อยู่ใน doomed:

Python
for fmeta in seen.get("files", {}).values():
    if fmeta.get("digest") in doomed:
        fmeta["expired"] = True


ใน trimtok/__init__.py:461-464 (_on_tool_result): บันทึก note_turn สำหรับ read_file เสมอ (แม้เป็น passthrough_reference) เพื่อให้ expire_old_turns นับอายุเทิร์นของไฟล์ได้จริง:

Python
if out is not None or stats.get("gate") == "passthrough_reference":
    note_turn(result, str(stats.get("digest", "")), session_id=sid, turn_id=turn)


ใน _handle_read_file_v42 (trimtok/dedup.py:780-878):

รับพารามิเตอร์ deadline: Any | None = None และเช็ก deadline.check()

เก็บค่า tok_before = estimate_tokens(result) เพียงครั้งเดียว

ใน Case C (Cold Oversize Skeleton): ตั้งค่า "skeleton_only": True ใน file_cache[cache_key] และใน Case A (file_cache_hit, บรรทัด 807): เช็ก and not prev_entry.get("skeleton_only", False) เพื่อที่หากเทิร์นแรก Agent ได้ไปแค่ Skeleton แล้วเทิร์นที่ 2 เรียก read_file ซ้ำเพื่อขออ่านไฟล์เต็ม ระบบจะคืนไฟล์ Verbatim ให้แทนที่จะส่ง Outline ซ้ำ

ใน Case B (file_delta): จำกัดเฉพาะไฟล์ที่ len(cur_lines) <= 1500 เพื่อป้องกัน SequenceMatcher ช้าเกิน 10ms และบันทึก ccr_put(prev_text) เพื่อให้ #prev_ref ดึงข้อมูลผ่าน trimtok_retrieve ได้จริง

ใน micro_minify_tool (trimtok/dedup.py:881-908):

ข้าม hygiene() หากพบ "diff --git" หรือ \n@@ - ใน result[:512]

เรียก remember() เฉพาะเมื่อ total_saved >= 4 หรือข้าม _index_json_inners เพื่อไม่ให้ parse JSON ซ้ำ 2 รอบ

ใน compress_tool_result (trimtok/dedup.py:1010-1035): เชื่อม adaptive_tool_head, BPE Token Gate และ Cascade Gate Rescue เข้าไปตามสเปก:

Python
     if len(folded) >= len(result):
         failed_hashes.add(_rh)
         return result, {
             "gate": "reverted_gate", "before_chars": len(result),
             "ms": (time.perf_counter() - t0) * 1000,
         }
     digest = _rh
     is_lossless_only = (
         v_saved == 0 and ss_saved == 0 and f_saved == 0 and i_saved == 0
         and dt_saved == 0 and ht_saved == 0 and sk_saved == 0 and not toon_used
     )
     if ht_saved > 0:
         head = headtail_head(ht_head, ht_tail, ht_omitted, digest)
     else:
         head = adaptive_tool_head(
             len(result) - len(folded), digest, len(result), is_lossless_only=is_lossless_only
         )
     new_result = f"{head}\n{folded}" if head else folded
     tok_before = estimate_tokens(result)
     tok_after = estimate_tokens(new_result)

     if len(new_result) >= len(result) or tok_after >= tok_before:
         if not is_lossless_only and ht_saved == 0:
             nano_head = adaptive_tool_head(len(result) - len(folded), digest, len(result), force_tier=1)
             nano_cand = f"{nano_head}\n{folded}"
             nano_tok = estimate_tokens(nano_cand)
             if len(nano_cand) < len(result) and nano_tok < tok_before:
                 new_result, tok_after = nano_cand, nano_tok
         if len(new_result) >= len(result) or tok_after >= tok_before:
             failed_hashes.add(_rh)
             return result, {
                 "gate": "reverted_gate", "before_chars": len(result),
                 "ms": (time.perf_counter() - t0) * 1000,
             }
     return new_result, {
         "gate": "ok", "digest": digest,
         "lossless_tier0": is_lossless_only and not head,
         "before_chars": len(result), "after_chars": len(new_result),
         "saved_chars": len(result) - len(new_result),
         "est_tokens_before": tok_before,
         "est_tokens_after": tok_after,
         ...

Fix 4: แก้ลำดับขนาด Tier ใน trimtok/signals.py:255-307

ใน adaptive_terminal_head (บรรทัด 258): เปลี่ยนเป็น if gross_saved_chars < 240: เพื่อให้ทุก Payload ที่ประหยัดตัวอักษรได้น้อยกว่า 240 ตัวอักษรใช้ Tier 1 Nano Header เสมอ (ไม่ว่าจะยาวเกิน 4,096 chars หรือไม่)

ใน adaptive_tool_head (บรรทัด 304-306): ให้ Tier 2 ใช้ tool_head(saved_chars, digest.lstrip("#")) (~45 chars) และ Tier 3 ใช้ [tt:tool kind=result saved={saved_chars} {ref} | retrieve(hash="{ref}")] (~68 chars) เพื่อให้ความยาว Header เรียงจากน้อยไปมาก (Tier 0 < Tier 1 < Tier 2 < Tier 3) อย่างถูกต้อง

ขั้นตอนถัดไปสำหรับการปิดงานแก้ไข Trimtok v4.2:
สร้าง Git Patch (Unified Diff) สำหรับแก้ทั้ง 5 Blockers ทันที
เพิ่ม Unit Tests ดักจับ Class Method Docstring และ Oversize Re-Read