#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / "skills" / "codex-with-cc"
WIN = WORKFLOW / "windows_scripts"


def run_pwsh(script: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script), *args],
        cwd=REPO,
        text=True,
        capture_output=True,
        env=merged_env,
    )


def run_pwsh_command(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        cwd=REPO,
        text=True,
        capture_output=True,
    )


def ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_id_from_output(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("RunId:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"RunId line missing from output:\n{output}")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_fake_claude_bin(root: Path) -> Path:
    fake_bin = root / "fake-claude-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    report = "\n".join(
        (
            "Process Log",
            "- inspected the wrapper chain",
            "",
            "Summary",
            "Fake Claude completed the wrapper test.",
            "",
            "Changed Files",
            "None",
            "",
            "Verification",
            "- fake verification passed",
            "",
            "Final Result",
            "PASS",
            "",
            "Risks Or Follow-ups",
            "None",
        )
    )
    assistant_record = json.dumps(
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": report}]}},
        separators=(",", ":"),
    )
    result_record = json.dumps({"type": "result", "subtype": "success"}, separators=(",", ":"))
    (fake_bin / "claude.cmd").write_text(
        '@echo off\n'
        'more > nul\n'
        f"echo {assistant_record}\n"
        f"echo {result_record}\n"
        "exit /b 0\n",
        encoding="utf-8",
    )
    return fake_bin


def test_delegate_wrapper_is_thin_and_forwards_to_python() -> None:
    scripts = WORKFLOW / "scripts"
    assert (scripts / "delegate_to_claude.py").exists()
    assert (scripts / "runtime.py").exists()
    assert (WIN / "_runtime.ps1").exists()

    delegate_text = (WIN / "delegate_to_claude.ps1").read_text(encoding="utf-8")
    assert "param(" not in delegate_text
    assert delegate_text.count("\n") <= 2
    assert "Invoke-CodexWithCcRuntime" in delegate_text
    assert "delegate_to_claude.py" in delegate_text

    with tempfile.TemporaryDirectory(prefix="codex_with_cc_wrapper_") as tmp:
        artifact_root = Path(tmp) / "artifacts"
        result = run_pwsh(
            WIN / "delegate_to_claude.ps1",
            "-Task",
            "wrapper forwarding dry run",
            "-Scope",
            "alpha;beta;gamma",
            "-Tests",
            "pytest;git diff --check",
            "-Mode",
            "review",
            "-Model",
            "sonnet",
            "-NamePrefix",
            "wrapper-test",
            "-MaxBudgetUsd",
            "0.35",
            "-ArtifactRoot",
            str(artifact_root),
            "-AllowParallel",
            "-SessionMode",
            "parallelpool",
            "-SessionKey",
            "wrapper-session",
            "-SessionLeaseTimeoutSeconds",
            "60",
            "-SessionLeaseWaitSeconds",
            "0",
            "-ResetParallelPool",
            "-LockTimeoutSeconds",
            "0",
            "-LockPollMilliseconds",
            "50",
            "-MaxRetryCount",
            "0",
            "-BypassPermissions",
            "-DryRun",
            env={"CODEX_CLAUDE_CHILD_THREAD": "1"},
        )
        assert result.returncode == 0, result.stdout + result.stderr
        run_id = run_id_from_output(result.stdout)
        config = load_json(artifact_root / f"config_{run_id}.json")
        prompt = (artifact_root / f"prompt_{run_id}.md").read_text(encoding="utf-8")

        assert config["mode"] == "Review"
        assert config["maxBudgetUsd"] == "0.35"
        assert config["allowParallel"] is True
        assert config["sessionMode"] == "ParallelPool"
        assert config["sessionKey"] == "wrapper-session"
        assert config["maxRetryCount"] == 0
        assert config["bypassPermissions"] is True
        assert "- alpha\n- beta\n- gamma" in prompt
        assert "- pytest\n- git diff --check" in prompt

        task_file = Path(tmp) / "task.md"
        task_file.write_text("task file forwarding dry run", encoding="utf-8")
        file_artifact_root = Path(tmp) / "file-artifacts"
        file_result = run_pwsh(
            WIN / "delegate_to_claude.ps1",
            "-TaskFile",
            str(task_file),
            "-ArtifactRoot",
            str(file_artifact_root),
            "-SessionKey",
            "wrapper-file-session",
            "-DryRun",
            env={"CODEX_CLAUDE_CHILD_THREAD": "1"},
        )
        assert file_result.returncode == 0, file_result.stdout + file_result.stderr
        file_run_id = run_id_from_output(file_result.stdout)
        file_config = load_json(file_artifact_root / f"config_{file_run_id}.json")
        file_prompt = (file_artifact_root / f"prompt_{file_run_id}.md").read_text(encoding="utf-8")
        assert file_config["taskFile"] == str(task_file.resolve())
        assert "task file forwarding dry run" in file_prompt

        invalid_retry = run_pwsh(
            WIN / "delegate_to_claude.ps1",
            "-Task",
            "invalid retry",
            "-ArtifactRoot",
            str(artifact_root),
            "-SessionKey",
            "wrapper-invalid-retry",
            "-MaxRetryCount",
            "101",
            "-DryRun",
            env={"CODEX_CLAUDE_CHILD_THREAD": "1"},
        )
        assert invalid_retry.returncode != 0
        assert "MaxRetryCount" in (invalid_retry.stdout + invalid_retry.stderr)


def assert_windows_wrapper_is_thin(script_name: str, python_script: str) -> None:
    wrapper_text = (WIN / script_name).read_text(encoding="utf-8")
    assert "param(" not in wrapper_text
    assert wrapper_text.count("\n") <= 2
    assert "Invoke-CodexWithCcRuntime" in wrapper_text
    assert python_script in wrapper_text


def test_windows_artifact_and_chain_wrappers_forward_to_python() -> None:
    assert_windows_wrapper_is_thin("verify_delegate_artifacts.ps1", "verify_delegate_artifacts.py")
    assert_windows_wrapper_is_thin("verify_delegate_chain.ps1", "verify_delegate_chain.py")
    assert_windows_wrapper_is_thin("run_real_delegate_chain_validation.ps1", "run_real_delegate_chain_validation.py")

    with tempfile.TemporaryDirectory(prefix="codex_with_cc_chain_wrapper_") as tmp:
        root = Path(tmp)
        artifact_root = root / "artifacts"
        fake_bin = make_fake_claude_bin(root)
        env = {
            "CODEX_CLAUDE_CHILD_THREAD": "1",
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        }

        def delegate(name: str, mode: str, *extra: str) -> str:
            result = run_pwsh(
                WIN / "delegate_to_claude.ps1",
                "-Task",
                name,
                "-ArtifactRoot",
                str(artifact_root),
                "-SessionKey",
                "chain-wrapper-session",
                "-SessionMode",
                mode,
                *extra,
                env=env,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            return run_id_from_output(result.stdout)

        anchor = delegate("anchor", "PrimaryAnchor", "-AllowParallel")
        parallel_a = delegate("parallel a", "ParallelPool", "-AllowParallel")
        parallel_b = delegate("parallel b", "ParallelPool", "-AllowParallel")
        reuse_1 = delegate("reuse one", "PrimaryReuse")
        reuse_2 = delegate("reuse two", "PrimaryReuse")

        verify = run_pwsh(
            WIN / "verify_delegate_artifacts.ps1",
            "-RunId",
            anchor,
            "-ArtifactRoot",
            str(artifact_root),
        )
        assert verify.returncode == 0, verify.stdout + verify.stderr
        assert f"Artifact verification passed for RunId: {anchor}" in verify.stdout

        chain = run_pwsh(
            WIN / "verify_delegate_chain.ps1",
            "-ArtifactRoot",
            str(artifact_root),
            "-SessionKey",
            "chain-wrapper-session",
            "-AnchorRunId",
            anchor,
            "-ParallelRunIds",
            f"{parallel_a},{parallel_b}",
            "-ReuseRunIds",
            f"{reuse_1},{reuse_2}",
        )
        assert chain.returncode == 0, chain.stdout + chain.stderr
        summary = json.loads(chain.stdout)
        assert summary["artifactContractValid"] is True
        assert summary["chainPassed"] is True

        array_chain = run_pwsh_command(
            "& "
            + ps_quote(WIN / "verify_delegate_chain.ps1")
            + " -ArtifactRoot "
            + ps_quote(artifact_root)
            + " -SessionKey 'chain-wrapper-session'"
            + " -AnchorRunId "
            + ps_quote(anchor)
            + " -ParallelRunIds @("
            + ps_quote(parallel_a)
            + ","
            + ps_quote(parallel_b)
            + ") -ReuseRunIds @("
            + ps_quote(reuse_1)
            + ","
            + ps_quote(reuse_2)
            + ")"
        )
        assert array_chain.returncode == 0, array_chain.stdout + array_chain.stderr


def test_runtime_helpers_do_not_reference_removed_install_scripts() -> None:
    win_runtime = (WIN / "_runtime.ps1").read_text(encoding="utf-8")
    mac_runtime = (WORKFLOW / "macos_scripts" / "_runtime.sh").read_text(encoding="utf-8")
    legacy_installer_stem = "_".join(("install", "codex", "with", "cc"))
    assert legacy_installer_stem not in win_runtime
    assert legacy_installer_stem not in mac_runtime


if __name__ == "__main__":
    test_delegate_wrapper_is_thin_and_forwards_to_python()
    test_windows_artifact_and_chain_wrappers_forward_to_python()
    test_runtime_helpers_do_not_reference_removed_install_scripts()
    print("windows wrapper forwarding tests passed")
