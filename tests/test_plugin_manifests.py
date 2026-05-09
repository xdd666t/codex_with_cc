#!/usr/bin/env python3
from pathlib import Path
import json


def test_plugin_manifest_and_docs_contract() -> None:
    repo = Path(__file__).resolve().parents[1]
    codex_plugin_path = repo / ".codex-plugin" / "plugin.json"
    skill_path = repo / "skills" / "codex-with-cc" / "SKILL.md"
    readme_text = (repo / "README.md").read_text(encoding="utf-8")
    ai_install_text = (repo / "AI_INSTALL.md").read_text(encoding="utf-8")
    legacy_scope_phrase = "".join(("全局", " skill 安装"))
    compat_install_phrase = "".join(("兼容", "安装"))

    assert codex_plugin_path.exists()
    assert not (repo / ".claude-plugin" / "plugin.json").exists()
    assert skill_path.exists()

    codex_plugin = json.loads(codex_plugin_path.read_text(encoding="utf-8"))
    assert codex_plugin["name"] == "codex-with-cc"
    assert codex_plugin["version"] == "1.0.1"
    assert codex_plugin["skills"] == "./skills/"

    codex_interface = codex_plugin["interface"]
    assert codex_interface["displayName"] == "Codex With CC"
    assert "Codex" in codex_interface["shortDescription"]
    assert "Claude Code" in codex_interface["shortDescription"]
    assert "aiskyhub" in codex_interface["longDescription"]
    assert "marketplace" in codex_interface["longDescription"].lower()
    assert "Read" in codex_interface["capabilities"]
    assert "Write" in codex_interface["capabilities"]
    assert any("aiskyhub/aiskyhub" in prompt for prompt in codex_interface["defaultPrompt"])

    assert "[AI_INSTALL.md](AI_INSTALL.md)" in readme_text
    assert "https://github.com/aiskyhub/codex_with_cc" in readme_text
    assert "安装或更新" in readme_text
    assert "当前 Codex 环境" in readme_text
    assert compat_install_phrase not in readme_text

    assert "marketplace-only" in ai_install_text
    assert "不是仓库主身份" not in ai_install_text
    assert legacy_scope_phrase not in ai_install_text
    assert "当前仓库只提供 Codex 插件入口，不提供 Claude 宿主插件配置。" in ai_install_text
