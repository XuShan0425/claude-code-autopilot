# Claude Code Agent Autopilot

> 把 Claude Code 安装进你的项目工作流：规划、执行、验证、提交、推送、PR、合并，全部自动化。

```bash
git clone git@github.com:XuShan0425/-.git /tmp/agent-template
cd /path/to/your-project
/tmp/agent-template/install.sh --profile generic
```

## 这是什么

一个面向 Claude Code 的**自动驾驶（autopilot）项目模板**。安装到你的仓库后：

- **Stop hook**——你在 Claude Code 里改完代码一停止，它就自动：验证 → 提交 → 推送 → 开 PR → 合并。
- **编排器**——把一个大需求拆成 EPIC + 多个 TASK，每个任务在独立 git worktree 里无人值守地执行并自动合并。
- **唯一的门是验证**：探测到的 lint / typecheck / test 必须通过才允许合并。
- **唯一的护栏是密钥**：`.env*`、`secrets/`、路径含 `secret` / `token` 的文件永远拒绝提交。
- 零依赖：纯文件 + Python 3 + Git worktree + GitHub CLI。

> ⚠️ 这是真正的全自动：默认 `bypassPermissions`（无确认弹窗），验证一过就**自动合并进 base 分支（包括 main）**。请只在你信任的项目里使用，并先在分支上试用。

## 前置要求

- Git
- Python 3.10+
- GitHub CLI（`gh`），且已完成 `gh auth login`
- Claude Code CLI（`claude`）
- 目标项目是 git 仓库，且已配置 GitHub 的 `origin` 远程

## 安装

```bash
# 1. 获取模板源码
git clone git@github.com:XuShan0425/-.git /tmp/agent-template

# 2. 进入你的项目
cd /path/to/your-project

# 3. 安装模板
/tmp/agent-template/install.sh --profile generic
```

安装会：把 `template/` 拷进当前仓库根 → 按 profile 往 `CLAUDE.md` 追加规则 → 部署内置 skills 到 `~/.claude/skills/`。加 `--force` 可覆盖已存在的文件。

### Profile

| Profile | 适用 | 探测的验证 |
|---------|------|-----------|
| `generic` | 任意项目 | 仓库自带的命令 |
| `node` | Node.js / TypeScript | `lint` / `typecheck` / `test`（按锁文件选包管理器） |
| `python` | Python | `ruff` / `mypy` / `pytest` / `tox` |

## 这个仓库怎么理解

这个仓库同时扮演两个角色：

- **根目录**：模板仓库自己的安装器与说明文件，只留 `install.sh`、`profiles/`、`README.md`、`.gitignore` 这类内容。
- **`template/`**：真正会被安装进用户项目的文件。`install.sh` 会把 `template/` 里的内容复制到目标项目根目录。

也就是说：

- `template/.claude/settings.json` → 安装后会变成 `your-project/.claude/settings.json`
- `template/orchestrator/agent-team.py` → 安装后会变成 `your-project/orchestrator/agent-team.py`
- `template/CLAUDE.md` → 安装后会变成 `your-project/CLAUDE.md`

如果你在这个模板仓库自身试跑命令，根目录偶尔会出现 `.agent-runs/`、`.agent-tasks/`、`docs/exec-plans/` 之类的运行时目录；它们**不是产品内容**，也不会被安装到用户项目中。

## 使用方式

### A. 主产品路线：先主 PRD，再规划

适用于：
- 新项目从 idea 开始
- 大功能
- 改变产品目标、目标用户、核心流程、MVP 范围、非目标、第一性原则

```bash
# 1. 先用 /prd 创建或更新主 PRD
#    主 PRD 会写到 docs/prd/active/PRD-001.md

# 2. 再基于主 PRD 生成 EPIC + TASK
python orchestrator/agent-team.py plan --from-prd docs/prd/active/PRD-001.md

# 3. 执行任务
python orchestrator/agent-team.py run TASK-001
python orchestrator/agent-team.py status
```

### B. 局部产品变更路线：先 feature brief，再规划

适用于：
- 附加功能
- 中等复杂度新能力
- 局部产品增强
- 不应重写主 PRD 的产品变更

```bash
# 1. 先用 /prd 生成或更新 feature brief
#    feature brief 会写到 docs/prd/changes/active/FEATURE-XXX.md

# 2. 再基于 feature brief 规划
python orchestrator/agent-team.py plan --from-brief docs/prd/changes/active/FEATURE-001.md

# 3. 执行任务
python orchestrator/agent-team.py run TASK-001
python orchestrator/agent-team.py status
```

在 Claude Code CLI 里推荐的用户路径是：

```text
/prd   创建或更新主 PRD，或生成 feature brief
/plan  从主 PRD、feature brief 或 direct issue 生成 EPIC/TASK
/run   执行 TASK
/status 查看状态
```

### C. 问题修复路线：直接规划

适用于：
- bugfix
- 小修
- 样式 / 文案修复
- 局部性能优化
- 不改变产品行为的重构
- 补测试

```bash
python orchestrator/agent-team.py plan "fix login button not responding"
python orchestrator/agent-team.py run TASK-001
python orchestrator/agent-team.py status
```

