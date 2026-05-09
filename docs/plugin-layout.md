# Codex Plugin Layout

This repository ships as a Codex plugin, with marketplace-first distribution through `aiskyhub/aiskyhub`.

## Structure

- `.codex-plugin/plugin.json`: Codex plugin manifest and UI metadata.
- `skills/`: Shared plugin content root for the Codex plugin.
- `skills/codex-with-cc/`: The real workflow implementation, runtime scripts, and contract docs.

## Why the runtime stays under `skills/codex-with-cc/`

The delegated runtime and contract tests assume that `skills/codex-with-cc/` is the canonical workflow root. Keeping that directory stable avoids breaking:

- platform-specific packaging of `windows_scripts/` and `macos_scripts/`
- verification scripts and path-sensitive tests

## Installation paths

- Source layout: this repository exposes `.codex-plugin/plugin.json` so it can be recognized as a Codex plugin source.
- Distribution path: install `codex-with-cc` from the `aiskyhub/aiskyhub` marketplace for Codex.
- No script-based cross-project installer is provided by this repository anymore.
