#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REQUIRED_MODEL = "gpt-5.3-codex";
const REQUIRED_EFFORT = "medium";
const ALLOWED_ROLES = new Set(["planner", "implementer", "researcher", "reviewer", "final-verifier"]);

const TRIGGER_PATTERNS = [
  /child[- ]?agent/i,
  /sub[- ]?agent/i,
  /child[- ]?thread/i,
  /sub[- ]?thread/i,
  /delegat(?:e|ion|ing)/i,
  /worker[- ]?execution/i,
  /子代理|子线程|多代理|委派|派工|执行层/,
];

const SPAWN_TOOL_NAMES = new Set([
  "spawn_agent",
  "task",
  "subagent",
  "agent",
  "worker",
]);

const FALLBACK_CONTEXT = [
  "codex-with-cc platform subagent gate:",
  "- Any child-agent, subagent, child-thread, delegation, worker-execution, 子代理, 子线程, 多代理, 委派, 派工, or 执行层 request must use the codex-with-cc workflow.",
  "- Required chain: Codex main thread -> Codex spawn_agent child thread -> delegate_to_claude.* -> Claude Code CLI.",
  "- Do not use the default Codex subagent flow, a host-provided worker shortcut, direct claude execution, or direct main-thread delegate_to_claude.* execution.",
  "- Use the staged flow: plan task graph, dispatch bounded tasks, execute with scoped worker context, review spec compliance, review quality, then verify the workflow.",
  "- Child spawn metadata must be model: gpt-5.3-codex, reasoning_effort: medium, fork_context: false.",
  "- The child must set CODEX_CLAUDE_CHILD_THREAD=1 and call delegate_to_claude.* with -TaskFile, -WorkflowId, -TaskId, -Role, and -SessionKey.",
  "- Legacy inline -Task and -Mode delegate arguments are forbidden.",
].join("\n");

function pluginRoot() {
  if (process.env.CLAUDE_PLUGIN_ROOT) {
    return process.env.CLAUDE_PLUGIN_ROOT;
  }
  if (process.env.CODEX_PLUGIN_ROOT) {
    return process.env.CODEX_PLUGIN_ROOT;
  }

  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
}

function readOptionalText(filePath) {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch {
    return "";
  }
}

function bootstrapContext() {
  const root = pluginRoot();
  const skillText = readOptionalText(path.join(root, "skills", "codex-with-cc", "SKILL.md"));
  const contractText = readOptionalText(path.join(root, "skills", "codex-with-cc", "CODEX_WITH_CC.md"));

  if (!skillText.trim() || !contractText.trim()) {
    return FALLBACK_CONTEXT;
  }

  return [
    "<EXTREMELY_IMPORTANT>",
    "You have codex-with-cc routing.",
    "",
    "**Below is the full content of your 'codex-with-cc' skill and workflow contract. Follow this routing contract whenever child-agent, subagent, child-thread, delegation, worker-execution, 子代理, 子线程, 多代理, 委派, 派工, or 执行层 appears.**",
    "",
    "## codex-with-cc SKILL.md",
    "",
    skillText.trim(),
    "",
    "## codex-with-cc CODEX_WITH_CC.md",
    "",
    contractText.trim(),
    "",
    "</EXTREMELY_IMPORTANT>",
  ].join("\n");
}

function readStdin() {
  return new Promise((resolve) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
  });
}

