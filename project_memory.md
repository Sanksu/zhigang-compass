# Project Memory — 智岗罗盘项目记忆

> 性质：项目级规则与踩坑记录（AGENTS.md §7 指定落点）。
> 变更需经用户确认后写入，不直接修改 AGENTS.md。

## 规则与约定

- **测试约定**：pytest-asyncio 未配置 auto 模式，async 测试须显式 `asyncio.run(...)` 包裹（遵循项目现有测试模式），不要依赖 pytest-asyncio 自动运行。
- **integration marker**：`-m integration` 标记的测试默认不参与全量运行（`-m not integration` 排除），需要真实外部服务。
- **ARQ 任务参数名**：超时参数为 `job_timeout`（非 `task_timeout`，坑 22）。
- **提交信息规范**：使用中文编写 commit message（`.trae/rules/git-commit-message.md`）。

## 坑记录

- **pytest-asyncio**：`pyproject.toml` 未配置 `asyncio_mode`，async def 测试会报 "not natively supported"，必须显式 `asyncio.run`。
- **position_freq_windows 同名合并**：graph_versions 快照中同名归一化岗位可能对应多个 pos_id，重建窗口序列时应**逐窗口求和**（该岗位当期被引用的总边数），而非取最长序列。
- **单期序列判定**：`evaluate_auto_transition` 对单期窗口波动为 0 会判定 STABLE，因此冷启动闸门（快照 < 2 期直接跳过）必须保留在任务层 `discovery_auto_transition`，判定层不做防御。

## 自动状态流转（AL-M4-05，设计文档 §7.2.1/§7.2.4）

- 数据源：`graph_versions` 快照序列（岗位频次 = 岗位作为边 source 的计数），与 trend_service 同源。
- 任务链：`discovery_daily`（候选池 + RAG 接地）→ `discovery_auto_transition`（emerging/stable/declining 自动流转）。
- 调度入口：`scripts/cron/discovery_daily.py` 每日 05:30 入队两个 ARQ 任务（Linux `crontab.example` / Windows `scheduled_tasks.ps1`）。
- emerging→stable 阈值：confidence ≥ 0.8 且连续 2 窗口波动 < 25% 且源 ≥ 2（§7.2.4）。
- 定义草案：`_generate_definition` 走 LLM 中文凝练（instructor 强校验），失败静默回退权威库原文/种子描述，不阻塞接地。
