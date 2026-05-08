from __future__ import annotations

import argparse
import os
import sys
from typing import Callable

from .artifacts import run_verify_artifacts, run_verify_chain
from .common import DelegateError
from .delegate import run_delegate
from .installer import run_install
from .opencode_delegate import run_opencode_delegate
from .real_chain import run_real_chain_validation
from .selftests import run_test_runtime, run_test_session_pool



def choice_arg(choices: list[str]) -> Callable[[str], str]:
    lookup = {choice.lower(): choice for choice in choices}

    def parse(value: str) -> str:
        selected = lookup.get(value.lower())
        if selected is None:
            expected = ", ".join(choices)
            raise argparse.ArgumentTypeError(f"invalid choice: {value!r} (choose from {expected})")
        return selected

    return parse



def int_range_arg(name: str, minimum: int, maximum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if parsed < minimum or parsed > maximum:
            raise argparse.ArgumentTypeError(f"{name} must be between {minimum} and {maximum}")
        return parsed

    return parse



def add_delegate_args(parser: argparse.ArgumentParser) -> None:
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument("-Task", dest="task")
    task_group.add_argument("-TaskFile", dest="task_file")
    parser.add_argument("-Scope", dest="scope", action="append", default=[])
    parser.add_argument("-Tests", dest="tests", action="append", default=[])
    parser.add_argument("-Mode", dest="mode", type=choice_arg(["Implement", "Fix", "Review"]), default="Implement")
    parser.add_argument("-Model", dest="model", default="")
    parser.add_argument("-Name", dest="name")
    parser.add_argument("-NamePrefix", dest="name_prefix", default="codex-delegate")
    parser.add_argument("-MaxBudgetUsd", dest="max_budget_usd")
    parser.add_argument("-ArtifactRoot", dest="artifact_root")
    parser.add_argument("-OutputPath", dest="output_path")
    parser.add_argument("-AllowParallel", dest="allow_parallel", action="store_true")
    parser.add_argument("-SessionMode", dest="session_mode", type=choice_arg(["PrimaryReuse", "PrimaryAnchor", "ParallelPool"]), default="PrimaryReuse")
    parser.add_argument("-SessionKey", dest="session_key")
    parser.add_argument("-SessionLeaseTimeoutSeconds", dest="session_lease_timeout_seconds", type=int, default=21600)
    parser.add_argument("-SessionLeaseWaitSeconds", dest="session_lease_wait_seconds", type=int, default=120)
    parser.add_argument("-ResetPrimarySession", dest="reset_primary_session", action="store_true")
    parser.add_argument("-ResetParallelPool", dest="reset_parallel_pool", action="store_true")
    parser.add_argument("-LockTimeoutSeconds", dest="lock_timeout_seconds", type=int, default=120)
    parser.add_argument("-LockPollMilliseconds", dest="lock_poll_milliseconds", type=int, default=500)
    parser.add_argument("-MaxRetryCount", dest="max_retry_count", type=int_range_arg("MaxRetryCount", 0, 100), default=5)
    parser.add_argument("-BypassPermissions", dest="bypass_permissions", action="store_true")
    parser.add_argument("-DryRun", dest="dry_run", action="store_true")



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex_with_cc scripts")
    sub = parser.add_subparsers(dest="command", required=True)
    delegate = sub.add_parser("delegate")
    add_delegate_args(delegate)
    delegate.set_defaults(func=run_delegate)

    opencode = sub.add_parser("opencode")
    opencode_sub = opencode.add_subparsers(dest="opencode_command", required=True)
    opencode_delegate = opencode_sub.add_parser("delegate_task")
    add_delegate_args(opencode_delegate)
    opencode_delegate.add_argument("-Variant", dest="variant")
    opencode_delegate.set_defaults(func=run_opencode_delegate)

    verify = sub.add_parser("verify-artifacts")
    verify.add_argument("-RunId", dest="run_id", required=True)
    verify.add_argument("-ArtifactRoot", dest="artifact_root")
    verify.set_defaults(func=run_verify_artifacts)

    chain = sub.add_parser("verify-chain")
    chain.add_argument("-ArtifactRoot", dest="artifact_root", required=True)
    chain.add_argument("-SessionKey", dest="session_key", required=True)
    chain.add_argument("-AnchorRunId", dest="anchor_run_id", required=True)
    chain.add_argument("-ParallelRunIds", dest="parallel_run_ids", nargs="+", required=True)
    chain.add_argument("-ReuseRunIds", dest="reuse_run_ids", nargs="+", required=True)
    chain.set_defaults(func=run_verify_chain)

    validation = sub.add_parser("run-real-chain-validation")
    validation.add_argument("-ValidationRoot", dest="validation_root")
    validation.add_argument("-Name", dest="name")
    validation.add_argument("-SessionKey", dest="session_key")
    validation.set_defaults(func=run_real_chain_validation)

    install = sub.add_parser("install")
    install.add_argument("--target-root", "-TargetRoot", dest="target_root", default=os.getcwd())
    install.add_argument("--platform", "-Platform", dest="platform", default="Auto")
    install.add_argument("--skip-agent-entrypoints", "-SkipAgentEntrypoints", dest="skip_agent_entrypoints", action="store_true")
    install.set_defaults(func=run_install)

    sub.add_parser("test-runtime").set_defaults(func=run_test_runtime)
    sub.add_parser("test-session-pool").set_defaults(func=run_test_session_pool)
    return parser



def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    try:
        return int(ns.func(ns))
    except DelegateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
