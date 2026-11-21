"""Native Hermes ContextEngine implementation for trimtok.

3-Layer Hybrid Compaction Architecture:
  - Tier 0 (`prune_tool_results_only`): proactive deterministic CCR stashing of old tool outputs
    with Ghost-Skill reload markers for pruned `skill_view` payloads.
  - Tier 1 (`compress` fast pass): zero-loss CCR stashing of middle tool outputs, oversized
    `tool_calls` arguments, and oversized user pastes. Finishes in <10ms if target headroom is met.
  - Tier 2 (`compress` deep pass): when Tier 1 still leaves context above target headroom,
    selects a token-budget tail, applies head decay after the first compaction, archives the raw
    middle transcript to CCR, and performs iterative rolling summarization via Hermes's auxiliary
    LLM (`agent.auxiliary_client.call_llm`) — with instant splice of background-prepared summaries
    from `on_turn_complete()` and automatic deterministic structured checkpoint fallback.
  - Tier 3 (Anti-Thrash): real-usage ineffective-compaction breaker, structural no-op backoff,
    and post-compaction preflight deferral so long sessions never enter a per-message loop.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("trimtok.context_engine")

try:
    from agent.context_engine import ContextEngine
except ImportError:
    # Standalone fallback when running outside of hermes-agent host environment
    class ContextEngine:  # type: ignore[no-redef]
        """Minimal fallback ABC matching Hermes agent.context_engine.ContextEngine."""

        threshold_percent: float = 0.50
        protect_first_n: int = 3
        protect_last_n: int = 6

        def on_session_reset(self) -> None:
            self.last_prompt_tokens = 0
            self.last_completion_tokens = 0
            self.last_total_tokens = 0
            self.compression_count = 0


_SUMMARY_SYSTEM_PROMPT = (
    "You are compacting older conversation turns into a concise, high-signal handoff checkpoint. "
    "Preserve: (1) user goals, constraints, and decisions made; (2) files modified, commands run, "
    "and exact error messages or root causes still relevant; (3) any trimtok CCR references (#c...) "
    "verbatim so details can be retrieved on demand. "
    "Drop: repetitive logs, superseded attempts, and conversational filler. "
    "Use short markdown headings and bullet points. Be factual; never invent details."
)

_ROLLING_SUMMARY_SYSTEM_PROMPT = (
    "You are updating an existing conversation checkpoint with newly completed turns. "
    "Merge the Previous Checkpoint and New Turns into ONE concise, unified handoff checkpoint. "
    "Preserve active goals, final decisions, modified files, unresolved errors, and verbatim "
    "trimtok CCR references (#c...). Drop superseded/completed noise. "
    "Use short markdown headings and bullet points. Be factual; never invent details."
)

_HANDOFF_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted by Trimtok into the "
    "checkpoint below. Treat this as background reference, NOT active instructions. Respond only "
    "to the latest user message after this checkpoint; your tools remain fully active."
)

_SKILL_PRUNED_MARKER_PREFIX = "[SKILL_PRUNED:"
_SKILL_PRUNED_MARKER_RE = re.compile(
    re.escape(_SKILL_PRUNED_MARKER_PREFIX) + r"[^\]]*?reload with skill_view\(name='([^']+)'\)"
)
_MAX_PRUNED_SKILL_MARKERS = 20

_REF_RE = re.compile(r"#c[0-9a-zA-Z]{2,16}")
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/)?[\w./\\-]+\.(?:py|ts|tsx|js|jsx|json|yaml|yml|md|toml|sql|sh|ps1|rs|go)")
_BACKTICK_RE = re.compile(r"`([^`\n]{3,64})`")
_IDENT_RE = re.compile(r"\b(?:[a-z]+_[a-z0-9_]{2,}|[A-Z][a-z]+[A-Z][A-Za-z0-9]*|[A-Z]{2,}_[A-Z0-9_]+)\b")
_HTML_TAG_RE = re.compile(r"</?b>")
_CHECKPOINT_FOOTER_RE = re.compile(r"\n*\[tt:checkpoint [^\]]+\]\s*$")
_RECALL_MARKER = "[tt:recall"
_MAX_RECALL_HITS = 3
_MAX_RECALL_TERMS = 4
_SUMMARY_INPUT_MAX_CHARS = 60_000
_SUMMARY_LLM_TIMEOUT_S = 45.0
_SUMMARY_LLM_COOLDOWN_S = 120.0
_STRUCTURAL_BACKOFF_S = 60.0
_ANTI_THRASH_RECOVERY_S = 300.0
_ANTI_THRASH_STRIKES = 2


def _skill_pruned_marker(skill_name: str) -> str:
    """Canonical Ghost-Skill reload marker matching Hermes ContextCompressor."""
    return (
        f"{_SKILL_PRUNED_MARKER_PREFIX} content lost in compression; "
        f"reload with skill_view(name='{skill_name}')]"
    )


def _skill_view_names_by_call_id(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Map each `skill_view` tool_call_id to its requested skill name."""
    names: dict[str, str] = {}
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if fn.get("name") != "skill_view":
                continue
            args_raw = fn.get("arguments")
            skill = ""
            if isinstance(args_raw, dict):
                skill = str(args_raw.get("name") or "").strip()
            elif isinstance(args_raw, str) and args_raw.strip():
                try:
                    parsed = json.loads(args_raw)
                    if isinstance(parsed, dict):
                        skill = str(parsed.get("name") or "").strip()
                except Exception:
                    pass
            tc_id = str(tc.get("id") or "").strip()
            if tc_id and skill:
                names[tc_id] = skill
    return names


def _is_summary_message(msg: Any) -> bool:
    if not isinstance(msg, dict):
        return False
    if msg.get("_compressed_summary"):
        return True
    content = msg.get("content")
    return isinstance(content, str) and content.startswith("[CONTEXT COMPACTION")


def _extract_summary_body(content: str) -> str:
    """Strip handoff framing and checkpoint footer from a prior summary message."""
    body = content.strip()
    if body.startswith(_HANDOFF_PREFIX):
        body = body[len(_HANDOFF_PREFIX) :].strip()
    elif body.startswith("[CONTEXT COMPACTION"):
        idx = body.find("\n\n")
        if idx > 0:
            body = body[idx + 2 :].strip()
    body = _CHECKPOINT_FOOTER_RE.sub("", body).strip()
    return body


