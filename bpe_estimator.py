"""trimtok bpe_estimator: fast stdlib BPE-token estimate (Trimtok 2.0 Phase 1).

Regex pre-tokenizer in the cl100k/o200k family shape; ~5% of true counts on
mixed ASCII/Thai/code text (refined by the plan's measurement step). Used for
measurement and the Token-Gain Gate reporting — never as the only gate.
Inputs over 32 KB are sampled (first 4 KB) so cost stays ~O(1).
"""

from __future__ import annotations

import re

_BPE_PAT = re.compile(
    r"'s|'t|'re|'ve|'m|'ll|'d| ?\w+| ?\d+| ?[^\s\w\d]+|\s+(?!\S)|\s+"
)

_SAMPLE_CHARS = 4096
_SAMPLE_THRESHOLD = 32768


def estimate_bpe_tokens(text: str) -> int:
    """Approximate BPE token count. Never raises; 0 for non-str/empty."""
    try:
        if not text or not isinstance(text, str):
            return 0
        if len(text) > _SAMPLE_THRESHOLD:
            sample = text[:_SAMPLE_CHARS]
            ratio = len(_BPE_PAT.findall(sample)) / _SAMPLE_CHARS
            return int(len(text) * ratio)
        return len(_BPE_PAT.findall(text))
    except Exception:
        return 0


def estimate_bpe_gain(before: str, after: str) -> int:
    """Token gain of a fold (before - after); >0 means the fold is worth it."""
    return estimate_bpe_tokens(before) - estimate_bpe_tokens(after)
