# jd_golden_110.jsonl 数据字典

## 1. 数据集用途
本数据集用于「智岗罗盘」Round1 岗位能力标注的模型评测 / 算法训练的最终 Gold 标签集（A01 单标注员最终确认），为 JD→结构化能力（title、required skills、bonus skills、经验范围、学历范围、core duties）的监督数据。

## 2. 样本数
- 总量 110 条（ANN-0001～ANN-0110）

## 3. 数据来源
- 唯一 Gold 源：`final/jd_annotation_round1_110_A01_FINAL.xlsx`
- 标注：single_annotator_human_review_with_final_QA（A01 单人最终确认 + 最终 QA，非双人独立标注）
- 规则：标注说明 规则 1～14
- 标注员一致性指标：不得描述为双人独立标注，不得虚构 Cohen's Kappa。

## 4～10. 字段说明表

| 字段名 | 类型 | 含义 | 模型输入 | Gold标签 | null语义 |
|---|---|---|---|---|---|
| sample_id | string | 样本唯一编号 ANN-NNNN | 是 | 否 | 永不 null |
| source | string|null | 原始来源平台 | 是 | 否 | 来源字段缺值 |
| source_id | string|null | 源平台ID | 是 | 否 | 源无平台ID |
| source_url | string|null | 源JD URL | 是 | 否 | 无URL时为null |
| job_title_raw | string|null | 原始招聘标题 | 是 | 否 | 正文无标题时null |
| company_name | string|null | 公司名称 | 是 | 否 | 未提供时null |
| location | string|null | 工作地点 | 是 | 否 | 未提供时null |
| detail_raw_text | string|null | 原始JD全文 | 是 | 否 | 正文缺失时null |
| responsibilities | string|null | 职责片段 | 是 | 否 | 未提供时null |
| requirements | string|null | 任职要求片段 | 是 | 否 | 未提供时null |
| source_education | string|null | 列表页学历 | 是（对比参考） | 否 | 未提供时null |
| source_experience | string|null | 列表页经验 | 是（对比参考） | 否 | 未提供时null |
| text_education | string|null | 正文学历抽取候选 | 是（对比参考） | 否 | 未抽取到时null |
| text_experience | string|null | 正文经验抽取候选 | 是（对比参考） | 否 | 未抽取到时null |
| education_conflict | string|null | 学历冲突标记 | 否 | 否 | 无冲突时null |
| experience_conflict | string|null | 经验冲突标记 | 否 | 否 | 无冲突时null |
| gold_title | string | Gold规范化岗位名称 | 否 | 是 | 理论永不null；若空为数据异常（已在QA检查） |
| gold_skills | string[] | required原子技能数组，JSON真实数组，不是字符串外壳 | 否 | 是 | 空数组表示空集合 |
| gold_bonus_skills | string[] | bonus原子技能数组（优先/加分/更佳项） | 否 | 是 | 空数组表示无bonus |
| gold_experience | object|null | 形如 {"min_years":int, "max_years":int|null}；无明确经验且不适用最低准入时必须为 null | 否 | 是 | 正文+列表均无经验信息，不代表 0 年经验 |
| gold_education | string|null | 枚举：大专/本科/硕士/博士/不限 之一；未知留 null，未在校学生≠不限 | 否 | 是 | 未知，不能视为不限；在校学生保留学历判断待澄清 |
| gold_core_duties | string[] | 3～6条职责概括 JSON 真实字符串数组 | 否 | 是 | 空数组为异常 |

## 11. OR / 至少N种关系
技能数组 gold_skills / gold_bonus_skills 以平铺形式存在，不编码 OR / 数量逻辑关系。这类关系在人工审核过程中通过 review_note 判定后，最终以人工结论为准，平铺输出数组。如需关系信息，需结合同一 JSONL 行的 requirements / text_experience 与规则 1～14 做后处理重建。

## 12. 标注方式声明
single_annotator_human_review_with_final_QA。
- 不存在第二名独立标注员。
- 不得描述为双人独立标注。
- 不得虚构 Cohen's Kappa / Krippendorff's Alpha 等跨标注员一致性指标。