import json
import sys
from pathlib import Path

root = Path("C:/Users/gugmn/Workspace/trimtok")
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "eval"))

import e2e_llm
from eval_real import DATA
from trimtok.ccr import get as ccr_get

tasks = e2e_llm._mk_tasks()
corpus = {json.loads(p.read_text(encoding="utf-8"))["id"]: json.loads(p.read_text(encoding="utf-8"))
          for p in (root / "eval" / "corpus").glob("*.json")}

bundled_tasks = []
for t in tasks:
    item = corpus.get(t["corpus"]) or corpus.get(t["corpus"] + ".py")
    if not item:
        continue
    content = item["content"]
    out, gate = e2e_llm.compress(item)
    narrow_v = e2e_llm.narrow(out, content, t)
    bundled_tasks.append({
        "id": t["id"],
        "query": t["query"],
        "system_note": t["system_note"],
        "truth": t["truth"],
        "gate": gate,
        "raw_sample": content[:12000],
        "comp_sample": narrow_v[:12000],
    })

tasks_json = json.dumps(bundled_tasks, indent=2, ensure_ascii=False)

runner_code = f"""# Colab E2E Evaluation Runner for trimtok
import sys
import time
import json
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

TASKS = {tasks_json}

def main():
    print("=" * 60)
    print("trimtok E2E LLM Evaluation on Colab GPU")
    print("=" * 60)
    if not torch.cuda.is_available():
        print("ERROR: CUDA not available", file=sys.stderr)
        sys.exit(1)
        
    device = torch.cuda.get_device_name(0)
    vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
    print(f"Device: {{device}} ({{vram:.2f}} GB VRAM)")
    
    model_id = "Qwen/Qwen2.5-Coder-7B-Instruct"
    print(f"Loading model: {{model_id}} (float16)...")
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.float16,
        device_map="auto"
    )
    print(f"Model loaded successfully in {{time.time() - t0:.1f}}s")
    
    def ask(system_note: str, context: str, query: str) -> tuple[str, int]:
        prompt = f"Here is a tool result from an AI agent:\\n\\n```\\n{{context}}\\n```\\n\\nTask: {{query}}"
        messages = [
            {{"role": "system", "content": system_note}},
            {{"role": "user", "content": prompt}}
        ]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer([text], return_tensors="pt").to("cuda")
        in_toks = inputs.input_ids.shape[1]
        
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=120,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id
            )
        response = tokenizer.decode(outputs[0][in_toks:], skip_special_tokens=True)
        return response.strip(), in_toks

    print("\\nRunning E2E benchmark across 5 tasks...")
    results = []
    for t in TASKS:
        tid = t["id"]
        sys_note = t["system_note"]
        query = t["query"]
        truth = t["truth"]
        
        # Test RAW
        raw_ans, raw_toks = ask(sys_note, t["raw_sample"], query)
        raw_ok = any(x in raw_ans for x in truth)
        
        # Test COMPRESSED
        comp_ans, comp_toks = ask(sys_note, t["comp_sample"], query)
        comp_ok = any(x in comp_ans for x in truth)
        
        saved_toks = raw_toks - comp_toks
        pct_saved = (saved_toks / raw_toks) * 100 if raw_toks > 0 else 0.0
        
        tag = "PASS" if (comp_ok if raw_ok else True) else "FAIL"
        print(f"\\n[{{tag}}] {{tid}}:")
        print(f"  Raw:        tokens={{raw_toks:,}} ok={{raw_ok}} -> {{raw_ans[:70]}}")
        print(f"  Compressed: tokens={{comp_toks:,}} ok={{comp_ok}} (saved {{pct_saved:.1f}}%) -> {{comp_ans[:70]}}")
        
        results.append({{
            "id": tid,
            "raw_ok": raw_ok,
            "comp_ok": comp_ok,
            "raw_toks": raw_toks,
            "comp_toks": comp_toks,
            "saved_toks": saved_toks,
            "raw_ans": raw_ans,
            "comp_ans": comp_ans
        }})
        
    print("\\n" + "=" * 60)
    print("E2E EVALUATION SUMMARY")
    print("=" * 60)
    total_raw = sum(r["raw_toks"] for r in results)
    total_comp = sum(r["comp_toks"] for r in results)
    total_saved = total_raw - total_comp
    overall_reduction = (total_saved / total_raw) * 100 if total_raw > 0 else 0.0
    
    raw_success = sum(1 for r in results if r["raw_ok"])
    comp_success = sum(1 for r in results if r["comp_ok"])
    both_success = sum(1 for r in results if r["raw_ok"] and r["comp_ok"])
    fidelity = (both_success / raw_success) * 100 if raw_success > 0 else 100.0
    
    print(f"Tasks tested: {{len(results)}}")
    print(f"Raw Success:       {{raw_success}}/{{len(results)}}")
    print(f"Compressed Success: {{comp_success}}/{{len(results)}}")
    print(f"Fidelity Preserved: {{fidelity:.1f}}%")
    print(f"Total Input Tokens (Raw):        {{total_raw:,}}")
    print(f"Total Input Tokens (Compressed): {{total_comp:,}}")
    print(f"Tokens Saved:                    {{total_saved:,}} ({{overall_reduction:.1f}}% token reduction)")
    print("=" * 60)

if __name__ == "__main__":
    main()
"""

out_path = root / "eval" / "colab_e2e_runner.py"
out_path.write_text(runner_code, encoding="utf-8")
print(f"Wrote {out_path} ({len(runner_code)} bytes)")
