#!/usr/bin/env python3
from pathlib import Path
import json

repo = Path(__file__).resolve().parents[1]
codex_plugin_path = repo / ".codex-plugin" / "plugin.json"
claude_plugin_path = repo / ".claude-plugin" / "plugin.json"
skill_path = repo / "skills" / "codex-with-cc" / "SKILL.md"
readme_text = (repo / "README.md").read_text(encoding="utf-8")
ai_install_text = (repo / "AI_INSTALL.md").read_text(encoding="utf-8")
legacy_scope_phrase = "".join(("全局", " skill 安装"))
compat_install_phrase = "".join(("兼容", "安装"))

assert codex_plugin_path.exists()
assert claude_plugin_path.exists()
assert skill_path.exists()

codex_plugin = json.loads(codex_plugin_path.read_text(encoding="utf-8"))
claude_plugin = json.loads(claude_plugin_path.read_text(encoding="utf-8"))

assert codex_plugin["name"] == "codex-with-cc"
assert claude_plugin["name"] == "codex-with-cc"
assert codex_plugin["skills"] == "./skills/"
assert claude_plugin["skills"] == "./skills/"

codex_interface = codex_plugin["interface"]
assert codex_interface["displayName"] == "Codex With CC"
assert "Codex" in codex_interface["shortDescription"]
assert "Claude Code" in codex_interface["shortDescription"]
assert "aiskyhub" in codex_interface["longDescription"]
assert "marketplace" in codex_interface["longDescription"].lower()
assert "Read" in codex_interface["capabilities"]
assert "Write" in codex_interface["capabilities"]
assert any("aiskyhub/aiskyhub" in prompt for prompt in codex_interface["defaultPrompt"])
assert "marketplace" in claude_plugin["description"].lower()

assert "双平台插件" in readme_text
assert ".codex-plugin/plugin.json" in readme_text
assert ".claude-plugin/plugin.json" in readme_text
assert "插件优先" in readme_text
assert "aiskyhub/aiskyhub" in readme_text
assert "codex plugin marketplace add aiskyhub/aiskyhub" in readme_text
assert "/plugin install codex-with-cc@aiskyhub --scope user" in readme_text
assert compat_install_phrase not in readme_text

assert "marketplace-only" in ai_install_text
assert "不是仓库主身份" not in ai_install_text
assert legacy_scope_phrase not in ai_install_text

print("plugin manifest tests passed")
