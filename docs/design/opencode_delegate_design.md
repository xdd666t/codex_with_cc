# Design: Codex → OpenCode Delegate

## 1. Overview

This document proposes adding OpenCode as a supported executor alongside Claude Code in the `codex-with-cc` workflow. Currently, the workflow chain is:

```
Codex main thread → Codex spawn_agent child thread → delegate_to_claude.* → Claude Code CLI
```

The goal is to add a parallel chain:

```
Codex main thread → Codex spawn_agent child thread → delegate_to_opencode.* → OpenCode CLI
```

Codex would choose whether to dispatch to Claude Code or OpenCode based on task characteristics, model availability, or explicit user preference.

## 2. Current Architecture (Claude Code Path)

### 2.1 File Layout

| Layer | Path | Purpose |
|-------|------|---------|
| Entry scripts | `{macos,windows}_scripts/delegate_to_claude.{sh,ps1}` | 2-line wrappers → `_runtime.sh delegate_to_claude.py` |
| Python entry | `scripts/delegate_to_claude.py` | Thin: `main(["delegate", *args])` |
| Runtime brain | `scripts/runtime.py` (~2489 lines) | Shared logic: session pool, file locking, stream parsing, retry, report validation |

### 2.2 Key Constants in runtime.py

```python
CHILD_MARKER_NAME = "CODEX_CLAUDE_CHILD_THREAD"
CHILD_MARKER_VALUE = "1"
SKILL_NAME = "codex-with-cc"
```

### 2.3 Claude-Specific Code Points

All Claude-specific code lives in `scripts/runtime.py`. These are the pieces that need variants:

| Code Point | Lines | What It Does | OpenCode Equivalent |
|------------|-------|-------------|---------------------|
| `CHILD_MARKER_NAME` | 24 | Guards entry: `CODEX_CLAUDE_CHILD_THREAD=1` | Need `CODEX_OPENCODE_CHILD_THREAD=1` |
| `new_claude_cli_args()` | 786-813 | Builds CLI arguments for claude binary | `new_opencode_cli_args()` |
| `update_stream_capture()` | 734-783 | Parses Claude's `stream-json` output | `update_opencode_stream_capture()` |
| `retry_decision()` | 828-866 | Detects Claude-specific errors (stale session, stream-json) | OpenCode-specific error patterns |
| `build_prompt()` | 885-964 | Builds delegation prompt mentioning "Claude Code" | OpenCode-flavored variant |
| `run_delegate()` | 1046-1486 | Core delegation: finds binary, invokes subprocess | Uses `shutil.which("opencode")` |
| `delegate_to_claude.py` | 3-line | Entry: `main(["delegate", ...])` | `delegate_to_opencode.py` |
| Artifact naming | - | `claude_*.md`, `claude-delegate/` dir | `opencode_*.md`, `opencode-delegate/` dir |
| `verify_artifacts()` | 1489 | Default artifact root includes `claude-delegate` | Respective root per executor |

### 2.4 Current Subcommand Routing

`runtime.main()` uses argparse with a single `delegate` subcommand → `run_delegate()`. This needs to become executor-aware.

## 3. OpenCode CLI Interface Analysis

### 3.1 `opencode run` — Programmatic Execution

```
opencode run [message] [options]
```

Key flags relevant to delegation:

| Flag | Purpose | Claude Equivalent |
|------|---------|-------------------|
| `--format json` | Line-delimited JSON output | `--output-format stream-json` |
| `--model provider/model` | Model selection | `--model <name>` |
| `--session <id>` | Continue specific session | `--session-id <id>` / `--resume` |
| `--continue` | Continue last session | no direct default-continue equivalent |
| `--dangerously-skip-permissions` | Auto-approve permissions | same |
| `--variant` | Reasoning effort (high/max/minimal) | implicit from model |
| `--title` | Session title | `--name` |
| `--thinking` | Show thinking blocks | `--verbose` (partially) |
| `--dir` | Working directory (for remote) | N/A (cwd is always set) |

### 3.2 OpenCode JSON Output Format

OpenCode's `--format json` produces line-delimited JSON events. Sample event types observed:

