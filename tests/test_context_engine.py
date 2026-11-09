"""Tests for TrimtokContextEngine: Native Hermes Context Engine."""

import pytest


def test_context_engine_contract():
    """TrimtokContextEngine must satisfy the Hermes ContextEngine ABC contract."""
    from trimtok.context_engine import TrimtokContextEngine

    engine = TrimtokContextEngine(context_length=128000, threshold_percent=0.50)
    assert engine.name == "trimtok"
    assert engine.context_length == 128000
    assert engine.threshold_percent == 0.50
    assert engine.threshold_tokens == 64000
    assert engine.protect_first_n >= 3
    assert engine.protect_last_n >= 6

    # update_from_response updates token tracking
    engine.update_from_response({"prompt_tokens": 70000, "completion_tokens": 1500, "total_tokens": 71500})
    assert engine.last_prompt_tokens == 70000
    assert engine.last_completion_tokens == 1500
    assert engine.last_total_tokens == 71500

    # should_compress fires when prompt_tokens >= threshold_tokens
    assert engine.should_compress(prompt_tokens=50000) is False
    assert engine.should_compress(prompt_tokens=70000) is True


def test_context_engine_compress_middle_stashing():
    """compress() must protect head and tail, while stashing middle bulky tool results to CCR."""
    import re
    from trimtok.context_engine import TrimtokContextEngine


    engine = TrimtokContextEngine(protect_first_n=2, protect_last_n=2)

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Run tests please."},
        {"role": "assistant", "content": "Running tests now..."},
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "TEST LOG\n" * 200,  # bulky middle tool result
        },
        {"role": "assistant", "content": "Tests had some output. Next checking files..."},
        {
            "role": "tool",
            "tool_call_id": "call_2",
            "content": "FILE DUMP\n" * 150,  # bulky middle tool result
        },
        {"role": "assistant", "content": "Now fixing the bug in code."},
        {"role": "user", "content": "Looks good, finish up."},  # tail
        {"role": "assistant", "content": "Done!"},  # tail
    ]

    compressed = engine.compress(messages)

    # Total message count is preserved (no dropped messages)
    assert len(compressed) == len(messages)

    # Head is completely preserved
    assert compressed[0]["content"] == "You are a helpful assistant."
    assert compressed[1]["content"] == "Run tests please."
    assert compressed[2]["content"] == "Running tests now..."

    # Tail is completely preserved
    assert compressed[-2]["content"] == "Looks good, finish up."
    assert compressed[-1]["content"] == "Done!"

    # Middle assistant messages preserved verbatim
    assert compressed[4]["content"] == "Tests had some output. Next checking files..."
    assert compressed[6]["content"] == "Now fixing the bug in code."

    # Middle tool results are stashed to CCR with retrieve marker
    tool1 = compressed[3]
    assert "[tt:stashed" in tool1["content"]
    assert "trimtok_retrieve" in tool1["content"]

    # CCR should contain the original full payload
    import trimtok.ccr as ccr
    m = re.search(r"(#c[0-9a-zA-Z]+)", tool1["content"])
    assert m is not None, f"Expected ref marker in {tool1['content']}"
    ref = m.group(1)
    retrieved = ccr.get(ref)
    assert retrieved == "TEST LOG\n" * 200


def test_context_engine_clone_for_agent():
    """clone_for_agent must return an isolated engine instance with copied configurations."""
    from trimtok.context_engine import TrimtokContextEngine

    engine = TrimtokContextEngine(context_length=200000, threshold_percent=0.40, protect_first_n=4, protect_last_n=10)
    engine.threshold_tokens = 80000
    engine.last_prompt_tokens = 50000

    cloned = engine.clone_for_agent()
    assert cloned is not engine
    assert isinstance(cloned, TrimtokContextEngine)
    assert cloned.context_length == 200000
    assert cloned.threshold_percent == 0.40
    assert cloned.threshold_tokens == 80000
    assert cloned.protect_first_n == 4
    assert cloned.protect_last_n == 10

    # Mutating cloned state does not pollute parent
    cloned.threshold_tokens = 50000
    assert engine.threshold_tokens == 80000


def test_plugin_register_context_engine():
    """trimtok.register(ctx) must register the context engine when supported."""
    from unittest.mock import MagicMock
    import trimtok
    from trimtok.context_engine import TrimtokContextEngine

    ctx = MagicMock()
    trimtok.register(ctx)

    assert ctx.register_context_engine.called
    engine_arg = ctx.register_context_engine.call_args[0][0]
    assert isinstance(engine_arg, TrimtokContextEngine)
    assert engine_arg.name == "trimtok"




