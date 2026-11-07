"""
Build Red-Team Runner for Colab GPU.
Packages target modules: ccr.py, delta_engine.py, mcp_proxy.py, kv_lock.py, ts_skeleton.py
Generates an executable Colab script: eval/colab_redteam_runner.py
"""
import json
import sys
from pathlib import Path

ROOT = Path("C:/Users/gugmn/Workspace/trimtok")

MODULES = [
    "trimtok/ccr.py",
    "trimtok/delta_engine.py",
    "trimtok/mcp_proxy.py",
    "trimtok/kv_lock.py",
    "trimtok/ts_skeleton.py",
]

module_sources = {}
for m in MODULES:
    p = ROOT / m
    if p.exists():
        # Keep source concise (omit empty lines / long comments if needed, but preserve full code structure)
        module_sources[m] = p.read_text(encoding="utf-8")
        print(f"Loaded {m}: {len(module_sources[m])} chars")

# We create targeted snippets focusing on high-risk functions
audit_targets = [
    {
        "name": "ccr.py - SQLite WAL & Concurrent Writes",
        "file": "trimtok/ccr.py",
        "code": module_sources["trimtok/ccr.py"][:12000],
        "focus": "Inspect SQLite WAL mode, concurrent process writes, Windows file lock handling (WinError 32), and sequence allocation for #c aliases."
    },
    {
        "name": "delta_engine.py - Compaction & Multi-Turn State",
        "file": "trimtok/delta_engine.py",
        "code": module_sources["trimtok/delta_engine.py"][:12000],
        "focus": "Inspect state eviction (_evict), thread safety under self._lock, and compaction guard boundary conditions."
    },
    {
        "name": "mcp_proxy.py - STDIO Stream & Buffer Deadlock",
        "file": "trimtok/mcp_proxy.py",
        "code": module_sources["trimtok/mcp_proxy.py"][:12000],
        "focus": "Inspect subprocess pipe reading, EOF handling, buffer overflow, and JSON-RPC line parsing deadlocks."
    },
    {
        "name": "kv_lock.py - Appendix Extraction & Memory Bounds",
        "file": "trimtok/kv_lock.py",
        "code": module_sources["trimtok/kv_lock.py"][:12000],
        "focus": "Inspect regex matching of start/end tags, malformed JSON handling, and mutation of global context."
    },
]

targets_json = json.dumps(audit_targets, indent=2, ensure_ascii=False)

colab_script = f"""# Colab GPU Deep Red-Team Audit Runner
import sys
import time
import json
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

TARGETS = {targets_json}

def main():
    print("=" * 60)
    print("trimtok Deep Red-Team Hardening Audit on Colab GPU")
    print("=" * 60)
    if not torch.cuda.is_available():
        print("ERROR: CUDA not available", file=sys.stderr)
        sys.exit(1)
        
    device = torch.cuda.get_device_name(0)
    vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
    print(f"Device: {{device}} ({{vram:.2f}} GB VRAM)")
    
    # Use 3B model for fast loading and generous context window
    model_id = "Qwen/Qwen2.5-Coder-3B-Instruct"
    print(f"Loading model: {{model_id}} in float16...")
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.float16,
        device_map="auto"
    )
    print(f"Model loaded successfully in {{time.time() - t0:.1f}}s")
    
    system_prompt = (
        "You are an elite principal security engineer and adversarial systems auditor. "
        "Audit the provided Python module code for subtle concurrency bugs, Windows OS edge cases (e.g. file lock WinError 32), "
        "memory leaks, race conditions, or unhandled exceptions that violate the fail-open contract. "
        "Be extremely rigorous, technical, and concise. "
        "Format your answer as a clear structured list with: "
        "1. Vulnerability Name & Severity (CRITICAL / HIGH / MEDIUM) "
        "2. Exact Location / Trigger Condition "
        "3. Concrete Exploit / Failure Scenario "
        "4. Minimal Surgical Fix"
    )
    
    all_findings = []
    
    for i, t in enumerate(TARGETS):
        name = t["name"]
        print(f"\\n[{{i+1}}/{{len(TARGETS)}}] Auditing {{name}}...")
        user_prompt = (
            f"Audit Target: {{t['file']}}\\n"
            f"Audit Focus Area: {{t['focus']}}\\n\\n"
            f"Source Code:\\n```python\\n{{t['code']}}\\n```\\n\\n"
            "Identify the top 1-2 most dangerous real vulnerabilities or edge-case bugs in this code."
        )
        messages = [
            {{"role": "system", "content": system_prompt}},
            {{"role": "user", "content": user_prompt}}
        ]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer([text], return_tensors="pt").to("cuda")
        in_toks = inputs.input_ids.shape[1]
        
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=500,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id
            )
        response = tokenizer.decode(outputs[0][in_toks:], skip_special_tokens=True).strip()
        print(f"\\n--- Audit Report for {{name}} ---")
        print(response)
        all_findings.append({{"target": name, "report": response}})
        
    print("\\n" + "=" * 60)
    print("RED-TEAM AUDIT COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    main()
"""

out_file = ROOT / "eval" / "colab_redteam_runner.py"
out_file.write_text(colab_script, encoding="utf-8")
print(f"Wrote {out_file} ({len(colab_script)} bytes)")
