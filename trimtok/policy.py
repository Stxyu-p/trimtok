"""Content policy: what may be compressed, and how far.

Two orthogonal questions, answered without a model and without I/O:

1. **Sensitivity** -- is this payload something an agent must read byte-exact?
   Secrets, security policy, and file-edit payloads lose their value the moment
   a character moves, so they stay verbatim even when they are huge.

2. **Pressure** -- how much context budget is left? A session at 90% pressure
   should fold harder than one at 20%; a session with room to spare should not
   pay the retrieval round-trip at all.

`classify()` returns a `Policy` and is pure: same input, same answer, no clock.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Paths whose content must never be rewritten: a masked secret is a broken
# secret, and a reflowed policy file is a policy nobody can diff.
_SECRET_PATH_RE = re.compile(
    r"(?:^|[/\\])(?:\.env(?:\.[\w-]+)?|\.npmrc|\.netrc|\.pypirc|\.git-credentials|"
    r"id_rsa|id_ed25519|id_ecdsa|.*\.pem|.*\.key|.*\.p12|.*\.pfx|"
    r"credentials(?:\.\w+)?|secrets?(?:\.\w+)?|.*\.kdbx)$",
    re.I,
)
_SECURITY_PATH_RE = re.compile(
    r"(?:^|[/\\])(?:security|auth|policy|policies|permissions?|acl|rbac|rules)"
    r"(?:[/\\]|\.\w+$)",
    re.I,
)
# Agent/skill instruction documents define workflows and constraints; folding
# prose in them into `[tt:prose ...]` stubs risks stub write-back corruption.
_INSTRUCTION_PATH_RE = re.compile(
    r"(?:^|[/\\])(?:SKILL|AGENTS|CLAUDE|GEMINI|SOUL|CURSORRULES)\.md$",
    re.I,
)
# Source files are safe to skeletonize even under security/, because the AST
# lane keeps every signature and leaves a resolvable ref. Data files are not.
_SOURCE_EXT = frozenset(
    ".py .pyi .js .jsx .ts .tsx .go .rs .java .kt .c .h .cpp .cc .rb .php .cs .swift .scala".split()
)
# A payload that *is* an edit instruction: folding it would corrupt the patch.
_EDIT_TOOLS = frozenset({
    "patch",
    "edit_file",
    "write_file",
    "replace_in_file",
    "apply_patch",
    "skill_manage",
    "multi_edit",
    "create_file",
    "str_replace_editor",
    "write_to_file",
    "replace_file_content",
})
_DIFF_HEAD_RE = re.compile(r"^(?:diff --git |--- a/|\+\+\+ b/|@@ -\d)", re.M)

# Inline secret shapes. Deliberately narrow: a false positive only costs one
# uncompressed payload, a false negative leaks a credential into the CCR vault.
_SECRET_VALUE_RES = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                       # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),             # GitHub tokens
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),                  # OpenAI-style
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),           # Slack
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),         # PEM
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),  # JWT
    re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*\S{8,}"),
)

# Compression ladder. `normal` is today's behaviour; the others trade recall for
# tokens in the direction the caller asks for.
LEVELS = ("off", "light", "normal", "aggressive")


@dataclass(frozen=True)
class Policy:
    verbatim: bool
    reason: str = ""
    level: str = "normal"
    toon_ok: bool = True

    @property
    def compress(self) -> bool:
        return not self.verbatim and self.level != "off"


def looks_secret(text: str, head_chars: int = 4096) -> str:
    """Return the matching shape name, or '' when nothing looks like a secret.

    Scans head, middle (on medium/large payloads), and tail so credentials buried
    in the middle or end of env dumps/configs cannot leak into the CCR vault.
    """
    if not isinstance(text, str) or not text:
        return ""
    n = len(text)
    if n <= head_chars * 2:
        window = text
    elif n <= head_chars * 4:
        window = text
    else:
        mid = n // 2
        half = head_chars // 2
        window = text[:head_chars] + "\n" + text[mid - half : mid + half] + "\n" + text[-head_chars:]
    for rx in _SECRET_VALUE_RES:
        if rx.search(window):
            return rx.pattern[:24]
    return ""


def classify(
    text: str,
    *,
    tool: str = "",
    path: str = "",
    pressure: float = 0.0,
    toon_ok: bool = True,
    allow_secret_scan: bool = True,
) -> Policy:
    """Decide verbatim-vs-compress and the compression level for one payload.

    `pressure` is the fraction of the context window already used (0..1); it
    only moves the level, never the verbatim decision.
    """
    if not isinstance(text, str):
        return Policy(verbatim=True, reason="non_str", level="off")
    if not text:
        return Policy(verbatim=False, reason="", level="normal", toon_ok=toon_ok)

    p = path or ""
    if p:
        if _SECRET_PATH_RE.search(p):
            return Policy(verbatim=True, reason="secret_path", level="off")
        if _INSTRUCTION_PATH_RE.search(p):
            return Policy(verbatim=True, reason="instruction_file", level="off")
        if _SECURITY_PATH_RE.search(p):
            tail_name = p.replace("\\", "/").rsplit("/", 1)[-1]
            ext = ("." + tail_name.rsplit(".", 1)[-1].lower()) if "." in tail_name else ""
            if ext not in _SOURCE_EXT:
                return Policy(verbatim=True, reason="security_path", level="off")

    tool_norm = (tool or "").strip().lower()
    if tool_norm in _EDIT_TOOLS:
        return Policy(verbatim=True, reason="edit_tool", level="off")
    # A diff from a tool payload *is* the edit instruction. A diff from a terminal
    # command (`git diff`) is a report: the pruner keeps every changed line, so it
    # can be folded safely. Exclude `terminal` so tool-routed `git diff` still folds.
    if tool_norm and tool_norm != "terminal" and _DIFF_HEAD_RE.search(text[:2048]):
        return Policy(verbatim=True, reason="patch_payload", level="off")

    if allow_secret_scan:
        shape = looks_secret(text)
        if shape:
            return Policy(verbatim=True, reason="secret_value", level="off")

    level = level_for_pressure(pressure)
    return Policy(verbatim=False, reason="", level=level, toon_ok=toon_ok)


def level_for_pressure(pressure: float) -> str:
    """Map remaining-context pressure to a compression level."""
    try:
        p = float(pressure)
    except Exception:
        return "normal"
    if not math.isfinite(p) or p <= 0.0:
        return "normal"
    if p < 0.35:
        return "light"
    if p < 0.75:
        return "normal"
    return "aggressive"


_LEVEL_BUDGET = {"light": 1.6, "normal": 1.0, "aggressive": 0.55}
_LEVEL_FOLD = {"light": 20, "normal": 8, "aggressive": 4}


def budget_for(level: str, default: int) -> int:
    mult = _LEVEL_BUDGET.get(level, 1.0)
    return max(10, int(default * mult))


def fold_min_lines(level: str) -> int:
    return _LEVEL_FOLD.get(level, 8)


# Ordered longest-first for deterministic matching regardless of PYTHONHASHSEED.
_TOON_FAMILIES_ORDERED: tuple[str, ...] = tuple(
    sorted(
        "claude anthropic gpt openai o3 o4 gemini google deepseek qwen qwq kimi "
        "moonshot glm zai zhipu minimax mistral llama grok xai".split(),
        key=lambda s: (-len(s), s),
    )
)
_TOON_FAMILIES = frozenset(_TOON_FAMILIES_ORDERED)
_SHORT_FAMILIES = frozenset({"o3", "o4", "zai", "xai", "gpt", "glm", "qwq"})
_FAMILY_BOUNDARY_RES = {
    fam: re.compile(rf"(?<![a-z0-9]){re.escape(fam)}(?![a-z0-9])")
    for fam in _SHORT_FAMILIES
}


def model_family_of(model_name: str) -> str:
    """Best-effort deterministic family key from a model id ('claude-sonnet-5' -> 'claude')."""
    n = (model_name or "").strip().lower()
    if not n:
        return ""
    for fam in _TOON_FAMILIES_ORDERED:
        if fam in _SHORT_FAMILIES:
            if _FAMILY_BOUNDARY_RES[fam].search(n):
                return fam
        elif fam in n:
            return fam
    return ""


def toon_allowed_for(family: str, mode: str = "auto") -> bool:
    """Whether TOON is appropriate for this model family under `mode`."""
    if mode == "never":
        return False
    if mode == "always":
        return True
    fam = model_family_of(family) or (family or "").strip().lower()
    if not fam:
        return True
    return fam in _TOON_FAMILIES
