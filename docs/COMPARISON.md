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
| **trimtok** (`v5.9.8`) | 3 | In-process hooks + Hermes ContextEngine + stdio MCP/proxy: terminal, tool results, `read_file`, `grep`, `web_search`/web intake | **Yes** — SQLite WAL + Zstd store (16 KB-capped FTS5), `#ref` retrieval & `trimtok_exec`, cross-process | **No** — deterministic rules only (200 ms guard) | 74.98% chars on a 32-payload corpus (reproducible, audited); see [telemetry](#what-trimtok-actually-measures) |
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

- **Population.** trimtok's 74.98% is a fixed 32-payload corpus of test output,
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

1. **Retrieval and compute-over-vault are the product, not an afterthought.**
   Every transform writes the original to a durable SQLite WAL + Zstd store
   (with a 16 KB-capped FTS5 BM25 index per chunk) and ships a `#ref` marker;
   `trimtok_retrieve` returns exact slices (`errors`, `diff`, `tail`, `--grep`,
   `--intent`) and `trimtok_exec` runs Python over `$TRIMTOK_PAYLOAD` without
   re-inflating the prompt — across turns, across processes, after a crash.
2. **No model in the hot path, strictly bounded latency.** Compression is
   deterministic rules: the same payload always produces the same bytes. A
   monotonic 200 ms guard runs inside regex/AST loops, files over 512 KB jump
   straight to a bounded head+tail pointer (`file_large_pointer`), and wide
   lines (`>= 500` chars) / `grep` / `web_search` have dedicated lossless-backed
   lanes. Cold `skill_view` strips empty boilerplate only so SOP rules stay 100%
   intact.
3. **It publishes an honesty audit.** `eval/audit_honesty.py` recomputes what the
   pipeline claims against what it can prove from the stored originals, and
   reports the gap. The project's own telemetry reports *delivered* savings, not
   attempted ones, including the gates that saved nothing.
4. **Per-version telemetry cohorts & native Hermes ContextEngine.** Savings are
   reported per release (including `trimtok_exec` counts in `trimtok gain`), and
   `context.engine: trimtok` replaces lossy LLM history summarization with
   deterministic CCR stashing plus an 80-char `Preview:` hint per stashed result.

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
| `eval/` corpus (`v5.9.8`) | 32 fixed payloads, 1,135,441 chars, reproducible | **74.98%** char reduction (`851,327` chars), **0** unproven chars |
| `eval/fix_accuracy.py` (`v5.9.8`) | Bug-locate accuracy after folding (no LLM) | **8/8 (100%)** LOCATE, **54.4%** token reduction (`56,933 → 25,956`) |
| `eval/decision_eval.py` (`v5.9.8`) | Critical action tokens retained | **9/9 (100%)** tokens retained, **91.2%** mean reduction |
| `usage.jsonl` (5.9.4 cohort, 2026-11-15) | Real hook traffic, chars delivered | 33.2% on 414 events |

Production telemetry records characters, not model tokens, and a lane that
reports 0% is usually a deliberate passthrough (a targeted line-range read or
cold SOP skill view must stay verbatim) rather than a failure. Anyone quoting a
single number for this tool across all versions is quoting an average over
multiple releases.

## Reproduce it

```bash
git clone https://github.com/Stxyu-p/trimtok && cd trimtok
pip install -e ".[dev]"
python -m unittest discover -s tests          # 179 unit & regression cases
python -m pytest tests/ -q                    # 280 total pytest cases
python eval/eval_real.py                      # corpus run
python eval/audit_honesty.py                  # claimed vs proven
python eval/fix_accuracy.py                   # bug-locate accuracy
python eval/decision_eval.py                  # decision-equivalence gate
trimtok gain                                  # your own telemetry, if installed
```

To check a competitor's number the same way, ask for its corpus, its unit and its
denominator. If a project cannot answer all three, its percentage is marketing.
