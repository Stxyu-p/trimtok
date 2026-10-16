"""trimtok bpe_estimator: fast BPE-token estimation and fast JSON ingestion (Trimtok 3.0 Phase 1).

Uses tiktoken / tokenizers singleton with strict offline loading (zero network calls),
falling back gracefully to regex + byte-class estimation.
Also provides fast_json_loads using jiter (Rust) with stdlib fallback.
Never raises.
"""

from __future__ import annotations

import json
import re
from typing import Any

_BPE_PAT = re.compile(
    r"'s|'t|'re|'ve|'m|'ll|'d| ?\w+| ?\d+| ?[^\s\w\d]+|\s+(?!\S)|\s+"
)

_SAMPLE_CHARS = 8192
_SAMPLE_THRESHOLD = 65536

# Singleton offline BPE Tokenizer
_TOKENIZER: Any = None

try:
    import tiktoken
    # Use offline cl100k_base (standard for GPT-4 / DeepSeek / modern tools)
    _TOKENIZER = tiktoken.get_encoding("cl100k_base")
except Exception:
    _TOKENIZER = None

# Fast JSON parser via jiter (Rust)
try:
    import jiter
    _JITER_AVAILABLE = True
except Exception:
    _JITER_AVAILABLE = False


def fast_json_loads(data: str | bytes) -> Any:
    """Parse JSON fast using jiter (Rust) when available, falling back to stdlib json.loads.

    Raises ValueError / json.JSONDecodeError on invalid JSON.
    """
    if _JITER_AVAILABLE:
        if isinstance(data, str):
            return jiter.from_json(data.encode("utf-8"))
        return jiter.from_json(data)
    if isinstance(data, bytes):
        return json.loads(data.decode("utf-8", "replace"))
    return json.loads(data)


def estimate_bpe_tokens(text: str) -> int:
    """Approximate or exact BPE token count. Never raises; 0 for non-str/empty."""
    try:
        if not text or not isinstance(text, str):
            return 0
        
        # Fast path with Rust tiktoken singleton
        if _TOKENIZER is not None:
            if len(text) > _SAMPLE_THRESHOLD:
                sample = text[:_SAMPLE_CHARS]
                count = len(_TOKENIZER.encode_ordinary(sample))
                return max(1, int(len(text) * (count / _SAMPLE_CHARS)))
            return len(_TOKENIZER.encode_ordinary(text))
        
        # Robust stdlib regex fallback
        if len(text) > _SAMPLE_THRESHOLD:
            sample = text[:_SAMPLE_CHARS]
            ratio = len(_BPE_PAT.findall(sample)) / _SAMPLE_CHARS
            return max(1, int(len(text) * ratio))
        return len(_BPE_PAT.findall(text))
    except Exception:
        return 0


def estimate_bpe_gain(before: str, after: str) -> int:
    """Token gain of a fold (before - after); >0 means the fold is worth it."""
    return estimate_bpe_tokens(before) - estimate_bpe_tokens(after)


def is_tiktoken_available() -> bool:
    """Return True if tiktoken offline tokenizer is loaded."""
    return _TOKENIZER is not None


def is_jiter_available() -> bool:
    """Return True if Rust jiter parser is loaded."""
    return _JITER_AVAILABLE
