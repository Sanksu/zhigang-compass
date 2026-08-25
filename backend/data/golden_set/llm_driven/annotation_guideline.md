# 标注规范 — 归一化（岗位名 / 技能名）独立人工集

> 配套：`data_golden_set/llm_driven/data_dictionary.md`（字段字典）
> 对齐：`backend/tests/evaluate/annotation_guideline.md`（既有 JD/简历/匹配标注规范，
> 本文件为其"名称归一化"子域补充，保持"双人盲标 + 第三人仲裁 + Kappa ≥ 0.7"口径。）
>
> **重要诚实声明：** 本规范针对的是 **DRAFT 集**（`position_normalization_draft.jsonl`、
> `skill_normalization_draft.jsonl`）。这些集合的标签当前是**机器建议**
> （`annotation_status="draft_auto"`），**不是**人类金标准。下文描述的标注过程
> 是把这些 DRAFT 转成**独立人工金标准**的规定流程。在标注完成前，任何 `gold_*`
> 字段都不得被当作最终金标准使用。

---

## 1. 目的

建立一套**独立于现有规则/别名派生集**的人工金标准，用于：

1. 校准 `app/services/llm_decision/position_name.py::decide_position_name`
2. 校准 `app/services/llm_decision/skill_normalize.py::decide_skill_normalize`
3. 交叉校验确定性规则 `dictionary.normalize_position_name`

现有 `normalization_150.jsonl`、`classification_150.jsonl` 均为**别名/白名单派生**的
确定性 gold；本集合刻意纳入需要人类裁决的硬案例（近义 / 同首字母异义 / 短 ASCII
保护 / 版本 / 中文缩写），使人工校准后的集合比派生集更独立、更能暴露两套决策器的
真实错误。

---

## 2. 标注对象与范围

| 集合 | 文件 | 标注字段 | 动作枚举 |
|------|------|---------|---------|
| 岗位名归一 | `position_normalization_draft.jsonl` | `gold_canonical`, `gold_is_new`, `gold_keep_original` | `canonical` / `is_new` / `keep_original` |
| 技能名归一 | `skill_normalization_draft.jsonl` | `gold_action`, `gold_standard` | `merge` / `keep` / `noise` |

标注者**只**写 `gold_*` 字段与 `annotation_status`（改为 `human`）、`needs_human`
（改为 `false`）、`annotation_note`（可选）。不得改动 `suggested_*`、
`candidates`、`slice`、`source_note` 等事实字段。

---

## 3. 采样策略（避免偏倚）

DRAFT 集已按切片分层，标注者**不得**自行挑样。为保证独立性与代表性：

- **必须覆盖全部切片**，且每切片至少含一部分数据：
  - 岗位名：`cjk` / `mixed` / `pure_en` / `intern` / `reject`
  - 技能名：`near_synonym` / `same_initial` / `short_ascii` / `version_variant` / `cjk_abbr`
- **不跳过 `reject` / `intern` / `keep` 行**：这些正是规则"拦截/保持原样"的边界，
  最能暴露决策器是否过度拦截或过度合并。若 DRAFT 建议 `keep_original=true` 或
  `gold_action=keep`，标注者须**独立判断**是否同意，而非顺手机器建议。
- 标注者视角按**原始标题/技能名**理解，不预先被告知机器建议的方向（盲标）；
  若知道机器建议可能引入锚定偏差。

---

## 4. 字段标注规则

### 4.1 岗位名归一（`gold_canonical` / `gold_is_new` / `gold_keep_original`）

- 不看机器建议，仅依据市场常识与候选清单判断。
- `gold_canonical`：该标题应归到的标准岗位名，**与原始标题同语言**（中文标题出中文，
  英文标题出英文）；`gold_keep_original=true` 时可保持原样或置空串（表示"无标准名，
  不入图"）。
- `gold_is_new`：仅当该岗位语义不在任何 `candidates` 中**且**确构成新岗位时为 `true`。
- `gold_keep_original`：标题本身即行业标准名、或改写会损失语义时 `true`。
- 一致性决策三选一：`canonical`（归并入标准名）/ `is_new`（确为新岗位）/
  `keep_original`（保持原样）。三者互斥，标注时只能选其一为主。

### 4.2 技能名归一（`gold_action` / `gold_standard`）

- `merge`：变体与标准名是**同一技能**（缩写↔全称、大小写、版本、中英对应）。
  此时 `gold_standard` 必须填入候选清单中的原样标准名（不得自创）。
- `keep`：变体与任何标准名的语义关系不确定，或该缩写无关联（≤6 位全大写缩写
  优先 `keep`）。`gold_standard`＝`variant`。
- `noise`：明显非技能（岗位名 / 教材名 / 平台名 / 业务活动词）。
- 裁决优先级：`noise` > 明确同义 `merge` > 拿不准 `keep`。**拿不准就 `keep`**，
  `keep` 不是错误，过度 `merge` 才是。

---

## 5. 标注流程与一致性（对齐既有规范）

1. **盲标**：每条数据由 2 位标注者**独立**标注，互不参考对方结果，也不参考机器建议
   （见 §3 锚定偏差警示）。
2. **一致性检查**：计算 Cohen's Kappa（按 `gold_action` / 岗位名三选一决策）。
3. **阈值**：**Kappa ≥ 0.7** 方可入库为正式金标准。
   - Kappa < 0.7：进入第 4 步仲裁。
4. **仲裁**：分歧项由第 3 位标注者裁决；仲裁后仍分歧的项**剔除**并记录原因
   （不作为金标准，避免硬凑一致性）。
5. **转正**：达到 Kappa ≥ 0.7 的行，标注者把 `annotation_status` 改为 `human`、
   `needs_human` 改为 `false`，`gold_*` 填入人类裁决值。
6. **质量复核**：随机抽取 ≥10% 已转正行做抽查，确认 `gold_*` 与 `annotation_status`
   一致（防机器建议被误当人类金标准）。

---

## 6. 机器建议与金标准的关系（诚实条款）

- 本 DRAFT 集所有 `suggested_*` 与初始 `gold_*` 均为**机器自动建议**，仅作参考。
- 机器建议**不是**金标准，也**不**代表"正确"答案。人类标注者应**独立**裁决，
  并有权覆盖任何机器建议。
- 在 `annotation_status` 仍为 `draft_auto` 时，禁止把该行 `gold_*` 作为任何
  评估的最终答案（评估脚本只在 `annotation_status=="human"` 的行上做正式报告，
  机器建议行仅作 draft 报告，见 `eval_llm_driven.py::eval_position_normalization`）。

---

## 7. 交付物

- 标注完成后的 `position_normalization_draft.jsonl` / `skill_normalization_draft.jsonl`
  （`annotation_status="human"`）。
- 一份**标注一致性报告**（每行双标结果、Kappa、仲裁记录、剔除行与原因）。
- 报告须注明：该集合已从 DRAFT 转正为**独立人工金标准**，并注明标注者与日期。

---

## 8. 禁止事项

- 严禁 AI 自动填写 `gold_*`、`annotation_status`、`needs_human` 为"人类最终答案"。
- 严禁把机器建议直接复制为人类金标准而不做独立裁决。
- 严禁只标注"预判正确"的行、跳过边界/疑难行（会产生偏倚）。
