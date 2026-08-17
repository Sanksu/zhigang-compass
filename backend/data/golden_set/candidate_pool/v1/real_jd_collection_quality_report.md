# 候选池采集质量报告

> **生成时间**: 2026-08-17 11:28:33
> **处理脚本**: merge_and_clean.py
> **SimHash 实现**: 项目标准 SimHash (app/services/data_quality/simhash.py)

---

## 一、原始数据

| 批次 | 文件 | 原始记录 | 成功解析 | 损坏 |
|------|------|---------|---------|------|
| v1/batch_data_ai.json | 30 | 30 | 0 |
| v1/batch_embedded_security.json | 26 | 26 | 0 |
| v1/batch_backend_fullstack.jsonl | 30 | 30 | 0 |
| v1/batch_frontend_test_ops.jsonl | 30 | 30 | 0 |
| real_jd_batch_20260815.json | 22 | 22 | 0 |
| real_jd_pilot_20.jsonl | 20 | 20 | 0 |

| **合计** | **158** | **158** | **0** |

### Pilot 重叠检测

| 指标 | 数量 |
|------|------|
| Pilot 总记录数 | 20 |
| Pilot 中与后续批次重叠 | 0 |
| Pilot 中独有的 | 20 |

### 合并后唯一记录

| 指标 | 数量 |
|------|------|
| 原始汇总 | 158 |
| 按 source_id 去重后 | 158 |

---

## 二、数据质量

| 质量指标 | 数量 |
|----------|------|
| 完整正文 (≥200字) | 141 |
| 低信息 (<200字) | 17 |
| 学历冲突 | 2 |
| 经验冲突 | 4 |
| 精确重复 (SHA-256) | 0 |
| 近似重复 (SimHash ≤3) | 0 对 |
| 无法追溯 (无URL) | 0 |
| 严重异常 (空正文/无标题/内容缺失) | 0 |

---

## 三、数据分层

| 层级 | 数量 | 占比 |
|------|------|------|
| **accepted** | 135 | 85.4% |
| **review_required** | 23 | 14.6% |
| **rejected** | 0 | 0.0% |
| **可进入人工标注** | 158 | 100.0% |

> ✅ 达标: accepted = 135 ≥ 100 条

### Review Required 原因分布

| 原因 | 数量 |
|------|------|
| low_information | 17 |
| experience_conflict | 4 |
| education_conflict | 2 |

### Rejected 原因分布

| 原因 | 数量 |
|------|------|

---

## 四、岗位覆盖 (accepted + review_required)

| 岗位类别 | 数量 | 占比 |
|----------|------|------|
| 后端开发 | 40 | 25.3% |
| AI/大模型 | 18 | 11.4% |
| 全栈开发 | 16 | 10.1% |
| 算法 | 15 | 9.5% |
| 嵌入式/C++ | 15 | 9.5% |
| 运维/DevOps | 12 | 7.6% |
| 测试 | 11 | 7.0% |
| 数据工程/大数据 | 10 | 6.3% |
| 前端开发 | 9 | 5.7% |
| 数据分析 | 7 | 4.4% |
| 网络/安全 | 3 | 1.9% |
| 其他技术岗 | 2 | 1.3% |

### Accepted 岗位覆盖

| 岗位类别 | 数量 | 占比 |
|----------|------|------|
| 后端开发 | 32 | 23.7% |
| AI/大模型 | 17 | 12.6% |
| 算法 | 15 | 11.1% |
| 嵌入式/C++ | 13 | 9.6% |
| 全栈开发 | 13 | 9.6% |
| 运维/DevOps | 12 | 8.9% |
| 数据工程/大数据 | 10 | 7.4% |
| 前端开发 | 8 | 5.9% |
| 数据分析 | 5 | 3.7% |
| 测试 | 5 | 3.7% |
| 网络/安全 | 3 | 2.2% |
| 其他技术岗 | 2 | 1.5% |

---

## 五、来源覆盖

| 平台 | 数量 | 占比 |
|------|------|------|
| zhilian | 158 | 100.0% |

---

## 六、字段完整率

| 字段 | 填充率 |
|------|--------|
| job_title_raw | 100.0% |
| company_name | 100.0% |
| location | 100.0% |
| salary | 70.9% |
| source_education | 100.0% |
| source_experience | 100.0% |
| text_education | 89.9% |
| text_experience | 65.8% |
| responsibilities | 100.0% |
| requirements | 98.7% |
| detail_raw_text | 100.0% |
| source_url | 100.0% |
| publish_time | 8.9% |

---

## 七、处理说明

1. **文件完整性**: 读取 6 个输入文件，全部成功解析
2. **Pilot 重叠**: 通过 source_id 精确匹配，检测到 0 条重叠，已合并保留信息更完整版本
3. **字段统一**: 所有记录统一为 21 个标准字段
4. **SHA-256**: 基于 `responsibilities + "\n" + requirements` 使用 `hashlib.sha256` 计算 64 位完整 SHA-256
5. **精确去重**: 依次检查 source_id → source_url → SHA256，保留信息更完整的一条，记录 `duplicate_of` 和 `duplicate_reason`
6. **SimHash 近似去重**: 使用项目标准 SimHash 实现 (`app/services/data_quality/simhash.py`)，仅对 `responsibilities + requirements` 计算，汉明距 ≤ 3 标记为 `duplicate_review_required`
7. **数据分层规则**:
   - **rejected**: 空正文 / 无URL / 无标题 / 精确重复 / 内容缺失
   - **review_required**: education_conflict / experience_conflict / low_information / approximate_duplicate
   - **accepted**: 通过所有检查，无冲突，无问题
8. **已知局限**: `publish_time` 字段全部为空（智联招聘数据源共性）

---

*报告由 merge_and_clean.py 自动生成*