def _estimate_content_chars(content: Any) -> int:
    """Fast character count for OpenAI string or multipart message content."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict):
                txt = part.get("text")
                if isinstance(txt, str):
                    total += len(txt)
                elif part.get("type") in ("image_url", "image", "input_image"):
                    total += 4000
            elif isinstance(part, str):
                total += len(part)
        return total
    return len(str(content)) if content is not None else 0


def _estimate_message_tokens(msg: Any) -> int:
    if not isinstance(msg, dict):
        return 4
    chars = _estimate_content_chars(msg.get("content"))
    tcs = msg.get("tool_calls")
    if isinstance(tcs, list):
        for tc in tcs:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                chars += len(str(fn.get("name") or "")) + len(str(fn.get("arguments") or ""))
    for rkey in ("reasoning", "reasoning_content"):
        rv = msg.get(rkey)
        if isinstance(rv, str):
            chars += len(rv)
    return max(4, chars // 4 + 4)


def _estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate token count for a message list without heavy dependencies."""
    if not messages:
        return 0
    try:
        from agent.model_metadata import estimate_messages_tokens_rough

        est = estimate_messages_tokens_rough(messages)
        if isinstance(est, int) and est > 0:
            return est
    except Exception:
        pass
    return sum(_estimate_message_tokens(m) for m in messages)


def _messages_fingerprint(messages: list[dict[str, Any]]) -> str:
    """Fast deterministic digest for matching background-prepared compaction windows."""
    h = hashlib.sha256()
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        h.update(str(msg.get("role") or "").encode("utf-8", "ignore"))
        h.update(b":")
        content = msg.get("content")
        if isinstance(content, str):
            h.update(str(len(content)).encode("ascii"))
            h.update(b":")
            h.update(content[:256].encode("utf-8", "ignore"))
            if len(content) > 256:
                h.update(content[-128:].encode("utf-8", "ignore"))
        tcs = msg.get("tool_calls")
        if isinstance(tcs, list):
            for tc in tcs:
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    h.update(str(fn.get("name") or "").encode("utf-8", "ignore"))
                    h.update(str(len(str(fn.get("arguments") or ""))).encode("ascii"))
        h.update(b"|")
    return h.hexdigest()[:16]


def _load_is_important():
    """Lazy import of trimtok's fault classifier."""
    try:
        from .signals import is_important
    except ImportError:
        from trimtok.signals import is_important
    return is_important


def _put_ccr(text: str) -> str:
    """Store payload in CCR fail-open; return '' on any error."""
    try:
        from trimtok import ccr

        return ccr.put(text) or ""
    except Exception:
        return ""


