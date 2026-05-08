# 设计方案：Codex → OpenCode 委派

## 1. 概述

本文档提出在 `codex-with-cc` 工作流中新增 OpenCode 作为与 Claude Code 并列的受支持执行器。当前工作流链路为：

```
Codex 主线程 → Codex spawn_agent 子线程 → delegate_to_claude.* → Claude Code CLI
```

目标是增加一条并行链路：

```
Codex 主线程 → Codex spawn_agent 子线程 → delegate_to_opencode.* → OpenCode CLI
```

Codex 可根据任务特征、模型可用性或用户显式偏好，选择调度到 Claude Code 还是 OpenCode。

## 2. 当前架构（Claude Code 路径）

### 2.1 文件布局

| 层级 | 路径 | 用途 |
|-------|------|---------|
| 入口脚本 | `{macos,windows}_scripts/delegate_to_claude.{sh,ps1}` | 2行包装 → `_runtime.sh delegate_to_claude.py` |
| Python 入口 | `scripts/delegate_to_claude.py` | 薄层：`main(["delegate", *args])` |
| 运行时核心 | `scripts/runtime.py` (~2489 行) | 共享逻辑：会话池、文件锁、流解析、重试、报告校验 |

### 2.2 runtime.py 中的关键常量

```python
CHILD_MARKER_NAME = "CODEX_CLAUDE_CHILD_THREAD"
CHILD_MARKER_VALUE = "1"
SKILL_NAME = "codex-with-cc"
```

### 2.3 Claude 专用代码点

所有 Claude 专用代码均位于 `scripts/runtime.py` 中。以下是需要变体的部分：

| 代码点 | 行号 | 作用 | OpenCode 对应 |
|------------|-------|-------------|---------------------|
| `CHILD_MARKER_NAME` | 24 | 入口守卫：`CODEX_CLAUDE_CHILD_THREAD=1` | 需要 `CODEX_OPENCODE_CHILD_THREAD=1` |
| `new_claude_cli_args()` | 786-813 | 为 claude 二进制构建命令行参数 | `new_opencode_cli_args()` |
| `update_stream_capture()` | 734-783 | 解析 Claude 的 `stream-json` 输出 | `update_opencode_stream_capture()` |
| `retry_decision()` | 828-866 | 检测 Claude 专用错误（过期会话、stream-json） | OpenCode 专用错误模式 |
| `build_prompt()` | 885-964 | 构建提及 "Claude Code" 的委派提示词 | OpenCode 风格变体 |
| `run_delegate()` | 1046-1486 | 核心委派：查找二进制、调用子进程 | 使用 `shutil.which("opencode")` |
| `delegate_to_claude.py` | 3行 | 入口：`main(["delegate", ...])` | `delegate_to_opencode.py` |
| 产物命名 | - | `claude_*.md`、`claude-delegate/` 目录 | `opencode_*.md`、`opencode-delegate/` 目录 |
| `verify_artifacts()` | 1489 | 默认产物根目录包含 `claude-delegate` | 各执行器各自的根目录 |

### 2.4 当前子命令路由

`runtime.main()` 通过 argparse 使用单一的 `delegate` 子命令 → `run_delegate()`。这需要变为执行器感知。

## 3. OpenCode CLI 接口分析

### 3.1 `opencode run` — 程序化执行

```
opencode run [message] [options]
```

与委派相关的关键标志：

| 标志 | 用途 | Claude 对应 |
|------|---------|-------------------|
| `--format json` | 行分隔 JSON 输出 | `--output-format stream-json` |
| `--model provider/model` | 模型选择 | `--model <name>` |
| `--session <id>` | 继续指定会话 | `--session-id <id>` / `--resume` |
| `--continue` | 继续上一次会话 | 无直接等价项 |
| `--dangerously-skip-permissions` | 自动批准权限 | 相同 |
| `--variant` | 推理深度（high/max/minimal） | 由模型隐式决定 |
| `--title` | 会话标题 | `--name` |
| `--thinking` | 显示思考块 | `--verbose`（部分等价） |
| `--dir` | 工作目录（用于远程） | N/A（总是设置 cwd） |

### 3.2 OpenCode JSON 输出格式

OpenCode 的 `--format json` 产生行分隔的 JSON 事件。观察到的示例事件类型：

```json
{"type":"step_start", ...}
{"type":"text", "part":{"type":"text","text":"hello world",...}}
{"type":"tool_use", "part":{"type":"tool","tool":"bash",...}}
{"type":"step_finish", ...}
```

