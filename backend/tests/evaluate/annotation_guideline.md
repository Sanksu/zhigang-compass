# 标注规范

> 黄金集标注规范，确保多人标注一致性（Kappa ≥ 0.7）。

## 1. JD 标注规范

### 1.1 标注字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| gold_title | string | ✅ | 标准化岗位名（如"后端开发工程师"而非"后端"） |
| gold_skills | string[] | ✅ | 必备技能，归一化后（如"Python"而非"python3"） |
| gold_bonus_skills | string[] | ✅ | 加分技能 |
| gold_experience | object | ✅ | {min_years, max_years} |
| gold_education | string | ✅ | 学历要求（博士/硕士/本科/大专/不限） |
| gold_core_duties | string[] | ✅ | 核心职责（≤ 5 条） |

### 1.2 技能归一化规则

- 统一英文大小写：`Python`（首字母大写）
- 去除版本号：`Python3` → `Python`，`React 18` → `React`
- 合并同义词：`JS` → `JavaScript`，`TS` → `TypeScript`
- 保留括号备注：`Docker（含 Kubernetes）`

### 1.3 JSONL 格式

```json
{"id": "jd_001", "source": "BOSS", "raw_text": "...", "gold_skills": ["Python", "FastAPI", "PostgreSQL"], "gold_title": "后端开发工程师"}
```

## 2. 简历标注规范

### 2.1 标注字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | string | ✅ | 姓名（脱敏后标注） |
| phone | string | ✅ | 手机号（脱敏后标注） |
| email | string | ✅ | 邮箱（脱敏后标注） |
| education | object[] | ✅ | [{school, major, degree, start, end}] |
| work_experience | object[] | ✅ | [{company, position, start, end, description}] |
| skills | object[] | ✅ | [{name, proficiency}] proficiency: 精通/熟悉/了解 |
| projects | object[] | ✅ | [{name, role, start, end, tech_stack}] |

## 3. 匹配标注规范

### 3.1 标注字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| resume_id | string | ✅ | 简历 ID |
| position_id | string | ✅ | 岗位 ID |
| match_score | float | ✅ | 0-1，≥ 0.6 为匹配 |
| match_label | bool | ✅ | true=匹配，false=不匹配 |
| top_k_rank | int | ✅ | 该简历对全量岗位推荐中的排名（1=最佳） |

## 4. 标注一致性

- 每条数据由 2 人独立标注
- 分歧项由第 3 人仲裁
- Cohen's Kappa ≥ 0.7 方可入库
- 标注完成后导出 `golden_set_*.jsonl`，提交至 `backend/data/golden_set/` 目录
