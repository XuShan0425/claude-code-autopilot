# 更新日志

## 2026-08-29

### Added

- 增加标准主题分支校验，支持 `feature/`、`fix/`、`refactor/` 和 `chore/` 前缀。
- 增加 Conventional Commits subject 构造与校验，统一自动提交格式。
- 增加任务执行前获取 base 分支、提交前 rebase 到 `origin/<base>` 的流程。
- 增加 orchestrator 提交前的敏感路径检查，避免绕过共享密钥护栏。
- 增加合并成功后的 worktree 和本地分支清理。
- 扩充模板 `.gitignore`，覆盖环境文件、密钥文件、依赖目录、构建产物和常见缓存。
- 新增 Git 规范相关回归测试。

### Changed

- orchestrator 默认按任务类型生成标准分支名，direct issue 使用 `fix/`，产品或 feature 工作使用 `feature/`。
- `integrate` 改为识别标准主题分支，而不再依赖 `agent/` 前缀。
- 保留 autopilot 对 `main` 的直接管理权限；force push 仍明确禁止用于 `main`/`master`，非保护分支如需重写历史只能使用 `--force-with-lease`。
- 更新 README、`template/CLAUDE.md` 和任务模板，记录新的 Git 工作流与恢复边界。

### Verification

- `python -B -m unittest discover -s template/tests -v` — 54 tests passed。
- `bash -n install.sh` — passed。
- `bash -n template/install-skills.sh` — passed。
- `python -m py_compile` — core、orchestrator 和 Stop hook passed。