```json
{"type":"step_start", ...}
{"type":"text", "part":{"type":"text","text":"hello world",...}}
{"type":"tool_use", "part":{"type":"tool","tool":"bash",...}}
{"type":"step_finish", ...}
```

Key observations:
- `step_start` / `step_finish` bracket each reasoning step
- `text` events carry assistant text output (analogous to Claude's `assistant` + `message.content[].text`)
- `tool_use` events carry tool invocations (analogous to some Claude stream events, but different structure)
- No direct equivalent of Claude's `result` event with `subtype: success`; instead, successful completion is indicated by the process exiting with code 0
- `step_finish` contains token usage and cost info (analogous to Claude's `result` event with cost details)
- There is **no** `system` event type; status messages come through different means

### 3.3 Session Management

```
opencode session list                  # List sessions
opencode session delete <sessionID>    # Delete a session
```

OpenCode sessions are identified by IDs like `ses_1f9cbf99fffeS4hvA5M5tjydLq`. The `--session` flag on `opencode run` resumes a specific session. The `--fork` flag creates a fork of the session (like a git branch).

OpenCode has no direct `--session-id` flag for creating new sessions with a known ID. New sessions are created when running without `--session` or `--continue`, and the session ID is discovered from the JSON output stream (in the `step_start` event).

### 3.4 Binary Detection

`shutil.which("opencode")` — works exactly the same as detecting Claude Code.

## 4. Key Differences & Design Implications

### 4.1 Output Stream Parsing

| Aspect | Claude Code | OpenCode |
|--------|-------------|----------|
| JSON format | `stream-json` | `--format json` |
| Success signal | `result` event with `subtype: success` | Process exits 0 (no explicit success event) |
| Assistant text | `assistant` → `message.content[].text` | `text` → `part.text` |
| Error patterns | `"No conversation found.*session ID"`, `"stream-json.*requires.*--verbose"` | Need to discover OpenCode error patterns |
| Streaming | Continuous output during execution | Same (line-delimited JSON) |

**Implication**: `update_stream_capture()` needs a variant. Success detection is simpler for OpenCode (just check exit code), but we lose the structured success signal that Claude provides. We should check for assistant text + exit code 0 instead.

### 4.2 Session Lifecycle

| Aspect | Claude Code | OpenCode |
|--------|-------------|----------|
| New session creation | `--session-id <new-id>` | implicit (no flag) |
| Session resume | `--resume <id>` | `--session <id>` |
| Session ID discovery | CLI arg | From JSON stream (`step_start.sessionID`) |
| Session fork | Not supported | `--fork` |
| Session listing | External tool | `opencode session list` |

**Implication**: The session pool approach (acquire lease → pass session ID → release) works similarly but the argument mapping differs. For a new session, OpenCode creates the session internally; we discover the ID from the first `step_start` event. For resume, we pass `--session <id>`.

This means the session pool must store *discovered* session IDs (from the output stream) rather than *pre-assigned* ones (from CLI arguments). On the first run for a session key, we omit `--session`. On subsequent runs, we pass `--session <stored_id>`.

**Alternative approach**: Pre-generate session IDs. But OpenCode doesn't accept pre-assigned IDs. So discovery is required.

### 4.3 CLI Argument Mapping

```
Claude:                            OpenCode:
--verbose                          (no exact equivalent; --print-logs for stderr)
--print                            (no equivalent; implicit in --format json)
--output-format stream-json        --format json
--model <name>                     --model provider/model
--name <name>                      --title <title>
--session-id <id>                  (no flag for new session, auto-assigned)
--resume <id>                      --session <id>
--permission-mode acceptEdits      (OpenCode permissions handled per-tool)
--dangerously-skip-permissions     --dangerously-skip-permissions
--max-budget-usd <n>               (no direct budget flag)
<prompt_text>                      <prompt_text> (positional)
```

### 4.4 Retry Logic Differences

| Condition | Claude Detection | OpenCode Detection |
|-----------|-----------------|--------------------|
| Stale session | `"No conversation found.*session ID"` in non-JSON lines | TBD — observe OpenCode's behavior when session is deleted/missing |
| Stream error | `"stream-json.*requires.*--verbose"` | TBD — observe OpenCode startup errors |
| Unstructured success | Exit 0 + saw text but no report headings | Same pattern applies |

**Implication**: `retry_decision()` needs an executor-aware variant. In the first implementation, we can start with a simpler retry model for OpenCode (just unstructured success detection), and add stale/stream error detection as we observe OpenCode's failure patterns.

## 5. Proposed Architecture

### 5.1 Design Principle: Executor Strategy Pattern

Rather than forking `run_delegate()` entirely, introduce executor-specific strategy objects that encapsulate:
- CLI argument building
- Stream output parsing
- Retry decision logic
- Prompt construction (less critical, mostly shared)

The session pool, file locking, artifact writing, report validation — all remain shared.

### 5.2 Subcommand Design

Add a new top-level subcommand to `runtime.py`:

```
runtime.py opencode delegate_task  # New: dispatches to OpenCode
runtime.py delegate               # Existing: dispatches to Claude Code (unchanged)
```

The `opencode` subcommand has its own argument group mirroring the `delegate` args but with OpenCode-specific additions (e.g., `-Variant`).

### 5.3 File Layout Changes

```
skills/codex-with-cc/
├── agents/
│   └── openai.yaml                    # [MODIFIED] Add OpenCode trigger words
├── CODEX_WITH_CC.md                   # [MODIFIED] Add OpenCode chain documentation
├── SKILL.md                           # [MODIFIED] Add OpenCode executor docs
├── scripts/
│   ├── runtime.py                     # [MODIFIED] Add OpenCode strategy + opencode subcommand
│   ├── delegate_to_claude.py          # [UNCHANGED]
│   ├── delegate_to_opencode.py        # [NEW] Thin: main(["opencode", "delegate_task", *args])
│   └── ... (other scripts unchanged)
├── macos_scripts/
│   ├── delegate_to_claude.sh          # [UNCHANGED]
│   ├── delegate_to_opencode.sh        # [NEW] 2-line wrapper
│   └── ... (others unchanged)
└── windows_scripts/
    ├── delegate_to_claude.ps1         # [UNCHANGED]
    ├── delegate_to_opencode.ps1       # [NEW] 2-line wrapper
    └── ... (others unchanged)
```

### 5.4 Data Model Changes

**Constants** (add to runtime.py):

```python
OPENCODE_CHILD_MARKER_NAME = "CODEX_OPENCODE_CHILD_THREAD"
OPENCODE_CHILD_MARKER_VALUE = "1"

DEFAULT_OPENCODE_ARTIFACT_DIR = "opencode-delegate"
DEFAULT_CLAUDE_ARTIFACT_DIR = "claude-delegate"
```

**Artifact paths** by executor:

| Executor | Artifact Root | Output Pattern | Config | Status | Stream | Trace |
|----------|-------------|----------------|--------|--------|--------|-------|
| Claude | `.../claude-delegate/` | `claude_<RunId>.md` | `config_<RunId>.json` | `status_<RunId>.json` | `stream_<RunId>.jsonl` | `trace_<RunId>.log` |
| OpenCode | `.../opencode-delegate/` | `opencode_<RunId>.md` | `config_<RunId>.json` | `status_<RunId>.json` | `stream_<RunId>.jsonl` | `trace_<RunId>.log` |

**Session pool**: Reuse the same pool directory (`session-pools/`) or separate pools per executor. **Recommendation**: Reuse the same pool since session keys are already namespaced by task; there's no conflict. The pool already stores `sessionId` values; Claude and OpenCode session IDs are distinguishable by format (OpenCode uses `ses_*` prefix).

### 5.5 Runtime Module Restructuring

Introduce executor strategy functions in `runtime.py`:

```python
# New: OpenCode-specific helpers

def new_opencode_cli_args(
    model: str,
    title: str,
    session_id: str | None,    # None = new session, str = resume
    bypass_permissions: bool,
    variant: str | None,
    prompt_text: str,
) -> list[str]:
    """Build OpenCode CLI arguments."""
    args = [
        "--format", "json",
        "--dangerously-skip-permissions",
    ]
    if model:
        args.extend(["--model", model])
    if title:
        args.extend(["--title", title])
    if session_id:
        args.extend(["--session", session_id])
    if variant:
        args.extend(["--variant", variant])
    args.append(prompt_text)
    return args

def update_opencode_stream_capture(
    record: dict[str, Any],
    state: dict[str, Any],
) -> list[str]:
    """Parse OpenCode JSON output records."""
    state.setdefault("assistantTexts", [])
    state.setdefault("traceLines", [])
    state.setdefault("finalText", "")
    state.setdefault("sawAssistantText", False)
    state.setdefault("sawStepFinish", False)
    state.setdefault("capturedFinalResultHeading", False)
    state.setdefault("sessionId", None)

    trace_lines: list[str] = []
    record_type = str(record.get("type", ""))

    if record_type == "step_start":
        session_id = record.get("sessionID", "")
        state["sessionId"] = session_id
        trace_lines.append(f"[step_start] session={session_id}")

    elif record_type == "step_finish":
        state["sawStepFinish"] = True
        reason = record.get("part", {}).get("reason", "")
        tokens = record.get("part", {}).get("tokens", {})
        cost = record.get("part", {}).get("cost")
        parts = [f"[step_finish] reason={reason}"]
        if tokens:
            parts.append(f"tokens={tokens}")
        if cost is not None:
            parts.append(f"cost={cost}")
        trace_lines.append(" ".join(parts))

    elif record_type == "text":
        text = record.get("part", {}).get("text", "").strip()
        if text:
            state["sawAssistantText"] = True
            if text_has_required_report_headings(text):
                state["capturedFinalResultHeading"] = True
            state["assistantTexts"].append(text)
            state["finalText"] = text
        trace_lines.append("[text]")

    elif record_type == "tool_use":
        tool_name = record.get("part", {}).get("tool", "")
        status = record.get("part", {}).get("state", {}).get("status", "")
        trace_lines.append(f"[tool_use] tool={tool_name} status={status}")

    elif record_type:
        trace_lines.append(f"[{record_type}]")
    else:
        trace_lines.append("[unknown-record]")

    state["traceLines"].extend(trace_lines)
    return trace_lines

def retry_decision_opencode(
    raw_lines: Iterable[str],
    saw_assistant_text: bool,
    saw_step_finish: bool,
    captured_final_result_heading: bool,
    exit_code: int,
) -> dict[str, Any]:
    """OpenCode-specific retry decision."""
    joined = "\n".join(non_json_raw_lines(raw_lines))

    has_structured_success = (
        saw_step_finish
        and captured_final_result_heading
        and exit_code == 0
    )

    decision = {
        "shouldRetry": False,
        "retryReason": "",
        "retryWithFreshSession": False,
        "hasStructuredSuccess": has_structured_success,
        "exitCode": exit_code,
        "sawAssistantText": saw_assistant_text,
        "sawStepFinish": saw_step_finish,
        "capturedFinalResultHeading": captured_final_result_heading,
        "retryWithReportRepair": False,
    }

    # OpenCode unstructured success detection
    if exit_code == 0 and saw_step_finish and saw_assistant_text and not has_structured_success:
        decision.update({
            "shouldRetry": True,
            "retryReason": "unstructured_success_report",
            "retryWithFreshSession": False,
            "retryWithReportRepair": True,
        })

    return decision
```

### 5.6 Delegate Function Structure

The OpenCode delegate function (`run_opencode_delegate()`) follows exactly the same structure as `run_delegate()` but with:

1. **Marker check**: Uses `OPENCODE_CHILD_MARKER_NAME` instead of `CHILD_MARKER_NAME`
2. **Artifact root**: Defaults to `.../opencode-delegate/` instead of `.../claude-delegate/`
3. **Output file prefix**: `opencode_<RunId>.md` instead of `claude_<RunId>.md`
4. **Binary detection**: `shutil.which("opencode")` instead of `shutil.which("claude")`
5. **CLI args**: Uses `new_opencode_cli_args()` instead of `new_claude_cli_args()`
6. **Stream parsing**: Uses `update_opencode_stream_capture()` instead of `update_stream_capture()`
7. **Retry logic**: Uses `retry_decision_opencode()` instead of `retry_decision()`
8. **Session lease**: Slightly adapted — on first run for a key, `sessionId=None` (new session); on resume, passes `--session <stored_id>`. The discovered session ID is saved back to the pool after the first run.
9. **Prompt**: Uses OpenCode-specific wording (e.g., "OpenCode worker" instead of "Claude worker")

**Code reuse strategy**: To avoid excessive duplication, extract the shared parts of `run_delegate()` into parameterized helper functions. The executor-specific differences are captured in a `DelegateExecutor` dataclass or a set of callbacks:

```python
@dataclasses.dataclass
class DelegateConfig:
    """Executor-specific configuration for a delegate run."""
    executor_name: str                    # "claude" or "opencode"
    child_marker_name: str                # "CODEX_CLAUDE_CHILD_THREAD" or "CODEX_OPENCODE_CHILD_THREAD"
    child_marker_value: str               # always "1"
    artifact_dir_name: str                # "claude-delegate" or "opencode-delegate"
    output_prefix: str                    # "claude_" or "opencode_"
    binary_name: str                      # "claude" or "opencode"
    build_cli_args: Callable[..., list[str]]   # new_claude_cli_args() or new_opencode_cli_args()
    parse_stream_record: Callable[..., list[str]]  # update_stream_capture() or update_opencode_stream_capture()
    make_retry_decision: Callable[..., dict[str, Any]]  # retry_decision() or retry_decision_opencode()
    build_prompt: Callable[..., str]       # build_prompt() or build_opencode_prompt()
```

Then `run_delegate()` becomes a generic `run_delegate_with_config(cfg: DelegateConfig, ns)`.

**Decision**: Use the `DelegateConfig` dataclass approach. Keep `run_delegate()` as-is for backward compatibility, add `run_delegate_with_config()` as the shared implementation, and make `run_delegate()` and the new `run_opencode_delegate()` both thin wrappers that construct `DelegateConfig` and call the shared function. This minimizes risk of breaking the existing Claude Code path.

### 5.7 Prompt Modifications

The OpenCode prompt should reference "OpenCode" instead of "Claude Code" / "Claude" and reference `delegate_to_opencode.*` instead of `delegate_to_claude.*`:

```python
def build_opencode_prompt(
    repo: Path,
    output_path: Path,
    mode: str,
    scope: list[str],
    tests: list[str],
    task_text: str,
) -> str:
    # Same structure as build_prompt() but with:
    # - "You are OpenCode acting as an implementation worker for Codex."
    # - reference delegate_to_opencode.* instead of delegate_to_claude.*
    # - same report heading contract
```

## 6. Entry Points

### 6.1 Python Entry (`scripts/delegate_to_opencode.py`)

```python
#!/usr/bin/env python3
import sys
from runtime import main

if __name__ == "__main__":
    raise SystemExit(main(["opencode", "delegate_task", *sys.argv[1:]]))
```

### 6.2 Shell Wrappers

**macOS** (`macos_scripts/delegate_to_opencode.sh`):
```bash
#!/bin/zsh
"${0:A:h}/_runtime.sh" delegate_to_opencode.py "$@"
```

**Windows** (`windows_scripts/delegate_to_opencode.ps1`):
```powershell
. (Join-Path $PSScriptRoot '_runtime.ps1')
Invoke-CodexWithCcRuntime -PythonScript 'delegate_to_opencode.py' -RemainingArgs $args
```

### 6.3 Runtime Subcommand Registration

In `build_parser()`:

```python
opencode = sub.add_parser("opencode")
opencode_sub = opencode.add_subparsers(dest="opencode_command", required=True)
opencode_delegate = opencode_sub.add_parser("delegate_task")
add_delegate_args(opencode_delegate)  # Reuse same args
opencode_delegate.add_argument("-Variant", dest="variant")  # OpenCode-specific
opencode_delegate.set_defaults(func=run_opencode_delegate)
```

## 7. Session Management for OpenCode

OpenCode's session model requires slightly different handling:

### 7.1 New Session Flow

1. Acquire session lease from pool. The pool record may have `sessionId: null` for a fresh key.
2. Invoke `opencode run` **without** `--session` and **without** `--continue`.
3. Parse the first `step_start` event to discover the assigned `sessionID`.
4. After the run completes: write the discovered `sessionID` back to the pool for future reuse.

### 7.2 Resume Session Flow

1. Acquire session lease from pool. The pool record contains `sessionId: "ses_xxx"`.
2. Invoke `opencode run --session ses_xxx`.
3. No discovery needed; the session ID is known from the pool.

### 7.3 Session Key

Session keys remain the same (task + scope + tests fingerprint). The pool stores `sessionId` values that can be either Claude-style or OpenCode-style.

## 8. Argument Mapping (User-Facing)

The `delegate_to_opencode.*` entry scripts accept the same arguments as `delegate_to_claude.*` where applicable, plus OpenCode-specific additions:

| Argument | Claude Path | OpenCode Path | Notes |
|----------|-------------|---------------|-------|
| `-Task` / `-TaskFile` | YES | YES | Same |
| `-Scope` | YES | YES | Same |
| `-Tests` | YES | YES | Same |
| `-Mode` | YES | YES | Same (`Implement`/`Fix`/`Review`) |
| `-Model` | YES | YES | Claude: `"sonnet"`, OpenCode: `"provider/model"` |
| `-Name` | YES | YES | Claude: `--name`, OpenCode: `--title` |
| `-NamePrefix` | YES | YES | Same |
| `-MaxBudgetUsd` | YES | NO | OpenCode has no budget flag; silently ignored |
| `-ArtifactRoot` | YES | YES | Same |
| `-OutputPath` | YES | YES | Same |
| `-AllowParallel` | YES | YES | Same |
| `-SessionMode` | YES | YES | Same (PrimaryReuse/PrimaryAnchor/ParallelPool) |
| `-SessionKey` | YES | YES | Same |
| `-SessionLeaseTimeoutSeconds` | YES | YES | Same |
| `-SessionLeaseWaitSeconds` | YES | YES | Same |
| `-ResetPrimarySession` | YES | YES | Same |
| `-ResetParallelPool` | YES | YES | Same |
| `-LockTimeoutSeconds` | YES | YES | Same |
| `-LockPollMilliseconds` | YES | YES | Same |
| `-MaxRetryCount` | YES | YES | Same |
| `-BypassPermissions` | YES | YES | Claude: `--dangerously-skip-permissions`, OpenCode: `--dangerously-skip-permissions` |
| `-DryRun` | YES | YES | Same |
| `-Variant` | NO | YES | OpenCode-specific: `--variant high/max/minimal` |

## 9. Documentation Changes

### 9.1 CODEX_WITH_CC.md

Add a new section for the OpenCode delegate chain, including:
- The OpenCode-specific chain `Codex main thread → spawn_agent → delegate_to_opencode.* → OpenCode CLI`
- The `CODEX_OPENCODE_CHILD_THREAD=1` marker requirement
- The standard OpenCode worker command (macOS and Windows)
- Any OpenCode-specific nuances (model format, Variant arg)

### 9.2 SKILL.md

- Update description to mention OpenCode as supported executor
- Add OpenCode trigger words (if applicable: "opencode", "opencode delegation")
- Update workflow contract to show both chains
- Update worker report contract (unchanged — same 6 headings)
- Document the `-Variant` argument for OpenCode

### 9.3 AGENTS.md

- Update the <!-- BEGIN/END CODEX_WITH_CC --> block to mention both executors or keep it executor-agnostic

## 10. Testing Strategy

### 10.1 Runtime Tests (`scripts/test_delegate_runtime.py`)

Add OpenCode-specific test scenarios:
- Marker enforcement (`CODEX_OPENCODE_CHILD_THREAD=1` required)
- Dry run with OpenCode config
- Fake OpenCode binary for structured/unstructured output tests
- Retry logic tests with fake binary

Follow the same pattern as the existing Claude tests (fake binary approach).

### 10.2 Session Pool Tests (`scripts/test_delegate_session_pool.py`)

Most pool logic is shared and already tested. Add:
- Session pool with OpenCode-style session IDs (`ses_*`)
- Session discovery from output stream
- Session resume with stored ID

### 10.3 Unit Tests (Python)

Add tests in `tests/` for:
- `new_opencode_cli_args()` argument building
- `update_opencode_stream_capture()` parsing
- `retry_decision_opencode()` decisions

### 10.4 Integration Tests

- End-to-end test with real `opencode run` invocation (requires opencode installed)
- Chain validation: anchor → parallel → reuse with OpenCode

## 11. Migration & Backward Compatibility

### 11.1 Guarantees

- **Existing Claude Code workflow is 100% backward compatible** — no changes to `delegate_to_claude.*` behavior
- **Existing artifacts are unaffected** — Claude and OpenCode artifacts live in separate directories
- **Existing session pools are unaffected** — OpenCode sessions use the same pool format but with different session ID patterns

### 11.2 Installation

The installer (`run_install()`) is updated to:
- Copy both `delegate_to_claude.*` and `delegate_to_opencode.*` entry scripts
- Chmod both `.sh` wrappers on macOS

### 11.3 No Breaking Changes

- No argument renames
- No behavior changes to existing `delegate` subcommand
- No changes to artifact format or session pool format

## 12. Implementation Order

| Phase | Scope | Files |
|-------|-------|-------|
| 1. Constants & types | Add `DelegateConfig`, constants to runtime.py | `scripts/runtime.py` (top section) |
| 2. CLI builder | `new_opencode_cli_args()` | `scripts/runtime.py` |
| 3. Stream parser | `update_opencode_stream_capture()` | `scripts/runtime.py` |
| 4. Retry logic | `retry_decision_opencode()` | `scripts/runtime.py` |
| 5. Prompt builder | `build_opencode_prompt()` | `scripts/runtime.py` |
| 6. Delegate function | `run_opencode_delegate()` + shared `run_delegate_with_config()` | `scripts/runtime.py` |
| 7. Subcommand routing | `build_parser()` additions | `scripts/runtime.py` |
| 8. Entry scripts | `delegate_to_opencode.{py,sh,ps1}` | `scripts/`, `macos_scripts/`, `windows_scripts/` |
| 9. Documentation | CODEX_WITH_CC.md, SKILL.md, AGENTS.md | `skills/codex-with-cc/`, root |
| 10. Tests | Runtime tests, session pool tests, unit tests | `scripts/`, `tests/` |

## 13. Open Questions

1. **OpenCode error patterns**: Need to empirically discover what errors OpenCode emits on startup failure, stale session, etc., to populate `retry_decision_opencode()`. The initial implementation can handle `unstructured_success_report` only and add other patterns later.

2. **Model format**: OpenCode uses `provider/model` format (e.g., `openai/gpt-5.3-codex`). Claude uses short names (e.g., `sonnet`). Should the `-Model` argument be interpreted differently based on executor? **Proposal**: Yes — when executor is opencode, `-Model` expects `provider/model` format; when executor is claude, it expects short names.

3. **Session ID discovery race**: If `opencode run` is invoked multiple times simultaneously for the same new session key, there's a race where both instances discover different session IDs. Mitigation: The delegate lock prevents this for non-parallel mode; for parallel pool mode, each parallel slot gets its own session ID anyway.

4. **Parallel pool behavior**: Should OpenCode's parallel pool behave identically to Claude's (reusable sessions)? **Proposal**: Yes — same session pool logic, just with OpenCode session IDs.

## 14. Conclusion

The proposed design adds OpenCode as a peer executor alongside Claude Code with minimal code duplication. The strategy pattern (`DelegateConfig` dataclass) encapsulates executor-specific differences while preserving all shared infrastructure (session pool, file locking, artifact writing, report validation). The existing Claude Code path is untouched, ensuring zero regression risk.
