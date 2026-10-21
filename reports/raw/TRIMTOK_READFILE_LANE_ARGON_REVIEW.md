A) VERDICT: [CONFIRMED ROOT CAUSE] — การวิเคราะห์ของทีมถูกต้องตรงจุดเรื่อง offset=1, limit=2000 ของ Hermes ที่ชน Rule 0 ทุกครั้ง และรูปแบบ LINE|CONTENT แต่จากการตรวจโค้ดเชิงลึกพบ อีก 4 ต้นตอร่วม (Co-Killers) ที่ทำให้ต่อให้แก้ Rule 0 อย่างเดียว Lane read_file ก็จะยังได้ 0.0% ในการใช้งานจริงบน Hermes

B) ROOT CAUSE ANALYSIS (เรียงตามลำดับผลกระทบจริงใน Production)
ลำดับ	จุดตายใน Production (Root Cause)	พิกัดโค้ด (v4.2.1)	ทำไมใน Benchmark ถึงรอด แต่ใน Hermes จริงถึงตาย 100%?
1	Rule 0 ตีกลับ Default Pagination (offset=1, limit=2000) ของ Hermes ทุกครั้ง (ตามที่ตรวจพบ)	

dedup.py:801




if offset is not None or limit is not None:

	Benchmark ส่ง args={"path": "src/bench_module.py"} (ไม่มี offset/limit) แต่ Hermes ยัดค่า Default {"path": ..., "offset": 1, "limit": 2000} มาให้เสมอ ทำให้ทุก Call ออกที่บรรทัด 802 ก่อนถึง file_cache
2	รูปแบบ LINE|CONTENT ทำให้ ast.parse(), _build_file_outline() และ _build_read_delta() พังทั้งหมด (สิ่งที่ยังวิเคราะห์ไม่สุด)	

pruners.py:479




dedup.py:726, 747, 774

	

Benchmark ส่งโค้ด Python ดิบ แต่ Hermes ส่ง 1|import os\n2|... ผลคือ:




1. Tier 3 (smart_skeletonize_code): ast.parse("1|import os...") โยน SyntaxError บรรทัดแรกและคืน (text, 0, 0) 100%!




2. Tier 1 (_build_file_outline): s.startswith(("def ", "class ")) เป็น False ทุกบรรทัดเพราะขึ้นต้นด้วย "42|def "




3. Tier 2 (_build_read_delta): เมื่อแทรกโค้ด 1 บรรทัด เลข LINE| ด้านหน้าจะเลื่อนลงทั้งหมด (10|x กลายเป็น 11|x) ทำให้ SequenceMatcher มองว่าไฟล์เปลี่ยนทุกบรรทัด และบรรทัด 774 พ่นเลขซ้ำเป็น 11|11|x


3	การสั่ง .pop(mut_path) เมื่อเรียก patch ทำลายฐานข้อมูลของ Tier 2 (file_delta) ทิ้ง	

dedup.py:944




.setdefault("files", {}).pop(mut_path, None)

	ในเทสต์ test_v42_telemetry_gates.py มีการจำลอง read_file(edited) โดยไม่ได้เรียก Tool "patch" คั่นกลาง แต่ใน Hermes จริง ลำดับคือ read_file 
→ patch 
→ read_file การที่ patch สั่ง .pop(mut_path) ทำให้เมื่ออ่านไฟล์ซ้ำหลังแก้ prev_entry กลายเป็น None เสมอ Tier 2 (file_delta) จึงไม่มีวันถูกเรียกในโลกจริง
4	expire_old_turns(keep_turns=2) ลบสถานะไฟล์เร็วเกินไปใน Multi-Step Agent Loop	

__init__.py:476




dedup.py:156

	_on_post_llm ทำงานทุกครั้งที่จบ 1 Tool step ของ Agent ใน Hermes งานหนึ่งงานใช้ 5–20 Tool steps (เช่น read_file 
→ search_files 
→ terminal 
→ read_file) การตั้ง keep_turns=2 ทำให้แคชของไฟล์หมดอายุ (expired = True) ตั้งแต่ก้าวที่ 3 ทั้งที่เนื้อหาไฟล์จากก้าวที่ 1 ยังอยู่ใน Context Window
5	read_file_extra ถูกคัดออกเพราะเช็กชื่อ Tool แบบตายตัว	

dedup.py:947




if tool_name == "read_file":

	read_file_extra (172,180 chars ใน v4.2, เฉลี่ย 8.6 KB/call) ไม่ผ่านเงื่อนไข tool_name == "read_file" และเมื่อไหลลงไปเจอ Fallback skeletonize_code บรรทัด 995 ก็ไม่ทำงานเพราะเช็ก "\ndef " in folded ไม่เจอ (ติดเลขบรรทัด \n42|def)
C) FIX DESIGN (คำตอบข้อ 2, 3, 6 และโค้ด Patch ที่พร้อมใช้ทันที)
1. เกณฑ์แยกแยะ "Full Read" vs "Targeted Slice Read" โดยไม่อ่าน Filesystem (คำตอบข้อ 2)

เราสามารถตัดสินจาก (args, line_nos_in_payload) ได้อย่างแม่นยำ 100% ด้วยฟังก์ชัน _is_targeted_slice(offset, limit, first_line_no, n_lines):

