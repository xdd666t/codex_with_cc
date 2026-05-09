#!/usr/bin/env python3
from pathlib import Path
import json
import re

repo = Path(__file__).resolve().parents[1]
skill = repo / "skills" / "codex-with-cc"
skill_md = skill / "SKILL.md"
openai_yaml = skill / "agents" / "openai.yaml"
codex_plugin = repo / ".codex-plugin" / "plugin.json"
legacy_installer_stem = "_".join(("install", "codex", "with", "cc"))

assert (skill / "CODEX_WITH_CC.md").exists()
assert (skill / "scripts" / "runtime.py").exists()
assert (skill / "scripts" / "delegate_to_claude.py").exists()
assert not (skill / "scripts" / f"{legacy_installer_stem}.py").exists()
assert not (skill / "scripts" / "codex_with_cc_runtime" / "installer.py").exists()
assert not (repo / f"{legacy_installer_stem}.ps1").exists()
assert not (repo / f"{legacy_installer_stem}.sh").exists()
assert not (repo / "tests" / f"test_{legacy_installer_stem}.py").exists()
assert (skill / "windows_scripts" / "delegate_to_claude.ps1").exists()
assert (skill / "macos_scripts" / "delegate_to_claude.sh").exists()
assert codex_plugin.exists()
assert not (repo / ".claude-plugin" / "plugin.json").exists()
assert "docs/codex_with_cc" not in (skill / "CODEX_WITH_CC.md").read_text(encoding="utf-8")

codex_manifest = json.loads(codex_plugin.read_text(encoding="utf-8"))
assert codex_manifest["name"] == "codex-with-cc"
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
    "current working directory",
    "gpt-5.3-codex",
    "fork_context: false",
    "Process Log",
    "Summary",
    "Changed Files",
    "Verification",
    "Final Result",
    "Risks Or Follow-ups",
):
    assert required in text
assert "installed globally under `$CODEX_HOME/skills/codex-with-cc`" not in text
assert "plugin-managed installation" in text

metadata = openai_yaml.read_text(encoding="utf-8")
assert 'display_name: "Codex With CC"' in metadata
assert 'default_prompt: "Use $codex-with-cc' in metadata
assert "allow_implicit_invocation: true" in metadata

print("codex_with_cc skill tests passed")
