#!/usr/bin/env python3
from pathlib import Path

repo = Path(__file__).resolve().parents[1]


def test_ai_install_doc_contract() -> None:
    text = (repo / "AI_INSTALL.md").read_text(encoding="utf-8")
    legacy_installer_stem = "_".join(("install", "codex", "with", "cc"))
    legacy_scope_phrase = "".join(("全局", " skill"))
    compat_phrase = "".join(("兼容", "路径"))

    assert "[marketplaces.aiskyhub]" in text
    assert '[plugins."codex-with-cc@aiskyhub"]' in text
    assert "如果宿主环境还没有安装 `codex` CLI，先自动安装官方 CLI，再继续后续步骤。" in text
    assert "### 1. 检查并安装 Codex CLI" in text
    assert "Get-Command codex -ErrorAction SilentlyContinue" in text
    assert "command -v codex" in text
    assert "npm i -g @openai/codex" in text
    assert "安装完成后再次确认 `codex` 命令可用，再继续 marketplace 安装流程。" in text
    assert "如果 `npm` 不存在、CLI 安装失败、或安装后仍然无法调用 `codex`，直接报告失败并停止，不要跳过这一步继续执行。" in text
    assert "codex plugin marketplace add aiskyhub/aiskyhub" in text
    assert "codex-with-cc@aiskyhub" in text
    assert "--scope user" in text
    assert "$codex-with-cc" in text
    assert "docs/codex_with_cc" in text
    assert "doc/codex_with_cc" in text
    assert ".codex/skills/codex-with-cc" in text
    assert "<!-- BEGIN CODEX_WITH_CC --> ... <!-- END CODEX_WITH_CC -->" in text
    assert "如果 `AGENTS.md` 删除托管块后变空，可以直接删除整个文件。" in text
    assert "$HOME/.codex/skills/codex-with-cc" in text
    assert "$env:USERPROFILE\\.codex\\skills\\codex-with-cc" in text
    assert "先清理项目下旧安装产物和用户级旧版 `codex-with-cc` skill" in text
    assert "Any user mention of child-agent, subagent, sub-agent, child-thread, subthread, delegation, worker-execution, or Chinese equivalents such as 子代理、子线程、多代理、委派、派工、执行层 is a workflow trigger." in text
    assert "codex_with_cc/scripts/delegate_to_claude.py" not in text
    assert "/plugin install codex-with-cc@aiskyhub --scope user" in text
    assert "当前仓库只提供 Codex 插件入口，不提供 Claude 宿主插件配置。" in text
    assert ".claude-plugin/marketplace.json" not in text
    assert "/plugin marketplace list" not in text
    assert "claude plugin marketplace list" not in text
    assert "/plugin marketplace add aiskyhub/aiskyhub" not in text
    assert "/reload-plugins" not in text
    assert "## 安装或更新完成后告知用户" in text
    assert "不要只说“好了”或“已完成”" in text
    assert f"{legacy_installer_stem}.ps1" not in text
    assert f"{legacy_installer_stem}.sh" not in text
    assert legacy_scope_phrase not in text
    assert compat_phrase not in text
    assert f"scripts/{legacy_installer_stem}.py" not in text
    assert "macOS 支持尚未实现" not in text