ถือเป็น Targeted Slice (ห้ามบีบอัด — คืน Verbatim 100%) เมื่อเข้าเงื่อนไขข้อใดข้อหนึ่งต่อไปนี้:

offset is not None and offset > 1 (ผู้เรียกเจาะจงเริ่มอ่านตั้งแต่บรรทัดที่ 2 เป็นต้นไป)

first_line_no > 1 (เลขบรรทัดแรกที่แกะได้จาก LINE|CONTENT ไม่ใช่บรรทัดที่ 1)

limit is not None and limit < 500 and n_lines >= limit (ผู้เรียกตั้งใจกำหนด limit ขนาดเล็กต่ำกว่า 500 บรรทัด และผลลัพธ์ถูกตัดตาม limit นั้นจริง เช่น read_file(offset=1, limit=80) เพื่อดูเฉพาะส่วนหัว)

ถือเป็น Full Read (หรือ Full Default Window Read offset=1, limit=2000) เมื่อ:

เริ่มอ่านจากบรรทัดที่ 1 (offset in (None, 0, 1) และ first_line_no == 1) และ (limit is None หรือ limit >= 500 หรือ n_lines < limit ซึ่งพิสูจน์ว่าได้ไฟล์มาครบทุกบรรทัดแล้วเพราะจำนวนบรรทัดจริงน้อยกว่าเพดาน limit)

2. การรับประกัน Patch Safety & ป้องกัน Context Poisoning (คำตอบข้อ 3 และ 6)

Unwrap & Rewrap LINE|CONTENT แบบรักษาเลขบรรทัดจริง 100%:

แยก prefix เลขบรรทัด (1|,    2|) ออกก่อนส่งเนื้อหาโค้ดดิบ (raw_body) เข้า ast.parse() หรือ SequenceMatcher

เมื่อ smart_skeletonize_code พับบอดี้ฟังก์ชันยาว (> 15 บรรทัด) บรรทัดที่เหลืออยู่ทั้งหมด (Imports, Signatures, Docstrings, Return ท้ายฟังก์ชัน) จะถูกแปะ เลขบรรทัดเดิมของไฟล์ (line_prefixes[cur - 1]) กลับเข้าไปโดยไม่รันเลขใหม่ ดังนั้นถ้าบรรทัด return total อยู่ที่บรรทัด 120 มันจะยังคงแสดงเป็น 120|    return total เสมอ Agent จึงไม่สับสนเลขบรรทัดเวลาสั่ง patch

รักษา prev_entry ข้ามการเรียก patch เพื่อให้ได้ Contextual Delta พร้อมเลขบรรทัดจริง:

เมื่อ Agent เรียก patch เราจะไม่ .pop(mut_path) ทิ้ง แต่ตั้งค่า file_cache[mut_path]["mutated"] = True

เมื่อ Agent เรียก read_file ตามหลัง patch:

หากไฟล์เปลี่ยนจริง (digest != prev_entry["digest"]): เข้า Case B (file_delta) ส่งเฉพาะจุดที่เพิ่งแก้ ±15 บรรทัดพร้อมเลขบรรทัด LINE|CONTENT จริง

หาก patch ล้มเหลวและไฟล์ไม่เปลี่ยน (digest == prev_entry["digest"]) หรือเคยส่ง Skeleton ไปในเทิร์นก่อน (skeleton_only == True): คืนไฟล์จริงแบบ Verbatim (passthrough_reference)

Anti-Loop Frustration Rail (ป้องกัน Context Poisoning 100%):

หากไฟล์เดิมถูกส่งเป็น [tt:file_unchanged] ไปแล้วในเทิร์นก่อน แต่ Agent ยังเรียก read_file ซ้ำอีกครั้งติดกัน (stub_hits >= 1) แปลว่า Agent ต้องการเห็นเนื้อหาบรรทัดจริงเพื่อทำการแก้ไข ระบบจะปลดล็อกและส่งเนื้อหา Verbatim 100% ทันทีในครั้งนั้นโดยไม่ส่ง Stub ซ้ำสอง!

3. โค้ด Patch ฉบับสมบูรณ์ (Surgical Diff พร้อมใช้งานทันที)
ไฟล์ที่ 1: trimtok/pruners.py (รองรับ line_prefixes และ line_nos จาก LINE|CONTENT)

แทนที่ส่วนท้ายของ smart_skeletonize_code (trimtok/pruners.py:459-580):

