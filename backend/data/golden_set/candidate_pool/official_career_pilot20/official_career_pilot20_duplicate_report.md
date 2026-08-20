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

## 三、与现有智联数据的跨源疑似重复（只读检查，不修改智联数据/Gold）

本次已在本地完整读取并执行跨源检查，不存在单次 Read 截断问题。

比较范围：
- 企业官网 Pilot：20 条
- 智联完整候选数据（candidate_pool/v1）：158 条
- Gold 完整读取：110 / 110
- 合计参考记录去重后：268 条
- 完整比对：268 vs 20

比较维度：normalized company / normalized title / location city prefix / 正文相似度（SequenceMatcher ratio + SimHash + Jaccard）分级。

疑似命中数量：**0**
- STRONG 疑似重复：**0**
- WEAK 疑似重复：**0**
- 跨源疑似重复总计：**0**

无明显跨源疑似重复。
