# JD 抽取六维评测补齐方案（评审稿，2026-08-20）

> 状态：**待算法岗张恺天确认**（决策点见 §9，可在本 PR 评论区逐条打勾或标注修改）。
> 目标：把「六维评测」从当前 4 维（title/skills/bonus/education）补到 6 维——
> 启用 110 条 Round1 人工 gold（PR #316）已有的 `gold_experience` / `gold_core_duties` 两维。
> 关联：`temp/JD六维评测补齐_补丁稿_20260819.md`（本方案由此定稿）。

---

## 1. 现状

- `backend/app/services/extraction/schemas.py::JDExtractionResult` **缺 `experience_range` / `core_duties` 两字段**
  → 评测链只能把这两维标为 "Schema coverage gap"（`tests/evaluate/run_manual_jd_eval.py` 硬编码 + HTML 报告 `experience_gap/core_duties_gap`），**六维实测只有 4 维**。
- 110 条 gold 六维**齐备**（`data/golden_set/final/jd_golden_110.jsonl`，A01+QA，EXPORT_PASS），
  `gold_experience`（108 非空 / 2 null）、`gold_core_duties`（实测 2~8 条）均可作评测基准。
- 评测工具已就绪：`--gold-jsonl` 输入源（PR #319，110/110 预检放行）。

## 2. 改动面（3 处 + 1 确认）

| # | 文件 | 改动 |
|---|------|------|
| 1 | `schemas.py` | `JDExtractionResult` 增 `experience_range` 与 `core_duties` |
| 2 | `prompts.py` | `TASK_TEMPLATE` + `BATCH_TASK_TEMPLATE` 新增规则（**双模板需同步**，`replace_all`） |
| 3 | `run_manual_jd_eval.py` | 两处硬编码 "Schema coverage gap" 替换为真实对比（口径见 §5） |
| 依赖 | — | 是否需要 core_duties 落库到岗位聚合，或仅评测（Decision D3） |

## 3. 字段定义（对齐数据字典 null 语义）

```python
class ExperienceRange(BaseModel):
    min_years: Optional[int] = None
    max_years: Optional[int] = None

class JDExtractionResult(BaseModel):
    ...
    experience_range: Optional[ExperienceRange] = Field(
        default=None,
        description="经验年限区间；正文无明确年限且无最低准入时置 null（不代表 0 年经验）",
    )
    core_duties: list[str] = Field(
        default_factory=list,
        description="核心职责概要，2~8 条精炼短语（正文职责段归纳，实测 2~8，推荐 3~6）",
    )
```

**null 语义**（沿用数据字典）：无明确年限 → `experience_range=null`（≠0 年）；`core_duties` 空数组为异常、由 QA 拦截。

## 4. Prompt 规则草稿（克制原则，防回退）

- 经验：仅当正文出现"X 年以上 / X-Y 年 / 至少 X 年 / X 年经验"等显式年限才填 min/max；"经验不限"不填；与列表页冲突以正文为准。
- core_duties：只从职责段归纳 2~8 条、每条 5~20 字短语；禁止整段抄写；技能/福利/公司介绍不收录（技能由 requirements 承担）。

## 5. 评测口径决策点（核心，需逐条确认）

### D1 — experience 评测口径

| 候选 | 口径 | 价值 | 成本 | 现有基础 | 建议 |
|---|---|---|---|---|---|
| **A（推荐）** | **区间重叠判定**：预测区间与 gold 区间相交→命中；单方 null→未命中；双方 null→命中 | 语义正确、可解释 | 低（纯函数） | gold 已 object 化 | **推荐** |
| B | raw-exact + overlap 双指标并存 | 更能暴露边界 | 中（双指标口径） | 同 A | 可作 A 的补充输出，不单独启用 |

### D2 — core_duties 评测口径

| 候选 | 口径 | 价值 | 成本 | 现有基础 | 风险 | 建议 |
|---|---|---|---|---|---|---|
| **A（推荐）** | **词面/语义 containment**：gold 每条职责在预测侧是否有命中（词面 + 别名归一），微平均 | 确定性、离线可复现、与 skills 口径同构 | 低 | normalize/别名既有 | 措辞不一可能偏低 | **推荐** |
| B | **Rouge-L 均值**（gold↔预测 逐条最大 LCS） | 对标学术惯例、容忍改写 | 中 | 需引入 rouge 计算（可手写 LCS，不引依赖） | 对职责粒度不敏感 | 作 A 的备用 
| C | **LLM judge** | 最语义化 | 高（开销 + 非确定性） | 无 | 与"评测确定性补全"原则冲突、费用 | **不推荐现阶段** |

### D3 — core_duties 是否落库岗位聚合

- 现状 `typical_scenarios` 已承载相近的"职责/场景"语义（岗位聚合写入）。
- 候选：独立 `core_duties` 字段（评测用，不落聚合）**or** 与 `typical_scenarios` 共用口径。
- 建议：**先独立字段仅评测**，避免与既有聚合语义打架；落库口径后续单独评审。

### D4 — 起步门槛

- 不建议一次性"六维全达标 ≥0.90"作为开关：core_duties 是新维度，初版指标可能偏低。
- 建议：**先落字段 + prompt（生产有数据）→ 指标准独立迭代**；skills F1 不倒退为硬护栏（§7）。

## 6. 回退护栏

1. 双模板（单条/批量）同规则同步，禁止只改一处。
2. prompt 改动后跑 **51 条盲审回归**（当前 F1 0.950）→ skills F1 不得回退超过 ±0.01（LLM 非确定性噪声带）。
3. 新增字段下班前验证 110 条 gold 复测：experience 维可评测（非 gap）、core_duties 维出首版指标。

## 7. 完成判定（张恺天确认后执行）

1. `JDExtractionResult` 含两字段，prompts 双模板同规则、51 条回归达标。
2. 110 条 gold 复测：experience 维非 gap、core_duties 维首版指标产出。
3. `evaluate.py --task jd_llm` 报告不再显示 experience/core_duties 为 "Schema coverage gap"。

## 8. 实施顺序

1. 本方案 PR 由张恺天确认（评论打勾/修改）。
2. 实施 PR-1：schemas + prompts（含回归）+ eval 接线 → CI 全绿 → 合入。
3. 实施 PR-2（可选）：110 条 gold 复测归档 + 口径微调（若 core_duties 首版偏低，按 D2 决策迭代）。

## 9. 待确认决策点清单

- [ ] **D1** experience 口径：`区间重叠判定`（候选 A，推荐）
- [ ] **D2** core_duties 口径：`词面 containment`（候选 A，推荐；B 作备用，C 不采用）
- [ ] **D3** core_duties 是否落库岗位聚合：`先独立字段仅评测`（推荐）
- [ ] **D4** 起步门槛：`先落字段+prompt、指标准独立迭代`（推荐，不设一次性六维门槛）

> 张恺天可按清单打勾确认，或指出修改项（如改用候选 B / 调整阈值）；确认后按 §8 拆实施 PR。
