# bonus（加分项）弱维评审稿 — required/bonus 口径错位分析

> 日期：2026-08-27 · 关联基准：六维评测 r3（`data/golden_set/review/evaluation_110_r3/manual_jd_eval_report.md`，2026-08-26）
> 状态：**待算法评审（张恺天）** · 本文只作分析，不改任何代码 / gold / prompt

---

## 1. 结论先行

bonus 弱维的剩余短板**已从「抽不够」转为「口径错位」**，且**主要责任在 gold 标注口径，而非模型分类**。

- 最新 r3 评测 bonus 纯模型 F1 = **0.644**（precision 0.59 / recall 0.71），aligned（含评测侧确定性补漏）F1 = **0.775**，avg sample F1 = **0.880**。
- 此前记忆中的 **0.7491 为修复前旧值**，已过时。
- **FP=52 > FN=31**：主要问题不是模型漏抽加分项，而是**模型输出的加分项（nice）大量被判为 FP**——根因是 **gold 集把「加分/优先/任选」语境的技能误收进了必备（gold_skills），导致 gold_bonus_skills 大多为空，模型输出的任何 nice 都无 gold 可对齐**。

错误类别统计里「**required/bonus skill mixing: 14 条**」的逐条复盘如下，**11 条是 gold 口径问题，3 条才是模型真分类偏差**。

---

## 2. 逐条证据（14 条 mixing）

mixing 的判定方向有两种：
- **A 类**：`pred_required_matches_gold_bonus` = 模型把技能标为**必备(must)**，但 gold 把它放在**加分(gold_bonus_skills)**——模型「过度晋级」。
- **B 类**：`pred_bonus_matches_gold_required` = 模型把技能标为**加分(nice)**，但 gold 把它放在**必备(gold_skills)**——gold「过度收编」。

### A 类（模型把 gold 加分项标成必备）—— 仅 3 条，模型可能偏保守

| 样本 | 岗位 | 技能 | 正文语境 |
|------|------|------|---------|
| ANN-0063 | 算法工程师（视频） | TensorRT | 「有TensorRT等推理框架**优化经验者优先**」 |
| ANN-0088 | 全栈软件工程师 | Docker, Kubernetes | 「容器化部署（Docker/Kubernetes）**经验者优先**」 |
| ANN-0097 | Java/后端开发工程师 | 分布式技术 | 正文未直接命中 |

这 3 条正文均为明确加分语境，模型标 must 属**分类偏差**（偏保守，与 prompt「把握不准时倾向 nice」相悖）。

### B 类（gold 把加分项收进必备 skills）—— 11 条，gold 口径问题为主

| 样本 | 岗位 | 技能 | 正文语境（是加分项的证据） |
|------|------|------|---------|
| ANN-0005 | ETL数据工程师 | C, C++, C#, Java, Python | 「了解Java、Python、C、C++、C#等语言中的**一种或几种**」 |
| ANN-0006 | 数据分析工程师 | 自动驾驶技术栈 | 「具备自动驾驶数据制备**经验优先**」 |
| ANN-0014 | 数据分析工程师 | DID, 因果推断, 工具变量, 断点回归 | 「熟悉因果推断（**如**DID、断点回归、工具变量等）**...者优先**」 |
| ANN-0016 | 英语高级Python 全栈工程师 | Django, FastAPI, Flask | 「熟练后端框架（FastAPI/Django/Flask **任选**）**+ 前端基础（Vue/React 优先）**」 |
| ANN-0032 | 数据分析及应用工程师 | 数据建模, 数据挖掘 | 正文未直接命中 |
| ANN-0052 | python爬虫工程师AI | Agentic AI, React, SSE, ToolCalling | 「...（SSE）功能」「...ToolCalling工具链」 |
| ANN-0054 | 算法工程师 | APS | 「APS排产排程项目：参与...」 |
| ANN-0055 | 机器学习算法工程师 | 推荐, 用户画像 | 「参与过用户画像建模...**开发工作优先**」 |
| ANN-0056 | AI模型聚合平台后端开发工程师 | API网关, NewAPI | 「有NewAPI二次开发、...API网关开发**经验者优先**」 |
| ANN-0077 | 多模态理解与生成前沿算法研究员 | 机器学习, 自然语言处理 | 「**加分项：** 1、具有优秀的基础算法、扎实的机器学习基础...**者优先**」 |
| ANN-0093 | 后端开发工程师JAVA | 接口安全 | 「有安全相关**经验**（接口安全、风控等）」 |