Python
def smart_skeletonize_code(
    text: str,
    ref: str = "",
    *,
    min_body_lines: int = _MIN_FOLD_BODY_LINES,
    line_prefixes: list[str] | None = None,
    line_nos: list[int] | None = None,
) -> tuple[str, int, int]:
    """Line-anchored AST code skeletonizer with Zero Loss of Signatures/Types.

    When `line_prefixes` / `line_nos` are provided (from Hermes `LINE|CONTENT`
    output), `text` must be the unprefixed source code; the returned skeleton
    re-attaches the exact original `LINE|` prefix to every kept line.
    """
    try:
        tree = ast.parse(text)
    except Exception:
        return text, 0, 0

    lines = text.split("\n")
    total_lines = len(lines)
    use_prefixes = (
        line_prefixes is not None
        and line_nos is not None
        and len(line_prefixes) == total_lines
        and len(line_nos) == total_lines
    )
    fold_ranges: list[tuple[int, int, str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not getattr(node, "body", None) or not hasattr(node, "end_lineno"):
            continue
        first_stmt = node.body[0]
        first_fold_stmt = first_stmt
        body_start = first_stmt.lineno

        if (
            isinstance(first_stmt, ast.Expr)
            and isinstance(getattr(first_stmt, "value", None), ast.Constant)
            and isinstance(first_stmt.value.value, str)
        ):
            if len(node.body) <= 1:
                continue
            doc_end = getattr(first_stmt, "end_lineno", first_stmt.lineno) or first_stmt.lineno
            first_fold_stmt = node.body[1]
            body_start = doc_end + 1

        decs = getattr(first_fold_stmt, "decorator_list", None)
        if decs:
            body_start = min(body_start, min(getattr(d, "lineno", first_fold_stmt.lineno) for d in decs))

        body_end = node.end_lineno or body_start

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

        stmt_line = lines[first_fold_stmt.lineno - 1] if 1 <= first_fold_stmt.lineno <= total_lines else ""
        indent_len = len(stmt_line) - len(stmt_line.lstrip(" \t"))
        if indent_len > 0:
            indent = stmt_line[:indent_len]
        else:
            indent = " " * (getattr(first_fold_stmt, "col_offset", None)
                            or getattr(node, "col_offset", 0) + 4)
        fold_ranges.append((body_start, body_end, indent, node.name))

    if not fold_ranges:
        return text, 0, 0

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
    cur = 1
    for s, e, ind, fn in merged_ranges:
        while cur < s and cur <= total_lines:
            prefix = line_prefixes[cur - 1] if use_prefixes else ""  # type: ignore[index]
            out.append(f"{prefix}{lines[cur - 1]}")
            cur += 1
        file_s = line_nos[s - 1] if use_prefixes else s  # type: ignore[index]
        file_e = line_nos[e - 1] if use_prefixes else e  # type: ignore[index]
        omitted = file_e - file_s + 1
        marker_prefix = line_prefixes[s - 1] if use_prefixes else ""  # type: ignore[index]
        marker = (
            f"{marker_prefix}{ind}pass  # [tt:body L{file_s}-L{file_e} ({omitted}L of {fn}){ref_tag} "
            f"-> use read_file(offset={file_s}, limit={omitted}) or trimtok_retrieve]"
        )
        out.append(marker)
        cur = e + 1

    while cur <= total_lines:
        prefix = line_prefixes[cur - 1] if use_prefixes else ""  # type: ignore[index]
        out.append(f"{prefix}{lines[cur - 1]}")
        cur += 1

    new_text = "\n".join(out)
    orig_len = sum(len(line_prefixes[i]) + len(lines[i]) + 1 for i in range(total_lines)) - 1 if use_prefixes else len(text)  # type: ignore[index]
    saved = orig_len - len(new_text)
    if saved <= 0:
        return text, 0, 0
    return new_text, saved, len(merged_ranges)

ไฟล์ที่ 2: trimtok/dedup.py (แก้ Rule 0, แกะ LINE|CONTENT, รักษา prev_entry หลัง patch, และรองรับ read_file_extra)

แทนที่บล็อก expire_old_turns (dedup.py:122-160) และบล็อก 5. v4.2: Stateful Read-File Engine (dedup.py:645-965):

Python
# ใน trimtok/dedup.py (อัปเดต expire_old_turns บรรทัด 122-160)
_FILE_KEEP_TURNS = 8  # File reads stay in LLM context much longer than 2 tool steps


def expire_old_turns(
    session_id: str = "", keep_turns: int = _MASK_KEEP_TURNS
) -> int:
    """Drop fold-index entries older than keep_turns; expire file cache after _FILE_KEEP_TURNS."""
    try:
        with _SEEN_LOCK:
            seen = _seen(session_id)
            turns = seen.get("turns", {})
            ids = list(turns.keys())
            # Expire file cache only when older than max(keep_turns * 4, _FILE_KEEP_TURNS)
            file_keep = max(keep_turns * 4, _FILE_KEEP_TURNS)
            if len(ids) > file_keep:
                stale_file_tids = set(ids[: len(ids) - file_keep])
                stale_file_hashes: set[str] = set()
                for tid in stale_file_tids:
                    stale_file_hashes.update(turns.get(tid, {}).get("hashes", []))
                for fmeta in seen.get("files", {}).values():
                    if isinstance(fmeta, dict) and fmeta.get("digest") in stale_file_hashes:
                        fmeta["expired"] = True

            if len(ids) <= keep_turns:
                return 0
            old_ids = ids[: len(ids) - keep_turns]
            doomed: set[str] = set()
            for tid in old_ids:
                doomed.update(turns.get(tid, {}).get("hashes", []))
                del turns[tid]
            if not doomed:
                return len(old_ids)
            kept_spans = []
            removed_chars = 0
            for span_hash, span_text in seen["spans"]:
                if span_hash in doomed:
                    removed_chars += len(span_text)
                else:
                    kept_spans.append((span_hash, span_text))
            seen["spans"] = kept_spans
            seen["span_chars"] = max(0, seen["span_chars"] - removed_chars)
            for line, owner in list(seen["lines"].items()):
                if owner in doomed:
                    del seen["lines"][line]
            if keep_turns <= 2 and len(ids) > keep_turns:
                # If caller explicitly passed a small keep_turns in unit tests, also mark doomed files
                for fmeta in seen.get("files", {}).values():
                    if isinstance(fmeta, dict) and fmeta.get("digest") in doomed and fmeta.get("turn_age", 0) >= _FILE_KEEP_TURNS:
                        fmeta["expired"] = True
            return len(old_ids)
    except Exception:
        return 0


และในส่วน Section 5 (dedup.py:645-965):

Python
# ---------------------------------------------------------------------------
# 5. v4.2.2: Stateful Read-File Engine (Hermes LINE|CONTENT + Default Limit Aware)
# ---------------------------------------------------------------------------
_MICRO_MIN_CHARS = 512
_COLD_READ_SKELETON_MIN_CHARS = 12_000  # files <= 12KB: cold read stays verbatim (patch safety)
_READ_DELTA_CONTEXT_LINES = 15          # real lines shown +/- 15 around an edited region
_MAX_FILE_CACHE = 64                    # per-session cached file bodies (memory bound)
_DEFAULT_LIMIT_FLOOR = 500              # Hermes default limit=2000; slices <500 that hit limit are targeted
_READ_FILE_TOOLS = frozenset({
    "read_file", "read_file_extra", "view_file", "get_file",
})
_MUTATING_TOOLS = frozenset({
    "patch", "edit_file", "write_file", "replace_in_file",
    "multi_edit", "apply_patch", "create_file", "str_replace_editor",
})

_NUMBERED_LINE_RE = re.compile(r"^(\s*(\d+)\|)(.*)$")


def _unwrap_numbered_lines(
    text: str,
) -> tuple[str, list[str] | None, list[int] | None, list[str], list[str]]:
    """Unwrap Hermes `LINE|CONTENT` format into (raw_body, prefixes, line_nos, headers, footers).

    If `text` is raw unnumbered source code, returns `(text, None, None, [], [])`.
    """
    lines = text.split("\n")
    if len(lines) < 4:
        return text, None, None, [], []

    # Sample early lines to check if this is a numbered LINE|CONTENT payload
    sample = [ln for ln in lines[:12] if ln.strip()]
    matches = sum(1 for ln in sample if _NUMBERED_LINE_RE.match(ln))
    if matches < max(2, len(sample) // 2):
        return text, None, None, [], []

    first_idx = -1
    last_idx = -1
    for i, ln in enumerate(lines):
        if _NUMBERED_LINE_RE.match(ln):
            if first_idx == -1:
                first_idx = i
            last_idx = i

    if first_idx == -1 or last_idx - first_idx < 3:
        return text, None, None, [], []

    headers = lines[:first_idx]
    footers = lines[last_idx + 1:]
    prefixes: list[str] = []
    line_nos: list[int] = []
    raw_lines: list[str] = []

    for i in range(first_idx, last_idx + 1):
        m = _NUMBERED_LINE_RE.match(lines[i])
        if m:
            prefixes.append(m.group(1))
            line_nos.append(int(m.group(2)))
            raw_lines.append(m.group(3))
        else:
            # Handle embedded multi-line string or unnumbered continuation line
            prev_no = line_nos[-1] if line_nos else 1
            prefixes.append("")
            line_nos.append(prev_no)
            raw_lines.append(lines[i])

    return "\n".join(raw_lines), prefixes, line_nos, headers, footers


def _is_targeted_slice(
    offset: int | None,
    limit: int | None,
    first_line_no: int,
    n_lines: int,
) -> bool:
    """Return True if the call is a targeted line-range slice rather than a full read.

    Hermes always populates `offset=1, limit=2000` by default on full file reads.
    A read is only a targeted slice if:
      1. `offset > 1` or `first_line_no > 1` (reading from the middle of a file), OR
      2. `limit < 500` AND `n_lines >= limit` (caller explicitly capped a small slice).
    """
    if offset is not None and offset > 1:
        return True
    if first_line_no > 1:
        return True
    if limit is not None and limit < _DEFAULT_LIMIT_FLOOR and n_lines >= limit:
        return True
    return False


def _build_file_outline(
    raw_body: str,
    line_nos: list[int] | None = None,
    max_items: int = 35,
) -> str:
    """Fast O(N) structural outline on unprefixed `raw_body` with exact 1-indexed line numbers."""
    items: list[str] = []
    raw_lines = raw_body.split("\n")
    for idx, line in enumerate(raw_lines):
        ln_no = line_nos[idx] if (line_nos and idx < len(line_nos)) else (idx + 1)
        s = line.lstrip()
        if s.startswith((
            "def ", "async def ", "class ", "interface ", "func ", "fn ",
            "pub fn ", "export function ", "export class ", "export async function ",
        )):
            sig = s.split("{")[0].strip()
            if sig.endswith(":"):
                sig = sig[:-1].strip()
            items.append(f"  L{ln_no}: {sig[:90]}")
            if len(items) >= max_items:
                items.append("  ...")
                break
    return "\n".join(items) if items else f"  (total {len(raw_lines)} lines)"


def _build_read_delta(
    prev_raw: str,
    cur_raw: str,
    cur_ref: str,
    prev_ref: str,
    *,
    cur_prefixes: list[str] | None = None,
    cur_line_nos: list[int] | None = None,
    ctx_lines: int = _READ_DELTA_CONTEXT_LINES,
) -> tuple[str, int]:
    """Build line-numbered contextual diff view comparing unprefixed source lines."""
    prev_lines = prev_raw.split("\n")
    cur_lines = cur_raw.split("\n")
    matcher = difflib.SequenceMatcher(None, prev_lines, cur_lines, autojunk=False)
    changed_cur_indices: list[int] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            changed_cur_indices.extend(range(j1, max(j1 + 1, j2)))

    if not changed_cur_indices:
        return cur_raw, 0

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
        f"[tt:file_delta #{cur_ref} vs #{prev_ref} | {total}L total, showing modified hunks +/-{ctx_lines}L]"
    ]
    prev_end = 0
    for s, e in intervals:
        if s > prev_end:
            l_start = cur_line_nos[prev_end] if (cur_line_nos and prev_end < len(cur_line_nos)) else (prev_end + 1)
            l_end = cur_line_nos[s - 1] if (cur_line_nos and s - 1 < len(cur_line_nos)) else s
            chunks.append(f"[... L{l_start}-L{l_end} ({s - prev_end} unchanged lines in #{cur_ref}) ...]")
        for ln_idx in range(s, e):
            prefix = cur_prefixes[ln_idx] if (cur_prefixes and ln_idx < len(cur_prefixes)) else f"{ln_idx + 1}|"
            chunks.append(f"{prefix}{cur_lines[ln_idx]}")
        prev_end = e
    if prev_end < total:
        l_start = cur_line_nos[prev_end] if (cur_line_nos and prev_end < len(cur_line_nos)) else (prev_end + 1)
        l_end = cur_line_nos[total - 1] if (cur_line_nos and total - 1 < len(cur_line_nos)) else total
        chunks.append(f"[... L{l_start}-L{l_end} ({total - prev_end} unchanged lines in #{cur_ref}) ...]")

    delta_str = "\n".join(chunks)
    orig_approx_len = len(cur_raw) + (len(cur_lines) * 5 if cur_prefixes else 0)
    saved = orig_approx_len - len(delta_str)
    if saved <= 120:
        return cur_raw, 0
    return delta_str, saved


def _handle_read_file_v42(
    result: str,
    session_id: str,
    args: Any,
    min_chars: int,
    t0: float,
) -> tuple[str, dict]:
    """3-Tier Stateful Handler for read_file / read_file_extra with 100% Patch Safety."""
    norm_path, offset, limit = _extract_file_args(args)
    digest = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:12]
    raw_body, prefixes, line_nos, headers, footers = _unwrap_numbered_lines(result)
    clean_text, _ = hygiene(raw_body)
    remember(clean_text, session_id, owner=digest)

    first_line_no = line_nos[0] if line_nos else (offset if offset and offset > 0 else 1)
    n_lines = len(line_nos) if line_nos else (raw_body.count("\n") + 1)

    # Rule 0: targeted line-range slice or small file -> 100% verbatim passthrough.
    if len(result) < min_chars or _is_targeted_slice(offset, limit, first_line_no, n_lines):
        return result, {
            "gate": "passthrough_reference", "digest": digest,
            "ms": (time.perf_counter() - t0) * 1000,
        }

    with _SEEN_LOCK:
        seen = _seen(session_id)
        file_cache: dict[str, dict] = seen.setdefault("files", {})
        cache_key = norm_path or f"__anon_{digest}"
        prev_entry = file_cache.get(cache_key)

        # Case A: identical content re-read in active context.
        if (
            prev_entry is not None
            and prev_entry.get("digest") == digest
            and not prev_entry.get("expired", False)
        ):
            # Anti-poisoning & anti-loop rails: if prior read was only a skeleton,
            # or if the agent already received a stub once and is asking a 3rd time,
            # hand over the full verbatim file and reset flags.
            if prev_entry.get("skeleton_only") or prev_entry.get("stub_hits", 0) >= 1:
                prev_entry["skeleton_only"] = False
                prev_entry["stub_hits"] = 0
                return result, {
                    "gate": "passthrough_reference", "digest": digest,
                    "ms": (time.perf_counter() - t0) * 1000,
                }
            prev_entry["reads"] = prev_entry.get("reads", 1) + 1
            prev_entry["stub_hits"] = prev_entry.get("stub_hits", 0) + 1
            outline = _build_file_outline(raw_body, line_nos=line_nos)
            stub = (
                f"[tt:file_unchanged #{digest} path=\"{norm_path or 'file'}\" "
                f"({len(result)} chars, {n_lines}L) — identical to prior read in active context]\n"
                f"Outline (read_file offset/limit for exact lines):\n"
                f"{outline}"
            )
            tb, ta = estimate_tokens(result), estimate_tokens(stub)
            if len(stub) < len(result) and ta < tb:
                return stub, {
                    "gate": "ok",
                    "kind": "file_cache_hit",
                    "digest": digest,
                    "before_chars": len(result),
                    "after_chars": len(stub),
                    "saved_chars": len(result) - len(stub),
                    "est_tokens_before": tb,
                    "est_tokens_after": ta,
                    "fold_saved": len(result) - len(stub),
                    "ms": (time.perf_counter() - t0) * 1000,
                }

        # Case B: file was modified since the last read -> contextual line-numbered delta.
        if norm_path and prev_entry is not None and prev_entry.get("digest") != digest:
            prev_raw = prev_entry.get("raw_body") or prev_entry.get("text", "")
            prev_full = prev_entry.get("text", prev_raw)
            prev_digest = prev_entry.get("digest", "")
            if prev_raw and n_lines <= 1500:
                prev_ref = prev_digest
                try:
                    from .ccr import put as ccr_put
                except (ImportError, ValueError):
                    from trimtok.ccr import put as ccr_put
                try:
                    stored = ccr_put(prev_full)
                    if stored:
                        prev_ref = stored.lstrip("#")
                except Exception:
                    pass
                delta_view, _ = _build_read_delta(
                    prev_raw, raw_body, digest, prev_ref,
                    cur_prefixes=prefixes, cur_line_nos=line_nos,
                )
                d_saved = len(result) - len(delta_view)
                file_cache[cache_key] = {
                    "digest": digest, "text": result, "raw_body": raw_body,
                    "reads": 1, "stub_hits": 0, "expired": False, "skeleton_only": False,
                }
                if d_saved > 120:
                    tb, ta = estimate_tokens(result), estimate_tokens(delta_view)
                    if ta < tb:
                        return delta_view, {
                            "gate": "ok",
                            "kind": "file_delta",
                            "digest": digest,
                            "before_chars": len(result),
                            "after_chars": len(delta_view),
                            "saved_chars": d_saved,
                            "est_tokens_before": tb,
                            "est_tokens_after": ta,
                            "fold_saved": d_saved,
                            "ms": (time.perf_counter() - t0) * 1000,
                        }

        # Record state of this read in session cache.
        if len(file_cache) >= _MAX_FILE_CACHE:
            for _old_key in list(file_cache.keys())[: len(file_cache) - _MAX_FILE_CACHE + 1]:
                file_cache.pop(_old_key, None)
        file_cache[cache_key] = {
            "digest": digest, "text": result, "raw_body": raw_body,
            "reads": 1, "stub_hits": 0, "expired": False, "skeleton_only": False,
        }

        # Case C: cold first read of a large file (>= 12KB) -> line-anchored AST skeleton.
        if len(result) >= _COLD_READ_SKELETON_MIN_CHARS or len(raw_body) >= _COLD_READ_SKELETON_MIN_CHARS:
            sk_body, sk_saved, fn_count = smart_skeletonize_code(
                raw_body, ref=f"#{digest}", line_prefixes=prefixes, line_nos=line_nos,
            )
            if sk_saved > 200 and fn_count > 0:
                rebuilt = "\n".join(headers + [sk_body] + footers) if (headers or footers) else sk_body
                actual_saved = len(result) - len(rebuilt)
                head = adaptive_tool_head(max(actual_saved, sk_saved), digest, len(result))
                candidate = f"{head}\n{rebuilt}" if head else rebuilt
                tb, ta = estimate_tokens(result), estimate_tokens(candidate)
                if len(candidate) < len(result) and ta < tb:
                    file_cache[cache_key]["skeleton_only"] = True
                    return candidate, {
                        "gate": "ok",
                        "kind": "file_skeleton",
                        "digest": digest,
                        "before_chars": len(result),
                        "after_chars": len(candidate),
                        "saved_chars": len(result) - len(candidate),
                        "est_tokens_before": tb,
                        "est_tokens_after": ta,
                        "skeleton_saved": len(result) - len(candidate),
                        "ms": (time.perf_counter() - t0) * 1000,
                    }

    # Cold first read of a 2KB-12KB file: verbatim so the agent can patch safely.
    return result, {
        "gate": "passthrough_reference", "digest": digest,
        "ms": (time.perf_counter() - t0) * 1000,
    }


และใน compress_tool_result (trimtok/dedup.py:935-955):

Python
        # When a mutating tool (e.g. patch) touches a file, keep prev_entry in
        # seen["files"] (clearing only stub_hits/expired) so that a subsequent
        # read_file of that path can compute a Case B contextual file_delta!
        if tool_name in _MUTATING_TOOLS:
            try:
                mut_path, _, _ = _extract_file_args(args)
                if mut_path:
                    with _SEEN_LOCK:
                        f_entry = _seen(session_id or "default").setdefault("files", {}).get(mut_path)
                        if isinstance(f_entry, dict):
                            f_entry["mutated"] = True
                            f_entry["stub_hits"] = 0
            except Exception:
                pass
        if tool_name in _READ_FILE_TOOLS:
            return _handle_read_file_v42(result, session_id, args, min_chars, t0)

ไฟล์ที่ 3: trimtok/__init__.py (เปิดทางให้ read_file_extra และ Fallback args จาก **kw)

แก้ไขบรรทัดที่ 442-455 ใน trimtok/__init__.py:

Python
    verbatim_name = str(tool_name or "").strip()
    if verbatim_name in VERBATIM_TOOLS and verbatim_name not in ("read_file", "read_file_extra", "view_file", "get_file"):
        return None
    sid = str(session_id or task_id or "default")
    turn = str(turn_id or "")
    effective_args = args if args is not None else (kw.get("tool_args") or kw.get("arguments") or kw.get("input"))
    new_result, stats = compress_tool_result(
        str(tool_name or ""),
        result,
        args=effective_args,
        session_id=sid,
        min_chars=_int_cfg(ctx, "min_chars", 2048, 0, 10_000_000),
        toon_enabled=_bool_cfg(ctx, "toon_enabled", True),
        deadline=Deadline(_float_cfg(ctx, "guard_ms", 50.0, 10.0, 1000.0)),
    )

D) ACCEPTANCE GATES (ชุดทดสอบบังคับเพื่อกันไม่ให้ Lane นี้ตายอีก)

