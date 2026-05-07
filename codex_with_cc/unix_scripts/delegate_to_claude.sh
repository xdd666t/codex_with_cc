#!/usr/bin/env bash
set -euo pipefail

ARTIFACT_SCHEMA_VERSION=2
INVOCATION_CONTRACT='spawn_agent_child_only'
REQUIRED_CHILD_THREAD_MARKER_NAME='CODEX_CLAUDE_CHILD_THREAD'
REQUIRED_CHILD_THREAD_MARKER_VALUE='1'

TASK=""
TASK_FILE=""
SCOPE=()
TESTS=()
MODE="Implement"
MODEL="sonnet"
NAME=""
NAME_PREFIX="codex-delegate"
MAX_BUDGET_USD=""
ARTIFACT_ROOT=""
OUTPUT_PATH=""
ALLOW_PARALLEL=false
SESSION_MODE="PrimaryReuse"
SESSION_KEY=""
SESSION_LEASE_TIMEOUT_SECONDS=21600
SESSION_LEASE_WAIT_SECONDS=120
RESET_PRIMARY_SESSION=false
RESET_PARALLEL_POOL=false
LOCK_TIMEOUT_SECONDS=120
LOCK_POLL_MILLISECONDS=500
MAX_RETRY_COUNT=5
BYPASS_PERMISSIONS=false
DRY_RUN=false

usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Required (one of):
  -t, --task TEXT           Inline task text
  -f, --task-file PATH      Path to task file

Options:
  -s, --scope ITEMS         Scope items (semicolon-separated or multiple flags)
  --tests ITEMS             Test commands (semicolon-separated or multiple flags)
  -m, --mode MODE           Task mode: Implement, Fix, Review (default: Implement)
  --model MODEL             Claude model (default: sonnet)
  --name NAME               Session name
  --name-prefix PREFIX      Session name prefix (default: codex-delegate)
  --max-budget-usd AMOUNT   Maximum budget in USD
  --artifact-root PATH      Artifact root directory
  --output-path PATH        Output report path
  --allow-parallel          Allow parallel execution (skip global lock)
  --session-mode MODE       Session mode: PrimaryReuse, PrimaryAnchor, ParallelPool
  --session-key KEY         Session key
  --session-lease-timeout SECONDS  Session lease timeout (default: 21600)
  --session-lease-wait SECONDS     Session lease wait (default: 120)
  --reset-primary-session   Reset primary session
  --reset-parallel-pool     Reset parallel pool
  --lock-timeout SECONDS    Lock timeout (default: 120)
  --lock-poll MS            Lock poll interval in ms (default: 500)
  --max-retry-count N       Maximum retry count (default: 5)
  --bypass-permissions      Skip permission checks
  --dry-run                 Dry run without invoking Claude
  -h, --help                Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--task)
            TASK="$2"
            shift 2
            ;;
        -f|--task-file)
            TASK_FILE="$2"
            shift 2
            ;;
        -s|--scope)
            IFS=';' read -ra PARTS <<< "$2"
            SCOPE+=("${PARTS[@]}")
            shift 2
            ;;
        --tests)
            IFS=';' read -ra PARTS <<< "$2"
            TESTS+=("${PARTS[@]}")
            shift 2
            ;;
        -m|--mode)
            MODE="$2"
            shift 2
            ;;
        --model)
            MODEL="$2"
            shift 2
            ;;
        --name)
            NAME="$2"
            shift 2
            ;;
        --name-prefix)
            NAME_PREFIX="$2"
            shift 2
            ;;
        --max-budget-usd)
            MAX_BUDGET_USD="$2"
            shift 2
            ;;
        --artifact-root)
            ARTIFACT_ROOT="$2"
            shift 2
            ;;
        --output-path)
            OUTPUT_PATH="$2"
            shift 2
            ;;
        --allow-parallel)
            ALLOW_PARALLEL=true
            shift
            ;;
        --session-mode)
            SESSION_MODE="$2"
            shift 2
            ;;
        --session-key)
            SESSION_KEY="$2"
            shift 2
            ;;
        --session-lease-timeout)
            SESSION_LEASE_TIMEOUT_SECONDS="$2"
            shift 2
            ;;
        --session-lease-wait)
            SESSION_LEASE_WAIT_SECONDS="$2"
            shift 2
            ;;
        --reset-primary-session)
            RESET_PRIMARY_SESSION=true
            shift
            ;;
        --reset-parallel-pool)
            RESET_PARALLEL_POOL=true
            shift
            ;;
        --lock-timeout)
            LOCK_TIMEOUT_SECONDS="$2"
            shift 2
            ;;
        --lock-poll)
            LOCK_POLL_MILLISECONDS="$2"
            shift 2
            ;;
        --max-retry-count)
            MAX_RETRY_COUNT="$2"
            shift 2
            ;;
        --bypass-permissions)
            BYPASS_PERMISSIONS=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKFLOW_CONTAINER="$(dirname "$WORKFLOW_ROOT")"
if [[ "$(basename "$WORKFLOW_CONTAINER")" == "docs" ]]; then
    REPO_ROOT="$(cd "$WORKFLOW_CONTAINER/.." && pwd)"
else
    REPO_ROOT="$WORKFLOW_CONTAINER"
fi

ENTRY_PATH="$WORKFLOW_ROOT/CODEX_WITH_CC.md"
SESSION_POOL_HELPER_PATH="$SCRIPT_DIR/claude_session_pool.sh"
BACKEND_HELPER_PATH="$SCRIPT_DIR/claude_delegate_backend_helpers.sh"

if [[ -z "$ARTIFACT_ROOT" ]]; then
    ARTIFACT_ROOT="$REPO_ROOT/.codex/codex_with_cc/claude-delegate"