---

## 3. 核心判断

**B 类 11 条里，绝大多数正文是明确的加分/优先级/任选语境**，与 prompt 里第 106-109 行「条件结构显式规则」完全一致（"优先/任选/一项或多项/如...等" 应标 nice 且不进 skills）。**模型在这些样本上反而符合 prompt 规则**。

问题出在 **gold 标注**：这 11 条被 reviewer 放进了 `gold_skills`（必备），却没放进 `gold_bonus_skills`（加分）。后果：

1. `gold_bonus_skills` 在 gold 集里**大量为空** → bonus 维度「无 gold 可对齐」。
2. 模型按规则输出 nice → 全部落入 `bonus_cmp["fp"]`（因为 gold_bonus 为空）→ **bonus FP 虚高（52）**。
3. 同时这些技能本应计在 skills 维度——模型把它们放 nice 而 gold 放 skills → **skills 维度也丢 recall**。

这是**双输**：既压低了 bonus 的 precision，又压低了 skills 的 recall。根因不是模型，是 **gold 的「必备 vs 加分」标注口径与实际正文语境不一致**。

---

## 4. 需要张恺天拍板的方向

这不是代码 bug，是**评测口径 / gold 标注口径**的一致性决策。候选方向：

- **方向 1（治本，推荐）**：**修订 gold 集**——把这 11 条里正文为加分语境的技能从 `gold_skills` 移到 `gold_bonus_skills`（对齐 prompt 规则 + 生产口径）。评审后 bonus 与 skills 两维分数会更真实。代价：gold 是「冻结基线」（见 llm-driven-golden-baseline-0824 记忆），修订需走 gold 变更流程。
- **方向 2**：维持 gold 现状，调整**评测口径**——接受「加分项归属必备」的历史约定，把 bonus 维度的精确率问题标记为「已知 gold 口径」，不再单列。代价：bonus 弱维永远偏低，掩盖真实模型表现。
- **方向 3**：仅**校准 prompt**——把「把握不准时倾向 nice」改为「严格按显式标记判定」并强化「如/等/任选 优先词 → nice」。但基于上述证据，**模型大多已判对**，改 prompt 收益有限，风险是过度收紧伤 skills recall。

**我的倾向：方向 1**。证据充分指向 gold 口径错位（11/14），修订 gold 是治本；但这必须由张恺天确认 gold 基线变更，本文只定位不执行。

---

## 5. 支撑材料

- 评测报告：`backend/data/golden_set/review/evaluation_110_r3/manual_jd_eval_report.md`
  - bonus_skills_micro 区段（第 57-74 行）：F1 0.644 / aligned 0.775 / avg 0.880
  - error_types「required/bonus skill mixing: 14」（第 332 / 467 行）
  - 文末口径声明「skills 与 requirements[nice] 的映射应在后续算法评审中确认」（第 339 行）
- 逐条 predictions：`backend/data/golden_set/review/evaluation_110_r3/manual_jd_eval_predictions.jsonl`（`comparison.required_bonus_mixing` 字段）
- gold 源：`backend/data/golden_set/final/jd_golden_110.jsonl`（`detail_raw_text` / `gold_skills` / `gold_bonus_skills`）
- prompt 规则：`backend/app/services/extraction/prompts.py` 第 102-119 行（条件结构显式规则 / 反向保护 / 列举式必备判定）