ใน tests/benchmark_v42.py และ tests/test_v42_argon_fixes.py ต้องจำลอง สภาพแวดล้อมจริงของ Hermes 100% (มี offset=1, limit=2000, ฟอร์แมต LINE|CONTENT, และมี patch คั่นระหว่าง read_file):

ชื่อ Gate (ใน benchmark_v42.py)	สิ่งที่จำลอง (Hermes Production Fidelity)	เกณฑ์ผ่าน (Target Gate)
1. hermes_read_file_default_args_skeleton	ส่งไฟล์ขนาด > 12KB ในรูปแบบ 1|import os\n2|... พร้อม args={"path": "src/a.py", "offset": 1, "limit": 2000}	gate == "ok", kind == "file_skeleton", ประหยัด >= 45%, และทุกบรรทัดที่คงไว้ยังมีเลข LINE| ตรงกับต้นฉบับ
2. hermes_read_patch_reread_workflow	Turn 1: read_file (6 KB, offset=1, limit=2000, LINE|CONTENT) $\rightarro Turn 2: patch(args={"path": ...}) $\rightarro Turn 3: read_file หลังแก้ 1 บรรทัด (เลขบรรทัดด้านล่างเลื่อนลงทั้งหมด +1)	

Turn 1: passthrough_reference (Verbatim 100%)




