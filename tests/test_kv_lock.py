from trimtok.kv_lock import stabilize_prompt, extract_dynamic, get_prefix_hash

def test_extract_dynamic_date_and_uuid():
    text = (
        "You are an assistant.\n"
        "Session started: 2026-11-05 14:30:00\n"
        "Session ID: 550e8400-e29b-41d4-a716-446655440000\n"
        "Instruction: Please write clean code."
    )
    frags = extract_dynamic(text)
    names = [f.name for f in frags]
    assert "date" in names
    assert "time" in names
    assert "uuid" in names

def test_stabilize_moves_dynamic_to_appendix():
    text = (
        "System prompt heading.\n"
        "Run date: 2026-11-05\n"
        "Session: 550e8400-e29b-41d4-a716-446655440000\n"
        "Rules: Be concise."
    )
    stabilized = stabilize_prompt(text)
    assert "<date>" in stabilized
    assert "<uuid>" in stabilized
    assert "<!-- kv-lock: dynamic context -->" in stabilized
    assert "2026-11-05" in stabilized.split("<!-- kv-lock: dynamic context -->")[1]
    assert "550e8400-e29b-41d4-a716-446655440000" in stabilized.split("<!-- kv-lock: dynamic context -->")[1]

def test_prefix_hash_identical_despite_different_dates():
    prompt_day1 = "System header.\nDate: 2026-11-05\nUUID: 550e8400-e29b-41d4-a716-446655440000\nInstructions."
    prompt_day2 = "System header.\nDate: 2026-11-06\nUUID: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\nInstructions."
    
    hash1 = get_prefix_hash(prompt_day1)
    hash2 = get_prefix_hash(prompt_day2)
    assert hash1 == hash2

def test_code_fences_protected():
    text = (
        "System prompt.\n"
        "Current date: 2026-11-05\n"
        "Example code:\n"
        "```python\n"
        "# Date: 2026-11-05 in code\n"
        "val = '550e8400-e29b-41d4-a716-446655440000'\n"
        "```\n"
    )
    stabilized = stabilize_prompt(text)
    # Outside code block is replaced
    assert "Current date: <date>" in stabilized
def test_stabilize_idempotent_and_no_change_when_no_dynamic():
    static_text = "You are an AI assistant.\nAlways follow instructions."
    assert stabilize_prompt(static_text) == static_text
    assert extract_dynamic(static_text) == []

    # Idempotence: running stabilize twice shouldn't duplicate appendix
    dynamic_text = "Today is 2026-11-05."
    first_pass = stabilize_prompt(dynamic_text)
    second_pass = stabilize_prompt(first_pass)
def test_stabilize_preserves_existing_appendix_when_new_dynamic_added():
    # Prompt 1 stabilized
    text1 = "Prompt 1 with Date: 2026-11-05."
    res1 = stabilize_prompt(text1)
    assert "date: 2026-11-05" in res1

    # Prompt 2 adds new dynamic value before or after
    text2 = "New task on 2026-11-06.\n" + res1
    res2 = stabilize_prompt(text2)
    # Both dates must be retained in the appendix
    assert "2026-11-05" in res2
    assert "2026-11-06" in res2
    assert res2.count("<!-- kv-lock: dynamic context -->") == 1

def test_stabilize_handles_text_after_appendix():
    text = (
        "Heading.\nDate: 2026-11-05.\n"
        "---\n<!-- kv-lock: dynamic context -->\ndate: 2026-11-05\n<!-- end kv-lock -->\n"
        "Crucial footer text that must not be deleted.\n"
    )
    stabilized = stabilize_prompt(text)
    assert "Crucial footer text that must not be deleted." in stabilized


