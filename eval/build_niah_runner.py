import json
import random
import sys
from pathlib import Path

root = Path("C:/Users/gugmn/Workspace/trimtok")
sys.path.insert(0, str(root))

import trimtok as tt
from trimtok.core import compress_terminal
from trimtok.signals import Deadline

# Build realistic 1,200-line server log haystack
noise_templates = [
    "2026-11-08 14:{min:02d}:{sec:02d}.{ms:03d} [INFO] worker-{w} HTTP GET /api/v1/health status=200 latency=1.2ms user_agent=pingdom",
    "2026-11-08 14:{min:02d}:{sec:02d}.{ms:03d} [DEBUG] conn-pool-{w}: active=14 idle=36 max=50 wait_queue_ms=0.1",
    "2026-11-08 14:{min:02d}:{sec:02d}.{ms:03d} [INFO] telemetry-flush: buffered=491 records sent to kafka-broker-02 in 4.5ms",
    "2026-11-08 14:{min:02d}:{sec:02d}.{ms:03d} [DEBUG] gc-stats: minor_gc_count={w} time_ms=0.8 reclaimed_bytes=4194304",
]

lines = []
for i in range(400):
    t = noise_templates[i % len(noise_templates)]
    lines.append(t.format(min=(i // 60) % 60, sec=i % 60, ms=(i * 13) % 1000, w=i % 8))

# Needles at 25%, 50%, 75%
needles = [
    (int(400 * 0.25), "CRITICAL_ALERT_EVENT: security_token_override='alpha-9-omega-key'", "alpha-9-omega-key", "What is the security_token_override value?"),
    (int(400 * 0.50), "DATABASE_CLUSTER_FAULT: replica_port_override=59144 reason=split_brain", "59144", "What is the replica_port_override number?"),
    (int(400 * 0.75), "DIAGNOSTIC_FAILURE: error_code_signature='ERR_CORPUS_DESYNC_88'", "ERR_CORPUS_DESYNC_88", "What is the error_code_signature?"),
]

for idx, needle_line, secret, q in needles:
    lines.insert(idx, needle_line)

haystack_raw = "\n".join(lines)

# Compress haystack with trimtok
out_comp, st = compress_terminal("tail -n 1200 server.log", haystack_raw, session_id="niah_test", deadline=Deadline(200.0))

print(f"Haystack Raw Chars:        {len(haystack_raw):,}")
print(f"Haystack Compressed Chars: {len(out_comp):,}")
print(f"Reduction Ratio:           {(1 - len(out_comp)/len(haystack_raw))*100:.1f}%")

test_cases = []
for idx, needle_line, secret, question in needles:
    test_cases.append({
        "question": question,
        "secret": secret,
        "raw_haystack": haystack_raw,
        "comp_haystack": out_comp,
    })

cases_json = json.dumps(test_cases, indent=2, ensure_ascii=False)

niah_runner_code = f"""# Colab NIAH (Needle In A Haystack) Benchmark
import sys, time, json, torch
from transformers import AutoTokenizer, AutoModelForCausalLM

TESTS = {cases_json}

def main():
    print("=" * 60)
    print("trimtok Needle-In-A-Haystack (NIAH) Benchmark on Colab GPU")
    print("=" * 60)
    if not torch.cuda.is_available():
        print("ERROR: CUDA not available", file=sys.stderr)
        sys.exit(1)
        
    device = torch.cuda.get_device_name(0)
    print(f"GPU: {{device}}")
    
    # 3B model: fits 100% in VRAM with zero CPU offload
    model_id = "Qwen/Qwen2.5-Coder-3B-Instruct"
    print(f"Loading {{model_id}} in float16...")
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.float16,
        device_map="auto"
    )
    print(f"Model loaded in {{time.time() - t0:.1f}}s")
    
    def ask(context: str, query: str) -> tuple[str, int]:
        prompt = f"Below is a server log tail. Find the exact answer to the question.\\n\\n```\\n{{context}}\\n```\\n\\nQuestion: {{query}}\\nAnswer strictly with the exact value requested."
        messages = [
            {{"role": "system", "content": "You are a precise diagnostic engine. Answer strictly with the extracted target string."}},
            {{"role": "user", "content": prompt}}
        ]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer([text], return_tensors="pt").to("cuda")
        in_toks = inputs.input_ids.shape[1]
        
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=40,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id
            )
        res = tokenizer.decode(outputs[0][in_toks:], skip_special_tokens=True).strip()
        return res, in_toks

    print("\\nExecuting NIAH evaluations across depths (25%, 50%, 75%)...")
    scores = []
    for i, t in enumerate(TESTS):
        q = t["question"]
        secret = t["secret"]
        depth = ["25% Depth", "50% (Exact Center)", "75% Depth"][i]
        
        print(f"\\n--- [Needle #{{i+1}} - {{depth}}] ---")
        print(f"Target Secret: {{secret}}")
        
        # Test RAW
        raw_ans, raw_toks = ask(t["raw_haystack"], q)
        raw_ok = secret in raw_ans
        print(f"  Raw:        tokens={{raw_toks:,}} ok={{raw_ok}} -> '{{raw_ans}}'")
        
        # Test COMPRESSED
        comp_ans, comp_toks = ask(t["comp_haystack"], q)
        comp_ok = secret in comp_ans
        saved_pct = (1 - comp_toks/raw_toks) * 100
        print(f"  Compressed: tokens={{comp_toks:,}} ok={{comp_ok}} (saved {{saved_pct:.1f}}%) -> '{{comp_ans}}'")
        
        scores.append((depth, raw_ok, comp_ok, raw_toks, comp_toks))

    print("\\n" + "=" * 60)
    print("NIAH BENCHMARK SCORECARD")
    print("=" * 60)
    for depth, r_ok, c_ok, r_t, c_t in scores:
        status = "RETAINED (100% PASS)" if c_ok else "LOST"
        print(f"{{depth:20s}} | Raw: {{'✔' if r_ok else '✘'}} ({{r_t:,}} tok) | Comp: {{'✔' if c_ok else '✘'}} ({{c_t:,}} tok) | {{status}}")
    print("=" * 60)

if __name__ == "__main__":
    main()
"""

out_file = root / "eval" / "colab_niah_runner.py"
out_file.write_text(niah_runner_code, encoding="utf-8")
print(f"Wrote {out_file} successfully.")