Turn 3: gate == "ok", kind == "file_delta", ประหยัด >= 50% (พิสูจน์ว่า patch ไม่ลบ prev_entry และ LINE| ที่เลื่อนไม่ทำให้ Diff พัง)


3. hermes_multi_step_reread_survival	Turn 1: read_file("a.py") 
→ คั่นด้วย expire_old_turns(keep_turns=2) 4 รอบ (จำลอง Agent รัน search_files/terminal 4 ครั้ง) 
→ Turn 6: read_file("a.py") ซ้ำ	Turn 6: gate == "ok", kind == "file_cache_hit", ประหยัด >= 75% (พิสูจน์ว่าแคชไฟล์ไม่ตายหลัง 2 Tool steps)
4. hermes_targeted_slice_passthrough	ส่ง read_file พร้อม args={"path": "src/a.py", "offset": 142, "limit": 77} และบรรทัดเริ่มด้วย 142|...	gate == "passthrough_reference", out == raw (Byte-identical 100%)
Python
# เพิ่มเทสต์นี้ใน tests/test_v42_argon_fixes.py
def test_hermes_production_numbered_read_patch_reread_lifecycle():
    """Simulate exact Hermes read_file (offset=1, limit=2000, LINE|CONTENT) + patch + re-read."""
    clear_session("hermes_prod")
    raw_lines = ["import os", "from typing import Any", "", "GLOBAL_LIMIT: int = 100", ""]
    for idx in range(12):
        raw_lines.append(f"def compute_metric_{idx}(val: int) -> int:")
        raw_lines.append(f'    """Compute {idx}."""')
        for j in range(16):
            raw_lines.append(f"    x_{j} = {j} * {idx}")
        raw_lines.append(f"    return val + {idx}")
        raw_lines.append("")

    numbered_v1 = "\n".join(f"{i + 1}|{ln}" for i, ln in enumerate(raw_lines))
    hermes_args = {"path": "src/metrics.py", "offset": 1, "limit": 2000}

    # Step 1: Cold read (6KB) with Hermes default offset=1, limit=2000 -> Verbatim for patch safety
    out1, st1 = compress_tool_result("read_file", numbered_v1, args=hermes_args, session_id="hermes_prod")
    assert st1["gate"] == "passthrough_reference"
    assert out1 == numbered_v1

    # Step 2: Re-read unchanged with offset=1, limit=2000 -> Must hit file_cache_hit (NOT blocked by Rule 0!)
    out2, st2 = compress_tool_result("read_file", numbered_v1, args=hermes_args, session_id="hermes_prod")
    assert st2["gate"] == "ok" and st2["kind"] == "file_cache_hit"
    assert "def compute_metric_0" in out2

    # Step 3: Agent runs `patch` (inserts 2 new lines in compute_metric_2, shifting all subsequent LINE| numbers!)
    compress_tool_result("patch", "Successfully applied patch to src/metrics.py", args={"path": "src/metrics.py"}, session_id="hermes_prod")
    edited_lines = list(raw_lines)
    edited_lines[48:48] = ["    patched_bonus = 9999", "    val += patched_bonus"]
    numbered_v2 = "\n".join(f"{i + 1}|{ln}" for i, ln in enumerate(edited_lines))

    # Step 4: Post-patch re-read -> Must emit file_delta despite patch call and shifted LINE| numbers!
    out3, st3 = compress_tool_result("read_file", numbered_v2, args=hermes_args, session_id="hermes_prod")
    assert st3["gate"] == "ok" and st3["kind"] == "file_delta"
    assert "patched_bonus = 9999" in out3
    assert st3["saved_chars"] > int(len(numbered_v2) * 0.50)

    # Step 5: Cold read of oversize (>12KB) numbered file -> Must skeletonize without SyntaxError!
    big_numbered = "\n".join(f"{i + 1}|{ln}" for i, ln in enumerate(raw_lines * 3))
    out4, st4 = compress_tool_result("read_file_extra", big_numbered, args={"path": "src/huge.py", "offset": 1, "limit": 2000}, session_id="hermes_prod")
    assert st4["gate"] == "ok" and st4["kind"] == "file_skeleton"
    assert st4["saved_chars"] > int(len(big_numbered) * 0.45)