fi
RESOLVED_ARTIFACT_ROOT="$(cd "$(dirname "$ARTIFACT_ROOT")" 2>/dev/null && pwd)/$(basename "$ARTIFACT_ROOT")" || RESOLVED_ARTIFACT_ROOT="$ARTIFACT_ROOT"

if [[ ! -f "$SESSION_POOL_HELPER_PATH" ]]; then
    echo "Missing Claude session pool helper: $SESSION_POOL_HELPER_PATH" >&2
    exit 1
fi
if [[ ! -f "$BACKEND_HELPER_PATH" ]]; then
    echo "Missing Claude delegate backend helper: $BACKEND_HELPER_PATH" >&2
    exit 1
fi

source "$SESSION_POOL_HELPER_PATH"
source "$BACKEND_HELPER_PATH"

if [[ ! -f "$ENTRY_PATH" ]]; then
    echo "Missing workflow entry document: $ENTRY_PATH" >&2
    exit 1
fi

CHILD_THREAD_MARKER="${!REQUIRED_CHILD_THREAD_MARKER_NAME:-}"
if [[ "$CHILD_THREAD_MARKER" != "$REQUIRED_CHILD_THREAD_MARKER_VALUE" ]]; then
    echo "delegate_to_claude.sh may only run inside a Codex spawn_agent child thread." >&2
    echo "Missing required child-thread marker '$REQUIRED_CHILD_THREAD_MARKER_NAME=$REQUIRED_CHILD_THREAD_MARKER_VALUE'." >&2
    echo "Main-thread/direct invocation is forbidden." >&2
    exit 1
fi

if ! command -v claude &>/dev/null; then
    echo "Claude Code CLI was not found. Install or expose the 'claude' command first." >&2
    exit 1
fi

# Check other required tools early to fail fast with clear message
if ! command -v jq &>/dev/null; then
    echo "Required tool 'jq' was not found. Install 'jq' and retry." >&2
    exit 1
fi
if ! command -v flock &>/dev/null; then
    echo "Required tool 'flock' was not found. Ensure util-linux (flock) is available." >&2
    exit 1
fi

if [[ -n "$TASK_FILE" ]]; then
    if [[ ! -f "$TASK_FILE" ]]; then
        echo "Task file was not found: $TASK_FILE" >&2
        exit 1
    fi
    TASK_TEXT=$(cat "$TASK_FILE")
else
    TASK_TEXT="$TASK"
fi

if [[ -z "$TASK_TEXT" ]]; then
    echo "Task text cannot be empty." >&2
    exit 1
fi

if [[ $LOCK_TIMEOUT_SECONDS -lt 0 ]]; then
    echo "LockTimeoutSeconds must be >= 0. Current: $LOCK_TIMEOUT_SECONDS" >&2
    exit 1
fi
if [[ $LOCK_POLL_MILLISECONDS -lt 50 ]]; then
    echo "LockPollMilliseconds must be >= 50. Current: $LOCK_POLL_MILLISECONDS" >&2
    exit 1
fi

SCOPE_STR=$(printf '%s\n' "${SCOPE[@]}" | sort | tr '\n' '|' | sed 's/|$//')
TESTS_STR=$(printf '%s\n' "${TESTS[@]}" | sort | tr '\n' '|' | sed 's/|$//')

mkdir -p "$RESOLVED_ARTIFACT_ROOT"

EFFECTIVE_SESSION_KEY=$(get_effective_session_key "$SESSION_KEY")
SAFE_SESSION_KEY=$(get_safe_session_key "$EFFECTIVE_SESSION_KEY")
SESSION_POOLS_ROOT="$RESOLVED_ARTIFACT_ROOT/session-pools"
SESSION_STATE_PATH="$SESSION_POOLS_ROOT/$SAFE_SESSION_KEY.json"
SESSION_STATE_LOCK_PATH="$SESSION_POOLS_ROOT/$SAFE_SESSION_KEY.lock"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S_%3N")
RUN_ID="${TIMESTAMP}_$(head -c 8 /dev/urandom 2>/dev/null | xxd -p 2>/dev/null || echo "$RANDOM$RANDOM" | head -c 8)"

if [[ -z "$NAME" ]]; then
    EFFECTIVE_NAME="${NAME_PREFIX}-${RUN_ID}"
else
    EFFECTIVE_NAME="$NAME"
fi

if [[ -z "$OUTPUT_PATH" ]]; then
    OUTPUT_PATH="$RESOLVED_ARTIFACT_ROOT/claude_${RUN_ID}.md"
fi
RESOLVED_OUTPUT_PATH="$OUTPUT_PATH"

STATUS_PATH="$RESOLVED_ARTIFACT_ROOT/status_${RUN_ID}.json"
CONFIG_PATH="$RESOLVED_ARTIFACT_ROOT/config_${RUN_ID}.json"
PROMPT_PATH="$RESOLVED_ARTIFACT_ROOT/prompt_${RUN_ID}.md"
RAW_STREAM_PATH="$RESOLVED_ARTIFACT_ROOT/stream_${RUN_ID}.jsonl"
TRACE_PATH="$RESOLVED_ARTIFACT_ROOT/trace_${RUN_ID}.log"
LOCK_PATH="$RESOLVED_ARTIFACT_ROOT/delegate.lock"

TASK_FINGERPRINT=$(get_task_fingerprint "$TASK_TEXT" "$SCOPE_STR" "$TESTS_STR" "$MODE")

writable=$(test_claude_delegate_path_writable "$RESOLVED_OUTPUT_PATH")
if [[ "$writable" != "true" ]]; then
    echo "Path is not writable: $RESOLVED_OUTPUT_PATH" >&2
    exit 1
fi

