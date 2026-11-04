# eval — the real-payload scoreboard

No mocks, no synthetic text. Every payload in `corpus/` records its `origin` (the file
path it was read from or the exact command that produced it), so any number here can be
traced back and re-derived.

```
python eval/build_corpus.py            # (re)build corpus/*.json from this machine
python eval/eval_real.py --label x     # measure, writes eval/result-x.json
```

`eval_real.py` calls the same entry points Hermes calls (`compress_terminal`,
`compress_tool_result`) with a real `Deadline(200)` per payload, and points
`TRIMTOK_DATA_DIR` at a throwaway dir so it never touches `~/.trimtok` telemetry.

## What the numbers mean

**Reduction** = `sum(saved)/sum(before)` over the corpus. This corpus is deliberately
compressible-only (every payload >= 2KB, real logs and real source), so it reads much
higher than blended production traffic (9.5%). Use it for *deltas*, never as a headline.

## Accuracy gates (a payload with any failure is a defect, not a trade-off)

| gate | invariant |
|---|---|
| C1 | every line missing from the output is recoverable through a `#ref` that resolves |
| C2 | an error line may be hidden only if the header says `errors_hidden=N` and all N are retrievable |
| C3 | when both parse as JSON, they parse equal |
| C4 | every top-level `def`/`class` name survives (code outline invariant) |
| C5 | a read_file payload is byte-identical or replaced by content whose refs restore it |

Baseline at v5.3.3 (32 payloads, 1,135,441 chars): **73.14%** reduction, 20/32 touched,
0 gate failures. Every folded line is resolvable through a `#ref`, patch-safe
(`LINE|` ordering checked), and announced in the output -- `file_skeleton`
headers now carry `[folded=N L -> trimtok_retrieve]`.

## Ceiling probe (how far can we go without harming intelligence)

```
python eval/probe_ceiling.py      # per-payload: hidden lines, anchor retention, fault announcement
python eval/audit_honesty.py      # claimed vs proven reduction (announce + recover + patch-safe)
```

- **SILENT LINE LOSS:** every hidden line must resolve via `#ref` (currently 0).
- **ANCHOR RETENTION:** navigation anchors (file:line, paths, test ids, error
  classes) surviving in visible text, averaged by payload weight. Log dumps
  fold below 0.8 by design (old-turn history is legitimately windowed);
  code/diff/test payloads stay at 1.0.
- **UNANNOUNCED FAULTS:** terminal fault lines hidden without `errors_hidden=N`.
- **PROVEN REDUCTION:** savings that survive announce + recover + patch-order
  checks (v5.3.3: 73.14% claimed = 73.14% proven).
