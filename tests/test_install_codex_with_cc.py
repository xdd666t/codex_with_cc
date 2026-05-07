#!/usr/bin/env python3
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
installer = repo / "codex_with_cc" / "scripts" / "install_codex_with_cc.py"


def run_install(target: Path, platform: str, *extra: str) -> str:
    result = subprocess.run(
        [sys.executable, str(installer), "--target-root", str(target), "--platform", platform, *extra],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout + result.stderr


def run_install_result(target: Path, platform: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(installer), "--target-root", str(target), "--platform", platform, *extra],
        cwd=repo,
        text=True,
        capture_output=True,
    )


with tempfile.TemporaryDirectory(prefix="codex_with_cc_install_") as tmp:
    root = Path(tmp)
    target = root / "host-project"
    target.mkdir()
    (target / "README.md").write_text("# Host Project\n", encoding="utf-8")
    (target / ".gitignore").write_text("build\n.claude\n", encoding="utf-8")
    (target / "AGENTS.md").write_text("# Existing Host Instructions\n\nKeep this project-specific rule.\n", encoding="utf-8")

    out = run_install(target, "Windows")
    workflow = target / "docs" / "codex_with_cc"
    task_root = target / ".codex" / "codex_with_cc" / "tasks"
    assert (workflow / "CODEX_WITH_CC.md").exists()
    assert (workflow / "scripts" / "delegate_to_claude.py").exists()
    assert (workflow / "scripts" / "runtime.py").exists()
    assert (workflow / "tests" / "test_delegate_runtime.py").exists()
    assert (workflow / "tests" / "windows_scripts" / "test_delegate_runtime.ps1").exists()
    assert not (workflow / "tests" / "macos_scripts").exists()
    assert (workflow / "windows_scripts" / "delegate_to_claude.ps1").exists()
    assert not (workflow / "macos_scripts").exists()
    assert task_root.exists()
    assert not (task_root / ".gitkeep").exists()
    assert ".codex/codex_with_cc" in (target / ".gitignore").read_text(encoding="utf-8")
    assert ".codex\n" not in (target / ".gitignore").read_text(encoding="utf-8")
    agents = (target / "AGENTS.md").read_text(encoding="utf-8")
    assert "Keep this project-specific rule." in agents
    assert "<!-- BEGIN CODEX_WITH_CC -->" in agents
    assert "docs/codex_with_cc/CODEX_WITH_CC.md" in agents
    assert "`docs/codex_with_cc/CODEX_WITH_CC.md`" in agents
    assert "Codex main thread -> Codex child agent -> delegate_to_claude.* -> Claude Code CLI" in agents
    assert "Agent entrypoints updated: AGENTS.md" in out

    (workflow / "obsolete.txt").write_text("stale", encoding="utf-8")
    (workflow / "HOST_PROJECT_RULES.md").write_text("stale host rules", encoding="utf-8")
    (workflow / "PROJECT_MEMORY.md").write_text("stale project memory", encoding="utf-8")
    stale_doc_workflow = target / "doc" / "codex_with_cc"
    stale_doc_workflow.mkdir(parents=True)
    (stale_doc_workflow / "obsolete.txt").write_text("stale doc workflow", encoding="utf-8")
    (target / "doc" / "keep.md").write_text("keep", encoding="utf-8")
    (task_root / ".gitkeep").write_text("", encoding="utf-8")
    run_install(target, "Windows")
    agents_after_reinstall = (target / "AGENTS.md").read_text(encoding="utf-8")
    assert not (workflow / "obsolete.txt").exists()
    assert not (workflow / "HOST_PROJECT_RULES.md").exists()
    assert not (workflow / "PROJECT_MEMORY.md").exists()
    assert not stale_doc_workflow.exists()
    assert (target / "doc" / "keep.md").exists()
    assert not (task_root / ".gitkeep").exists()
    assert agents_after_reinstall.count("<!-- BEGIN CODEX_WITH_CC -->") == 1

    doc_only = root / "doc-only-host-project"
    (doc_only / "doc").mkdir(parents=True)
    doc_only_out = run_install(doc_only, "Windows")
    assert (doc_only / "doc" / "codex_with_cc" / "CODEX_WITH_CC.md").exists()
    assert not (doc_only / "docs").exists()
    assert "Next: read doc/codex_with_cc/CODEX_WITH_CC.md" in doc_only_out

    both_docs = root / "both-docs-host-project"
    (both_docs / "doc").mkdir(parents=True)
    (both_docs / "docs").mkdir(parents=True)
    run_install(both_docs, "Windows", "--skip-agent-entrypoints")
    assert (both_docs / "docs" / "codex_with_cc" / "CODEX_WITH_CC.md").exists()
    assert not (both_docs / "doc" / "codex_with_cc").exists()

    docs_file = root / "docs-file-host-project"
    docs_file.mkdir()
    (docs_file / "docs").write_text("not a directory", encoding="utf-8")
    docs_file_result = run_install_result(docs_file, "Windows", "--skip-agent-entrypoints")
    assert docs_file_result.returncode != 0
    assert "Install document path is not a directory" in (docs_file_result.stdout + docs_file_result.stderr)

    mac_target = root / "mac-host-project"
    (mac_target / "doc").mkdir(parents=True)
    run_install(mac_target, "macOS", "--skip-agent-entrypoints")
    mac_workflow = mac_target / "doc" / "codex_with_cc"
    assert (mac_workflow / "scripts" / "delegate_to_claude.py").exists()
    assert (mac_workflow / "scripts" / "runtime.py").exists()
    assert (mac_workflow / "tests" / "test_delegate_runtime.py").exists()
    assert (mac_workflow / "tests" / "macos_scripts" / "test_delegate_runtime.sh").exists()
    assert not (mac_workflow / "tests" / "windows_scripts").exists()
    assert (mac_workflow / "macos_scripts" / "_runtime.sh").exists()
    assert (mac_workflow / "macos_scripts" / "delegate_to_claude.sh").exists()
    assert not (mac_workflow / "macos_scripts" / "README.md").exists()
    assert not (mac_workflow / "windows_scripts").exists()

    self_source = root / "self-source"
    shutil.copytree(repo / "codex_with_cc", self_source / "codex_with_cc")
    result = subprocess.run(
        [
            sys.executable,
            str(self_source / "codex_with_cc" / "scripts" / "install_codex_with_cc.py"),
            "--target-root",
            str(self_source),
            "--platform",
            "macOS",
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "Refusing to install codex_with_cc into its own source repository" in (result.stdout + result.stderr)
    assert (self_source / "codex_with_cc" / "CODEX_WITH_CC.md").exists()
    assert (self_source / "codex_with_cc" / "scripts" / "runtime.py").exists()

print("install tests passed")
