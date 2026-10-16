"""trimtok marker: one module for the [tt:...] shape (arch C1).

Every marker the pipeline emits and every gate that skips compressed output
go through here. Format change happens once. Never raises.
"""

from __future__ import annotations

import re

# Matches every marker generation: legacy ([tt:seen ... retrieve:<hex>]), the
# v2 ref form ([tt:... #c1]), and the compact pointer form ([@#c1], [@#c1x5]).
SENTINEL_RE = re.compile(
    r"\[(?:@#[0-9A-Za-z]+(?:x\d+)?\]"
    r"|tt:(?:legend\]|(?:terminal|tool|seen|masked|template|shingle|headtail|delta)\b"
    r"[^\]]*(?:retrieve:[0-9a-f]+|#[0-9A-Za-z]+)))"
)

# Single source of truth (zero-spaghetti law 1): KEEP prefix + template-slot
# regex live here because core + dedup both need them. The slot pattern is
# the stronger of the two historical copies: volatile placeholders, UUIDs,
# short times, and digits inside identifiers (func_0) all become {}.
KEEP_PREFIX = "TRIMTOK_KEEP:"

# Error preservation patterns live in polarity.py (Phase 2: single source,
# +HTTP-status faults). is_important stays here as the name all folds import.
try:
    from .polarity import is_fault_line, looks_suspicious  # noqa: F401
except ImportError:  # direct unit-test import
    from polarity import is_fault_line, looks_suspicious  # type: ignore  # noqa: F401


def is_important(line: str) -> bool:
    """True when a line contains an error, exception, or failure indicator."""
    if not line:
        return False
    if line.startswith(KEEP_PREFIX):
        return True
    return is_fault_line(line)

SLOT_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|<TAG>|<UUID>|<SESS>|<TS>|\b\d{1,2}:\d{2}:\d{2}\b|(?<!\d)\d+",
    re.I,
)

LEGEND_FOLD = "[tt:legend] Fold markers: [repeated Nx], [@#ref] (xN = N lines). Full originals in CCR via trimtok_retrieve."
LEGEND_TOON = (
    "[tt:legend] TOON table (lossless): \"@prefix(A)=base\" = shared path "
    "shorthand; \"@rows[N]{f1,f2}:\" header, then N rows, cells split by \"|\" "
    "(quoted = literal)."
)
LEGEND_DELTA = (
    "[tt:legend] Delta vs previous run of the same command: + added, "
    "- resolved/removed, ! still failing; refs = full snapshots via trimtok_retrieve."
)


def delta_head(cmd: str, sim: float, added: int, removed: int, still: int, cur_ref: str, prev_ref: str) -> str:
    return (
        f'[tt:delta cmd="{cmd[:60].replace(chr(34), chr(39))}" sim={sim:.0%} '
        f"+#{added} -#{removed} !#{still} {cur_ref} {prev_ref}]"
    )


def terminal_head(kind: str, hidden: int, ref: str) -> str:
    return f"[tt:terminal kind={kind} hidden={hidden} #{ref}]"


def tool_head(saved: int, ref: str) -> str:
    return f"[tt:tool kind=result saved={saved} #{ref}]"


# Pointer family: folded line runs collapse to a minimal ref stub so repeated
# folds stay cheap — '#c1' alias when stored, 12-hex before the store swap.
# Format: [@#ref] single line; [@#refxN] N-line span (Argon plan Stage 0).
def seen_lines(count: int, ref: str) -> str:
    return f"[@#{ref}x{count}]"


def seen_line(ref: str) -> str:
    return f"[@#{ref}]"


def masked(chars: int, ref: str) -> str:
    return f"[tt:masked chars={chars} #{ref}]"


def template_head(count: int, ref: str) -> str:
    return f"[tt:template lines={count} #{ref}]"


def shingle_head(count: int, ref: str) -> str:
    # count kept for call-shape compatibility (callers always pass 1)
    return f"[tt:shingle #{ref}]"


def headtail_head(kept_head: int, kept_tail: int, omitted: int, ref: str) -> str:
    # head/tail counts are derivable from the stub body itself; only the
    # omission count and the ref are load-bearing. Params kept for call shape.
    return f"[tt:headtail omitted={omitted} #{ref}]"


# v2 refs are short Base62 aliases (#c1) or 12-hex digests; both forms carry
# the same meaning: "the full original sits in CCR under this ref".
_HASH_RE = re.compile(r"(?:retrieve:|#)([0-9A-Za-z]{1,16})")


try:
    from .bpe_estimator import estimate_bpe_tokens as _est_bpe
except ImportError:
    from bpe_estimator import estimate_bpe_tokens as _est_bpe


def estimate_tokens(text: str) -> int:
    """Accurate BPE token estimate via bpe_estimator. Never raises."""
    try:
        return _est_bpe(text)
    except Exception:
        return 0



# W-guards: digit-dense identifiers. NOTE (learned the hard way):
#  - `:\d{2,5}` needs (?<!\d) or "20:00:01" timestamps exempt everything
#  - "line\s+\d+" matches prose "content line 30" — source-line refs are
#    already covered by error rails (important never folds)
_EXEMPT_RE = re.compile(
    r"(?i)port\s+\d{2,5}|(?<!\d):\d{2,5}\b|[0-9a-f]{7,40}|"
    r"\b\d{3,}\s+(errors|lines|files|rows|tests|passed|failed)\b"
)


def is_exempt(line: str) -> bool:
    """Never-fold lines (LLMLingua-2 force-reserve shape): digit-dense
    identifiers (ports, counts, hashes, line numbers) fold unsafely — the
    number IS the content. KEEP lines always exempt. Never raises."""
    try:
        if (line or "").startswith("TRIMTOK_KEEP:"):
            return True
        return bool(_EXEMPT_RE.search(line or ""))
    except Exception:
        return False


def find_hashes(text: str) -> list[str]:
    """All retrieve hashes in a marker-carrying output. Never raises."""
    try:
        return _HASH_RE.findall(text or "")
    except Exception:
        return []


def is_compressed(text: str) -> bool:
    """True when the head window carries a real marker (sentinel gate)."""
    try:
        return bool(SENTINEL_RE.search((text or "")[:2000]))
    except Exception:
        return False
