# 岗位名 LLM 第二轮审查设计方案

> 状态：已实现·灰度默认关（2026-08-15 评审稿；M1 实验脚本 #460、M2 代码 `position_review.py` #457 已合并，`runtime_config.position_review_enabled` 默认 False；M3 灰度观察与 M4 规则反哺待开启开关后推进）
> 背景：08-15 图谱治理会话发现碎片/泛词岗位名问题，本方案探讨用 LLM 自动审查补充人工规则治理。
> 关联：T-04 碎片岗位名治理（已完成两批规则治理）、幻觉防控三道防线（设计文档 §6.3）

## 1. 背景与问题

### 1.1 现状

LLM 从 JD 标题抽取岗位名时，会产生非标准岗位名（08-15 图谱扫描 89 个低频碎片）：

| 类型 | 示例 | 成因 |
|---|---|---|
| 缩写/产品名当岗位 | GTM、CNO、Pega、Salesforce、CMDB发现 | LLM 把工具/缩写当岗位名 |
| 荒谬组合 | AI 证据、BLT 首席、340B 项目分析、Gemini 应用合作伙伴 | 公司/产品/短语拼成岗位名 |
| 泛词 | AI应用、AI产品、AI 与自动化、IT 支持 | "AI+方向/业务"拼凑，无标准语义 |
| 剥壳碎片 | 智能、人事、应用 | 复杂标题解析不稳 |

### 1.2 现有治理手段与局限

现有治理为**人工规则事后兜底**（全部为 08-09 ~ 08-15 会话积累）：

1. `_POSITION_STOPWORDS`：岗位停用词（精确拦截，不入图）——约 200 词
2. `_POSITION_KEYWORDS`：关键词族 → 标准岗位映射（子串/词边界）
3. `_GENERIC_ROUTED_FAMILIES` + `_POSITION_SKILL_ROUTING`：失真兜底族按 JD 技能路由
4. `_POSITION_WHITELIST`：白名单岗位（精确保留）
5. `_TECH_STACKS`：技术栈细分维度保留

**局限**：
- **长尾覆盖不足**：每个新碎片名都要人工审计后补规则（08-15 两批治理共拦截 66 词，图谱仍有 50 个低频非标准名观察中）
- **空格变体漏网**：LLM 输出空格波动（"CMDB 发现" vs "CMDB发现"）需双写规则
- **规则判定机械**：子串匹配无法理解语义（"AI 应用" 命中算法族、"应用AI客户" 语义是业务岗）
- **人工成本**：每次图谱扫描 → 人工分类 → 补规则，闭环周期长

## 2. 方案目标与定位

**目标**：用 LLM 对规则未拦截的岗位名做质量审查（分类 + 可选修正），自动覆盖长尾碎片/泛词。

**定位：幻觉防控第四道防线**（抽取后审查），**不替代**现有规则：

```
防线1 规则归一化（normalize_position_name，最快、可复现）
防线2 停用词/白名单（精确语义）
防线3 技能路由（失真兜底族）
防线4 [新增] LLM 岗位名审查（长尾兜底）
```

**原则**（与项目既有 LLM 兜底模式一致）：
- LLM 增强 + 规则优先 + 门控 + 失败降级
- 审查结果可审计（落库）
- 不一致时规则为准

## 3. 总体架构

```
batch_extract（LLM 抽取 JD）
  → normalize_position_name（防线 1-3，规则拦截）
  → 未拦截且（低频/未知）岗位名
      → LLM 审查（防线 4）：{valid, category, standard_name, reason}
        → 门控：
          ① instructor/pydantic 强校验
          ② valid=false → 岗位名标记 invalid，不入图（走 rejected 路径）
          ③ valid=true 且给出 standard_name → 必须通过 normalize_position_name
             校验（与规则库一致才采用，否则只标记不采用）
        → 落库 snapshot.position_review（可审计）
  → 入图 / 写 extraction
```

## 4. 详细设计

### 4.1 触发条件

只审查**规则未拦截**的岗位名，避免全量审查成本：

- `normalize_position_name(name, skills)` 原样返回（未被关键词族/停用词/白名单处理）
- 且为低频/新出现（jd 频次 < 5 或首次出现）——高频岗位名已被市场验证，不审
- 单条 LLM 调用（不批量），超时 15s，失败降级跳过

### 4.2 Prompt 设计（草案）

```
系统：你是岗位名质量审查助手。判断给定的岗位名是否为有效的标准
岗位名，并给出修正建议。只依据通用招聘市场常识，不臆造。

任务：判断岗位名 "{name}"（该岗位 JD 包含技能：{skills}）
输出 JSON：
{
  "valid": bool,            // 是否为有效岗位名
  "category": "standard|generic|abbreviation|company|gibberish|other",
                            // standard=有效标准名；generic=泛词；
                            // abbreviation=缩写/产品名；company=公司名；
                            // gibberish=荒谬组合/拼凑
  "standard_name": str|null, // valid=true 且可修正时给出标准岗位名
  "reason": str             // 判断依据（一句话）
}
要求：
1. 只识别"岗位"名（如"算法工程师""产品经理"），工具/平台/缩写/公司名
   不是岗位名（category=abbreviation/company）
2. 泛词（"AI应用""IT支持"）category=generic；若技能可明确方向可给
   standard_name（如技能含"计算机视觉"→"机器视觉算法工程师"）
3. 不确定时 valid=true 保守保留（宁可不审，不可误杀）
```