关键观察：
- `step_start` / `step_finish` 包围每个推理步骤
- `text` 事件承载助手文本输出（类似于 Claude 的 `assistant` + `message.content[].text`）
- `tool_use` 事件承载工具调用（类似于 Claude 的某些流事件，但结构不同）
- 没有与 Claude 的 `result` 事件中 `subtype: success` 的直接等价项；成功完成由进程以退出码 0 退出来表示
- `step_finish` 包含 token 用量和费用信息（类似于 Claude 的 `result` 事件中的费用详情）
- **没有** `system` 事件类型；状态消息通过其他方式传达

### 3.3 会话管理

```
opencode session list                  # 列出会话
opencode session delete <sessionID>    # 删除会话
```

OpenCode 会话由类似 `ses_1f9cbf99fffeS4hvA5M5tjydLq` 的 ID 标识。`opencode run` 的 `--session` 标志用于恢复特定会话。`--fork` 标志创建会话的分支（类似 git 分支）。

OpenCode 没有直接的 `--session-id` 标志用于以已知 ID 创建新会话。新会话在运行时如果不使用 `--session` 或 `--continue` 标志则自动创建，会话 ID 从 JSON 输出流中获取（在 `step_start` 事件中）。

### 3.4 二进制检测

`shutil.which("opencode")` — 与检测 Claude Code 的方式完全相同。

## 4. 关键差异及设计影响

### 4.1 输出流解析

| 方面 | Claude Code | OpenCode |
|--------|-------------|----------|
| JSON 格式 | `stream-json` | `--format json` |
| 成功信号 | `result` 事件，`subtype: success` | 进程退出码 0（无显式成功事件） |
| 助手文本 | `assistant` → `message.content[].text` | `text` → `part.text` |
| 错误模式 | `"No conversation found.*session ID"`、`"stream-json.*requires.*--verbose"` | 需要观察发现 OpenCode 的错误模式 |
| 流式输出 | 执行期间持续输出 | 相同（行分隔 JSON） |

**影响**：`update_stream_capture()` 需要变体。OpenCode 的成功检测更简单（仅检查退出码），但我们失去了 Claude 提供的结构化成功信号。应改为检查助手文本 + 退出码 0。

### 4.2 会话生命周期

| 方面 | Claude Code | OpenCode |
|--------|-------------|----------|
| 新会话创建 | `--session-id <new-id>` | 隐式创建（无标志） |
| 会话恢复 | `--resume <id>` | `--session <id>` |
| 会话 ID 发现 | 从 CLI 参数获取 | 从 JSON 流中获取（`step_start.sessionID`） |
| 会话分支 | 不支持 | `--fork` |
| 会话列表 | 外部工具 | `opencode session list` |

**影响**：会话池方案（获取租约 → 传递会话 ID → 释放）工作方式类似，但参数映射不同。对于新会话，OpenCode 内部创建会话；我们从第一个 `step_start` 事件中发现 ID。对于恢复，我们传递 `--session <id>`。

这意味着会话池必须存储**从输出流中发现**的会话 ID，而非**预先分配**的（来自 CLI 参数）。对于某个会话键的首次运行，我们省略 `--session`。后续运行时，我们传递 `--session <stored_id>`。

**替代方案**：预先生成会话 ID。但 OpenCode 不接受预分配的 ID。因此需要从流中发现。

### 4.3 CLI 参数映射

```
Claude:                            OpenCode:
--verbose                          （无精确等价项；--print-logs 用于 stderr）
--print                            （无等价项；--format json 中转义隐含）
--output-format stream-json        --format json
--model <name>                     --model provider/model
--name <name>                      --title <title>
--session-id <id>                  （无标志，自动分配新会话）
--resume <id>                      --session <id>
--permission-mode acceptEdits      （OpenCode 按工具处理权限）
--dangerously-skip-permissions     --dangerously-skip-permissions
--max-budget-usd <n>               （无直接预算标志）
<prompt_text>                      <prompt_text>（位置参数）
```

### 4.4 重试逻辑差异

| 条件 | Claude 检测 | OpenCode 检测 |
|-----------|-----------------|--------------------|
| 过期会话 | 非 JSON 行中匹配 `"No conversation found.*session ID"` | 待定 — 观察 OpenCode 在会话被删除/丢失时的行为 |
| 流错误 | `"stream-json.*requires.*--verbose"` | 待定 — 观察 OpenCode 启动错误 |
| 无结构成功 | 退出码 0 + 看到文本但无报告标题 | 相同模式适用 |

