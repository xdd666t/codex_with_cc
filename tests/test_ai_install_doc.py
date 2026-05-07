#!/usr/bin/env python3
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
text = (repo / "AI_INSTALL.md").read_text(encoding="utf-8")

assert "<workflow-root>/<platform_scripts>/delegate_to_claude.* -> scripts/*.py -> Claude Code CLI" in text
assert "codex_with_cc/scripts/delegate_to_claude.py" in text
assert "<workflow-root>\\tests\\windows_scripts\\test_delegate_runtime.ps1" in text
assert "<workflow-root>/tests/macos_scripts/test_delegate_runtime.sh" in text
assert "Windows 目标项目不要安装 `macos_scripts`；macOS 目标项目不要安装 `windows_scripts`。两个平台都必须安装共享的 `scripts/*.py`。" in text
assert "macOS 支持尚未实现" not in text

print("ai install doc tests passed")