function parseInput(text) {
  if (!text.trim()) {
    return {};
  }

  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

function writeJson(value) {
  process.stdout.write(JSON.stringify(value));
}

function getEventName(input) {
  return input.hook_event_name || input.hookEventName || input.eventName || "";
}

function getToolName(input) {
  return input.tool_name || input.toolName || "";
}

function getToolInput(input) {
  return input.tool_input || input.toolInput || {};
}

function getPrompt(input) {
  return input.prompt || input.user_prompt || input.userPrompt || "";
}

function stringify(value) {
  if (typeof value === "string") {
    return value;
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function containsSubagentTrigger(text) {
  return TRIGGER_PATTERNS.some((pattern) => pattern.test(text));
}

function additionalContext(eventName) {
  return {
    hookSpecificOutput: {
      hookEventName: eventName,
      additionalContext: bootstrapContext(),
    },
  };
}

function deny(reason) {
  return {
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: reason,
    },
  };
}

function prop(input, snakeName, camelName) {
  if (input && Object.prototype.hasOwnProperty.call(input, snakeName)) {
    return input[snakeName];
  }
  if (input && Object.prototype.hasOwnProperty.call(input, camelName)) {
    return input[camelName];
  }
  return undefined;
}

function isFalse(value) {
  return value === false || value === "false";
}

function hasChildMarker(serialized) {
  return /CODEX_CLAUDE_CHILD_THREAD\s*(?:=|:)\s*["']?1["']?/i.test(serialized);
}

function hasDelegateEntrypoint(serialized) {
  return /delegate_to_claude(?:\.(?:ps1|sh|cmd|bat))?/i.test(serialized);
}

function hasTaskFile(serialized) {
  return /(?:^|[\s"'])(?:-TaskFile|--task-file)\b/i.test(serialized);
}

function hasLegacyInlineTask(serialized) {
  return /(?:^|[\s"'])-(?:Task)\b/i.test(serialized);
}

function hasWorkflowId(serialized) {
  return /(?:^|[\s"'])(?:-WorkflowId|--workflow-id)\b/i.test(serialized);
}

function hasTaskId(serialized) {
  return /(?:^|[\s"'])(?:-TaskId|--task-id)\b/i.test(serialized);
}

function roleValue(serialized) {
  const match = serialized.match(/(?:^|[\s"'])(?:-Role|--role)\s+["']?([A-Za-z-]+)/i);
  return match ? match[1].toLowerCase() : "";
}

function hasRole(serialized) {
  return /(?:^|[\s"'])(?:-Role|--role)\b/i.test(serialized);
}

function hasSessionKey(serialized) {
  return /(?:^|[\s"'])(?:-SessionKey|--session-key)\b/i.test(serialized);
}

function hasReviewForTaskId(serialized) {
  return /(?:^|[\s"'])(?:-ReviewForTaskId|--review-for-task-id)\b/i.test(serialized);
}

function hasReviewKind(serialized) {
  return /(?:^|[\s"'])(?:-ReviewKind|--review-kind)\b/i.test(serialized);
}

function hasLegacyMode(serialized) {
  return /(?:^|[\s"'])(?:-Mode|--mode)\b/i.test(serialized);
}

function hasScope(serialized) {
  return /(?:^|[\s"'])(?:-Scope|--scope)\b/i.test(serialized);
}

function hasAllowParallel(serialized) {
  return /(?:^|[\s"'])-(?:AllowParallel)\b|(?:^|[\s"'])--allow-parallel\b/i.test(serialized);
}

function hasForbiddenEffort(serialized) {
  return /(?:^|[\s"'])--effort\b/i.test(serialized);
}

function hasDirectClaudeCommand(serialized) {
  return /(?:^|[\s;&|"'`])(?:\.\/|\.\\|[\w:/\\.-]*[/\\])?claude(?:\.cmd|\.exe)?(?=$|[\s;&|"'`])/i.test(serialized);
}

function validateWorkflowPayload(payload) {
  const serialized = stringify(payload);
  const problems = [];

  if (prop(payload, "model", "model") !== REQUIRED_MODEL) {
    problems.push(`model must be ${REQUIRED_MODEL}`);
  }
  if (prop(payload, "reasoning_effort", "reasoningEffort") !== REQUIRED_EFFORT) {
    problems.push(`reasoning_effort must be ${REQUIRED_EFFORT}`);
  }
  if (!isFalse(prop(payload, "fork_context", "forkContext"))) {
    problems.push("fork_context: false is required");
  }
  if (hasDirectClaudeCommand(serialized)) {
    problems.push("direct Claude CLI execution is forbidden");
  }
  if (!hasChildMarker(serialized)) {
    problems.push("CODEX_CLAUDE_CHILD_THREAD=1 is required");
  }
  if (!hasDelegateEntrypoint(serialized)) {
    problems.push("delegate_to_claude.* is required");
  }
  if (!hasTaskFile(serialized)) {
    problems.push("-TaskFile is required");
  }
  if (hasLegacyInlineTask(serialized)) {
    problems.push("legacy inline -Task is forbidden; use -TaskFile");
  }
  if (!hasWorkflowId(serialized) && !prop(payload, "workflow_id", "workflowId")) {
    problems.push("-WorkflowId is required");
  }
  if (!hasTaskId(serialized) && !prop(payload, "task_id", "taskId")) {
    problems.push("-TaskId is required");
  }
  if (!hasSessionKey(serialized) && !prop(payload, "session_key", "sessionKey")) {
    problems.push("-SessionKey is required");
  }
  const role = String(prop(payload, "role", "role") || roleValue(serialized) || "").toLowerCase();
  if (!hasRole(serialized) && !role) {
    problems.push("-Role is required");
  } else if (!ALLOWED_ROLES.has(role)) {
    problems.push(`-Role must be one of ${Array.from(ALLOWED_ROLES).join(", ")}`);
  }
  if (role === "reviewer" && (!hasReviewForTaskId(serialized) || !hasReviewKind(serialized))) {
    problems.push("reviewer runs require -ReviewForTaskId and -ReviewKind");
  }
  if (hasLegacyMode(serialized)) {
    problems.push("legacy -Mode is forbidden; use -Role");
  }
  if (hasAllowParallel(serialized) && !hasScope(serialized)) {
    problems.push("-Scope is required when -AllowParallel is used");
  }
  if (hasForbiddenEffort(serialized)) {
    problems.push("delegate_to_claude.* must not pass --effort");
  }

  return problems;
}

function handlePreToolUse(input) {
  const toolName = getToolName(input);
  const toolInput = getToolInput(input);
  const serialized = stringify(toolInput);
  const normalizedToolName = toolName.toLowerCase();

  if (normalizedToolName === "bash") {
    const problems = [];

    if (hasDirectClaudeCommand(serialized)) {
      problems.push("direct Claude CLI execution is forbidden");
    }

    if (hasDelegateEntrypoint(serialized)) {
      if (!hasChildMarker(serialized)) {
        problems.push("CODEX_CLAUDE_CHILD_THREAD=1 is required");
      }
      if (!hasTaskFile(serialized)) {
        problems.push("-TaskFile is required");
      }
      if (hasLegacyInlineTask(serialized)) {
        problems.push("legacy inline -Task is forbidden; use -TaskFile");
      }
      if (!hasWorkflowId(serialized)) {
        problems.push("-WorkflowId is required");
      }
      if (!hasTaskId(serialized)) {
        problems.push("-TaskId is required");
      }
      if (!hasSessionKey(serialized)) {
        problems.push("-SessionKey is required");
      }
      if (!hasRole(serialized)) {
        problems.push("-Role is required");
      } else if (!ALLOWED_ROLES.has(roleValue(serialized))) {
        problems.push(`-Role must be one of ${Array.from(ALLOWED_ROLES).join(", ")}`);
      }
      if (roleValue(serialized) === "reviewer" && (!hasReviewForTaskId(serialized) || !hasReviewKind(serialized))) {
        problems.push("reviewer runs require -ReviewForTaskId and -ReviewKind");
      }
      if (hasLegacyMode(serialized)) {
        problems.push("legacy -Mode is forbidden; use -Role");
      }
      if (hasAllowParallel(serialized) && !hasScope(serialized)) {
        problems.push("-Scope is required when -AllowParallel is used");
      }
      if (hasForbiddenEffort(serialized)) {
        problems.push("delegate_to_claude.* must not pass --effort");
      }
    }

    if (problems.length > 0) {
      writeJson(deny(`codex-with-cc platform gate blocked Bash: ${problems.join("; ")}.`));
    }
    return;
  }

  if (!SPAWN_TOOL_NAMES.has(normalizedToolName)) {
    return;
  }

  const problems = validateWorkflowPayload(toolInput);
  if (problems.length > 0) {
    writeJson(deny(`codex-with-cc platform gate blocked ${toolName}: ${problems.join("; ")}.`));
  }
}

const input = parseInput(await readStdin());
const eventName = getEventName(input);

if (eventName === "SessionStart") {
  writeJson(additionalContext("SessionStart"));
} else if (eventName === "UserPromptSubmit") {
  if (containsSubagentTrigger(getPrompt(input))) {
    writeJson(additionalContext("UserPromptSubmit"));
  }
} else if (eventName === "PreToolUse") {
  handlePreToolUse(input);
}
