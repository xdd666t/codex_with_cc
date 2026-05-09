#!/usr/bin/env python3
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
text = (repo / "AI_INSTALL.md").read_text(encoding="utf-8")
legacy_installer_stem = "_".join(("install", "codex", "with", "cc"))
legacy_scope_phrase = "".join(("全局", " skill"))
compat_phrase = "".join(("兼容", "路径"))

assert "[marketplaces.aiskyhub]" in text
assert '[plugins."codex-with-cc@aiskyhub"]' in text
assert "codex plugin marketplace add aiskyhub/aiskyhub" in text
assert "codex-with-cc@aiskyhub" in text
assert "--scope user" in text
assert "$codex-with-cc" in text
assert "Any user mention of child-agent, subagent, sub-agent, child-thread, subthread, delegation, worker-execution, or Chinese equivalents such as 子代理、子线程、多代理、委派、派工、执行层 is a workflow trigger." in text
assert "codex_with_cc/scripts/delegate_to_claude.py" not in text
assert "/plugin marketplace list" in text
assert "claude plugin marketplace list" in text
assert "/plugin marketplace add aiskyhub/aiskyhub" in text
assert "/plugin install codex-with-cc@aiskyhub --scope user" in text
assert "/reload-plugins" in text
assert f"{legacy_installer_stem}.ps1" not in text
assert f"{legacy_installer_stem}.sh" not in text
assert legacy_scope_phrase not in text
assert compat_phrase not in text
assert f"scripts/{legacy_installer_stem}.py" not in text
assert "macOS 支持尚未实现" not in text

print("ai install doc tests passed")