**影响**：`retry_decision()` 需要执行器感知的变体。在首次实现中，可以先为 OpenCode 使用较简单的重试模型（仅处理无结构成功检测），随着观察 OpenCode 的失败模式再逐步添加过期/流错误检测。

## 5. 提出的架构

### 5.1 设计原则：执行器策略模式

不采用完全复制 `run_delegate()` 的方式，而是引入执行器专属的策略对象，封装：
- CLI 参数构建
- 流输出解析
- 重试决策逻辑
- 提示词构建（次关键，大部分可共享）

会话池、文件锁、产物写入、报告校验——全部保持共享。

### 5.2 子命令设计

在 `runtime.py` 中新增顶级子命令：

```
runtime.py opencode delegate_task  # 新增：调度到 OpenCode
runtime.py delegate               # 现有：调度到 Claude Code（不变）
```

`opencode` 子命令拥有自己的参数组，镜像 `delegate` 的参数，并加上 OpenCode 专属的补充参数（如 `-Variant`）。

### 5.3 文件布局变化

```
skills/codex-with-cc/
├── agents/
│   └── openai.yaml                    # [修改] 添加 OpenCode 触发词
├── CODEX_WITH_CC.md                   # [修改] 添加 OpenCode 链路文档
├── SKILL.md                           # [修改] 添加 OpenCode 执行器文档
├── scripts/
│   ├── runtime.py                     # [修改] 添加 OpenCode 策略 + opencode 子命令
│   ├── delegate_to_claude.py          # [不变]
│   ├── delegate_to_opencode.py        # [新增] 薄层：main(["opencode", "delegate_task", *args])
│   └── ... (其他脚本不变)
├── macos_scripts/
│   ├── delegate_to_claude.sh          # [不变]
│   ├── delegate_to_opencode.sh        # [新增] 2行包装
│   └── ... (其他不变)
└── windows_scripts/
    ├── delegate_to_claude.ps1         # [不变]
    ├── delegate_to_opencode.ps1       # [新增] 2行包装
    └── ... (其他不变)
```

### 5.4 数据模型变更

**常量**（添加到 runtime.py）：

```python
OPENCODE_CHILD_MARKER_NAME = "CODEX_OPENCODE_CHILD_THREAD"
OPENCODE_CHILD_MARKER_VALUE = "1"

DEFAULT_OPENCODE_ARTIFACT_DIR = "opencode-delegate"
DEFAULT_CLAUDE_ARTIFACT_DIR = "claude-delegate"
```

**各执行器的产物路径**：

| 执行器 | 产物根目录 | 输出模式 | 配置 | 状态 | 流 | 跟踪 |
|----------|-------------|----------------|--------|--------|--------|-------|
| Claude | `.../claude-delegate/` | `claude_<RunId>.md` | `config_<RunId>.json` | `status_<RunId>.json` | `stream_<RunId>.jsonl` | `trace_<RunId>.log` |
| OpenCode | `.../opencode-delegate/` | `opencode_<RunId>.md` | `config_<RunId>.json` | `status_<RunId>.json` | `stream_<RunId>.jsonl` | `trace_<RunId>.log` |

**会话池**：复用同一池目录 (`session-pools/`) 或各执行器独立池。**建议**：复用同一池，因为会话键已按任务命名空间隔离，不存在冲突。池中已存储 `sessionId` 值；Claude 和 OpenCode 的会话 ID 可通过格式区分（OpenCode 使用 `ses_*` 前缀）。

### 5.5 运行时模块重构

在 `runtime.py` 中引入执行器策略函数：

```python
# 新增：OpenCode 专用辅助函数

def new_opencode_cli_args(
    model: str,
    title: str,
    session_id: str | None,    # None = 新会话，str = 恢复
    bypass_permissions: bool,
    variant: str | None,
    prompt_text: str,
) -> list[str]:
    """构建 OpenCode CLI 参数。"""
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
    """解析 OpenCode JSON 输出记录。"""
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
    """OpenCode 专用重试决策。"""
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

    # OpenCode 无结构成功检测
    if exit_code == 0 and saw_step_finish and saw_assistant_text and not has_structured_success:
        decision.update({
            "shouldRetry": True,
            "retryReason": "unstructured_success_report",
            "retryWithFreshSession": False,
            "retryWithReportRepair": True,
        })

    return decision
```

