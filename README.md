> 如果你是 Codex，并且用户让你把本仓库集成到某个项目里：请阅读 [AI_INSTALL.md](AI_INSTALL.md)。
>
> 下面内容是给人看的，AI无需阅读。

# Codex With CC

`codex_with_cc` 现在是一个 **Codex / Claude Code 双平台插件**，并且只通过 `aiskyhub/aiskyhub` marketplace 分发。

## 插件优先

仓库根目录已经提供双平台 manifest：

- `.codex-plugin/plugin.json`
- `.claude-plugin/plugin.json`

两个平台共享同一份 `skills/` 内容目录，真正的工作流实现仍然位于 `skills/codex-with-cc/`。这样做的目的很直接：让仓库可以被识别成插件，同时不破坏现有安装器、验证脚本和运行时路径假设。

核心委派链路保持不变：

```text
Codex main thread -> Codex child agent -> Claude Code CLI
```

更完整的目录说明见 [docs/plugin-layout.md](docs/plugin-layout.md)。

## Marketplace 安装

### Codex

1. 先检查 `~/.codex/config.toml` 是否已经包含 `[marketplaces.aiskyhub]`。
2. 如果没有，执行：

```bash
codex plugin marketplace add aiskyhub/aiskyhub
```

3. 然后在 Codex 里执行：

```text
/plugin install codex-with-cc@aiskyhub --scope user
```

4. 安装后即可直接使用 `$codex-with-cc`，或者触发子代理 / 委派关键词。

### Claude Code

1. 先执行 `/plugin marketplace list` 或 `claude plugin marketplace list` 检查是否已添加 `aiskyhub`。
2. 如果没有，执行：

```text
/plugin marketplace add aiskyhub/aiskyhub
```

3. 安装插件：

```text
/plugin install codex-with-cc@aiskyhub --scope user
```

4. 如果当前会话还没刷新插件状态，补一条：

```text
/reload-plugins
```

## 它在做什么

这个插件把高 token 消耗的子代理执行层，稳定地转发给 Claude Code CLI，同时让 Codex 主线程继续负责：

- 理解需求
- 拆任务和派工
- 审核子代理结果
- 决定返工与最终交付

工作流约束写在 [skills/codex-with-cc/CODEX_WITH_CC.md](skills/codex-with-cc/CODEX_WITH_CC.md) 里，skill 入口在 [skills/codex-with-cc/SKILL.md](skills/codex-with-cc/SKILL.md)。

## 什么时候适合用

- 大范围代码阅读和模块梳理
- 多文件实现任务
- 多代理并行方案探索
- 一个代理实现，另一个代理专门审查
- 迁移、重构、补测试、查调用链这类长上下文工作

不太适合：

- 只有一两行的小改动
- 需要主线程实时交互判断的极短任务
- 文件冲突极高、写入边界尚未拆清的并行改动

## AI 引导安装

```text
请把 https://github.com/xdd666t/codex_with_cc 调度子代理工作流集成或更新到codex的skill中。
```

这句安装口令现在应该把 AI 引导到 [AI_INSTALL.md](AI_INSTALL.md)，并且先检查 `aiskyhub/aiskyhub` marketplace，再从 marketplace 安装 `codex-with-cc@aiskyhub`。仓库不再提供脚本式安装回退。

## 使用姿势

核心心法还是一句：让 Codex 做 leader，让子代理当执行层。

你可以直接这样下命令：

```text
你拆解 xxx 任务，安排给多个子代理实现。你负责审核子代理结果，不符合要求就打回让他们重改，直到符合要求为止。
```

或者这样让它做方案对打：

```text
请启动多个子代理分别提出 xxx 的实现方案。每个方案需要说明优缺点、复杂度、风险和迁移成本。你汇总后给出推荐方案，不要直接照抄任何一个子代理。
```

## 运行时特性

现有运行时没有因为插件化而缩水，仍然保留：

- `PrimaryReuse`、`PrimaryAnchor`、`ParallelPool` 三种 Claude session 复用模式
- 任务指纹、租约锁和会话回收
- 子线程强校验 `CODEX_CLAUDE_CHILD_THREAD=1`
- 审计产物落盘与链路校验
- 运行时、session pool、artifact、delegate chain 自检脚本

也就是说，这次改造主要是“给仓库补上标准插件入口和文档身份”，不是重写底层协议。