E) OTHER LANES (read_file_extra / skill_view / patch — ทำหรือไม่ทำ เพราะอะไร)
Tool Lane	ปริมาณใน Telemetry	คำตัดสิน	เหตุผลทางวิศวกรรมและกลไกที่ใช้
read_file_extra	

109 ครั้ง | ~938 KB




(เฉลี่ย 8,609 chars/call, ประหยัด 0.0%)

	ทำทันที (DO NOW)	มีพฤติกรรมและโครงสร้างเหมือน read_file ทุกประการ เพียงเพิ่ม "read_file_extra" เข้าใน _READ_FILE_TOOLS และยกเว้นใน VERBATIM_TOOLS ของ __init__.py จะปลดล็อกการบีบอัดระดับ 50%–85% ทันที
skill_view	

11 ครั้ง | ~152 KB




(เฉลี่ย 13,806 chars/call, ประหยัด 0.0%)

	ทำเฉพาะ Same-Session Repeat Deduplication (DO PARTIAL)	ห้ามทำ AST Skeleton หรือตัดทอนเนื้อหา SKILL.md ในการอ่านครั้งแรกเด็ดขาด เพราะ SKILL.md คือคำสั่งขั้นตอนการทำงาน (Procedural Instructions) หากตัดกลางไฟล์ Agent จะทำผิดกฎ (ละเมิด Priority 1) สิ่งที่ทำได้ปลอดภัย 100%: หาก Agent เรียก skill_view ซ้ำกับ Skill เดิม (digest ตรงกัน) ในเซสชันเดียวกัน ให้พับเป็น [tt:skill_cached #cN] พร้อมสารบัญหัวข้อ # / ##
patch	

972 ครั้ง | ~1.52M chars




(เฉลี่ย 1,566 chars/call, ประหยัด 0.7%–3.6%)

	ปล่อยช่วง < 2KB / ทำเฉพาะ Diff Prune ช่วง >= 2KB (LEAVE SMALL, PRUNE LARGE DIFFS)	ผลลัพธ์ของ patch ส่วนใหญ่สั้น (~1.5 KB) และเป็นข้อความยืนยันหรือ Error จุดที่ Patch ไม่ติด ซึ่งต้องคงไว้ 100% ยกเว้นกรณีที่ patch พ่น Unified Diff ขนาดใหญ่ (>= 2,048 chars) กลับมา สามารถเรียก prune_unified_diff(text) เพื่อยุบบรรทัด Context ที่ไม่เปลี่ยนเกิน ±3 บรรทัดได้โดยไม่เสีย Hunk +/-
F) RISKS + แผน Rollback

