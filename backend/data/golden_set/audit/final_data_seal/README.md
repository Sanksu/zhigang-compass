# Final Data Seal — 数据模块最终封板审计

- 审计时间（北京时间）：2026-09-08 15:20 UTC+08:00
- 分支：feature/data-final-seal（audit_base_sha = 0c111e97d26af7030a7fb9244615b8d5b18367d3，ff-only 刷新自 c44504303aaa）
- 最终 Gate：**DATA_MODULE_READY_FOR_FINAL_SUBMISSION**

## 目录产物

| 文件 | 说明 |
|---|---|
| final_data_seal_report.md | 封板审计主报告（§二十 全部章节） |
| final_data_manifest.csv | DATA_ASSET_MANIFEST（资产路径/角色/记录数/来源/is_gold/eval·train 许可） |
| final_data_checksums.csv | 全部被审计文件的 exists/records/size/SHA256 |
| final_data_quality_summary.json | 结构化全量审计结果（机器可读） |
| final_data_security_scan.md | 安全扫描报告（脱敏，不含任何 secret 正文） |
| scripts/final_data_seal_audit.py | 只读审计脚本（stdlib，无第三方依赖，不调用 LLM） |

## 复跑方式

```powershell
# 在仓库根目录，任意 Python 3.10+（仅标准库）
python backend/data/golden_set/audit/final_data_seal/scripts/final_data_seal_audit.py
```

脚本只读四类数据资产（candidate_pool/v1、final、official_career_50、audit/unified_jd_audit），
仅向本目录写产物；不修改任何数据、不调用 LLM、不自动修复。

## 口径说明

- Zhilian SHA 公式与 audit/unified_jd_audit 验收脚本一致：SHA256(responsibilities + "\n" + requirements)
- Gold↔Zhilian 110 条 source_id 重叠为 EXPECTED_PARENT_CHILD_OVERLAP（设计内父子关系），不报 DATA_LEAKAGE
- Official Career 2 条 future publish_time 为 SOURCE_REPORTED_FUTURE_TIME，保留 source 原值
- 训练许可：Gold 评测专用（exclusion manifest 排除训练）；无明确规则处标 NOT_DEFINED_DO_NOT_ASSUME
