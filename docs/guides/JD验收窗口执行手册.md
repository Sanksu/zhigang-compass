# JD 解析验收窗口执行手册（08.31–09.01）

> 目的：AL-M5-01 / TE-M5-02 正式验收**一次通过**，杜绝临场发挥。
> 结论口径：**词面真值对齐 F1**（PR #330/#331），三跑取中位 ≥ 0.90 即达标（设计文档 §13.3）。
> 本手册由 08-21 自检评估报告 TOP1 风险（验收窗口执行风险）派生。

---

## 0. 前置检查清单（T-1 日 08.30 完成）

- [ ] `develop` 最新且 CI 绿：`git pull` 后确认包含 #329（60s 超时）/ #330 / #331 / #332
- [ ] 黄金集就位：`backend/data/golden_set/final/jd_golden_110.jsonl` 存在且 **110 行**
- [ ] LLM provider 可用：`configs/llm_providers.yaml` 主 provider（deepseek/opencode 网关）密钥有效——用任意一条 JD 做一次冒烟抽取确认非 401/超时
- [ ] Docker 5 服务健康：`docker ps` 中 api/postgres/redis/neo4j/worker 全部 healthy
- [ ] 磁盘余量 > 1GB（`backend/reports/` 归档）
- [ ] 时间预算：单跑约 30–60 分钟（110 条 × 单条最长 60s），三跑预留半天

## 1. Preflight（每跑前必做，不消耗 LLM 额度）

```bash
cd backend
uv run python tests/evaluate/run_manual_jd_eval.py --gold-jsonl data/golden_set/final/jd_golden_110.jsonl
```

- 输出 `READY` → 进入第 2 步
- 输出 `BLOCKED`（exit 2）→ 按 `evaluation_110/` 下 blocker report 修复后重跑 preflight
- **禁止跳过 preflight 直接 `--run`**

## 2. 正式三跑

```bash
uv run python tests/evaluate/run_manual_jd_eval.py --gold-jsonl data/golden_set/final/jd_golden_110.jsonl --run
```

- 连续执行 **3 次**（run1/run2/run3），每跑间隔 ≥ 5 分钟
- 60s 超时已内建为脚本常量 `_EVAL_LLM_TIMEOUT_SECONDS = 60`，**无需额外环境变量**；生产默认 ASYNC 30s 不受影响
- 每跑产物两处：
  - `data/golden_set/review/evaluation_110/` 四件套（cases CSV / validation JSON / predictions JSONL / report MD）——**会被下一跑覆盖**
  - `backend/reports/eval_jd_llm_{ts}.json` ——**时间戳归档不覆盖，这是判读依据**
- 若需保留每跑四件套：每跑结束后手动复制 `evaluation_110/` → `evaluation_110_run{n}/`

## 3. 判读标准（按序执行，不得跳步）

1. **完整性检查**：三份归档 JSON 中 `real_llm_success_samples == 110`。任何一跑出现 fallback/failed > 0：先查 provider 健康，修复后**整跑重做**（不接受部分补测）
2. **指标读取**：每份归档的 `skills_micro_aligned.f1`（对齐口径：FP 词面豁免、幻觉单列）与 `target_met`
3. **达标判定**：三跑 `skills_micro_aligned.f1` 的**中位数 ≥ 0.90 → 达标**；记录三次原始值 + 中位值
4. **未达标处置**：**禁止当场修改 prompt / gold / 评测代码后混入同一窗口出数**。按归档 `error_types` 分解（model-added / conditional marker / missed）形成分析报告，提交张恺天决策；如需变更，窗口重开并在进度跟踪记录

## 4. 汇总出报告

```bash
uv run python scripts/evaluate.py --task jd_llm
```

- 只读最近归档生成 HTML 报告（不重复消耗 LLM 额度）：`backend/reports/eval_{date}.html`
- 报告含 raw 与对齐双口径对照，对外宣称一律引用**对齐口径**

## 5. 归档入库（验收当日完成）

- [ ] 以第 3 跑四件套刷新 `backend/data/golden_set/review/evaluation_110/`，走 `docs/data-*` 分支 PR 提交
- [ ] 三份 `eval_jd_llm_*.json` 复制到 `evaluation_110/archives/` 一并入库（reports/ 本身 gitignored）
- [ ] 更新 `docs/project/进度跟踪.md` AL-M5-01 / TE-M5-02 状态与数值（含三跑原始值 + 中位）
- [ ] 设计文档 §9.6 / §13.3 数字同步（如有口径声明变化）

## 6. 红线

- ❌ 禁止修改 gold 标注、prompt、评测代码后混入同一窗口出数
- ❌ 禁止用 raw 口径替代对齐口径宣称达标（PPT/报告引用须注明"词面对齐口径，三跑中位"）
- ❌ 禁止无 preflight 直接 `--run`；禁止单跑出正式结论
