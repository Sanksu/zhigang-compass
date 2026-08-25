# 数据字典 — 归一化结果 DRAFT 集（岗位名 / 技能名）

> 本文件解释 `data/golden_set/llm_driven/` 下两个 **DRAFT** 集合的字段含义、
> 生成来源、以及人工校准规则。两份文件都是**机器建议草稿**，不是人类金标准。

---

## 0. 一份诚实的入口说明

`position_normalization_draft.jsonl` 与 `skill_normalization_draft.jsonl` 中的：

- `annotation_status` 恒为 **`draft_auto`**：表示该行标签由确定性规则 / 词面启发式
  自动生成，**未经任何人类裁决**。
- `needs_human` 恒为 **`true`**：表示每一行都必须由人类标注者复核后才能成为金标准。

**机器建议 ≠ 金标准。** 本文件与 `annotation_guideline.md` 共同说明：人类标注者有权
（且应当）覆盖任何自动建议。覆盖后，将 `annotation_status` 改为 `human`、
`needs_human` 改为 `false`，并把 `gold_*` 字段改写为人类裁决值。

---

## 1. position_normalization_draft.jsonl

用于同时 (a) 评分 `app/services/llm_decision/position_name.py::decide_position_name`
（LLM 决策器）以及 (b) 校准 `app/services/extraction/dictionary.py::normalize_position_name`
（规则路径）。每条记录来自一个真实 JD（`final/jd_golden_110.jsonl`）或一个代表性英文
岗位名（`_EN_POSITION_MAP`）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 记录唯一标识，`pos_<sample_id>`（JD 派生）或 `pos_en_<n>`（纯英文切片） |
| `raw_title` | string | 原始岗位标题（JD 的 `job_title_raw`，或英文映射键） |
| `source` | string | 来源平台（`zhilian` / `synthetic_en`） |
| `skills` | string[] | JD 抽取技能名（LLM 决策器 prompt 的证据之一）；英文切片为空 |
| `candidates` | string[] | 候选标准岗位名（≤8 个）。来自仓库内确定性岗位词表（`_POSITION_WHITELIST` ∪ `_POSITION_KEYWORDS` 族 ∪ `_POSITION_SKILL_ROUTING` 兜底族），等价生产 `PositionCandidateRecaller` 的词面回退路径 |
| `gold_canonical` | string | **目标字段（初值=机器建议）。** 该标题应归到的标准岗位名；`gold_keep_original=true` 时为空串（表示无标准名，保持原样） |
| `gold_is_new` | bool | **目标字段。** 是否构成新岗位（不在候选内且语义独立）；初值恒 `false`（建议先归到已知候选） |
| `gold_keep_original` | bool | **目标字段。** 是否保持原始标题不改名。初值：规则归一为空（reject / intern / 失真无技能）为 `true`，其余 `false` |
| `slice` | string | 切片：`cjk`（纯中文）/ `mixed`（中英混合）/ `intern`（实习岗）/ `reject`（规则归一为空，含停用词/技能词/失真无技能）/ `pure_en`（纯英文） |
| `source_note` | string | 派生来源（`jd_golden_110.jsonl #<id>` 或 `_EN_POSITION_MAP`） |
| `gold_title_ref` | string | 参考值：JD 黄金集已有的人工 `gold_title`（仅参考，**不等于** `gold_canonical`——两者口径不同，恰是人工需裁决之处） |
| `suggested_canonical` | string | 机器建议的标准名（= `gold_canonical` 初值，镜像冗余以显式区分） |
| `suggested_is_new` | bool | 机器建议是否新岗位 |
| `suggested_keep_original` | bool | 机器建议是否保持原样 |
| `suggested_via` | string | 建议产出来源：`normalize_position_name`（规则主路径）/ `_translate_en_position`（英文翻译）/ `norm_reject` / `norm_intern` |
| `annotation_status` | string | 恒为 `draft_auto`（机器建议草稿，未人工裁决） |
| `needs_human` | bool | 恒为 `true` |

### 1.1 人工如何裁决（采样详见 annotation_guideline.md §3）

- **gold_canonical**：人类标注者根据市场常识，从 `candidates` 中选取该标题应归到的
  标准岗位名（与原始标题同语言）。若确属候选之外的新岗位，置 `gold_is_new=true`、
  `gold_canonical` 填合理标准名；若规则归一为空但实际是合法岗位（如"单片机工程师"
  当前被规则拦截），人类应放宽并填标准名、置 `gold_keep_original=false`。
