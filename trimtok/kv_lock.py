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
from typing import List, Set, Tuple

APPENDIX_START = "<!-- kv-lock: dynamic context -->"
APPENDIX_END = "<!-- end kv-lock -->"
APPENDIX_SEP = "---"

# Code block pattern to avoid modifying user examples or source code
FENCE_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)

PATTERNS = [
    (
        "date",
        re.compile(
            r"\b(?:"
            r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?"
            r"|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
            r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}"
            r")\b",
            re.IGNORECASE,
        ),
        "<date>",
    ),
    (
        "time",
        re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\s*(?:UTC|GMT|[A-Z]{3}))?\b", re.IGNORECASE),
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


def extract_dynamic(content: str) -> list[DynamicFragment]:
    """Extract dynamic tokens outside of code blocks and outside existing kv-lock appendix."""
    if not content:
        return []

    # Strip existing appendix if present to avoid re-extracting previously locked values
    main_content = content
    if APPENDIX_START in content:
        main_content = content.split(APPENDIX_START, 1)[0]
        sep_marker = f"\n{APPENDIX_SEP}\n"
        if main_content.endswith(sep_marker):
            main_content = main_content[:-len(sep_marker)]

    fragments: list[DynamicFragment] = []
    seen: Set[str] = set()

    # Only inspect non-code portions
    parts = FENCE_RE.split(main_content)
    prose_parts = [parts[i] for i in range(0, len(parts), 2)]
    prose_text = "\n".join(prose_parts)

    for name, regex, placeholder in PATTERNS:
        for match in regex.finditer(prose_text):
            val = match.group(0).strip()
            if val and val not in seen:
                seen.add(val)
                fragments.append(DynamicFragment(name=name, original=val, placeholder=placeholder))

    return fragments


def stabilize_prompt(content: str) -> str:
    """Stabilize prompt content for KV-cache alignment.

    Extracts dynamic fragments outside of code blocks, replaces them with
    placeholders, and relocates real values into an appendix at the end.
    Idempotent: strips prior kv-lock appendix before processing.
    """
    if not content:
        return content

    # Check for existing appendix to maintain idempotence
    base_content = content
    existing_appendix = ""
    if APPENDIX_START in content:
        base_content = content.split(APPENDIX_START, 1)[0]
        sep_marker = f"\n{APPENDIX_SEP}\n"
        if base_content.endswith(sep_marker):
            base_content = base_content[:-len(sep_marker)]
        existing_appendix = content[len(base_content):]

    fragments = extract_dynamic(base_content)
    if not fragments:
        return content

    # Sort fragments by original length descending to avoid substring collision
    sorted_frags = sorted(fragments, key=lambda f: len(f.original), reverse=True)

    parts = FENCE_RE.split(base_content)
    new_parts: list[str] = []

    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Code block: leave completely untouched
            new_parts.append(part)
        else:
            # Prose block: apply replacements
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
    for frag in fragments:
        appendix_lines.append(f"{frag.name}: {frag.original}")
    appendix_lines.append(APPENDIX_END)

    return stabilized_prefix + "\n" + "\n".join(appendix_lines)


def get_prefix_hash(content: str) -> str:
    """Compute SHA-256 hash of the stable prefix before dynamic appendix."""
    stabilized = stabilize_prompt(content)
    sep_marker = f"\n{APPENDIX_SEP}\n{APPENDIX_START}"
    idx = stabilized.find(sep_marker)
    prefix = stabilized[:idx] if idx != -1 else stabilized
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()