SCOPE_TEXT=""
if [[ ${#SCOPE[@]} -gt 0 ]]; then
    SCOPE_TEXT=$(printf -- '- %s\n' "${SCOPE[@]}")
else
    SCOPE_TEXT="- No explicit file scope was provided. Infer the narrowest safe scope from the task and current code."
fi

TESTS_TEXT=""
if [[ ${#TESTS[@]} -gt 0 ]]; then
    TESTS_TEXT=$(printf -- '- %s\n' "${TESTS[@]}")
else
    TESTS_TEXT="- Run the smallest relevant verification you can identify from the change."
fi

WORKER_PROTOCOL_TEXT="- This prompt is already the only allowed Claude worker context for this delegated run.
- Never call docs/codex_with_cc/unix_scripts/delegate_to_claude.sh, claude, or spawn_agent recursively from inside this worker.
- Treat docs/codex_with_cc/CODEX_WITH_CC.md as the workflow contract to inspect when the task scope requires it, not as an execution recipe for this worker.
- If the task is an audit or validation, inspect the scoped files and run the listed verification commands directly instead of creating nested delegate runs.
- If you think another delegate run is required, stop and explain why in Final Result instead of invoking it yourself."

PROMPT="You are Claude Code acting as an implementation worker for Codex.

This worker script is reserved for Codex spawn_agent child threads. It is not a valid main-thread entry point.

Codex owns architecture, task boundaries, and final review. Your job is to execute the delegated task directly in this repository, keep changes narrow, verify them, and report exactly what changed.

Repository root:
$REPO_ROOT

Delegated output report path:
$RESOLVED_OUTPUT_PATH

Mode:
$MODE

Allowed / intended scope:
$SCOPE_TEXT

Required or expected verification:
$TESTS_TEXT

Worker protocol:
$WORKER_PROTOCOL_TEXT

Task:
$TASK_TEXT

Hard requirements:
- Read docs/codex_with_cc/CODEX_WITH_CC.md before scanning other repository files.
- Use docs/codex_with_cc/CODEX_WITH_CC.md as the single workflow contract for delegation, audit flow, session mode interpretation, and worker report requirements.
- Follow all applicable project-defined skills and workflow skills before implementing or changing behavior, especially Codex project skills under .codex. Read the target project's agent/rule files and referenced skill documents when they apply to the delegated task.
- Keep edits inside the intended scope unless the task is impossible without a small supporting change.
- You must run necessary verification before handing work back. Run every command listed under Required or expected verification; if none is listed, infer the smallest meaningful format/analyze/test command for the changed area.
- Do not return code that you know fails to compile, analyze, or pass the required focused tests. Fix verification failures and rerun them until they pass.
- If verification is blocked by an external dependency or a clearly pre-existing unrelated failure, report the exact command, failure summary, and why it is not caused by your changes.
- Never claim verification passed unless you actually ran the command and saw it pass.
- Process and summarize your own CLI output. The Codex child thread will forward your final structured result; it should not reinterpret long logs for you.
- Treat this script as a child-thread worker entry only. Do not reinterpret it as permission for the Codex main thread to invoke Claude directly.
- Write enough detail in Process Log for the user to understand what happened, but keep raw verbose command output in the transcript/log instead of duplicating it.
- Finish with these exact headings:
  Process Log
  Summary
  Changed Files
  Verification
  Final Result
  Risks Or Follow-ups"

echo "$PROMPT" > "$PROMPT_PATH"

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
cat > "$CONFIG_PATH" <<EOF
{
  "artifactSchema": $ARTIFACT_SCHEMA_VERSION,
  "invocationContract": "$INVOCATION_CONTRACT",
  "childThreadMarkerName": "$REQUIRED_CHILD_THREAD_MARKER_NAME",
  "childThreadMarkerValidated": true,
  "runId": "$RUN_ID",
  "repoRoot": "$REPO_ROOT",
  "mode": "$MODE",
  "model": "$MODEL",
  "sessionName": "$EFFECTIVE_NAME",
  "sessionMode": "$SESSION_MODE",
  "sessionKey": "$EFFECTIVE_SESSION_KEY",
  "sessionStatePath": "$SESSION_STATE_PATH",
  "sessionStateLockPath": "$SESSION_STATE_LOCK_PATH",
  "promptPath": "$PROMPT_PATH",
  "outputPath": "$RESOLVED_OUTPUT_PATH",
  "statusPath": "$STATUS_PATH",
  "rawStreamPath": "$RAW_STREAM_PATH",
  "tracePath": "$TRACE_PATH",
  "lockPath": "$LOCK_PATH",
  "taskFile": ${TASK_FILE:+\"$TASK_FILE\"},
  "maxBudgetUsd": ${MAX_BUDGET_USD:-null},
  "bypassPermissions": $BYPASS_PERMISSIONS,
  "allowParallel": $ALLOW_PARALLEL,
  "initialSessionId": null,
  "initialResume": null,
  "attemptCount": 0,
  "retryCount": 0,
  "maxRetryCount": $MAX_RETRY_COUNT,
  "updatedAt": "$NOW"
}
EOF

cat > "$STATUS_PATH" <<EOF
{
  "artifactSchema": $ARTIFACT_SCHEMA_VERSION,
  "invocationContract": "$INVOCATION_CONTRACT",
  "childThreadMarkerName": "$REQUIRED_CHILD_THREAD_MARKER_NAME",
  "childThreadMarkerValidated": true,
  "runId": "$RUN_ID",
  "status": "starting",
  "pid": $$,
  "outputPath": "$RESOLVED_OUTPUT_PATH",
  "promptPath": "$PROMPT_PATH",
  "rawStreamPath": "$RAW_STREAM_PATH",
  "tracePath": "$TRACE_PATH",
  "linesWritten": 0,
  "outputBytes": 0,
  "exitCode": null,
  "attemptCount": 0,
  "retryCount": 0,
  "maxRetryCount": $MAX_RETRY_COUNT,
  "attempts": [],
  "updatedAt": "$NOW"
}
EOF

complete_claude_delegate_startup_failure() {
    local failure_message="$1"
    local failure_summary="STARTUP_FAILURE: $failure_message"
    
    local failure_output="Process Log
- Delegate worker failed before Claude Code execution started.
- Startup failure: $failure_message

Summary
The delegate run did not reach Claude Code execution.

Changed Files
None

Verification
- not run; delegate startup failed before worker execution

Final Result
FAIL / NEED_HUMAN_INTERVENTION
$failure_summary

Risks Or Follow-ups
- Retry only after the startup blocker is resolved."
    
    echo "$failure_output" > "$RESOLVED_OUTPUT_PATH"
    
    if [[ ! -f "$RAW_STREAM_PATH" ]]; then
        touch "$RAW_STREAM_PATH"
    fi
    if [[ ! -f "$TRACE_PATH" ]]; then
        echo "[startup-failure] $failure_message" > "$TRACE_PATH"
    fi
    
    local output_bytes
    output_bytes=$(wc -c < "$RESOLVED_OUTPUT_PATH" 2>/dev/null || echo 0)
    
    NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    cat > "$STATUS_PATH" <<EOF
{
  "artifactSchema": $ARTIFACT_SCHEMA_VERSION,
  "invocationContract": "$INVOCATION_CONTRACT",
  "childThreadMarkerName": "$REQUIRED_CHILD_THREAD_MARKER_NAME",
  "childThreadMarkerValidated": true,
  "runId": "$RUN_ID",
  "status": "failed",
  "pid": $$,
  "outputPath": "$RESOLVED_OUTPUT_PATH",
  "promptPath": "$PROMPT_PATH",
  "rawStreamPath": "$RAW_STREAM_PATH",
  "tracePath": "$TRACE_PATH",
  "linesWritten": 0,
  "outputBytes": $output_bytes,
  "exitCode": 1,
  "attemptCount": 1,
  "retryCount": 0,
  "maxRetryCount": $MAX_RETRY_COUNT,
  "failureDisposition": "NEED_HUMAN_INTERVENTION",
  "failureSummary": "$failure_summary",
  "attempts": [
    {
      "attempt": 1,
      "sessionId": "",
      "resume": false,
      "retryReason": null,
      "exitCode": 1,
      "sawAssistantText": false,
      "sawResultSuccess": false,
      "capturedFinalResult": true
    }
  ],
  "updatedAt": "$NOW"
}
EOF
    
    cat > "$CONFIG_PATH" <<EOF
{
  "artifactSchema": $ARTIFACT_SCHEMA_VERSION,
  "invocationContract": "$INVOCATION_CONTRACT",
  "childThreadMarkerName": "$REQUIRED_CHILD_THREAD_MARKER_NAME",
  "childThreadMarkerValidated": true,
  "runId": "$RUN_ID",
  "repoRoot": "$REPO_ROOT",
  "mode": "$MODE",
  "model": "$MODEL",
  "sessionName": "$EFFECTIVE_NAME",
  "sessionMode": "$SESSION_MODE",
  "sessionKey": "$EFFECTIVE_SESSION_KEY",
  "sessionStatePath": "$SESSION_STATE_PATH",
  "sessionStateLockPath": "$SESSION_STATE_LOCK_PATH",
  "promptPath": "$PROMPT_PATH",
  "outputPath": "$RESOLVED_OUTPUT_PATH",
  "statusPath": "$STATUS_PATH",
  "rawStreamPath": "$RAW_STREAM_PATH",
  "tracePath": "$TRACE_PATH",
  "lockPath": "$LOCK_PATH",
  "taskFile": ${TASK_FILE:+\"$TASK_FILE\"},
  "maxBudgetUsd": ${MAX_BUDGET_USD:-null},
  "bypassPermissions": $BYPASS_PERMISSIONS,
  "allowParallel": $ALLOW_PARALLEL,
  "initialSessionId": "",
  "initialResume": false,
  "sessionId": "",
  "resume": false,
  "attemptCount": 1,
  "retryCount": 0,
  "maxRetryCount": $MAX_RETRY_COUNT,
  "failureDisposition": "NEED_HUMAN_INTERVENTION",
  "failureSummary": "$failure_summary",
  "updatedAt": "$NOW"
}
EOF
}

LOCK_FD=""
cleanup_lock() {
    if [[ -n "$LOCK_FD" ]]; then
        flock -u "$LOCK_FD" 2>/dev/null || true
        exec 3>&- 2>/dev/null || true
    fi
    rm -f "$LOCK_PATH" 2>/dev/null || true
}

if [[ "$ALLOW_PARALLEL" != "true" ]]; then
    mkdir -p "$(dirname "$LOCK_PATH")"
    
    LOCK_DEADLINE=$(( $(date +%s) + LOCK_TIMEOUT_SECONDS ))
    
    while true; do
        exec 3>"$LOCK_PATH"
        if flock -x -n 3; then
            LOCK_FD=3
            
            cat > "$LOCK_PATH" <<EOF
{
  "runId": "$RUN_ID",
  "sessionName": "$EFFECTIVE_NAME",
  "pid": $$,
  "startedAt": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "mode": "$MODE"
}
EOF
            break
        fi
        
        if [[ $(date +%s) -ge $LOCK_DEADLINE ]]; then
            local lock_snapshot=""
            if [[ -f "$LOCK_PATH" ]]; then
                lock_snapshot=$(cat "$LOCK_PATH" 2>/dev/null || echo "<unreadable>")
            fi
            complete_claude_delegate_startup_failure "Another delegate_to_claude run is still active. Use --allow-parallel to bypass, or wait. Lock: $LOCK_PATH. Holder: $lock_snapshot"
            echo "Another delegate_to_claude run is still active. Use --allow-parallel to bypass, or wait. Lock: $LOCK_PATH. Holder: $lock_snapshot" >&2
            exit 1
        fi
        
        sleep 0.5
    done
fi

trap cleanup_lock EXIT

cd "$REPO_ROOT"

SESSION_LEASE=$(acquire_claude_session_lease \
    "$SESSION_STATE_PATH" \
    "$SESSION_STATE_LOCK_PATH" \
    "$EFFECTIVE_SESSION_KEY" \
    "$SESSION_MODE" \
    "$RUN_ID" \
    "$TASK_FINGERPRINT" \
    "$SESSION_LEASE_TIMEOUT_SECONDS" \
    "$SESSION_LEASE_WAIT_SECONDS" \
    "$RESET_PRIMARY_SESSION" \
    "$RESET_PARALLEL_POOL")

SESSION_ID=$(echo "$SESSION_LEASE" | jq -r '.sessionId')
RESUME=$(echo "$SESSION_LEASE" | jq -r '.resume')

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
cat > "$CONFIG_PATH" <<EOF
{
  "artifactSchema": $ARTIFACT_SCHEMA_VERSION,
  "invocationContract": "$INVOCATION_CONTRACT",
  "childThreadMarkerName": "$REQUIRED_CHILD_THREAD_MARKER_NAME",
  "childThreadMarkerValidated": true,
  "runId": "$RUN_ID",
  "repoRoot": "$REPO_ROOT",
  "mode": "$MODE",
  "model": "$MODEL",
  "sessionName": "$EFFECTIVE_NAME",
  "sessionMode": "$SESSION_MODE",
  "sessionKey": "$EFFECTIVE_SESSION_KEY",
  "sessionStatePath": "$SESSION_STATE_PATH",
  "sessionStateLockPath": "$SESSION_STATE_LOCK_PATH",
  "promptPath": "$PROMPT_PATH",
  "outputPath": "$RESOLVED_OUTPUT_PATH",
  "statusPath": "$STATUS_PATH",
  "rawStreamPath": "$RAW_STREAM_PATH",
  "tracePath": "$TRACE_PATH",
  "lockPath": "$LOCK_PATH",
  "taskFile": ${TASK_FILE:+\"$TASK_FILE\"},
  "maxBudgetUsd": ${MAX_BUDGET_USD:-null},
  "bypassPermissions": $BYPASS_PERMISSIONS,
  "allowParallel": $ALLOW_PARALLEL,
  "initialSessionId": "$SESSION_ID",
  "initialResume": $RESUME,
  "sessionId": "$SESSION_ID",
  "resume": $RESUME,
  "attemptCount": 0,
  "retryCount": 0,
  "maxRetryCount": $MAX_RETRY_COUNT,
  "updatedAt": "$NOW"
}
EOF

echo "Delegating to Claude Code: $(command -v claude)"
echo "RunId: $RUN_ID"
echo "Session Name: $EFFECTIVE_NAME"
echo "Session Mode: $SESSION_MODE"
echo "Session Key: $EFFECTIVE_SESSION_KEY"
echo "Claude Session Id: $SESSION_ID"
echo "Claude Session Argument: $([ "$RESUME" == "true" ] && echo "--resume" || echo "--session-id") $SESSION_ID"
echo "Prompt: $PROMPT_PATH"
echo "Output: $RESOLVED_OUTPUT_PATH"
echo "Status: $STATUS_PATH"
echo "Trace: $TRACE_PATH"
echo "Raw Stream: $RAW_STREAM_PATH"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "Dry run enabled; Claude Code was not invoked."
    
    NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    cat > "$STATUS_PATH" <<EOF
{
  "artifactSchema": $ARTIFACT_SCHEMA_VERSION,
  "invocationContract": "$INVOCATION_CONTRACT",
  "childThreadMarkerName": "$REQUIRED_CHILD_THREAD_MARKER_NAME",
  "childThreadMarkerValidated": true,
  "runId": "$RUN_ID",
  "status": "completed",
  "pid": $$,
  "outputPath": "$RESOLVED_OUTPUT_PATH",
  "promptPath": "$PROMPT_PATH",
  "rawStreamPath": "$RAW_STREAM_PATH",
  "tracePath": "$TRACE_PATH",
  "linesWritten": 0,
  "outputBytes": 0,
  "exitCode": 0,
  "attemptCount": 0,
  "retryCount": 0,
  "maxRetryCount": $MAX_RETRY_COUNT,
  "attempts": [],
  "updatedAt": "$NOW"
}
EOF
    
    release_claude_session_lease \
        "$SESSION_STATE_PATH" \
        "$SESSION_STATE_LOCK_PATH" \
        "$EFFECTIVE_SESSION_KEY" \
        "$SESSION_LEASE" \
        "$RUN_ID" \
        "$TASK_FINGERPRINT"
    
    exit 0
fi

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
cat > "$STATUS_PATH" <<EOF
{
  "artifactSchema": $ARTIFACT_SCHEMA_VERSION,
  "invocationContract": "$INVOCATION_CONTRACT",
  "childThreadMarkerName": "$REQUIRED_CHILD_THREAD_MARKER_NAME",
  "childThreadMarkerValidated": true,
  "runId": "$RUN_ID",
  "status": "running",
  "pid": $$,
  "outputPath": "$RESOLVED_OUTPUT_PATH",
  "promptPath": "$PROMPT_PATH",
  "rawStreamPath": "$RAW_STREAM_PATH",
  "tracePath": "$TRACE_PATH",
  "linesWritten": 0,
  "outputBytes": 0,
  "exitCode": null,
  "attemptCount": 0,
  "retryCount": 0,
  "maxRetryCount": $MAX_RETRY_COUNT,
  "attempts": [],
  "updatedAt": "$NOW"
}
EOF

PROMPT_TEXT=$(cat "$PROMPT_PATH")

ATTEMPT=0
MAX_ATTEMPTS=$((MAX_RETRY_COUNT + 1))
RETRY_COUNT=0
DELEGATE_SUCCEEDED=false
EXIT_CODE=-1
FINAL_TEXT=""
OUTPUT_RESOLUTION=""
FAILURE_DISPOSITION=""
FAILURE_SUMMARY=""

CAPTURE_STATE_FILE=$(mktemp)
echo '{"assistantTexts":[],"traceLines":[],"finalText":"","sawAssistantText":false,"sawResultSuccess":false,"capturedFinalResultHeading":false}' > "$CAPTURE_STATE_FILE"

RAW_STREAM_TMP=$(mktemp)
TRACE_TMP=$(mktemp)

while [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; do
    ATTEMPT=$((ATTEMPT + 1))
    
    echo '{"assistantTexts":[],"traceLines":[],"finalText":"","sawAssistantText":false,"sawResultSuccess":false,"capturedFinalResultHeading":false}' > "$CAPTURE_STATE_FILE"
    
    local -a ATTEMPT_RAW_LINES=()
    
    NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    
    local attempt_resume="false"
    if [[ "$ATTEMPT" -eq 1 ]]; then
        attempt_resume="$RESUME"
    else
        attempt_resume=$(echo "$SESSION_LEASE" | jq -r '.resume')
    fi
    
    ATTEMPT_RECORD=$(jq -n \
        --argjson attempt "$ATTEMPT" \
        --arg session_id "$SESSION_ID" \
        --argjson resume "$attempt_resume" \
        '{
            attempt: $attempt,
            sessionId: $session_id,
            resume: $resume,
            retryReason: null,
            exitCode: null,
            sawAssistantText: false,
            sawResultSuccess: false,
            capturedFinalResult: false,
            outputWasNormalized: false,
            sawStaleSessionText: false,
            sawStreamJsonVerboseError: false
        }')
    
    # Build claude CLI args as an array to preserve quoting and whitespace
    mapfile -t CLAUDE_ARGS < <(new_claude_delegate_cli_args \
        "$MODEL" \
        "$EFFECTIVE_NAME" \
        "$SESSION_ID" \
        "$attempt_resume" \
        "${MAX_BUDGET_USD:-}" \
        "$BYPASS_PERMISSIONS" \
        "$PROMPT_TEXT")
    
    if [[ $ATTEMPT -eq 1 ]]; then
        jq --arg sid "$SESSION_ID" --argjson resume "$RESUME" \
            '.initialSessionId = $sid | .initialResume = $resume | .sessionId = $sid | .resume = $resume | .attemptCount = 1' \
            "$CONFIG_PATH" > "${CONFIG_PATH}.tmp" && mv "${CONFIG_PATH}.tmp" "$CONFIG_PATH"
    else
        jq --arg sid "$SESSION_ID" --argjson resume "$attempt_resume" \
            '.sessionId = $sid | .resume = $resume | .attemptCount = '"$ATTEMPT" \
            "$CONFIG_PATH" > "${CONFIG_PATH}.tmp" && mv "${CONFIG_PATH}.tmp" "$CONFIG_PATH"
    fi
    
    jq --argjson attempt "$ATTEMPT" --argjson retry "$RETRY_COUNT" \
        '.attemptCount = $attempt | .retryCount = $retry' \
        "$STATUS_PATH" > "${STATUS_PATH}.tmp" && mv "${STATUS_PATH}.tmp" "$STATUS_PATH"
    
    echo "Attempt $ATTEMPT/$MAX_ATTEMPTS"
    echo "Claude Session Id: $SESSION_ID"
    echo "Claude Session Argument: $([ "$attempt_resume" == "true" ] && echo "--resume" || echo "--session-id") $SESSION_ID"
    
    echo "[attempt] $ATTEMPT session=$SESSION_ID resume=$attempt_resume" >> "$TRACE_TMP"
    
    set +e
    while IFS= read -r line; do
        if [[ -z "$line" ]]; then
            continue
        fi
        
        ATTEMPT_RAW_LINES+=("$line")
        echo "$line" >> "$RAW_STREAM_TMP"
        
        local trace_line=""
        if echo "$line" | jq -e . >/dev/null 2>&1; then
            trace_line=$(update_claude_delegate_stream_capture "$line" "$CAPTURE_STATE_FILE")
        else
            trace_line="[raw] non-json output line"
        fi
        
        if [[ -n "$trace_line" ]]; then
            echo "$trace_line" >> "$TRACE_TMP"
        fi
        
        LINES_WRITTEN=$(jq -r '.linesWritten // 0' "$STATUS_PATH")
        jq --argjson lines $((LINES_WRITTEN + 1)) --arg now "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
            '.linesWritten = $lines | .lastOutputAt = $now' \
            "$STATUS_PATH" > "${STATUS_PATH}.tmp" && mv "${STATUS_PATH}.tmp" "$STATUS_PATH"
    done < <(claude "${CLAUDE_ARGS[@]}" 2>&1)
    EXIT_CODE=$?
    set -e
    
    CAPTURE_STATE=$(cat "$CAPTURE_STATE_FILE")
    FINAL_TEXT=$(echo "$CAPTURE_STATE" | jq -r '.finalText')
    if [[ -z "$FINAL_TEXT" ]]; then
        local texts
        texts=$(echo "$CAPTURE_STATE" | jq -r '.assistantTexts[-1] // empty')
        if [[ -n "$texts" ]]; then
            FINAL_TEXT="$texts"
        fi
    fi
    
    SAW_ASSISTANT_TEXT=$(echo "$CAPTURE_STATE" | jq -r '.sawAssistantText')
    SAW_RESULT_SUCCESS=$(echo "$CAPTURE_STATE" | jq -r '.sawResultSuccess')
    CAPTURED_FINAL_RESULT_HEADING=$(echo "$CAPTURE_STATE" | jq -r '.capturedFinalResultHeading')
    
    RAW_LINES_STR=$(printf '%s\n' "${ATTEMPT_RAW_LINES[@]}")
    RETRY_DECISION=$(get_claude_delegate_retry_decision \
        "$RAW_LINES_STR" \
        "$attempt_resume" \
        "$EXIT_CODE" \
        "$SAW_ASSISTANT_TEXT" \
        "$SAW_RESULT_SUCCESS" \
        "$CAPTURED_FINAL_RESULT_HEADING")
    
    SHOULD_RETRY=$(echo "$RETRY_DECISION" | jq -r '.shouldRetry')
    RETRY_WITH_FRESH_SESSION=$(echo "$RETRY_DECISION" | jq -r '.retryWithFreshSession')
    RETRY_REASON=$(echo "$RETRY_DECISION" | jq -r '.retryReason')
    SAW_STALE_SESSION_TEXT=$(echo "$RETRY_DECISION" | jq -r '.sawStaleSessionText')
    SAW_STREAM_JSON_VERBOSE_ERROR=$(echo "$RETRY_DECISION" | jq -r '.sawStreamJsonVerboseError')
    
    ATTEMPT_RECORD=$(echo "$ATTEMPT_RECORD" | jq \
        --argjson exit_code "$EXIT_CODE" \
        --argjson saw_assistant "$SAW_ASSISTANT_TEXT" \
        --argjson saw_result "$SAW_RESULT_SUCCESS" \
        --argjson captured "$CAPTURED_FINAL_RESULT_HEADING" \
        --argjson stale "$SAW_STALE_SESSION_TEXT" \
        --argjson stream_err "$SAW_STREAM_JSON_VERBOSE_ERROR" \
        '.exitCode = $exit_code | .sawAssistantText = $saw_assistant | .sawResultSuccess = $saw_result | .capturedFinalResult = $captured | .sawStaleSessionText = $stale | .sawStreamJsonVerboseError = $stream_err')
    
    if [[ "$SHOULD_RETRY" == "true" ]] && [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; then
        RETRY_COUNT=$((RETRY_COUNT + 1))
        ATTEMPT_RECORD=$(echo "$ATTEMPT_RECORD" | jq --arg reason "$RETRY_REASON" '.retryReason = $reason')
        
        jq --argjson retry "$RETRY_COUNT" --arg reason "$RETRY_REASON" \
            '.retryCount = $retry | .lastRetryReason = $reason' \
            "$STATUS_PATH" > "${STATUS_PATH}.tmp" && mv "${STATUS_PATH}.tmp" "$STATUS_PATH"
        
        if [[ "$RETRY_WITH_FRESH_SESSION" == "true" ]]; then
            echo "Warning: Claude rejected resumed session '$SESSION_ID'. Retrying once with a fresh session." >&2
            echo "[retry] stale session id rejected; resetting lease for a fresh Claude session" >> "$TRACE_TMP"
            
            SESSION_LEASE=$(reset_claude_session_lease_for_fresh_session \
                "$SESSION_STATE_PATH" \
                "$SESSION_STATE_LOCK_PATH" \
                "$EFFECTIVE_SESSION_KEY" \
                "$SESSION_LEASE" \
                "$RUN_ID" \
                "$TASK_FINGERPRINT" \
                "stale_claude_session")
            
            SESSION_ID=$(echo "$SESSION_LEASE" | jq -r '.sessionId')
        else
            echo "Warning: Claude startup failed before structured output was produced. Retrying once with the current session arguments." >&2
            echo "[retry] stream-json startup failed before structured output; retrying with current session" >> "$TRACE_TMP"
        fi
        
        continue
    fi
    
    if [[ "$SHOULD_RETRY" == "true" ]]; then
        FAILURE_DISPOSITION="NEED_HUMAN_INTERVENTION"
        FAILURE_SUMMARY=$(get_claude_delegate_failure_summary \
            "$RAW_LINES_STR" \
            "$RETRY_REASON" \
            "$ATTEMPT" \
            "$MAX_RETRY_COUNT" \
            "$EXIT_CODE")
        
        jq --arg disp "$FAILURE_DISPOSITION" --arg summary "$FAILURE_SUMMARY" --arg reason "$RETRY_REASON" \
            '.failureDisposition = $disp | .failureSummary = $summary | .finalRetryReason = $reason' \
            "$STATUS_PATH" > "${STATUS_PATH}.tmp" && mv "${STATUS_PATH}.tmp" "$STATUS_PATH"
        
        jq --arg disp "$FAILURE_DISPOSITION" --arg summary "$FAILURE_SUMMARY" --arg reason "$RETRY_REASON" \
            '.failureDisposition = $disp | .failureSummary = $summary | .finalRetryReason = $reason' \
            "$CONFIG_PATH" > "${CONFIG_PATH}.tmp" && mv "${CONFIG_PATH}.tmp" "$CONFIG_PATH"
        
        echo "[failure] retry ceiling reached; forcing NEED_HUMAN_INTERVENTION" >> "$TRACE_TMP"
        echo "[failure] $FAILURE_SUMMARY" >> "$TRACE_TMP"
        
        FINAL_TEXT="Process Log
- Delegate worker detected a retryable Claude failure and exhausted the configured retry budget.
- Automatic recovery stopped to avoid unbounded compute burn.

Summary
Automatic retry recovery hit the configured ceiling and requires human or Codex intervention.

Changed Files
None

Verification
- not run; the worker never reached a trustworthy execution state

Final Result
FAIL / NEED_HUMAN_INTERVENTION
$FAILURE_SUMMARY

Risks Or Follow-ups
- Inspect Claude CLI startup/session health before retrying this delegated task again."
    fi
    
    OUTPUT_RESOLUTION=$(get_claude_delegate_output_resolution \
        "$FINAL_TEXT" \
        "$RESOLVED_OUTPUT_PATH" \
        "$EXIT_CODE" \
        "$SAW_RESULT_SUCCESS" \
        "$CAPTURED_FINAL_RESULT_HEADING")
    
    EXISTING_STRUCTURED_OUTPUT=$(echo "$OUTPUT_RESOLUTION" | jq -r '.existingStructuredOutput')
    FINAL_TEXT_HAS_FINAL=$(echo "$OUTPUT_RESOLUTION" | jq -r '.finalTextHasFinalResult')
    OUTPUT_WAS_NORMALIZED=$(echo "$OUTPUT_RESOLUTION" | jq -r '.outputWasNormalized')
    SHOULD_PERSIST=$(echo "$OUTPUT_RESOLUTION" | jq -r '.shouldPersistFinalText')
    DELEGATE_SUCCEEDED=$(echo "$OUTPUT_RESOLUTION" | jq -r '.delegateSucceeded')
    PERSISTED_TEXT=$(echo "$OUTPUT_RESOLUTION" | jq -r '.persistedFinalText')
    
    if [[ "$EXISTING_STRUCTURED_OUTPUT" == "true" ]] || [[ "$FINAL_TEXT_HAS_FINAL" == "true" ]]; then
        ATTEMPT_RECORD=$(echo "$ATTEMPT_RECORD" | jq '.capturedFinalResult = true')
    fi
    if [[ "$OUTPUT_WAS_NORMALIZED" == "true" ]]; then
        ATTEMPT_RECORD=$(echo "$ATTEMPT_RECORD" | jq '.capturedFinalResult = true | .outputWasNormalized = true')
    fi
    
    break
done

if [[ -z "$OUTPUT_RESOLUTION" ]]; then
    OUTPUT_RESOLUTION=$(get_claude_delegate_output_resolution \
        "$FINAL_TEXT" \
        "$RESOLVED_OUTPUT_PATH" \
        "$EXIT_CODE" \
        "false" \
        "false")
fi

SHOULD_PERSIST=$(echo "$OUTPUT_RESOLUTION" | jq -r '.shouldPersistFinalText')
PERSISTED_TEXT=$(echo "$OUTPUT_RESOLUTION" | jq -r '.persistedFinalText')

if [[ "$SHOULD_PERSIST" == "true" ]]; then
    echo "$PERSISTED_TEXT" > "$RESOLVED_OUTPUT_PATH"
elif [[ ! -f "$RESOLVED_OUTPUT_PATH" ]]; then
    echo "Claude delegate finished without a structured text result." > "$RESOLVED_OUTPUT_PATH"
fi

cp "$RAW_STREAM_TMP" "$RAW_STREAM_PATH"
cp "$TRACE_TMP" "$TRACE_PATH"
rm -f "$RAW_STREAM_TMP" "$TRACE_TMP" "$CAPTURE_STATE_FILE"

OUTPUT_HAS_FINAL=$(test_claude_delegate_has_final_result "$RESOLVED_OUTPUT_PATH")

OUTPUT_BYTES=$(wc -c < "$RESOLVED_OUTPUT_PATH" 2>/dev/null || echo 0)

if [[ "$DELEGATE_SUCCEEDED" == "true" ]] && [[ "$OUTPUT_HAS_FINAL" == "true" ]]; then
    FINAL_STATUS="completed"
else
    FINAL_STATUS="failed"
fi

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
cat > "$STATUS_PATH" <<EOF
{
  "artifactSchema": $ARTIFACT_SCHEMA_VERSION,
  "invocationContract": "$INVOCATION_CONTRACT",
  "childThreadMarkerName": "$REQUIRED_CHILD_THREAD_MARKER_NAME",
  "childThreadMarkerValidated": true,
  "runId": "$RUN_ID",
  "status": "$FINAL_STATUS",
  "pid": $$,
  "outputPath": "$RESOLVED_OUTPUT_PATH",
  "promptPath": "$PROMPT_PATH",
  "rawStreamPath": "$RAW_STREAM_PATH",
  "tracePath": "$TRACE_PATH",
  "linesWritten": $(wc -l < "$RAW_STREAM_PATH" 2>/dev/null || echo 0),
  "outputBytes": $OUTPUT_BYTES,
  "exitCode": $EXIT_CODE,
  "attemptCount": $ATTEMPT,
  "retryCount": $RETRY_COUNT,
  "maxRetryCount": $MAX_RETRY_COUNT,
  "outputWasNormalized": $OUTPUT_WAS_NORMALIZED,
  "failureDisposition": ${FAILURE_DISPOSITION:+\"$FAILURE_DISPOSITION\"},
  "failureSummary": ${FAILURE_SUMMARY:+\"$FAILURE_SUMMARY\"},
  "attempts": [],
  "updatedAt": "$NOW"
}
EOF

if [[ $EXIT_CODE -ne 0 ]]; then
    if [[ "$FAILURE_DISPOSITION" == "NEED_HUMAN_INTERVENTION" ]]; then
        echo "Claude delegate retry ceiling reached: $FAILURE_SUMMARY" >&2
        exit 1
    fi
    echo "Claude Code exited with code $EXIT_CODE" >&2
    exit 1
fi

if [[ "$FINAL_STATUS" != "completed" ]]; then
    if [[ "$FAILURE_DISPOSITION" == "NEED_HUMAN_INTERVENTION" ]]; then
        echo "Claude delegate retry ceiling reached: $FAILURE_SUMMARY" >&2
        exit 1
    fi
    echo "Claude Code finished without a valid structured Final Result report. Output: $RESOLVED_OUTPUT_PATH" >&2
    exit 1
fi

release_claude_session_lease \
    "$SESSION_STATE_PATH" \
    "$SESSION_STATE_LOCK_PATH" \
    "$EFFECTIVE_SESSION_KEY" \
    "$SESSION_LEASE" \
    "$RUN_ID" \
    "$TASK_FINGERPRINT"
