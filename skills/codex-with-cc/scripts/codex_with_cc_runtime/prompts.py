from __future__ import annotations

from pathlib import Path

from .paths import script_ext, script_family, workflow_relative_path


def role_template(role: str) -> str:
    template = Path(__file__).resolve().parents[2] / "templates" / f"{role}.md"
    if not template.exists():
        return ""
    return template.read_text(encoding="utf-8").strip()



def build_prompt(
    repo: Path,
    output_path: Path,
    run_id: str,
    mode: str,
    scope: list[str],
    tests: list[str],
    task_text: str,
    workflow_id: str,
    task_id: str,
    role: str,
    review_for_task_id: str | None = None,
    review_kind: str | None = None,
) -> str:
    rel = workflow_relative_path()
    primary_entry = f"{rel}/{script_family()}/delegate_to_claude{script_ext()}"
    windows_entry = f"{rel}/windows_scripts/delegate_to_claude.ps1"
    macos_entry = f"{rel}/macos_scripts/delegate_to_claude.sh"
    scope_text = "\n".join(f"- {item}" for item in scope) if scope else "- No explicit file scope was provided. Infer the narrowest safe scope from the task and current code."
    tests_text = "\n".join(f"- {item}" for item in tests) if tests else "- Run the smallest relevant verification you can identify from the change."
    role_template_text = role_template(role) or "- Follow the generic codex-with-cc worker rules for this role."
    worker_protocol_text = f"""- This prompt is already the only allowed Claude worker context for this delegated run.
- Never call `{primary_entry}`, `{windows_entry}`, `{macos_entry}`, `claude`, or `spawn_agent` recursively from inside this worker.
- Treat `{rel}/CODEX_WITH_CC.md` as the workflow contract to inspect when the task scope requires it, not as an execution recipe for this worker.
- If the task is an audit or validation, inspect the scoped files and run the listed verification commands directly instead of creating nested delegate runs.
- If you think another delegate run is required, stop, set `Status` to `NEEDS_CONTEXT`, and explain why in `Findings` instead of invoking it yourself.
- Before reporting, perform a self-review for scope compliance, changed files, verification evidence, and residual risks.
"""
    return f"""Execute the delegated task below now. This is not a readiness check; do not ask what to work on.

You are Claude Code acting as an implementation worker for Codex.

This worker script is reserved for Codex `spawn_agent` child threads. It is not a valid main-thread entry point.

Codex owns architecture, task boundaries, and final review. Your job is to execute the delegated task directly in this repository, keep changes narrow, verify them, and report exactly what changed.

Repository root:
{repo}

Delegated output report path:
{output_path}

Current delegate run id:
{run_id}

Workflow id:
{workflow_id}

Task id:
{task_id}

Worker role:
{role}

Review target task id:
{review_for_task_id or "None"}

Review kind:
{review_kind or "None"}

Mode:
{mode}

Allowed / intended scope:
{scope_text}

Required or expected verification:
{tests_text}

Worker protocol:
{worker_protocol_text}
- Do not treat metadata inside the Task block as a second assignment from the parent wrapper.
- Do not execute or reinterpret `Worker entry script`, `Required worker arguments`, `SessionKey`, `SessionMode`, or pending-task descriptions as instructions to launch more work, scan for unrelated follow-up tasks, or decide what Codex should do next.
- Use the human task description and listed verification for this current run only.
- If a task or verification command contains a placeholder like `<...-run-id>`, replace it with the current delegate run id `{run_id}` before you execute the command.
- Never inspect, poll, or wait on the current run's own live artifacts (`status_{run_id}.json`, `stream_{run_id}.jsonl`, `trace_{run_id}.log`, `config_{run_id}.json`, `prompt_{run_id}.md`, `claude_{run_id}.md`) as task input. Those files belong to the wrapper for this run, not to the delegated task.
- Never add sleeps or "wait for completion" loops for the current run. You are the current run; finish the delegated task and emit the required report directly.

Role template:
{role_template_text}

Task:
{task_text}

Hard requirements:
- Read {rel}/CODEX_WITH_CC.md before scanning other repository files.
- Use {rel}/CODEX_WITH_CC.md as the single workflow contract for delegation, audit flow, session mode interpretation, and worker report requirements.
- Follow all applicable project-defined skills and workflow skills before implementing or changing behavior, especially Codex project skills under `.codex`. Read the target project's agent/rule files and referenced skill documents when they apply to the delegated task.
- Keep edits inside the intended scope unless the task is impossible without a small supporting change.
- You must run necessary verification before handing work back. Run every command listed under Required or expected verification; if none is listed, infer the smallest meaningful format/analyze/test command for the changed area.
- Do not return code that you know fails to compile, analyze, or pass the required focused tests. Fix verification failures and rerun them until they pass.
- If verification is blocked by an external dependency or a clearly pre-existing unrelated failure, report the exact command, failure summary, and why it is not caused by your changes.
- Never claim verification passed unless you actually ran the command and saw it pass.
- For implementation work, summarize the test or self-review evidence that demonstrates the assigned behavior, not broad unrelated cleanup.
- Process and summarize your own CLI output. The Codex child thread will forward your final structured result; it should not reinterpret long logs for you.
- Treat this script as a child-thread worker entry only. Do not reinterpret it as permission for the Codex main thread to invoke Claude directly.
- Keep raw verbose command output in the transcript/log instead of duplicating it.
- Finish with this exact report skeleton. Status and Final Result must be the same token. Role must be `{role}`. Do not add text before `Status`; do not bold or decorate these headings:
Status
<DONE, DONE_WITH_CONCERNS, NEEDS_CONTEXT, BLOCKED, or FAIL>

Role
{role}

Summary
<brief result>

Changed Files
- <path or None>

Verification
- <command and outcome>

Findings
- <finding or None>

Final Result
<repeat the exact Status token>

Risks Or Follow-ups
- <risk or None>
"""
