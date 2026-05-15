#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / "skills" / "codex-with-cc"
SCRIPTS = WORKFLOW / "scripts"


def install_simulated_plugin(temp_root: Path) -> tuple[Path, Path]:
    codex_home = temp_root / ".codex"
    project_root = temp_root / "target-project"
    plugin_root = codex_home / "plugins" / "cache" / "aiskyhub" / "codex-with-cc" / "1.0.0" / "skills" / "codex-with-cc"
    project_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SCRIPTS, plugin_root / "scripts")
    shutil.copy2(WORKFLOW / "CODEX_WITH_CC.md", plugin_root / "CODEX_WITH_CC.md")
    return codex_home, project_root


def run_python(script: Path, *args: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        env=env,
    )


def test_marketplace_install_uses_project_cwd_for_default_paths() -> None:
    with tempfile.TemporaryDirectory(prefix="codex_with_cc_marketplace_paths_") as tmp:
        temp_root = Path(tmp)
        codex_home, project_root = install_simulated_plugin(temp_root)
        plugin_scripts = codex_home / "plugins" / "cache" / "aiskyhub" / "codex-with-cc" / "1.0.0" / "skills" / "codex-with-cc" / "scripts"
        env = {
            **os.environ,
            "CODEX_HOME": str(codex_home),
            "CODEX_CLAUDE_CHILD_THREAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(plugin_scripts),
        }

        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json; "
                    "from codex_with_cc_runtime.paths import repo_root, workflow_relative_path; "
                    "print(json.dumps({'repo_root': str(repo_root()), 'workflow_relative_path': workflow_relative_path()}))"
                ),
            ],
            cwd=str(project_root),
            text=True,
            capture_output=True,
            env=env,
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr
        payload = json.loads(probe.stdout)
        assert Path(payload["repo_root"]).resolve() == project_root.resolve()
        rel = payload["workflow_relative_path"]
        assert rel
        assert rel.endswith("/skills/codex-with-cc") or rel.endswith("\\skills\\codex-with-cc") or Path(rel).is_absolute()

        delegate = plugin_scripts / "delegate_to_claude.py"
        task_root = project_root / ".codex" / "codex_with_cc" / "tasks" / "install"
        task_root.mkdir(parents=True)
        task_file = task_root / "marketplace-install-dry-run.md"
        task_file.write_text("marketplace install dry run", encoding="utf-8")
        dry_run = run_python(
            delegate,
            "-TaskFile",
            str(task_file),
            "-WorkflowId",
            "wf-marketplace-paths",
            "-TaskId",
            "task-marketplace-paths",
            "-Role",
            "researcher",
            "-SessionKey",
            "marketplace-paths",
            "-DryRun",
            cwd=project_root,
            env=env,
        )
        assert dry_run.returncode == 0, dry_run.stdout + dry_run.stderr
        artifact_root = project_root / ".codex" / "codex_with_cc" / "claude-delegate"
        assert artifact_root.exists()
        assert list(artifact_root.glob("config_*.json"))
        assert list(artifact_root.glob("status_*.json"))

        real_chain = plugin_scripts / "run_real_delegate_chain_validation.py"
        chain_run = run_python(
            real_chain,
            "-Name",
            "marketplace-defaults",
            "-SessionKey",
            "marketplace-session",
            cwd=project_root,
            env=env,
        )
        assert chain_run.returncode == 0, chain_run.stdout + chain_run.stderr
        validation_root = project_root / ".codex" / "codex_with_cc" / "claude-delegate-validation" / "marketplace-defaults"
        assert validation_root.exists()
        assert list((validation_root / "tasks").rglob("*.md"))


def test_repo_root_is_execution_cwd_even_when_runtime_is_loaded_from_source() -> None:
    with tempfile.TemporaryDirectory(prefix="codex_with_cc_source_cwd_") as tmp:
        project_root = Path(tmp) / "target-project"
        project_root.mkdir()
        env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(SCRIPTS),
        }
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "from codex_with_cc_runtime.paths import repo_root; print(repo_root())",
            ],
            cwd=str(project_root),
            text=True,
            capture_output=True,
            env=env,
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr
        assert Path(probe.stdout.strip()).resolve() == project_root.resolve()