### 5.6 委派函数结构

OpenCode 委派函数 (`run_opencode_delegate()`) 遵循与 `run_delegate()` 完全相同的结构，但有以下差异：

1. **标记检查**：使用 `OPENCODE_CHILD_MARKER_NAME` 而非 `CHILD_MARKER_NAME`
2. **产物根目录**：默认为 `.../opencode-delegate/` 而非 `.../claude-delegate/`
3. **输出文件前缀**：`opencode_<RunId>.md` 而非 `claude_<RunId>.md`
4. **二进制检测**：`shutil.which("opencode")` 而非 `shutil.which("claude")`
5. **CLI 参数**：使用 `new_opencode_cli_args()` 而非 `new_claude_cli_args()`
6. **流解析**：使用 `update_opencode_stream_capture()` 而非 `update_stream_capture()`
7. **重试逻辑**：使用 `retry_decision_opencode()` 而非 `retry_decision()`
8. **会话租约**：略有调整 —— 对于某键的首次运行，`sessionId=None`（新会话）；恢复时传递 `--session <stored_id>`。首次运行后将发现的会话 ID 写回池中。
9. **提示词**：使用 OpenCode 专用措辞（例如 "OpenCode worker" 而非 "Claude worker"）

**代码复用策略**：为避免过度重复，将 `run_delegate()` 的共享部分提取为参数化的辅助函数。执行器专属差异通过 `DelegateExecutor` dataclass 或一组回调函数捕获：

```python
@dataclasses.dataclass
class DelegateConfig:
    """委派运行的执行器专属配置。"""
    executor_name: str                    # "claude" 或 "opencode"
    child_marker_name: str                # "CODEX_CLAUDE_CHILD_THREAD" 或 "CODEX_OPENCODE_CHILD_THREAD"
    child_marker_value: str               # 始终为 "1"
    artifact_dir_name: str                # "claude-delegate" 或 "opencode-delegate"
    output_prefix: str                    # "claude_" 或 "opencode_"
    binary_name: str                      # "claude" 或 "opencode"
    build_cli_args: Callable[..., list[str]]   # new_claude_cli_args() 或 new_opencode_cli_args()
    parse_stream_record: Callable[..., list[str]]  # update_stream_capture() 或 update_opencode_stream_capture()
    make_retry_decision: Callable[..., dict[str, Any]]  # retry_decision() 或 retry_decision_opencode()
    build_prompt: Callable[..., str]       # build_prompt() 或 build_opencode_prompt()
```

然后将 `run_delegate()` 变为通用的 `run_delegate_with_config(cfg: DelegateConfig, ns)`。

**决策**：采用 `DelegateConfig` dataclass 方案。为向后兼容保留 `run_delegate()` 不变，添加 `run_delegate_with_config()` 作为共享实现，让 `run_delegate()` 和新的 `run_opencode_delegate()` 都成为构建 `DelegateConfig` 并调用共享函数的薄包装。这最小化了破坏现有 Claude Code 路径的风险。

### 5.7 提示词修改

OpenCode 提示词应使用 "OpenCode" 替代 "Claude Code" / "Claude"，并引用 `delegate_to_opencode.*` 替代 `delegate_to_claude.*`：

```python
def build_opencode_prompt(
    repo: Path,
    output_path: Path,
    mode: str,
    scope: list[str],
    tests: list[str],
    task_text: str,
) -> str:
    # 与 build_prompt() 结构相同，但：
    # - "You are OpenCode acting as an implementation worker for Codex."
    # - 引用 delegate_to_opencode.* 而非 delegate_to_claude.*
    # - 相同的报告标题契约
```

## 6. 入口点

### 6.1 Python 入口 (`scripts/delegate_to_opencode.py`)

```python
#!/usr/bin/env python3
import sys
from runtime import main

if __name__ == "__main__":
    raise SystemExit(main(["opencode", "delegate_task", *sys.argv[1:]]))
```

### 6.2 Shell 包装

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

### 6.3 运行时子命令注册

在 `build_parser()` 中：

```python
opencode = sub.add_parser("opencode")
opencode_sub = opencode.add_subparsers(dest="opencode_command", required=True)
opencode_delegate = opencode_sub.add_parser("delegate_task")
add_delegate_args(opencode_delegate)  # 复用相同的参数
opencode_delegate.add_argument("-Variant", dest="variant")  # OpenCode 专属
opencode_delegate.set_defaults(func=run_opencode_delegate)
```

