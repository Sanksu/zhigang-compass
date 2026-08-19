# jd_golden_110 导出 QA 报告

生成时间（北京时间）：2026-08-18 22:54:14

## 【源冻结检查】
- A01_FINAL SHA256: `e1d1982c8cef18e5e155e78ddd3f04b7140cf878e6dd308e65ef4f7440742a86`
- manifest SHA256: `e1d1982c8cef18e5e155e78ddd3f04b7140cf878e6dd308e65ef4f7440742a86`
- 一致性：✅ PASS

## 【JSONL结构】
- 路径：`D:\du_yan\jiebang_guashuai_jingsai\人工标注工作区\final\export\jd_golden_110.jsonl`
- 大小：391398 bytes (382.22 KB)
- SHA256：`ceedfa6987fee665ea53f17678e8f06cb197a632bc828f99c5b962615c508061`
- 行数：110 / 110  ✅ PASS
- 唯一sample_id：110 / 110  ✅ PASS
- 顺序 ANN-0001~ANN-0110：✅ PASS

## 【Excel ↔ JSONL Gold逐字段一致性】
- title 一致：110/110  ✅ PASS
- skills 一致：110/110  ✅ PASS
- bonus 一致：110/110  ✅ PASS
- experience 一致：110/110  ✅ PASS
- education 一致：110/110  ✅ PASS
- core_duties 一致：110/110  ✅ PASS

## 【原始JD证据一致性】
- job_title_raw 一致：110/110  ✅ PASS
- detail_raw_text 一致：110/110  ✅ PASS
- responsibilities 一致：110/110  ✅ PASS
- requirements 一致：110/110  ✅ PASS
- source_id 一致：110/110  ✅ PASS
- source_url 一致：110/110  ✅ PASS

## 【空值处理】
- gold_experience null 数量：2
- ANN-0042 gold_experience == null：✅ PASS
- ANN-0043 gold_experience == null：✅ PASS
- gold_education null 数量：1

## 【ANN-0042/0043经验】
- ANN-0042 gold_experience：None
- ANN-0043 gold_experience：None

## 【禁止字段泄漏检查】
- 禁止字段命中总数：0  ✅ PASS

## 【标签统计】
- 样本总数：110
- gold_skills：总出现 1460 次 / 唯一 686 个 / 平均 13.27 / 最小 4 / 最大 31
- gold_bonus_skills：非空样本 46 / 总 106 次
- gold_experience：null 2 / 非 null 108
- gold_education：{'本科': 83, '硕士': 11, '大专': 12, '博士': 1, 'null': 1, '不限': 2}
- gold_core_duties：平均 4.68 / 最小 2 / 最大 8

## 【SHA256】
- source（A01_FINAL.xlsx）：`e1d1982c8cef18e5e155e78ddd3f04b7140cf878e6dd308e65ef4f7440742a86`
- export（jd_golden_110.jsonl）：`ceedfa6987fee665ea53f17678e8f06cb197a632bc828f99c5b962615c508061`

## 【机器读取】
- json.loads 成功：110 / 110  ✅ PASS
- 类型校验失败：0  ✅ PASS

## 【最终结论】
# **EXPORT_PASS**