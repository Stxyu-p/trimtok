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

Baseline at v4.2.6+ (32 payloads, 1,135,441 chars): **62.47%** reduction, 13/32 touched,
2 defects: `t-log-errors` C2-faults(333), `t-log-gateway-stdio` C2-faults(104) —
the terminal lane hides error lines without saying so.
