#!/usr/bin/env python3
from pathlib import Path
import json
import re

repo = Path(__file__).resolve().parents[1]
skill = repo / "skills" / "codex-with-cc"
workflow_skill_names = (
    "codex-with-cc-planning",
    "codex-with-cc-dispatching",
    "codex-with-cc-worker",
    "codex-with-cc-reviewing",
    "codex-with-cc-finishing",
)
skill_md = skill / "SKILL.md"
openai_yaml = skill / "agents" / "openai.yaml"
codex_plugin = repo / ".codex-plugin" / "plugin.json"
legacy_installer_stem = "_".join(("install", "codex", "with", "cc"))


def test_codex_with_cc_skill_contract() -> None:
    assert (skill / "CODEX_WITH_CC.md").exists()
    assert (skill / "scripts" / "runtime.py").exists()
    assert (skill / "scripts" / "delegate_to_claude.py").exists()
    assert (skill / "scripts" / "validate_delegate_task.py").exists()
    assert not (skill / "scripts" / "check_subagent_gate.py").exists()
    assert not (skill / "scripts" / "codex_with_cc_runtime" / "subagent_gate.py").exists()
    assert not (skill / "scripts" / f"{legacy_installer_stem}.py").exists()
    assert not (skill / "scripts" / "codex_with_cc_runtime" / "installer.py").exists()
    assert not (repo / f"{legacy_installer_stem}.ps1").exists()
    assert not (repo / f"{legacy_installer_stem}.sh").exists()
    assert not (repo / "tests" / f"test_{legacy_installer_stem}.py").exists()
    assert (skill / "windows_scripts" / "delegate_to_claude.ps1").exists()
    assert (skill / "windows_scripts" / "validate_delegate_task.ps1").exists()
    assert (skill / "macos_scripts" / "delegate_to_claude.sh").exists()
    assert (skill / "macos_scripts" / "validate_delegate_task.sh").exists()
    assert codex_plugin.exists()
    assert not (repo / ".claude-plugin" / "plugin.json").exists()
    assert "docs/codex_with_cc" not in (skill / "CODEX_WITH_CC.md").read_text(encoding="utf-8")
    assert "<installed-workflow-root>" in (skill / "CODEX_WITH_CC.md").read_text(encoding="utf-8")
    assert "<installed codex-with-cc plugin root>" not in (skill / "CODEX_WITH_CC.md").read_text(encoding="utf-8")

    codex_manifest = json.loads(codex_plugin.read_text(encoding="utf-8"))
    assert codex_manifest["name"] == "codex-with-cc"
    assert codex_manifest["version"] == "1.0.3"
    assert codex_manifest["skills"] == "./skills/"
    assert "aiskyhub" in codex_manifest["interface"]["longDescription"]
    assert any("aiskyhub/aiskyhub" in prompt for prompt in codex_manifest["interface"]["defaultPrompt"])

    text = skill_md.read_text(encoding="utf-8")
    frontmatter = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    assert frontmatter, "SKILL.md must start with YAML frontmatter"
    frontmatter_text = frontmatter.group(1)

    assert "name: codex-with-cc" in frontmatter_text
    description_match = re.search(r"^description:\s*(.+)$", frontmatter_text, re.MULTILINE)
    assert description_match, "description is required"
    description = description_match.group(1)
    assert len(description) <= 1024
    for trigger in (
        "child-agent",
        "subagent",
        "sub-agent",
        "child-thread",
        "subthread",
        "delegation",
        "worker-execution",
        "子代理",
        "子线程",
        "多代理",
        "委派",
        "派工",
        "执行层",
    ):
        assert trigger in description

    for required in (
        "spawn_agent",
        "CODEX_CLAUDE_CHILD_THREAD=1",
        "delegate_to_claude",
        "WorkflowId",
        "TaskId",
        "Role",
        "current working directory",
        "gpt-5.3-codex",
        "fork_context: false",
        "Status",
        "Role",
        "Summary",
        "Changed Files",
        "Verification",
        "Findings",
        "Final Result",
        "Risks Or Follow-ups",
        "Operating Method",
        "spec compliance",
        "workflow-level verification",
        "validate_delegate_task",
        "final-verifier",
    ):
        assert required in text
    assert "check_subagent_gate" not in text
    assert "installed globally under `$CODEX_HOME/skills/codex-with-cc`" not in text
    assert "plugin-managed installation" in text

    metadata = openai_yaml.read_text(encoding="utf-8")
    assert 'display_name: "Codex With CC"' in metadata
    assert 'default_prompt: "Use $codex-with-cc' in metadata
    assert "allow_implicit_invocation: true" in metadata

    contract = (skill / "CODEX_WITH_CC.md").read_text(encoding="utf-8")
    assert "workflow/task/run" in contract
    assert "Workflow Method" in contract
    assert "Review in two passes" in contract
    assert "Status and Final Result must match" in contract
    assert "planner" in contract
    assert "implementer" in contract
    assert "researcher" in contract
    assert "reviewer" in contract
    assert "final-verifier" in contract
    assert "workflow_<WorkflowId>.json" in contract
    assert "validate_delegate_task" in contract
    assert "final-verifier gate" in contract

    for skill_name in workflow_skill_names:
        skill_file = repo / "skills" / skill_name / "SKILL.md"
        assert skill_file.exists()
        skill_text = skill_file.read_text(encoding="utf-8")
        assert "codex-with-cc" in skill_text
        assert "CODEX_WITH_CC.md" in skill_text


def test_workflow_docs_do_not_expose_version_branding() -> None:
    forbidden = ("v" + "2", "V" + "2", "v" + "3", "V" + "3", "codex_with_cc_" + "v" + "2")
    scanned: list[Path] = []
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo)
        if rel.parts[0] in {".git", "build", ".pytest_cache", "__pycache__"}:
            continue
        if any(part == "__pycache__" for part in rel.parts):
            continue
        scanned.append(rel)
        haystack = rel.as_posix()
        if path.suffix.lower() in {".md", ".py", ".js", ".json", ".yaml", ".yml", ".ps1", ".sh"}:
            haystack += "\n" + path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in haystack, f"unexpected version branding token {token!r} in {rel}"
