"""The auto-map hook must only ever describe a real project.

It used to call build_codebase_map() on whatever Path.cwd() was, so inside a
host application every session was handed a map of that app's uv cache.
"""
import os
from pathlib import Path

import trimtok
from trimtok import _pre_llm_call, _project_root


def _make_project(path: Path, marker: str = ".git") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    target = path / marker
    if marker == ".git":
        target.mkdir()
    else:
        target.write_text("", encoding="utf-8")
    return path


def test_project_root_finds_the_marker(tmp_path: Path):
    root = _make_project(tmp_path / "repo")

    assert _project_root(root) == root.resolve()


def test_project_root_walks_up_from_a_subdirectory(tmp_path: Path):
    root = _make_project(tmp_path / "repo", "pyproject.toml")
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)

    assert _project_root(nested) == root.resolve()


def test_project_root_returns_none_without_markers(tmp_path: Path, monkeypatch):
    plain = tmp_path / "not-a-project"
    plain.mkdir()

    # The walk only stops at the filesystem root, so a temp dir nested inside a
    # real repo legitimately resolves to that repo. Force a marker-free chain.
    monkeypatch.setattr(trimtok, "_PROJECT_MARKERS", ("__no_such_marker__",))

    assert _project_root(plain) is None


def test_hook_skips_a_directory_that_is_not_a_project(tmp_path: Path, monkeypatch):
    plain = tmp_path / "host-app"
    plain.mkdir()
    monkeypatch.chdir(plain)
    monkeypatch.setattr(trimtok, "_project_root", lambda *a, **k: None)

    assert _pre_llm_call() is None


def test_hook_maps_the_project_it_runs_in(tmp_path: Path, monkeypatch):
    root = _make_project(tmp_path / "repo")
    (root / "mod.py").write_text("def handler(): pass\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.delenv("TRIMTOK_MAP", raising=False)

    result = _pre_llm_call()

    assert result and "Codebase map (" in result["context"]
    assert "handler" in result["context"]


def test_hook_injects_only_once_per_session(tmp_path: Path, monkeypatch):
    root = _make_project(tmp_path / "repo")
    (root / "mod.py").write_text("def handler(): pass\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.delenv("TRIMTOK_MAP", raising=False)

    history = [{"api_content": "Codebase map (1 directories):\n./: handler"}]

    assert _pre_llm_call(conversation_history=history) is None
