from pathlib import Path
from trimtok.codemap import build_codebase_map

def test_codemap_extracts_symbols_within_budget(tmp_path: Path):
    # Setup dummy project
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("class Server:\n    pass\n\ndef run():\n    pass\n", encoding="utf-8")
    (src / "utils.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    
    # Tests folder should be ignored
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_app.py").write_text("def test_run():\n    pass\n", encoding="utf-8")

    result = build_codebase_map(root=tmp_path, budget_chars=500)
    assert "src/: " in result
    assert "Server" in result
    assert "run" in result
    assert "helper" in result
    assert "tests/" not in result
    assert len(result) <= 500

def test_codemap_budget_truncation(tmp_path: Path):
    src = tmp_path / "pkg"
    src.mkdir()
    for i in range(20):
        (src / f"mod_{i}.py").write_text(f"def func_{i}(): pass\n", encoding="utf-8")
    
    # Small budget
    result = build_codebase_map(root=tmp_path, budget_chars=120)
    assert len(result) <= 120
    assert "pkg/: " in result
