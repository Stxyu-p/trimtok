from pathlib import Path
from trimtok.codemap import build_codebase_map

def test_codemap_focus_prioritizes_related_imports(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "service.py").write_text("from .auth import verify\nfrom .db import query\ndef run_service(): pass\n", encoding="utf-8")
    (src / "auth.py").write_text("def verify(token): pass\n", encoding="utf-8")
    (src / "db.py").write_text("def query(sql): pass\n", encoding="utf-8")
    
    # An unrelated module
    (src / "unrelated.py").write_text("def send_email(): pass\n", encoding="utf-8")

    # When focusing on service.py, auth and db symbols should be prioritized
    result = build_codebase_map(root=tmp_path, budget_chars=350, focus_file="src/service.py")
    assert "src/: " in result
    assert "run_service" in result
    assert "verify" in result
    assert "query" in result

def test_codemap_focus_budget_omits_unrelated(tmp_path: Path):
    pkg1 = tmp_path / "core"
    pkg1.mkdir()
    (pkg1 / "runner.py").write_text("from utils.math import add\ndef execute(): pass\n", encoding="utf-8")
    
    pkg2 = tmp_path / "utils"
    pkg2.mkdir()
    (pkg2 / "math.py").write_text("def add(a, b): pass\n", encoding="utf-8")

    # Unrelated large package
    pkg3 = tmp_path / "legacy"
    pkg3.mkdir()
    for i in range(10):
        (pkg3 / f"old_{i}.py").write_text(f"def old_func_{i}(): pass\n", encoding="utf-8")

    # Tight budget with focus on core/runner.py
    result = build_codebase_map(root=tmp_path, budget_chars=120, focus_file="core/runner.py")
    assert "core/: " in result
    assert "execute" in result
    assert "add" in result
    # Legacy should be truncated or omitted under tight budget
    assert "old_func_9" not in result

def test_codemap_focus_missing_file_falls_back_to_unfocused(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("def func(): pass\n", encoding="utf-8")

    focused = build_codebase_map(root=tmp_path, budget_chars=400, focus_file="nope/missing.py")
    plain = build_codebase_map(root=tmp_path, budget_chars=400)
    assert focused == plain

def test_codemap_focus_keeps_more_symbols_from_the_focus_file(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "target.py").write_text(
        "".join(f"def target_func_{i}(): pass\n" for i in range(6)), encoding="utf-8"
    )
    for i in range(10):
        (pkg / f"other_{i}.py").write_text(f"def other_func_{i}(): pass\n", encoding="utf-8")

    result = build_codebase_map(root=tmp_path, budget_chars=400, focus_file="pkg/target.py")
    # Busy directories collapse to one symbol per file, except the focus file.
    assert "target_func_5" in result