如果请求看起来像产品层变化，但没有主 PRD 或 feature brief，planner 应该拒绝继续，并提示先 `/prd`。

`run TASK-001` 的流程：创建 `agent/...` 分支的 worktree → 无头 Claude 执行任务 → 跑验证（失败则任务进 `failed`）→ 提交 → 推送 → `gh pr create` → `gh pr merge --squash` → 任务进 `completed`。全过程记录在 `.agent-runs/`。

### C. Stop hook（临时编辑）

最简单的方式：直接在 Claude Code 里改代码，然后停止。Stop hook 会自动完成验证、提交、推送、合并。验证失败时它会拦住停止，把你「打回去」继续修直到通过。

## 任务文件

每个任务是一个 markdown 文件，放在 `.agent-tasks/active/`，从 `TASK-template.md` 复制。关键字段：

- **Goal / Scope / Acceptance Criteria**：明确要做什么
- **Allowed Files / Forbidden Files**：worker 只允许动 Allowed 里的文件
- **Verification Commands**：合并前的门；不填则用自动探测到的命令
- **Branch**：`agent/...`
- **Base branch**：合并目标，默认 `main`

任务状态：`active` → `running` → `completed`（或 `failed`）。

## 安装后的目录结构

```
your-project/
├── CLAUDE.md                   # 项目规则（autopilot 语义）
├── orchestrator/agent-team.py  # plan / run / status / integrate
├── lib/agent_core.py           # hook 与 orchestrator 的共享逻辑
├── .claude/
│   ├── settings.json           # bypassPermissions + Stop hook
│   ├── hooks/stop-auto-pr.py   # 自动验证、提交、合并
│   └── commands/               # /prd /plan /run /status /integrate
├── .agent-tasks/               # active/ running/ completed/ failed/
├── .agent-runs/                # 运行日志（JSONL + 摘要）
├── docs/
│   ├── prd/
│   │   ├── active/             # 主 PRD（长期维护）
│   │   ├── completed/          # 主 PRD 退役/替换时预留
│   │   └── changes/
│   │       ├── active/         # feature briefs / 小 PRD
│   │       └── completed/      # 已完成的 feature briefs
│   └── exec-plans/             # 工程规划层（EPIC）
│       ├── active/
│       └── completed/
└── install-skills.sh           # skills 安装脚本
```

`docs/prd/active/PRD-001.md` 是**主 PRD**，代表项目的第一性原则与长期产品定义。`docs/prd/changes/active/FEATURE-XXX.md` 是**小 PRD / feature brief**，用于附加功能和局部产品变化。`docs/exec-plans/` 则是工程规划层。

## 内置 Skills

安装时部署到 `~/.claude/skills/`：

| Skill | 作用 |
|-------|------|
| `agent-product-consultant` | 维护主 PRD，或为局部产品变更生成 feature brief，不做工程拆解 |
| `agent-planner` | 把 PRD 或问题说明拆解为 EPIC + 多个有界的 TASK |
| `agent-worker` | 在 worktree 里只执行被指派的单个任务 |
| `agent-reviewer` | 合并前审查（autopilot 下最后一道防线） |
| `agent-integrator` | 多任务 EPIC 的整体追踪与冲突排查 |
| `gh-fix-ci` | 用 `gh` 诊断并修复失败的 GitHub Actions 检查 |

## Autopilot 行为与护栏

| 项 | 行为 |
|----|------|
| 权限 | `bypassPermissions`，无确认弹窗 |
| 合并 | 验证通过即 `squash` merge，**可合并进 main** |
| 分支 | 任务统一用 `agent/...` 前缀 |
| 质量门 | 探测到的 lint / typecheck / test 是唯一门；**探测不到命令 = 不设门** |
| 密钥护栏 | `.env*`、`secrets/`、含 `secret` / `token` 的路径永远拦截，不可关闭 |

> 因为「探测不到验证命令就不设门」，请在任务文件里显式写 `Verification Commands`——尤其是不带测试的静态项目。

## 验证命令如何被探测

hook 与编排器按以下顺序探测（来自 `package.json` / Python 工具配置）：

- **Node**：`package.json` 的 `lint` / `typecheck` / `test` 脚本（自动跳过 npm 默认的空 test）；包管理器按锁文件选择（pnpm > yarn > npm）。
- **Python**：`ruff check .`（可用时）→ `mypy .`（需配置）→ `pytest`（可用时）→ `tox`（需配置）。

探测不到任何命令时，验证步骤直接放行——此时门是空的，请手动在任务里指定。

## 卸载

```bash
rm -rf .claude orchestrator lib .agent-tasks .agent-runs docs/exec-plans install-skills.sh
# 再删除 CLAUDE.md 中 <!-- agent-env-template ... --> 之间的 profile 块
rm -rf ~/.claude/skills/{agent-product-consultant,agent-planner,agent-worker,agent-reviewer,agent-integrator,gh-fix-ci}
```

## 开发此模板

本仓库自身就是模板源码。跑测试：

```bash
python template/tests/test_stop_auto_pr.py
```
