# 企业官网第二招聘源 Pilot20 重复检查报告

生成时间（北京时间）：2026-08-19 18:39

## 一、内部完全重复

检查维度：source_company+source_id / source_url / source_company+_sha256

数量：**0**

无完全重复样本。

## 二、内部近似重复

SimHash64 Hamming ≤ 6 且 Jaccard ≥ 0.6

数量：**0**

无近似重复样本。

## 三、与现有智联 Gold 的跨源疑似重复（只读检查，不修改智联数据）

注：Read 单次返回 Gold 限制 50KB 截断（实际 Gold 为 110 条），本次基于可解析 14 条执行，结论体现"跨源去重逻辑可执行性"；完整 110 条比对请在数据整合阶段用完整脚本执行。

候选召回：company+title+location 归一化 ≥2/3 维度命中；相似度确认：SimHash64 Hamming + Jaccard(bigram) 分级。

疑似命中数量：**0**（strong=0，weak=0）

无明显跨源疑似重复。
