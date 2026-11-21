"""KV-Cache Alignment & Prompt Stabilization (QuantumLock pattern).

Stabilizes system prompts and instructions by replacing dynamic values
(dates, timestamps, UUIDs, session IDs) in the prefix with fixed placeholders
and moving the real values to an appendix at the tail of the message.

Ensures the static prompt prefix remains byte-identical across sessions and requests,
achieving up to 90% prompt-cache hit rates on modern LLM APIs (Anthropic, Gemini, DeepSeek).

Stdlib only, fail-open, code-fence aware.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Set

APPENDIX_START = "<!-- kv-lock: dynamic context -->"
APPENDIX_END = "<!-- end kv-lock -->"
APPENDIX_SEP = "---"

APPENDIX_BLOCK_RE = re.compile(
    r"\n*---\s*\n*<!-- kv-lock: dynamic context -->([\s\S]*?)<!-- end kv-lock -->\n*",
    re.MULTILINE,
)

# Code block pattern to avoid modifying user examples or source code
FENCE_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)
_APPENDIX_KV_RE = re.compile(r"^([a-z][a-z0-9_-]*):\s*(.*)$")

PATTERNS = [
    (
        "date",
        re.compile(
            r"\b(?:"
            r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?"
            r"|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),[ \t]+(?:January|February|March|April|May|June|July|August|September|October|November|December)[ \t]+\d{1,2},[ \t]+\d{4}"
            r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[ \t]+\d{1,2},[ \t]+\d{4}"
            r")\b",
            re.IGNORECASE,
        ),
        "<date>",
    ),
    (
        "time",
        # Case-sensitive uppercase TZ & horizontal spaces only so lowercase words ('for', 'you') and newlines are never eaten
        re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[ \t]*(?:UTC|GMT|[A-Z]{3}))?\b"),
        "<time>",
    ),
    (
        "uuid",
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<uuid>",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        "<jwt>",
    ),
]


@dataclass(frozen=True)
class DynamicFragment:
    """An extracted dynamic token from prompt text."""
    name: str
    original: str
    placeholder: str


def _extract_from_prose(prose_text: str) -> list[DynamicFragment]:
    fragments: list[DynamicFragment] = []
    seen: Set[str] = set()
    for name, regex, placeholder in PATTERNS:
        for match in regex.finditer(prose_text):
            val = match.group(0).strip()
            if val and val not in seen:
                seen.add(val)
                fragments.append(DynamicFragment(name=name, original=val, placeholder=placeholder))
    return fragments


def extract_dynamic(content: str) -> list[DynamicFragment]:
    """Extract dynamic tokens outside of code blocks and outside existing kv-lock appendix."""
    if not isinstance(content, str) or not content:
        return []

    parts = FENCE_RE.split(content)
    prose_parts = [APPENDIX_BLOCK_RE.sub("\n", parts[i]) for i in range(0, len(parts), 2)]
    return _extract_from_prose("\n".join(prose_parts))


def stabilize_prompt(content: str) -> str:
    """Stabilize prompt content for KV-cache alignment.

    Extracts dynamic fragments outside of code blocks, replaces them with
    placeholders, and relocates real values into an appendix at the end.
    Idempotent: parses prior kv-lock appendix, merges values, and replaces cleanly.
    """
    if not isinstance(content, str) or not content:
        return content

    # Split fences ONCE so appendix markers inside fenced code blocks stay untouched
    parts = FENCE_RE.split(content)

    existing_frags: list[DynamicFragment] = []
    seen_originals: Set[str] = set()
    cleaned_parts: list[str] = []

    for i, part in enumerate(parts):
        if i % 2 == 1:
            cleaned_parts.append(part)
            continue
        for m in APPENDIX_BLOCK_RE.finditer(part):
            pairs: list[list[str]] = []
            for line in m.group(1).splitlines():
                km = _APPENDIX_KV_RE.match(line)
                if km:
                    pairs.append([km.group(1).strip(), km.group(2).strip()])
                elif pairs and line.strip():
                    # Continuation of a multi-line value (e.g. URL/timestamp won't trigger false key split)
                    pairs[-1][1] += "\n" + line.strip()
            for k, v in pairs:
                if v and v not in seen_originals:
                    seen_originals.add(v)
                    existing_frags.append(DynamicFragment(name=k, original=v, placeholder=f"<{k}>"))
        cleaned_parts.append(APPENDIX_BLOCK_RE.sub("\n", part))

    prose_text = "\n".join(cleaned_parts[i] for i in range(0, len(cleaned_parts), 2))
    new_fragments = _extract_from_prose(prose_text)
    if not new_fragments and not existing_frags:
        return content

    combined_frags: list[DynamicFragment] = list(existing_frags)
    for frag in new_fragments:
        if frag.original not in seen_originals:
            seen_originals.add(frag.original)
            combined_frags.append(frag)

    # Sort fragments by original length descending to avoid substring collision
    sorted_frags = sorted(combined_frags, key=lambda f: len(f.original), reverse=True)

    new_parts: list[str] = []
    for i, part in enumerate(cleaned_parts):
        if i % 2 == 1:
            new_parts.append(part)
        else:
            prose = part
            for frag in sorted_frags:
                prose = prose.replace(frag.original, frag.placeholder)
            new_parts.append(prose)

    stabilized_prefix = "".join(new_parts).rstrip()

    appendix_lines = [
        "",
        APPENDIX_SEP,
        APPENDIX_START,
    ]
    for frag in combined_frags:
        appendix_lines.append(f"{frag.name}: {frag.original}")
    appendix_lines.append(APPENDIX_END)

    return stabilized_prefix + "\n" + "\n".join(appendix_lines)


def get_prefix_hash(content: str) -> str:
    """Compute SHA-256 hash of the stable prefix before dynamic appendix."""
    if not isinstance(content, str):
        return ""
    stabilized = stabilize_prompt(content)
    sep_marker = f"\n{APPENDIX_SEP}\n{APPENDIX_START}"
    idx = stabilized.find(sep_marker)
    prefix = stabilized[:idx] if idx != -1 else stabilized
    return hashlib.sha256(prefix.encode("utf-8", "replace")).hexdigest()