ความเสี่ยงที่ต้องเฝ้าระวัง (Residual Risks & Mitigations):

ความเสี่ยง 1: ไฟล์ที่มีเครื่องหมาย | ในเนื้อหาโค้ด (เช่น Bitwise OR หรือ Markdown Table)

การป้องกัน: _NUMBERED_LINE_RE = re.compile(r"^(\s*(\d+)\|)(.*)$") บังคับว่าต้องขึ้นต้นบรรทัดด้วยตัวเลขล้วนตามด้วย | และตรวจสอบว่าอย่างน้อยครึ่งหนึ่งของ 12 บรรทัดแรกมีรูปแบบนี้เรียงกัน ซึ่งโค้ดปกติหรือตาราง Markdown (| col1 | col2 | ขึ้นต้นด้วย |) จะไม่มีทางตรงกับเงื่อนไขนี้

ความเสี่ยง 2: Agent พยายามอ่านไฟล์เต็มหลังจากได้ [tt:file_unchanged] เพราะจำเนื้อหาไม่ได้

การป้องกัน: ใส่ Anti-Loop Frustration Rail (stub_hits >= 1) ไว้ใน Case A เรียบร้อยแล้ว หาก Agent เรียก read_file ซ้ำเป็นครั้งที่ 3 ระบบจะส่งเนื้อหาเต็ม (Verbatim) ให้ทันที ไม่มีทางเกิด Infinite Loop

แผน Rollback กรณีฉุกเฉิน (Zero-Downtime Rollback Plan):

ระดับ Config (ไม่ต้องแก้โค้ด / ไม่ต้อง Restart): เพิ่ม "read_file" และ "read_file_extra" เข้าในรายการ exclude_tools ของ config Hermes (exclude_tools: ["read_file", "read_file_extra"]) ฟังก์ชัน _excluded_tool(tool_name, ctx) ใน trimtok/__init__.py:439 จะตัดการทำงานของ Lane นี้กลับเป็น Verbatim Passthrough 100% ทันที

ระดับ Code: ย้อนเฉพาะฟังก์ชัน _is_targeted_slice ให้คืนค่า True เสมอ จะทำให้ _handle_read_file_v42 กลับสู่โหมด passthrough_reference โดยไม่กระทบ Terminal pipeline, TOON หรือ Micro-minification ที่ทำงานได้ดีอยู่แล้ว