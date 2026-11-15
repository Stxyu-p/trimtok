# How trimtok compares

Every tool in this space publishes a percentage. Almost none of them can be
ranked against each other, and this page explains why before it shows any number.

If you are choosing a token-reduction tool, the useful question is not "which
percentage is biggest" but **"what does it do to my context, and can I get the
original back"**. That is the axis this comparison is built on.

## The comparison table

Star counts read from the GitHub API on 2026-11-15. Saving figures are each
project's own published claim, quoted as they state it — see
[Why these numbers are not comparable](#why-these-numbers-are-not-comparable)
before using them to rank anything.

| Project | Stars | Layer it works on | Reversible | Model in the hot path | Claimed saving (as published) |
| --- | --: | --- | --- | --- | --- |
| **trimtok** | 3 | In-process hooks + stdio MCP: tool results, terminal output, file reads, web intake | **Yes** — SQLite WAL + Zstd store, `#ref` retrieval, cross-process | **No** — deterministic rules only | 74.79% chars on a 32-payload corpus (reproducible, audited); see [telemetry](#what-trimtok-actually-measures) |
| [LLMLingua](https://github.com/microsoft/LLMLingua) | 6,823 | Prompt text, as a library | No (drops tokens; GPT-4 recovery is prompted, not guaranteed) | **Yes** — GPT-2-small / phi-2 classifier | "up to 20x compression" |
| [claw-compactor](https://github.com/open-compress/claw-compactor) | 2,005 | 14-stage fusion pipeline over general text | Yes (reversible) | No | not stated as a single figure |
| [leanctx](https://github.com/jia-gao/leanctx) | 322 | Prompt text, drop-in library for production apps | Partial | No | "40-60%" |
| [squeez](https://github.com/claudioemmanuel/squeez) | 232 | Hooks for 7 AI CLI hosts (Claude Code, Copilot CLI, OpenCode, Gemini, …) | Partial | No | 90.7% per command (third-party measurement, not the project's own) |
| [token-crunch](https://github.com/micaelmalta/token-crunch) | 2 | Three Claude Code hooks, session store file | Partial (dedup by reference) | No | 84.4% on its own benchmark |
| [glyphdown](https://github.com/MikkoParkkola/glyphdown) | 2 | Claude Code plugin: tool results, dedup, system-prompt dialect | Yes, by meaning (`expand(compress(x)) == x`) | No | −31.7% on a 52-fixture corpus, −71.1% on large Bash dumps |
| [cctx](https://github.com/anil7948/cctx) | — | MCP server + Stop hook, codebase index, turn summaries | No | **Yes** — local Ollama model | 70-90% on long sessions |

Other tools you will meet in this space solve a different problem and are not
competitors: `rtk` and similar CLIs truncate command output before the agent sees
it (one-way), `tokless` wires other people's tools together, and
`markitdown` converts documents into clean Markdown so less noise enters context
in the first place.

## Why these numbers are not comparable

A percentage only means something together with the population it was measured on
and the unit it was measured in. In this space they rarely match:

- **Population.** trimtok's 74.79% is a fixed 32-payload corpus of test output,
  source files, JSON, logs and web pages. token-crunch's 84.4% is its own
  synthetic scenario list. squeez's 90.7% is per-command. glyphdown's −31.7% is
  52 real session fixtures. Averaging a per-command figure with a corpus figure
  produces a number that describes nothing.
- **Unit.** Characters are not tokens. Every model tokenizes differently, so a
  character reduction is a general property while a token percentage is
  model-specific. trimtok reports characters from telemetry for exactly this
  reason, and its corpus audit reports both.
- **Denominator.** A tool that only fires on large payloads can post a huge
  percentage while saving little total volume. trimtok's own telemetry shows the
  reverse case too: its largest single bucket is a deliberate 0% passthrough.

## Where trimtok is the same as the field

- Hooks on tool output, ANSI/JSON noise removal, and dedup of repeated reads are
  table stakes here. So are fail-open behaviour and a header telling the model
  what happened.
- Every serious tool in this list keeps error output intact. If a tool claims
  otherwise, treat that as a bug report.

## Where trimtok differs

1. **Retrieval is the product, not a feature.** Every transform writes the
   original to a durable SQLite WAL + Zstd store and ships a `#ref` marker;
   `trimtok_retrieve` returns the exact original — across turns, across processes,
   after a crash. Nothing is lost, so aggressive folding is safe to attempt.
2. **No model in the hot path.** Compression is deterministic rules: the same
   payload always produces the same bytes. That keeps the hook in single-digit
   milliseconds and makes the output reproducible and auditable.
3. **It publishes an honesty audit.** `eval/audit_honesty.py` recomputes what the
   pipeline claims against what it can prove from the stored originals, and
   reports the gap. The project's own telemetry reports *delivered* savings, not
   attempted ones, including the gates that saved nothing.
4. **Per-version telemetry cohorts.** Savings are reported per release, because an
   all-versions average hides which fix already shipped and which bucket is still
   at zero.

## Where trimtok is weaker — say it plainly

- **Prose compression.** A learned compressor (LLMLingua and friends) will beat
  deterministic rules on dense natural-language text. trimtok deliberately does
  not drop semantic content it cannot prove is redundant.
- **Distribution.** Three stars and no native binary. `glyphdown` ships a compiled
  hot path; `squeez` ships via npm to seven hosts. trimtok is Python and needs a
  working interpreter.
- **Host coverage.** It integrates through hooks and MCP, so hosts that expose
  neither need an adapter that does not exist yet.
- **Prompt-cache-aware system-prompt rewriting** (glyphdown's dialect) is not
  implemented. trimtok compresses tool results and file reads, not your
  instruction prose.

## What trimtok actually measures

Two different populations, never combined:

| Source | What it is | Number |
| --- | --- | --- |
| `eval/` corpus | 32 fixed payloads, 1,135,441 chars, reproducible | 74.79% char reduction, 0 unproven chars |
| `usage.jsonl` (5.9.4 cohort, 2026-11-15) | Real hook traffic, chars delivered | 33.2% on 414 events |

Production telemetry records characters, not model tokens, and a lane that
reports 0% is usually a deliberate passthrough (a targeted line-range read must
stay verbatim) rather than a failure. Anyone quoting a single number for this tool
across all versions is quoting an average over six releases.

## Reproduce it

```bash
git clone https://github.com/Stxyu-p/trimtok && cd trimtok
pip install -e ".[dev]"
python -m unittest discover -s tests          # unit suite
python eval/eval_real.py                      # corpus run
python eval/audit_honesty.py                  # claimed vs proven
trimtok gain                                  # your own telemetry, if installed
```

To check a competitor's number the same way, ask for its corpus, its unit and its
denominator. If a project cannot answer all three, its percentage is marketing.