def _search_ccr(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search CCR via SQLite FTS5 fail-open; return [] on any error."""
    try:
        from trimtok import ccr

        return ccr.search(query, limit=limit) or []
    except Exception:
        return []


def _extract_recall_terms(text: str, max_terms: int = _MAX_RECALL_TERMS) -> list[str]:
    """Extract high-signal technical symbols/paths from a user query for FTS5 auto-recall."""
    terms: list[str] = []

    def _add(tok: str) -> None:
        clean = tok.strip(" \t\r\n\"'.,;:()[]{}")
        if len(clean) < 3 or clean.startswith("#c"):
            return
        low = clean.lower()
        if any(t.lower() == low for t in terms):
            return
        terms.append(clean)

    # 1. Explicit backticked code/identifiers (highest signal)
    for m in _BACKTICK_RE.findall(text):
        for piece in re.split(r"\s+", m.strip()):
            _add(piece)
            if len(terms) >= max_terms:
                return terms

    # 2. File paths / basenames
    for p in _PATH_RE.findall(text):
        base = p.replace("\\", "/").rsplit("/", 1)[-1]
        _add(base)
        if len(terms) >= max_terms:
            return terms

    # 3. Code identifiers (snake_case, CamelCase, UPPER_CONST)
    for ident in _IDENT_RE.findall(text):
        _add(ident)
        if len(terms) >= max_terms:
            return terms

    return terms[:max_terms]


class TrimtokContextEngine(ContextEngine):
    """Trimtok native Hermes ContextEngine with 3-layer hybrid compaction, prepare-ahead, and anti-thrash."""

    def __init__(
        self,
        context_length: int = 128_000,
        threshold_percent: float = 0.50,
        protect_first_n: int = 3,
        protect_last_n: int = 6,
        min_stash_chars: int = 2048,
        fault_scan_lines: int = 2000,
        target_ratio: float = 0.65,
        min_args_stash_chars: int = 2048,
        min_user_stash_chars: int = 4096,
        proactive_prune_ratio: float = 0.60,
        tail_budget_ratio: float = 0.25,
        prepare_ahead_ratio: float = 0.80,
        enable_llm_summary: bool = True,
        enable_prepare_ahead: bool = True,
        enable_auto_recall: bool = True,
    ) -> None:
        self.context_length = int(context_length)
        self.threshold_percent = float(threshold_percent)
        self._config_threshold_percent = float(threshold_percent)
        self.protect_first_n = int(protect_first_n)
        self.protect_last_n = int(protect_last_n)
        self.threshold_tokens = int(self.context_length * self.threshold_percent)
        # A stash costs the agent a retrieval turn, so only results big enough to
        # repay it move. Below this the stub is longer than the saving.
        self.min_stash_chars = int(min_stash_chars)
        self.fault_scan_lines = int(fault_scan_lines)
        self.target_ratio = float(target_ratio)
        self.min_args_stash_chars = int(min_args_stash_chars)
        self.min_user_stash_chars = int(min_user_stash_chars)
        self.proactive_prune_ratio = float(proactive_prune_ratio)
        self.tail_budget_ratio = float(tail_budget_ratio)
        self.prepare_ahead_ratio = float(prepare_ahead_ratio)
        self.enable_llm_summary = bool(enable_llm_summary)
        self.enable_prepare_ahead = bool(enable_prepare_ahead)
        self.enable_auto_recall = bool(enable_auto_recall)
        self.model_thresholds: dict[str, float] = {}

        # Runtime token & compaction state
        self.last_prompt_tokens: int = 0
        self.last_completion_tokens: int = 0
        self.last_total_tokens: int = 0
        self.last_real_prompt_tokens: int = 0
        self.last_compression_rough_tokens: int = 0
        self.compression_count: int = 0

        # Anti-thrash & cooldown state
        self.awaiting_real_usage_after_compression: bool = False
        self._verify_compaction_cleared_threshold: bool = False
        self._ineffective_compression_count: int = 0
        self._anti_thrash_unblock_at: float = 0.0
        self._structural_no_op_backoff_until: float = 0.0
        self._structural_no_op_msg_count: int = 0
        self._llm_summary_cooldown_until: float = 0.0

        # Background prepare-ahead cache & session-scoped CCR recall refs
        self._prepare_lock = threading.Lock()
        self._prepared_fingerprint: str = ""
        self._prepared_middle_len: int = 0
        self._prepared_handoff: Optional[dict[str, Any]] = None
        self._prepare_in_flight: bool = False
        self._session_refs: set[str] = set()

    @property
    def name(self) -> str:
        return "trimtok"

    def update_from_response(self, usage: dict[str, Any]) -> None:
        if not isinstance(usage, dict):
            return
        self.last_prompt_tokens = int(usage.get("prompt_tokens") or 0)
        self.last_completion_tokens = int(usage.get("completion_tokens") or 0)
        self.last_total_tokens = int(usage.get("total_tokens") or 0)

        if self.last_prompt_tokens > 0:
            self.last_real_prompt_tokens = self.last_prompt_tokens
            if self.threshold_tokens > 0 and self.last_prompt_tokens < self.threshold_tokens:
                self._ineffective_compression_count = 0
                self._anti_thrash_unblock_at = 0.0
            if self._verify_compaction_cleared_threshold and self.threshold_tokens > 0:
                if self.last_prompt_tokens >= self.threshold_tokens:
                    self._ineffective_compression_count += 1
                    if self._ineffective_compression_count >= _ANTI_THRASH_STRIKES:
                        self._anti_thrash_unblock_at = time.time() + _ANTI_THRASH_RECOVERY_S
                    logger.warning(
                        "Trimtok compaction did not clear threshold: %d >= %d (strikes=%d)",
                        self.last_prompt_tokens,
                        self.threshold_tokens,
                        self._ineffective_compression_count,
                    )
                else:
                    self._ineffective_compression_count = 0
                    self._anti_thrash_unblock_at = 0.0

        self._verify_compaction_cleared_threshold = False
        self.awaiting_real_usage_after_compression = False

    def should_compress_info(self, prompt_tokens: Optional[int] = None) -> tuple[bool, Optional[str]]:
        """Return (should_compress, block_reason) with anti-thrash and backoff guards."""
        tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        if self.threshold_tokens <= 0 or tokens < self.threshold_tokens:
            return False, None

        now_mono = time.monotonic()
        if self._structural_no_op_backoff_until > now_mono:
            rem = self._structural_no_op_backoff_until - now_mono
            return False, f"structural_backoff:{rem:.0f}"

        if self._ineffective_compression_count >= _ANTI_THRASH_STRIKES:
            now_wall = time.time()
            if self._anti_thrash_unblock_at and now_wall >= self._anti_thrash_unblock_at:
                # Allow one probe after recovery window instead of blocking permanently
                self._ineffective_compression_count = _ANTI_THRASH_STRIKES - 1
                self._anti_thrash_unblock_at = 0.0
                return True, None
            return False, "ineffective"

        return True, None

    def should_compress(self, prompt_tokens: Optional[int] = None) -> bool:
        return self.should_compress_info(prompt_tokens)[0]

    def should_defer_preflight_to_real_usage(self, rough_tokens: int) -> bool:
        """Avoid re-compacting on a stale rough estimate right after a compaction pass."""
        if self.threshold_tokens <= 0 or rough_tokens < self.threshold_tokens:
            return False
        return self.awaiting_real_usage_after_compression

    def lift_structural_backoff_if_grown(self, msg_count: int) -> None:
        """Re-arm compaction once new messages have been appended after a structural no-op."""
        if self._structural_no_op_backoff_until > 0.0 and msg_count > self._structural_no_op_msg_count:
            self._structural_no_op_backoff_until = 0.0
            self._structural_no_op_msg_count = 0

    def on_session_reset(self) -> None:
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.last_real_prompt_tokens = 0
        self.last_compression_rough_tokens = 0
        self.compression_count = 0
        self.awaiting_real_usage_after_compression = False
        self._verify_compaction_cleared_threshold = False
        self._ineffective_compression_count = 0
        self._anti_thrash_unblock_at = 0.0
        self._structural_no_op_backoff_until = 0.0
        self._structural_no_op_msg_count = 0
        self._llm_summary_cooldown_until = 0.0
        with self._prepare_lock:
            self._prepared_fingerprint = ""
            self._prepared_middle_len = 0
            self._prepared_handoff = None
            self._session_refs.clear()

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
        api_mode: str = "",
        **kwargs: Any,
    ) -> None:
        self.context_length = int(context_length)
        base_pct = getattr(self, "_config_threshold_percent", self.threshold_percent)
        resolved_pct = base_pct
        try:
            from agent.context_compressor import resolve_model_threshold

            resolved_pct = resolve_model_threshold(
                model, getattr(self, "model_thresholds", {}), base_pct, provider
            )
        except Exception:
            if self.model_thresholds and model:
                low = model.lower()
                matches = [(k, v) for k, v in self.model_thresholds.items() if k.lower() in low]
                if matches:
                    matches.sort(key=lambda kv: len(kv[0]), reverse=True)
                    resolved_pct = float(matches[0][1])
        self.threshold_percent = float(resolved_pct)
        self.threshold_tokens = int(self.context_length * self.threshold_percent)

    # ------------------------------------------------------------------
    # Per-Request CCR FTS5 Auto-Hinting (`select_context`, cache-safe)
    # ------------------------------------------------------------------

    def _record_session_ref(self, ref: str) -> None:
        if ref:
            with self._prepare_lock:
                self._session_refs.add(ref)

    def _collect_session_refs(self, request_messages: list[dict[str, Any]]) -> set[str]:
        """Union of refs stashed by this engine and any stashed/checkpoint refs in the request."""
        with self._prepare_lock:
            refs = set(self._session_refs)
        for msg in request_messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, str) and ("[tt:stashed" in content or "[tt:checkpoint" in content):
                refs.update(_REF_RE.findall(content))
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict):
                    args_str = str((tc.get("function") or {}).get("arguments") or "")
                    if "tt_stashed_ref" in args_str:
                        refs.update(_REF_RE.findall(args_str))
        return refs

    def select_context(
        self,
        request_messages: list[dict[str, Any]],
        *,
        conversation_messages: Optional[list[dict[str, Any]]] = None,
        incoming_message: Optional[dict[str, Any]] = None,
        budget_tokens: int = 0,
    ) -> Optional[list[dict[str, Any]]]:
        """Inject a prompt-cache-safe 1-3 line CCR recall hint onto the trailing user turn.

        Returns None (byte-identical no-op) unless:
          1. This session has stashed/archived CCR refs,
          2. The trailing message in `request_messages` is a plain-text user turn (never mid-tool-loop,
             so the cached prefix before the latest user turn stays 100% intact), and
          3. High-signal technical terms in the user turn match session-scoped CCR entries via FTS5.
        """
        if not self.enable_auto_recall or not request_messages:
            return None

        last_msg = request_messages[-1]
        if not isinstance(last_msg, dict) or last_msg.get("role") != "user":
            return None
        user_text = last_msg.get("content")
        if not isinstance(user_text, str) or len(user_text.strip()) < 8 or _RECALL_MARKER in user_text:
            return None

        session_refs = self._collect_session_refs(request_messages)
        if not session_refs:
            return None

        query_source = user_text
        if isinstance(incoming_message, dict) and isinstance(incoming_message.get("content"), str):
            query_source = incoming_message["content"]
        terms = _extract_recall_terms(query_source)
        if not terms:
            return None

        already_cited = set(_REF_RE.findall(user_text))
        # Aggregate per-term FTS5 hits (ccr.search ANDs multi-word queries, so per-term avoids 0-hit traps)
        scores: dict[str, list[Any]] = {}
        for term in terms:
            for hit in _search_ccr(term, limit=4):
                alias = str(hit.get("alias") or "")
                if not alias or alias not in session_refs or alias in already_cited:
                    continue
                raw_snip = _HTML_TAG_RE.sub("", str(hit.get("snippet") or ""))
                clean_snip = " ".join(raw_snip.split())[:80]
                rank = float(hit.get("rank") or 0.0)
                if alias not in scores:
                    scores[alias] = [1, rank, clean_snip]
                else:
                    scores[alias][0] += 1
                    scores[alias][1] = min(scores[alias][1], rank)

        if not scores:
            return None

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1][0], kv[1][1]))[:_MAX_RECALL_HITS]
        lines = [
            f"{_RECALL_MARKER} matching stashed context in this session — call trimtok_retrieve if needed:"
        ]
        for alias, (_hits, _rank, snip) in ranked:
            lines.append(f"- {alias}: {snip}" if snip else f"- {alias}")
        lines[-1] += "]"

        out = list(request_messages)
        out[-1] = {**last_msg, "content": user_text + "\n\n" + "\n".join(lines)}
        return out

    # ------------------------------------------------------------------
    # Background Prepare-Ahead Hook (`on_turn_complete`)
    # ------------------------------------------------------------------

    def on_turn_complete(
        self,
        messages: list[dict[str, Any]],
        usage: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Pre-warm Tier 2 summary in a background daemon thread when approaching threshold."""
        if not self.enable_prepare_ahead or not messages or self.threshold_tokens <= 0:
            return
        if self._ineffective_compression_count >= _ANTI_THRASH_STRIKES:
            return

        tokens = 0
        if isinstance(usage, dict) and usage.get("prompt_tokens"):
            tokens = int(usage["prompt_tokens"])
        elif self.last_prompt_tokens > 0:
            tokens = self.last_prompt_tokens
        else:
            tokens = _estimate_messages_tokens(messages)

        prepare_floor = int(self.threshold_tokens * self.prepare_ahead_ratio)
        if tokens < prepare_floor or tokens >= self.threshold_tokens:
            return

        snapshot = [m.copy() for m in messages if isinstance(m, dict)]
        h, t = self._tier2_boundaries(snapshot)
        if t - h < 4:
            return
        middle = snapshot[h:t]
        fp = _messages_fingerprint(middle)

        with self._prepare_lock:
            if self._prepare_in_flight or (self._prepared_fingerprint == fp and self._prepared_handoff is not None):
                return
            self._prepare_in_flight = True

        def _worker() -> None:
            try:
                skill_names = _skill_view_names_by_call_id(snapshot)
                tier1_mid, _ = self._tier1_stash(middle, 0, len(middle), skill_names=skill_names)
                handoff = self._build_handoff_message(
                    tier1_mid, skill_names=skill_names, focus_topic=None, memory_context=""
                )
                if handoff is not None:
                    with self._prepare_lock:
                        self._prepared_fingerprint = fp
                        self._prepared_middle_len = len(middle)
                        self._prepared_handoff = handoff
            except Exception as exc:
                logger.debug("Trimtok prepare-ahead worker failed: %s", exc)
            finally:
                with self._prepare_lock:
                    self._prepare_in_flight = False

        threading.Thread(target=_worker, name="trimtok-prepare-ahead", daemon=True).start()

    # ------------------------------------------------------------------
    # Tier 0: Proactive deterministic tool-result prune (no LLM)
    # ------------------------------------------------------------------

    def prune_tool_results_only(
        self,
        messages: list[dict[str, Any]],
        current_tokens: Optional[int] = None,
        *,
        cold_cache: bool = False,
        turn_start: Optional[int] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Deterministically stash old fault-free tool results before full compaction."""
        if not messages:
            return messages, 0

        if not cold_cache:
            trigger = int(self.threshold_tokens * self.proactive_prune_ratio)
            if trigger <= 0 or current_tokens is None or current_tokens < trigger:
                return messages, 0

        head_boundary, tail_boundary = self._safe_boundaries(messages)
        if isinstance(turn_start, int) and 0 <= turn_start < len(messages):
            tail_boundary = min(tail_boundary, turn_start)
            tail_boundary = self._align_tail_boundary(messages, head_boundary, tail_boundary)
        if head_boundary >= tail_boundary:
            return messages, 0

        skill_names = _skill_view_names_by_call_id(messages)
        out: list[dict[str, Any]] = []
        pruned = 0
        reclaimed_chars = 0
        for i, msg in enumerate(messages):
            if head_boundary <= i < tail_boundary and msg.get("role") == "tool":
                content = msg.get("content", "")
                if self._should_stash(content):
                    stub = self._stash_tool_content(msg, content, skill_names=skill_names)
                    if stub is not None and len(stub) < len(content):
                        reclaimed_chars += len(content) - len(stub)
                        out.append({**msg, "content": stub})
                        pruned += 1
                        continue
            out.append(msg.copy())

        # Avoid busting a warm prompt cache for negligible savings
        if pruned == 0 or (not cold_cache and reclaimed_chars < self.min_stash_chars):
            return messages, 0
        return out, pruned

    # ------------------------------------------------------------------
    # Tier 1 + Tier 2: Full Hybrid Compaction
    # ------------------------------------------------------------------

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
        force: bool = False,
        memory_context: str = "",
    ) -> list[dict[str, Any]]:
        """Compact conversation history using 2-tier escalation (deterministic stash -> hybrid summary)."""
        if not messages:
            return []

        if force:
            self._structural_no_op_backoff_until = 0.0
            self._structural_no_op_msg_count = 0
            self._llm_summary_cooldown_until = 0.0

        head_boundary, tail_boundary = self._safe_boundaries(messages)
        if head_boundary >= tail_boundary:
            self._arm_structural_backoff(len(messages))
            return messages

        skill_names = _skill_view_names_by_call_id(messages)

        # --- Tier 1: Deterministic zero-loss CCR stashing (tools, tool_calls args, user pastes) ---
        tier1_msgs, tier1_changes = self._tier1_stash(
            messages, head_boundary, tail_boundary, skill_names=skill_names
        )
        before_tokens = _estimate_messages_tokens(messages)
        after_tier1_tokens = _estimate_messages_tokens(tier1_msgs) if tier1_changes else before_tokens

        target_tokens = int(self.threshold_tokens * self.target_ratio) if self.threshold_tokens > 0 else 0
        # Account for system prompt / schema overhead if caller provided a larger current_tokens
        overhead = max(0, (current_tokens or 0) - before_tokens)
        effective_after_tier1 = after_tier1_tokens + overhead

        t2_head, t2_tail = self._tier2_boundaries(tier1_msgs)
        needs_tier2 = (
            (t2_tail - t2_head) >= 4
            and target_tokens > 0
            and (
                (current_tokens is not None and effective_after_tier1 >= target_tokens)
                or after_tier1_tokens >= target_tokens
                or (force and tier1_changes == 0)
            )
        )

        final_msgs = tier1_msgs
        compacted = tier1_changes > 0

        # --- Tier 2: Hybrid Deep Compaction (Prepare-Ahead splice -> Auxiliary LLM -> Deterministic) ---
        if needs_tier2:
            tier2_msgs = self._tier2_compact_middle(
                tier1_msgs,
                raw_messages=messages,
                head_boundary=t2_head,
                tail_boundary=t2_tail,
                skill_names=skill_names,
                focus_topic=focus_topic,
                memory_context=memory_context,
            )
            if tier2_msgs is not None:
                after_tier2_tokens = _estimate_messages_tokens(tier2_msgs)
                if after_tier2_tokens < after_tier1_tokens:
                    final_msgs = tier2_msgs
                    compacted = True

        if not compacted:
            self._arm_structural_backoff(len(messages))
            return messages

        self.compression_count += 1
        self.last_compression_rough_tokens = _estimate_messages_tokens(final_msgs) + overhead
        self.awaiting_real_usage_after_compression = True
        self._verify_compaction_cleared_threshold = True
        self._structural_no_op_backoff_until = 0.0
        self._structural_no_op_msg_count = 0
        return final_msgs

    def _arm_structural_backoff(self, msg_count: int) -> None:
        self._structural_no_op_msg_count = msg_count
        self._structural_no_op_backoff_until = time.monotonic() + _STRUCTURAL_BACKOFF_S

    def _effective_protect_first_n(self, messages: list[dict[str, Any]]) -> int:
        """Decay head protection to 0 after the first compaction so opening turns don't fossilize."""
        if self.compression_count >= 1:
            return 0
        if messages and self.protect_first_n > 0:
            start = 1 if messages[0].get("role") == "system" else 0
            probe_end = min(len(messages), start + self.protect_first_n + 2)
            if any(_is_summary_message(m) for m in messages[start:probe_end]):
                return 0
        return self.protect_first_n

    def _safe_boundaries(self, messages: list[dict[str, Any]]) -> tuple[int, int]:
        """Compute (head_boundary, tail_boundary) for in-place Tier 1 stashing."""
        has_system = bool(messages) and messages[0].get("role") == "system"
        head_boundary = (1 if has_system else 0) + self.protect_first_n
        tail_boundary = max(head_boundary, len(messages) - self.protect_last_n)
        return head_boundary, tail_boundary

    def _tier2_boundaries(self, messages: list[dict[str, Any]]) -> tuple[int, int]:
        """Compute (head, tail) for Tier 2 folding using head decay and a token-budget tail."""
        has_system = bool(messages) and messages[0].get("role") == "system"
        head_raw = (1 if has_system else 0) + self._effective_protect_first_n(messages)
        tail_raw = self._find_token_budget_tail(messages, head_raw)
        return self._align_deletion_boundaries(messages, head_raw, tail_raw)

    def _find_token_budget_tail(self, messages: list[dict[str, Any]], head_boundary: int) -> int:
        """Select recent tail by token budget while always preserving the latest real user turn."""
        n = len(messages)
        if n <= head_boundary:
            return head_boundary

        if self.threshold_tokens <= 0:
            return max(head_boundary, n - self.protect_last_n)

        budget = int(self.threshold_tokens * self.tail_budget_ratio)
        if self.threshold_tokens >= 16_000:
            budget = max(4_000, min(32_000, budget))
        else:
            budget = max(512, budget)

        # Anchor: always keep the newest real (non-summary) user turn in the tail
        last_real_user = -1
        for i in range(n - 1, head_boundary - 1, -1):
            if messages[i].get("role") == "user" and not _is_summary_message(messages[i]):
                last_real_user = i
                break

        acc_tokens = 0
        cut = n
        for i in range(n - 1, head_boundary - 1, -1):
            msg_tokens = _estimate_message_tokens(messages[i])
            if cut < n and acc_tokens + msg_tokens > budget:
                # Guarantee at least the latest real user turn (or protect_last_n if no user turn)
                if last_real_user >= 0 and i >= last_real_user:
                    pass
                else:
                    break
            acc_tokens += msg_tokens
            cut = i

        # Ensure we keep at least 2 recent messages when possible
        min_tail_start = max(head_boundary, n - max(2, min(self.protect_last_n, 4)))
        if cut > min_tail_start and acc_tokens < budget:
            cut = min_tail_start
        return max(head_boundary, cut)

    @staticmethod
    def _align_deletion_boundaries(
        messages: list[dict[str, Any]], head_boundary: int, tail_boundary: int
    ) -> tuple[int, int]:
        """Align (head, tail) for Tier 2 message folding without orphaning tool_calls/tool pairs."""
        h = head_boundary
        while h < tail_boundary and messages[h].get("role") == "tool":
            h += 1
        t = tail_boundary
        while t > h and t < len(messages) and messages[t].get("role") == "tool":
            t -= 1
            if t > h and messages[t].get("role") == "assistant" and messages[t].get("tool_calls"):
                break
        return h, t

    @staticmethod
    def _align_tail_boundary(messages: list[dict[str, Any]], head_boundary: int, tail_boundary: int) -> int:
        """Ensure tail never starts with an orphaned tool result message."""
        while tail_boundary > head_boundary and tail_boundary < len(messages) and messages[tail_boundary].get("role") == "tool":
            tail_boundary -= 1
        return tail_boundary

    def _tier1_stash(
        self,
        messages: list[dict[str, Any]],
        head_boundary: int,
        tail_boundary: int,
        *,
        skill_names: Optional[dict[str, str]] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Deterministic CCR stashing for middle tool outputs, oversized tool_calls args, and user pastes."""
        snames = skill_names if skill_names is not None else _skill_view_names_by_call_id(messages)
        out: list[dict[str, Any]] = []
        changes = 0
        for i, msg in enumerate(messages):
            if head_boundary <= i < tail_boundary:
                role = msg.get("role")
                if role == "tool":
                    content = msg.get("content", "")
                    if self._should_stash(content):
                        stub = self._stash_tool_content(msg, content, skill_names=snames)
                        if stub is not None:
                            out.append({**msg, "content": stub})
                            changes += 1
                            continue
                elif role == "assistant" and isinstance(msg.get("tool_calls"), list):
                    stashed_msg, n_args = self._stash_tool_call_args(msg)
                    if n_args > 0:
                        out.append(stashed_msg)
                        changes += n_args
                        continue
                elif role == "user":
                    content = msg.get("content", "")
                    if self._should_stash_user(content):
                        stub = self._stash_user_content(content)
                        if stub is not None:
                            out.append({**msg, "content": stub})
                            changes += 1
                            continue
            out.append(msg.copy())
        return out, changes

    def _stash_tool_content(
        self,
        msg: dict[str, Any],
        content: str,
        *,
        skill_names: Optional[dict[str, str]] = None,
    ) -> Optional[str]:
        ref = _put_ccr(content)
        if not ref:
            return None
        self._record_session_ref(ref)
        lines = content.count("\n") + 1
        tool_name = str(msg.get("name") or msg.get("tool_name") or "").strip()
        label = f"{tool_name} result" if tool_name else "tool result"
        preview = self._preview_line(content)
        stub = f"[tt:stashed {label} ({lines} lines, {len(content):,} chars) · trimtok_retrieve '{ref}']"
        if preview:
            stub += f"\nPreview: {preview}"
        tc_id = str(msg.get("tool_call_id") or "").strip()
        skill_name = (skill_names or {}).get(tc_id)
        if skill_name:
            stub += f"\n{_skill_pruned_marker(skill_name)}"
        return stub

    def _stash_tool_call_args(self, msg: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """Stash oversized JSON tool_calls arguments (e.g. write_file/patch) to CCR as valid JSON stubs."""
        raw_tcs = msg.get("tool_calls") or []
        new_tcs: list[Any] = []
        stashed = 0
        for tc in raw_tcs:
            if not isinstance(tc, dict):
                new_tcs.append(tc)
                continue
            fn = tc.get("function")
            if isinstance(fn, dict):
                args_str = fn.get("arguments")
                if (
                    isinstance(args_str, str)
                    and len(args_str) >= self.min_args_stash_chars
                    and '"tt_stashed_ref"' not in args_str
                ):
                    ref = _put_ccr(args_str)
                    if ref:
                        self._record_session_ref(ref)
                        fn_name = str(fn.get("name") or "tool")
                        stub_args = json.dumps(
                            {
                                "tt_stashed_ref": ref,
                                "tool": fn_name,
                                "chars": len(args_str),
                                "hint": self._preview_line(args_str, max_chars=80),
                                "retrieve": f"trimtok_retrieve '{ref}'",
                            },
                            ensure_ascii=False,
                        )
                        tc_copy = dict(tc)
                        tc_copy["function"] = {**fn, "arguments": stub_args}
                        new_tcs.append(tc_copy)
                        stashed += 1
                        continue
            new_tcs.append(copy.deepcopy(tc))
        if stashed == 0:
            return msg, 0
        return {**msg, "tool_calls": new_tcs}, stashed

    def _should_stash_user(self, content: Any) -> bool:
        if not isinstance(content, str) or len(content) < self.min_user_stash_chars:
            return False
        if "[tt:stashed" in content or "[tt:checkpoint" in content or content.startswith("[CONTEXT COMPACTION"):
            return False
        return True

    def _stash_user_content(self, content: str) -> Optional[str]:
        ref = _put_ccr(content)
        if not ref:
            return None
        self._record_session_ref(ref)
        lines = content.count("\n") + 1
        head = content[:360].rstrip()
        tail = content[-180:].lstrip()
        return (
            f"{head}\n...\n{tail}\n"
            f"[tt:stashed user payload ({lines} lines, {len(content):,} chars) · trimtok_retrieve '{ref}']"
        )

    def _try_take_prepared_handoff(
        self, raw_middle: list[dict[str, Any]], tier1_middle: list[dict[str, Any]]
    ) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
        """Return (prepared_handoff, remaining_middle) on exact or prefix match, else (None, [])."""
        with self._prepare_lock:
            fp = self._prepared_fingerprint
            plen = self._prepared_middle_len
            handoff = self._prepared_handoff
            if not fp or handoff is None or plen <= 0:
                return None, []

            # Exact match on either raw_middle or tier1_middle
            if len(raw_middle) == plen and _messages_fingerprint(raw_middle) == fp:
                self._prepared_fingerprint = ""
                self._prepared_middle_len = 0
                self._prepared_handoff = None
                return handoff.copy(), []
            if len(tier1_middle) == plen and _messages_fingerprint(tier1_middle) == fp:
                self._prepared_fingerprint = ""
                self._prepared_middle_len = 0
                self._prepared_handoff = None
                return handoff.copy(), []

            # Prefix match: conversation grew by 1-2 turns since prepare-ahead ran
            if len(raw_middle) > plen and _messages_fingerprint(raw_middle[:plen]) == fp:
                self._prepared_fingerprint = ""
                self._prepared_middle_len = 0
                self._prepared_handoff = None
                return handoff.copy(), [m.copy() for m in tier1_middle[plen:]]

        return None, []

    def _tier2_compact_middle(
        self,
        messages: list[dict[str, Any]],
        *,
        raw_messages: Optional[list[dict[str, Any]]] = None,
        head_boundary: int,
        tail_boundary: int,
        skill_names: Optional[dict[str, str]] = None,
        focus_topic: Optional[str] = None,
        memory_context: str = "",
    ) -> Optional[list[dict[str, Any]]]:
        """Fold middle turns into a rolling LLM summary (or prepared/deterministic checkpoint) + CCR ref."""
        h, t = self._align_deletion_boundaries(messages, head_boundary, tail_boundary)
        if h >= t:
            return None
        head = [m.copy() for m in messages[:h]]
        middle = messages[h:t]
        tail = [m.copy() for m in messages[t:]]
        if not middle:
            return None

        # Fast-path: splice pre-warmed summary from on_turn_complete() in <2ms if no custom focus_topic
        if not focus_topic and not memory_context:
            raw_mid = (raw_messages[h:t] if raw_messages and len(raw_messages) == len(messages) else middle)
            prepared, remainder = self._try_take_prepared_handoff(raw_mid, middle)
            if prepared is not None:
                return head + [prepared] + remainder + tail

        handoff_msg = self._build_handoff_message(
            middle,
            skill_names=skill_names,
            focus_topic=focus_topic,
            memory_context=memory_context,
        )
        if handoff_msg is None:
            return None
        return head + [handoff_msg] + tail

    def _build_handoff_message(
        self,
        middle: list[dict[str, Any]],
        *,
        skill_names: Optional[dict[str, str]] = None,
        focus_topic: Optional[str] = None,
        memory_context: str = "",
    ) -> Optional[dict[str, Any]]:
        """Build the rolling/fresh handoff checkpoint message with CCR archive ref and Ghost-Skill markers."""
        if not middle:
            return None

        prior_summaries: list[str] = []
        new_turns: list[dict[str, Any]] = []
        for msg in middle:
            if _is_summary_message(msg):
                body = _extract_summary_body(str(msg.get("content") or ""))
                if body:
                    prior_summaries.append(body)
            else:
                new_turns.append(msg)

        serialized_middle = self._serialize_turns(middle)
        archive_ref = _put_ccr(serialized_middle)
        self._record_session_ref(archive_ref)

        prior_summary = prior_summaries[-1] if prior_summaries else ""
        turns_to_summarize = new_turns if prior_summary else middle

        summary_body = ""
        method = "deterministic"
        if self.enable_llm_summary and time.monotonic() >= self._llm_summary_cooldown_until:
            llm_summary = self._summarize_via_auxiliary_llm(
                self._serialize_turns(turns_to_summarize),
                prior_summary=prior_summary,
                focus_topic=focus_topic,
                memory_context=memory_context,
            )
            if llm_summary:
                summary_body = llm_summary
                method = "hybrid_llm_rolling" if prior_summary else "hybrid_llm"

        if not summary_body:
            summary_body = self._build_deterministic_checkpoint(
                turns_to_summarize,
                prior_summary=prior_summary,
                focus_topic=focus_topic,
            )

        pruned_skills = self._collect_pruned_skills(middle, skill_names=skill_names)
        if pruned_skills:
            markers = "\n".join(f"- {_skill_pruned_marker(s)}" for s in pruned_skills)
            summary_body += f"\n\n## Pruned Skills\n{markers}"

        footer = f"[tt:checkpoint {len(middle)} messages compacted ({method})"
        if archive_ref:
            footer += f" · full history: trimtok_retrieve '{archive_ref}'"
        footer += "]"

        handoff_content = f"{_HANDOFF_PREFIX}\n\n{summary_body}\n\n{footer}"
        return {
            "role": "user",
            "content": handoff_content,
            "_compressed_summary": True,
        }

    @staticmethod
    def _collect_pruned_skills(
        middle: list[dict[str, Any]],
        *,
        skill_names: Optional[dict[str, str]] = None,
    ) -> list[str]:
        """Collect unique skill names whose instructions were dropped/pruned in `middle`."""
        snames = skill_names or {}
        found: list[str] = []
        for msg in middle:
            if msg.get("role") == "tool":
                tc_id = str(msg.get("tool_call_id") or "").strip()
                sname = snames.get(tc_id)
                if sname and sname not in found:
                    found.append(sname)
            content = msg.get("content")
            if isinstance(content, str) and _SKILL_PRUNED_MARKER_PREFIX in content:
                for m in _SKILL_PRUNED_MARKER_RE.findall(content):
                    if m and m not in found:
                        found.append(m)
            if len(found) >= _MAX_PRUNED_SKILL_MARKERS:
                break
        return found[:_MAX_PRUNED_SKILL_MARKERS]

    def _summarize_via_auxiliary_llm(
        self,
        serialized_turns: str,
        *,
        prior_summary: str = "",
        focus_topic: Optional[str] = None,
        memory_context: str = "",
    ) -> Optional[str]:
        """Call Hermes auxiliary LLM for high-signal (or rolling) summarization; fail-open to None."""
        try:
            from agent.auxiliary_client import call_llm, extract_content_or_reasoning
        except Exception:
            return None

        bounded = serialized_turns
        if len(bounded) > _SUMMARY_INPUT_MAX_CHARS:
            half = _SUMMARY_INPUT_MAX_CHARS // 2
            bounded = bounded[:half] + "\n\n[... middle turns elided for summary budget ...]\n\n" + bounded[-half:]

        system_prompt = _ROLLING_SUMMARY_SYSTEM_PROMPT if prior_summary else _SUMMARY_SYSTEM_PROMPT
        prompt_parts = []
        if focus_topic:
            prompt_parts.append(f"Priority focus topic: {focus_topic}")
        if memory_context and memory_context.strip():
            prompt_parts.append(f"Persistent memory context:\n{memory_context.strip()[:2000]}")
        if prior_summary:
            prompt_parts.append(f"Previous Checkpoint:\n{prior_summary[:12_000]}")
            prompt_parts.append(f"New Turns Since Last Checkpoint:\n{bounded}")
        else:
            prompt_parts.append(f"Conversation turns to compact:\n{bounded}")

        try:
            response = call_llm(
                task="compression",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "\n\n".join(prompt_parts)},
                ],
                temperature=0.1,
                timeout=_SUMMARY_LLM_TIMEOUT_S,
            )
            text = (extract_content_or_reasoning(response) or "").strip()
            if len(text) < 40:
                self._llm_summary_cooldown_until = time.monotonic() + _SUMMARY_LLM_COOLDOWN_S
                return None
            return text
        except Exception as exc:
            logger.debug("Trimtok auxiliary LLM summary failed, using deterministic checkpoint: %s", exc)
            self._llm_summary_cooldown_until = time.monotonic() + _SUMMARY_LLM_COOLDOWN_S
            return None

    def _build_deterministic_checkpoint(
        self,
        middle: list[dict[str, Any]],
        *,
        prior_summary: str = "",
        focus_topic: Optional[str] = None,
    ) -> str:
        """Zero-LLM structured checkpoint extracting goals, conclusions, tools/files, errors, and #refs."""
        is_important = _load_is_important()
        user_goals: list[str] = []
        assistant_notes: list[str] = []
        tools_used: list[str] = []
        paths_seen: list[str] = []
        fault_lines: list[str] = []
        refs_seen: list[str] = []

        for msg in middle:
            role = msg.get("role")
            content = msg.get("content")
            text = content if isinstance(content, str) else ""

            for ref in _REF_RE.findall(text):
                if ref not in refs_seen and len(refs_seen) < 20:
                    refs_seen.append(ref)

            if role == "user" and text.strip():
                snippet = self._preview_line(text, max_chars=160)
                if snippet and snippet not in user_goals and len(user_goals) < 8:
                    user_goals.append(snippet)
            elif role == "assistant":
                if text.strip():
                    snippet = self._preview_line(text, max_chars=160)
                    if snippet and snippet not in assistant_notes and len(assistant_notes) < 8:
                        assistant_notes.append(snippet)
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        fn = tc.get("function") or {}
                        tname = str(fn.get("name") or "").strip()
                        if tname and tname not in tools_used and len(tools_used) < 16:
                            tools_used.append(tname)
                        args_str = str(fn.get("arguments") or "")
                        for p in _PATH_RE.findall(args_str):
                            if p not in paths_seen and len(paths_seen) < 16:
                                paths_seen.append(p)
                        for ref in _REF_RE.findall(args_str):
                            if ref not in refs_seen and len(refs_seen) < 20:
                                refs_seen.append(ref)
            elif role == "tool" and text:
                for idx, ln in enumerate(text.splitlines()):
                    if idx >= 400 or len(fault_lines) >= 8:
                        break
                    if is_important(ln):
                        clean = " ".join(ln.strip().split())[:160]
                        if clean and clean not in fault_lines:
                            fault_lines.append(clean)

        sections: list[str] = []
        if focus_topic:
            sections.append(f"## Focus\n- {focus_topic}")
        if prior_summary:
            # Preserve bounded carry-forward of prior checkpoint when rolling deterministically
            sections.append("## Prior Checkpoint\n" + prior_summary[:1200].strip())
        if user_goals:
            sections.append("## User Requests\n" + "\n".join(f"- {g}" for g in user_goals))
        if assistant_notes:
            sections.append("## Progress & Decisions\n" + "\n".join(f"- {n}" for n in assistant_notes))
        if tools_used or paths_seen:
            bits = []
            if tools_used:
                bits.append(f"- Tools: {', '.join(tools_used)}")
            if paths_seen:
                bits.append(f"- Files: {', '.join(paths_seen)}")
            sections.append("## Workspace Activity\n" + "\n".join(bits))
        if fault_lines:
            sections.append("## Errors Observed\n" + "\n".join(f"- {f}" for f in fault_lines))
        if refs_seen:
            sections.append("## Retrievable CCR Refs\n- " + ", ".join(refs_seen))
        return "\n\n".join(sections) if sections else "## Summary\n- Earlier conversation turns compacted."

    @staticmethod
    def _serialize_turns(messages: list[dict[str, Any]]) -> str:
        """Render message dicts into a clean readable transcript for CCR archival and summarization."""
        blocks: list[str] = []
        for idx, msg in enumerate(messages, 1):
            role = str(msg.get("role") or "unknown").upper()
            name = msg.get("name") or msg.get("tool_name")
            header = f"[{idx}] {role}" + (f" ({name})" if name else "")
            parts = [header]
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
            elif isinstance(content, list):
                text_bits = [str(p.get("text")) for p in content if isinstance(p, dict) and p.get("text")]
                if text_bits:
                    parts.append("\n".join(text_bits))
            tcs = msg.get("tool_calls")
            if isinstance(tcs, list) and tcs:
                tc_lines = []
                for tc in tcs:
                    if isinstance(tc, dict):
                        fn = tc.get("function") or {}
                        tc_lines.append(f"  -> {fn.get('name', 'tool')}({str(fn.get('arguments', ''))[:400]})")
                if tc_lines:
                    parts.append("\n".join(tc_lines))
            blocks.append("\n".join(parts))
        return "\n\n---\n\n".join(blocks)

    @staticmethod
    def _preview_line(content: str, max_chars: int = 80) -> str:
        """Extract a compact first-line hint so the agent knows what #ref contains."""
        for raw_ln in content.splitlines()[:8]:
            s = " ".join(raw_ln.strip().split())
            if not s or s.startswith("[tt:legend]"):
                continue
            if len(s) > max_chars:
                return s[: max_chars - 3] + "..."
            return s
        return ""

    def _should_stash(self, content: Any) -> bool:
        """Only a large, already-stashed-free, fault-free tool result is worth a round trip.

        A result the agent is debugging (a traceback it just caused) must stay
        readable: stashing it hides the one line it needs and costs a retrieval.
        """
        if not isinstance(content, str) or len(content) < self.min_stash_chars:
            return False
        if content.startswith("[tt:stashed"):
            return False
        is_important = _load_is_important()
        for idx, line in enumerate(content.splitlines()):
            if idx >= self.fault_scan_lines:
                return True
            if is_important(line):
                return False
        return True

    def clone_for_agent(self) -> TrimtokContextEngine:
        """Create an isolated engine instance for an agent/subagent."""
        cloned = TrimtokContextEngine(
            context_length=self.context_length,
            threshold_percent=self.threshold_percent,
            protect_first_n=self.protect_first_n,
            protect_last_n=self.protect_last_n,
            min_stash_chars=self.min_stash_chars,
            fault_scan_lines=self.fault_scan_lines,
            target_ratio=self.target_ratio,
            min_args_stash_chars=self.min_args_stash_chars,
            min_user_stash_chars=self.min_user_stash_chars,
            proactive_prune_ratio=self.proactive_prune_ratio,
            tail_budget_ratio=self.tail_budget_ratio,
            prepare_ahead_ratio=self.prepare_ahead_ratio,
            enable_llm_summary=self.enable_llm_summary,
            enable_prepare_ahead=self.enable_prepare_ahead,
            enable_auto_recall=self.enable_auto_recall,
        )
        cloned._config_threshold_percent = self._config_threshold_percent
        cloned.model_thresholds = dict(self.model_thresholds)
        cloned.threshold_tokens = self.threshold_tokens
        cloned.last_prompt_tokens = self.last_prompt_tokens
        cloned.last_completion_tokens = self.last_completion_tokens
        cloned.last_total_tokens = self.last_total_tokens
        cloned.last_real_prompt_tokens = self.last_real_prompt_tokens
        with self._prepare_lock:
            cloned._session_refs = set(self._session_refs)
        return cloned