- **gold_is_new**：仅当该岗位语义不在任何候选且确构成新岗位时 true。
- **gold_keep_original**：标题本身即标准名、或行业惯例不加改写时 true。

> 诚实提示：`candidates` 是按词面召回的**机器候选**，不是金标准清单。人类标注者
> 不被 `candidates` 绑定——可认为 `candidates` 之外的合理标准名应新建（`is_new=true`）。

---

## 2. skill_normalization_draft.jsonl

用于校准 `app/services/llm_decision/skill_normalize.py::decide_skill_normalize`
（LLM 决策器）。与 `normalization_150.jsonl`（纯别名派生）不同，本集合**刻意纳入
需要人类裁决的硬案例**，使人工校准后的集合比别名派生集更独立。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 记录唯一标识 `skill_draft_<n>` |
| `variant` | string | 技能名变体 / 口语表述（LLM 决策器输入） |
| `gold_action` | string | **目标字段。** `merge`（归并到标准名）/ `keep`（保持独立）/ `noise`（判定噪声）。初值=机器建议 |
| `gold_standard` | string | **目标字段。** `merge` 时的标准技能名；`keep` 时等于 `variant`。初值=机器建议 |
| `gold_keep` | bool | 便捷布尔：`gold_action == "keep"` |
| `slice` | string | 人工裁决切片：`near_synonym`（近似同义）/ `same_initial`（同首字母异义）/ `short_ascii`（短大写缩写保护）/ `version_variant`（版本/语种变体）/ `cjk_abbr`（中文缩写口语） |
| `source_note` | string | 该案例的人工裁决主题（为何需要人类判断） |
| `candidates` | string[] | 候选标准技能名（≤15），来自 `SKILL_WHITELIST` ∪ `_ALIAS_STANDARDS`；若命中 `SKILL_ALIAS` 则其落点置顶 |
| `suggested_action` | string | 机器建议动作 |
| `suggested_standard` | string | 机器建议标准名 |
| `suggested_via` | string | 建议产出来源：`SKILL_ALIAS` / `candidate_rank_key` / `candidate_rank_key_keep` / `ascii_short_protect` |
| `annotation_status` | string | 恒为 `draft_auto`（未人工裁决） |
| `needs_human` | bool | 恒为 `true` |

### 2.1 切片说明（人类裁决重点）

- **near_synonym**：近似同义，机器可并但边界模糊（如"大模型" vs "大语言模型"、
  "全栈开发" vs "全栈"）。人类须判断是"同一技能"还是"细分技能"。
- **same_initial**：同首字母但意义不同（如 `AS`、`GIS`、`ID`、`UI`）。词面最易误并，
  人类须靠领域上下文裁决，无把握时选择 `keep`。
- **short_ascii**：≤6 位全大写缩写（`AI`、`API`、`SQL`、`SRE`、`AWS`…）。原则上保护
  独立，但人类须判断是否与中文全称合并（如 AI 与"人工智能"）。
- **version_variant**：版本 / 语种变体（`Python3`、`Vue2`、`React 18`）。机器按规则归并
  去版本，人类裁决是否保留版本语义。
- **cjk_abbr**：中文缩写 / 口语（"小程序"、"AI编程"、"网络攻防"）。人类裁决是否归一。

---

## 3. 生成来源（可复现）

两个 DRAFT 文件均由 `backend/scripts/freeze_norm_gold_draft.py` 从仓库内确定性事实源
生成（无外部依赖、无网络、无 LLM）：

- 岗位名：`final/jd_golden_110.jsonl`（`job_title_raw` + `gold_skills`）与
  `dictionary.py::_EN_POSITION_MAP`（纯英文切片）。
- 技能名：`dictionary_data.py::SKILL_ALIAS` + `dictionary.py::SKILL_WHITELIST` /
  `_ALIAS_STANDARDS` + 人工设定的**裁决主题清单**（`_SKILL_HARD_CASES`）。

重跑 `uv run python scripts/freeze_norm_gold_draft.py` 得到同样的集合（确定性，
无随机源）。脚本只**生成**，**不**声称任何行为人类金标准。