### 4.3 Schema（Pydantic 强校验）

```python
class PositionReviewResult(BaseModel):
    valid: bool
    category: Literal["standard", "generic", "abbreviation", "company", "gibberish", "other"]
    standard_name: Optional[str] = None
    reason: str = ""

    # 后置校验：standard_name 非空时必须能通过 normalize 校验（否则拒绝采用）
    @model_validator(mode="after")
    def _check_standard_name(self):
        if self.standard_name:
            if not normalize_position_name(self.standard_name, skills):
                self.standard_name = None  # 与规则库不一致 → 只标记不采用
        return self
```

### 4.4 门控与决策表

| 审查结果 | 处置 |
|---|---|
| valid=false（generic/abbreviation/company/gibberish） | 岗位名标记 invalid → 不入图；写 `snapshot.position_review={valid:false, category, reason}` |
| valid=true, standard_name=null | 保留原名入图（保守） |
| valid=true, standard_name≠null 且通过 normalize 校验 | 用 standard_name 替换（等价于动态映射）；记录 `position_review={original, standard_name}` 供审计 |
| standard_name 未通过 normalize 校验 | 保留原名，仅记录（不一致以规则为准） |
| LLM 不可用/超时/校验失败 | 静默降级：保留原名（与 RAG 接地同语义） |

### 4.5 落库与审计

- 审查结果写 `snapshot["position_review"]`（含 original/category/standard_name/reason/reviewed_at）
- 可追溯：按 `position_review` 字段反查审查历史，评估审查准确率
- 与 `extraction_error`、`rejected_changes`（§11.4.1）同级审计语义

### 4.6 ETL 接入点

- 位置：`batch_extract` 抽取后、`normalize_position_name` 与入图之间（阶段 3 内）
- 独立函数 `review_position_name(name, skills, llm)`（纯逻辑，可单测）
- 配置开关：`configs/` 或 env（如 `POSITION_REVIEW_ENABLED`），默认关闭（先实验后启用）

## 5. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| LLM 误杀（valid=false 误判标准岗位） | 真实岗位不入图 | prompt 保守原则（不确定保留）；只对低频新名审查；人工审计抽查 |
| LLM 修正名与规则库冲突 | 图谱出现白名单外新名 | 门控③：修正名必须过 normalize 校验，不一致只标记 |
| 一致性波动 | 同岗位名不同批次审查结果不同 | 结果落库 + 规则层仍权威；若发现审查错误 → 直接补规则（审查结果反哺规则库） |
| 成本 | 每轮 ETL 增加 LLM 调用 | 只审规则未拦截的低频新名（每天预计 < 50 条），单条 15s 超时 |
| 存量碎片 | 已入图碎片不受影响 | 审查只解决新抽取；存量由规则治理 + 数据清理（T-04 已完成两批） |
| 雪球效应（审查错误被当作规则） | 错误映射扩散 | 审查结果**不写规则库**（只落库标记），规则变更仍需人工确认 |

## 6. 验证计划（先实验后上线）

### 阶段 M1：实验验证（需要 LLM key 环境）

1. 取当前图谱 50 个低频非标准岗位名作为测试集（08-15 扫描清单）
2. LLM 审查 → 人工核对
3. **通过标准**：
   - 分类准确率 ≥ 90%（generic/abbreviation/company/gibberish 识别）
   - 修正映射准确率 ≥ 80%（standard_name 与人工判定一致）
   - 误杀率 = 0（无标准岗位被 valid=false）
4. 未达标 → 迭代 prompt 或放弃修正只保留标记

### 阶段 M2：代码实现

- `app/services/extraction/position_review.py`（prompt + schema + 审查函数 + 门控）
- `batch_extract` 接入（默认关闭开关）
- 单测：mock LLM 覆盖决策表全分支（valid 各 category + 门控拒绝 + 降级）

### 阶段 M3：灰度上线

- 开启审查 → 观察一轮 ETL（审计 position_review 结果）
- 抽查审查质量（对比规则层结果）

### 阶段 M4：反哺规则库

- 审查结果中稳定的映射（多次一致）→ 人工确认后补入 `_POSITION_KEYWORDS`/`_POSITION_STOPWORDS`
- 形成"LLM 发现 → 人工确认 → 规则固化"的治理闭环

## 7. 不做的事（边界）

- 不替代 normalize_position_name（规则层始终先执行）
- 不自动写规则库（规则变更需人工确认）
- 不审查高频岗位名（市场已验证）
- 不处理存量碎片（规则治理 + 数据清理负责）

## 8. 关联项

- T-04 碎片治理（规则层，已完成两批）：`_POSITION_STOPWORDS` 66 词 + `_GENERIC_ROUTED_FAMILIES` 32 词
- 幻觉防控三道防线：设计文档 §6.3
- LLM 兜底先例：`cluster_llm.py`（图算法）、`grounding.py`（RAG 定义草案）、`judge_eval.py`（评测）
