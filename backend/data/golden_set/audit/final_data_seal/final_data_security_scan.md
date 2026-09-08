# Final Data Seal — 安全扫描报告

- 审计时间（北京时间）：2026-09-08 15:20 UTC+08:00
- 扫描范围：candidate_pool/v1、final/、candidate_pool/official_career_50、audit/unified_jd_audit 的数据文件，外加基线刷新后 golden_set/ 根目录新增评测集（education_semantics_v1 / golden_set_match_v3 / position_domain_eval），共 39 个文件（后缀 .jsonl/.csv/.json/.md/.txt）
- 扫描方式：高置信正则（sk- key / AWS AK / Bearer token / 凭据赋值 / .env 引用）；**不打印任何 secret 正文**
- 疑似项：**0**

**结论：未发现 API key / Bearer token / password / secret / .env 凭据 / LLM provider credential 泄漏。**

说明：JD 正文可能出现 “token/password” 等业务词汇（如岗位技能要求），此类为招聘文本内容而非凭据赋值，不构成 secret 泄漏；扫描规则要求 `key=value`/`Bearer ` 等凭据形态才命中。