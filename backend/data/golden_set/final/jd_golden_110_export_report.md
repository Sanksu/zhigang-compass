# jd_golden_110 导出 QA 报告

生成时间（北京时间）：2026-08-18 22:54:14

## 【源冻结检查】
- A01_FINAL SHA256: `e1d1982c8cef18e5e155e78ddd3f04b7140cf878e6dd308e65ef4f7440742a86`
- manifest 中 source_sha256 与该值一致：✅ PASS（注：manifest 文件自身不另行提供哈希，见文末 Revision Note）
- 一致性：✅ PASS

## 【JSONL结构】
- 路径：人工标注工作区 `final/export/jd_golden_110.jsonl`（标注员本地 Excel 源导出）
- 大小：原始导出 391398 bytes (382.22 KB, CRLF)；仓库内版本 391288 bytes (LF)
- SHA256：原始导出 `ceedfa6987fee665ea53f17678e8f06cb197a632bc828f99c5b962615c508061`；仓库内版本 `273351d990d4fa2df825b2292e7c9fdf363ca5528b361a32cd3bbfc8f1f40b5a`
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
- export（jd_golden_110.jsonl，仓库内 LF 版本）：`273351d990d4fa2df825b2292e7c9fdf363ca5528b361a32cd3bbfc8f1f40b5a`

## 【机器读取】
- json.loads 成功：110 / 110  ✅ PASS
- 类型校验失败：0  ✅ PASS

## 【最终结论】
# **EXPORT_PASS**

## 【Revision Note — 2026-08-19 审查修订】

本报告由审查（PR #316 review）修订，内容修订说明：

1. **校验和口径**：原报告 export SHA256 `ceedfa69…` 基于标注员本地 CRLF 导出文件计算；git 入库后换行符转为 LF（391288 字节），原校验和不可复现。manifest 已更新为仓库内版本 SHA256 `273351d9…`（`git hash-object` 可复现），原始 CRLF 版本哈希移入注释保留。
2. **manifest SHA256 笔误修正**：原「manifest SHA256」一行误复制为 source 的 SHA256，已修正为说明性描述。
3. **本地路径移除**：原文包含标注员个人机器绝对路径，已改写为相对描述「人工标注工作区 final/export/」。
4. **core_duties 范围**：样本实测范围 2～8 条（ANN-0075=2、ANN-0085=8），数据字典原声明「3～6 条」已同步修正。
5. **manifest.txt 编码**：原「北京时间」以 GBK 解码产生乱码（鍖椾含鏃堕棿），已修复为 UTF-8。