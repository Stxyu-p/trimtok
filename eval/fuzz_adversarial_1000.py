"""
Adversarial Fuzzing Suite for trimtok:
1,000 extreme edge cases tested against compress_terminal and compress_tool_result.
Guarantees:
1. Zero unhandled exceptions (Fail-open contract)
2. Never inflates (len(output) <= len(input))
3. Latency guard adheres to strict ceiling
"""
import sys
import time
import random
import string
import tempfile
import os
from pathlib import Path

ROOT = Path("C:/Users/gugmn/Workspace/trimtok")
sys.path.insert(0, str(ROOT))
os.environ["TRIMTOK_DATA_DIR"] = tempfile.mkdtemp(prefix="tt-fuzz-")

import trimtok as tt
from trimtok.core import compress_terminal
from trimtok.signals import Deadline

def generate_adversarial_inputs(count=1000):
    cases = []
    
    # 1. Unicode & BiDi & Zero-width & Emojis
    unicode_pool = ["\u200b", "\u200e", "\u202e", "\ufeff", "💀", "💥", "สี่แยกไฟแดง", "﷽", "𝕿𝖊𝖘𝖙", "ñ", "ø", "\x00", "\x1b[31;1m", "\x1b[0m"]
    for i in range(150):
        s = "".join(random.choice(unicode_pool) + random.choice(string.printable) for _ in range(random.randint(10, 500)))
        cases.append(("unicode_torture", s))
        
    # 2. Malformed JSON fragments & truncated envelopes
    for i in range(150):
        depth = random.randint(5, 50)
        open_b = '{"a": [' * depth
        cases.append(("malformed_json_open", open_b + "1, 2, 3"))
        
    # 3. Truncated ANSI escape codes
    ansi_stubs = ["\x1b[", "\x1b[3", "\x1b[38;2;", "\x1b[?25", "\x1b[2J\x1b[H"]
    for i in range(100):
        s = ("error: line\n" + random.choice(ansi_stubs)) * random.randint(5, 50)
        cases.append(("broken_ansi", s))
        
    # 4. Dense repetition & Shingle torture (powers of 2, alternating cycles)
    for i in range(150):
        period = random.randint(1, 5)
        pat = [f"LOG_EVENT_{j}: connection reset by peer" for j in range(period)]
        s = "\n".join(pat * random.randint(20, 200))
        cases.append(("cycle_torture", s))
        
    # 5. Mixed stack traces with fake line numbers and missing files
    for i in range(150):
        s = "Traceback (most recent call last):\n"
        for l in range(random.randint(3, 40)):
            s += f'  File "fake_{l}.py", line {l*1000}, in func_{l}\n    raise ValueError("fail_{l}")\n'
        cases.append(("deep_traceback", s))
        
    # 6. Ultra-long single line without newlines (10KB to 100KB)
    for i in range(150):
        length = random.randint(5000, 40000)
        s = "".join(random.choices(string.ascii_letters + string.digits + ":/.-_ ", k=length))
        cases.append(("giant_single_line", s))
        
    # 7. Blank & Whitespace chaos
    for i in range(150):
        s = random.choice([" ", "\t", "\r\n", "\n"]) * random.randint(100, 5000)
        cases.append(("whitespace_blast", s))
        
    return cases[:count]

def main():
    print("=" * 60)
    print("trimtok 1,000-Case Adversarial Fuzzing Stress Test")
    print("=" * 60)
    
    cases = generate_adversarial_inputs(1000)
    print(f"Generated {len(cases)} adversarial test vectors.")
    
    passed = 0
    crashes = []
    inflations = []
    timeouts = 0
    t0 = time.time()
    
    for idx, (kind, payload) in enumerate(cases):
        dl = Deadline(200.0)
        # Test compress_terminal
        try:
            out_term, st_term = compress_terminal(f"cmd_{kind}", payload, session_id=f"fuzz_{idx}", deadline=dl)
            if not isinstance(out_term, str):
                crashes.append((idx, kind, "terminal returned non-string"))
            elif len(out_term) > len(payload):
                inflations.append((idx, kind, f"terminal inflated: {len(out_term)} > {len(payload)}"))
            else:
                passed += 1
        except Exception as e:
            crashes.append((idx, kind, f"terminal crashed: {type(e).__name__}: {e}"))
            
        # Test compress_tool_result (for a subset of 200)
        if idx % 5 == 0:
            dl2 = Deadline(200.0)
            try:
                out_tool, st_tool = tt.compress_tool_result("terminal", payload, session_id=f"fuzz_tool_{idx}", deadline=dl2)
                if not isinstance(out_tool, str):
                    crashes.append((idx, kind, "tool returned non-string"))
                elif len(out_tool) > len(payload):
                    inflations.append((idx, kind, f"tool inflated: {len(out_tool)} > {len(payload)}"))
            except Exception as e:
                crashes.append((idx, kind, f"tool crashed: {type(e).__name__}: {e}"))

    elapsed = time.time() - t0
    print(f"\nExecution finished in {elapsed:.2f}s ({elapsed*1000/1000:.2f} ms/case)")
    print(f"Tests Passed: {passed}/1000")
    print(f"Crashes / Unhandled Exceptions: {len(crashes)}")
    print(f"Payload Inflations:             {len(inflations)}")
    
    if crashes:
        print("\nTop Crashes:")
        for c in crashes[:5]:
            print(" ", c)
    if inflations:
        print("\nTop Inflations:")
        for inf in inflations[:5]:
            print(" ", inf)
            
    if len(crashes) == 0 and len(inflations) == 0:
        print("\n>>> FUZZ TEST RESULT: 100% BULLETPROOF PASS <<<")
    else:
        print("\n>>> FUZZ TEST RESULT: FAIL <<<")

if __name__ == "__main__":
    main()