## 7. OpenCode 会话管理

OpenCode 的会话模型需要略有不同的处理方式：

### 7.1 新会话流程

1. 从池中获取会话租约。对于全新键，池记录中 `sessionId` 可能为 `null`。
2. **不使用** `--session` 和 `--continue`，调用 `opencode run`。
3. 解析第一个 `step_start` 事件以发现分配的 `sessionID`。
4. 运行完成后：将发现的 `sessionID` 写回池中以供后续复用。

### 7.2 恢复会话流程

1. 从池中获取会话租约。池记录中包含 `sessionId: "ses_xxx"`。
2. 调用 `opencode run --session ses_xxx`。
3. 无需发现步骤；会话 ID 已从池中获知。

### 7.3 会话键

会话键保持不变（任务 + 范围 + 测试指纹）。池中存储的 `sessionId` 值可以是 Claude 风格或 OpenCode 风格。

## 8. 参数映射（用户侧）

`delegate_to_opencode.*` 入口脚本在适用处接受与 `delegate_to_claude.*` 相同的参数，并加上 OpenCode 专属的补充参数：

| 参数 | Claude 路径 | OpenCode 路径 | 备注 |
|----------|-------------|---------------|-------|
| `-Task` / `-TaskFile` | 支持 | 支持 | 相同 |
| `-Scope` | 支持 | 支持 | 相同 |
| `-Tests` | 支持 | 支持 | 相同 |
| `-Mode` | 支持 | 支持 | 相同（`Implement`/`Fix`/`Review`） |
| `-Model` | 支持 | 支持 | Claude: `"sonnet"`，OpenCode: `"provider/model"` |
| `-Name` | 支持 | 支持 | Claude: `--name`，OpenCode: `--title` |
| `-NamePrefix` | 支持 | 支持 | 相同 |
| `-MaxBudgetUsd` | 支持 | 不支持 | OpenCode 无预算标志；静默忽略 |
| `-ArtifactRoot` | 支持 | 支持 | 相同 |
| `-OutputPath` | 支持 | 支持 | 相同 |
| `-AllowParallel` | 支持 | 支持 | 相同 |
| `-SessionMode` | 支持 | 支持 | 相同（PrimaryReuse/PrimaryAnchor/ParallelPool） |
| `-SessionKey` | 支持 | 支持 | 相同 |
| `-SessionLeaseTimeoutSeconds` | 支持 | 支持 | 相同 |
| `-SessionLeaseWaitSeconds` | 支持 | 支持 | 相同 |
| `-ResetPrimarySession` | 支持 | 支持 | 相同 |
| `-ResetParallelPool` | 支持 | 支持 | 相同 |
| `-LockTimeoutSeconds` | 支持 | 支持 | 相同 |
| `-LockPollMilliseconds` | 支持 | 支持 | 相同 |
| `-MaxRetryCount` | 支持 | 支持 | 相同 |
| `-BypassPermissions` | 支持 | 支持 | 相同：`--dangerously-skip-permissions` |
| `-DryRun` | 支持 | 支持 | 相同 |
| `-Variant` | 不支持 | 支持 | OpenCode 专属：`--variant high/max/minimal` |

## 9. 文档变更

### 9.1 CODEX_WITH_CC.md

新增 OpenCode 委派链路章节，包括：
- OpenCode 专用链路 `Codex 主线程 → spawn_agent → delegate_to_opencode.* → OpenCode CLI`
- `CODEX_OPENCODE_CHILD_THREAD=1` 标记要求
- 标准 OpenCode 工作命令（macOS 和 Windows）
- OpenCode 特有的注意事项（模型格式、Variant 参数）

### 9.2 SKILL.md

- 更新描述以提及 OpenCode 作为支持的执行器
- 添加 OpenCode 触发词（如适用："opencode"、"opencode delegation"）
- 更新工作流契约以展示两条链路
- 更新工作报告契约（不变——同样的 6 个标题）
- 文档化 OpenCode 的 `-Variant` 参数

### 9.3 AGENTS.md

- 更新 `<!-- BEGIN/END CODEX_WITH_CC -->` 块以提及两个执行器，或保持执行器无关

## 10. 测试策略

### 10.1 运行时测试 (`scripts/test_delegate_runtime.py`)

添加 OpenCode 专用测试场景：
- 标记强制检查（需要 `CODEX_OPENCODE_CHILD_THREAD=1`）
- 使用 OpenCode 配置的干运行
- 使用伪造 OpenCode 二进制进行结构化/无结构输出测试
- 使用伪造二进制的重试逻辑测试

遵循与现有 Claude 测试相同的模式（伪造二进制方案）。

### 10.2 会话池测试 (`scripts/test_delegate_session_pool.py`)

大部分池逻辑是共享的且已经过测试。新增：
- 使用 OpenCode 风格会话 ID（`ses_*`）的会话池
- 从输出流中发现会话
- 使用已存储 ID 的会话恢复

### 10.3 单元测试（Python）

在 `tests/` 中添加针对以下内容的测试：
- `new_opencode_cli_args()` 参数构建
- `update_opencode_stream_capture()` 解析
- `retry_decision_opencode()` 决策

### 10.4 集成测试

- 使用真实 `opencode run` 调用的端到端测试（需要已安装 opencode）
- 链验证：锚点 → 并行 → 复用的 OpenCode 链路

## 11. 迁移与向后兼容性

### 11.1 保证

- **现有 Claude Code 工作流 100% 向后兼容** —— `delegate_to_claude.*` 行为无变化
- **现有产物不受影响** —— Claude 和 OpenCode 产物存放在不同目录中
- **现有会话池不受影响** —— OpenCode 会话使用相同的池格式，但会话 ID 模式不同

### 11.2 安装

安装器 (`run_install()`) 更新为：
- 同时复制 `delegate_to_claude.*` 和 `delegate_to_opencode.*` 入口脚本
- 在 macOS 上对两个 `.sh` 包装都执行 chmod

### 11.3 无破坏性变更

- 无参数重命名
- 现有 `delegate` 子命令行为无变化
- 产物格式和会话池格式无变化

## 12. 实现顺序

| 阶段 | 范围 | 涉及文件 |
|-------|-------|-------|
| 1. 常量和类型 | 向 runtime.py 添加 `DelegateConfig`、常量 | `scripts/runtime.py`（顶部区域） |
| 2. CLI 构建器 | `new_opencode_cli_args()` | `scripts/runtime.py` |
| 3. 流解析器 | `update_opencode_stream_capture()` | `scripts/runtime.py` |
| 4. 重试逻辑 | `retry_decision_opencode()` | `scripts/runtime.py` |
| 5. 提示词构建器 | `build_opencode_prompt()` | `scripts/runtime.py` |
| 6. 委派函数 | `run_opencode_delegate()` + 共享的 `run_delegate_with_config()` | `scripts/runtime.py` |
| 7. 子命令路由 | `build_parser()` 新增内容 | `scripts/runtime.py` |
| 8. 入口脚本 | `delegate_to_opencode.{py,sh,ps1}` | `scripts/`、`macos_scripts/`、`windows_scripts/` |
| 9. 文档 | CODEX_WITH_CC.md、SKILL.md、AGENTS.md | `skills/codex-with-cc/`、根目录 |
| 10. 测试 | 运行时测试、会话池测试、单元测试 | `scripts/`、`tests/` |

## 13. 待解决问题

1. **OpenCode 错误模式**：需要经验性地发现 OpenCode 在启动失败、过期会话等情况下发出的错误信息，以填充 `retry_decision_opencode()`。初始实现可以仅处理 `unstructured_success_report`，后续再添加其他模式。

2. **模型格式**：OpenCode 使用 `provider/model` 格式（如 `openai/gpt-5.3-codex`）。Claude 使用短名称（如 `sonnet`）。`-Model` 参数是否应根据执行器不同而有不同解释？**建议**：是的——当执行器为 opencode 时，`-Model` 期望 `provider/model` 格式；当执行器为 claude 时，期望短名称。

3. **会话 ID 发现竞态**：如果多个 `opencode run` 实例同时针对相同的全新会话键调用，存在两个实例发现不同会话 ID 的竞态条件。缓解措施：委派锁可防止非并行模式下的此问题；对于并行池模式，每个并行槽各自获得自己的会话 ID。

4. **并行池行为**：OpenCode 的并行池是否应与 Claude 的行为完全相同（可复用会话）？**建议**：是的——相同的会话池逻辑，只是使用 OpenCode 会话 ID。

## 14. 结论

本设计以最少的代码重复将 OpenCode 作为与 Claude Code 并列的执行器。策略模式（`DelegateConfig` dataclass）封装了执行器专属的差异，同时保留了所有共享基础设施（会话池、文件锁、产物写入、报告校验）。现有的 Claude Code 路径不受影响，确保零回归风险。
