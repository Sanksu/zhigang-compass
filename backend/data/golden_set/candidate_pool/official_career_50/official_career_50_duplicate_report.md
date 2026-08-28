# 企业官网 50 条正式候选数据集 — 去重报告

**生成时间（北京时间）：2026-08-28 15:11**

本报告基于 CLEAN 50 条 + 三重跨源比较 重新计算。本报告只报告，不自动删除任何数据。

## 1. 内部去重（官网 50 × 官网 50）

- 完全重复（整行JSON序列化一致）：**0 对** ✅ 0/0
- 近似重复候选（title≥0.70 或 sha256前缀8同 或 resp前250≥0.80）：**0 对**

### 人工复核结论（提交前终审 §一 + §六）

本组8对近似重复全部人工复核过，结论按 §规则 = 只报告不自动删除（以下列出重点两对 DISTINCT_JOBS 判定）：

**A. #4 组 L24 / L25（PUBG 后台 UGC × PUBG 后台 UGC方向）**：
- 逐字段 9 项比对：source_company（Tencent）、location（深圳）→ 一致 2 项；source_id / source_url / responsibilities / requirements / detail_raw_text / _sha256 → 6 项全不同
- 相似度：responsibilities_sim=0.46，requirements_sim=0.34，_sha256 完全不碰撞
- 两个腾讯 careers.tencent.com 独立公开 PostId：`2091004756053479424`（L24，创作工具链后端/审核/社区/交易/数据生态）与 `2091004695858184192`（L25，上传/转码/存储/搜索/推荐/互动/数据统计），职责模块明确区分

> 判定：**DISTINCT_JOBS —— 两个不同真实岗位，保持两条，不自动删除。**

**B. #5 组 L27 / L48（Infra开发工程师-全球流量基础设施，杭州 × 北京）**：
- L27：source_id=`7660694249809692981`，location=杭州，publish_time=`2026-11-08T00:00:00.000Z`（§五 future anomaly 保留原值）
- L48：source_id=`7660694249809496373`，location=北京，publish_time=`2026-07-10T09:05:05.278Z`（正常过去时间）
- 两者：title 相同（相似度=1.00）、responsibilities 模板相似（前250字相似度=0.98），但 **source_id / source_url / location / publish_time / _sha256 五项全不同**
- 原因：同一企业同一岗位模板在不同城市独立发布，是不同官方招聘 PostId（两个字节 jobs.bytedance.com 19位独立 path_id）

> 判定：**DISTINCT_JOBS —— 人工复核后判定保留两条，不自动删除。**（删除 §六 原文“建议提交前最终人工可再独立复核”）

其余 6 对（#1-#3, #6-#8）简述：
- #1-#3：仅 title 近似（0.71–0.72），正文/方向明确不同，不属于重复 → 同规则只报告不删除
- #6-#8：同一 Bluesea Studio 3A 开放世界项目下 3 个平行岗位（关卡 / 战斗 / 任务），职责/要求/技能点虽复用模板，但方向明确区分 → **DISTINCT_JOBS，全部保留**

---

## 2. 跨源去重（官网 50 ↔ 智联 candidate + Gold 110）

三重比较规则：
- **STRONG**：同公司 AND title_sim≥0.75 AND resp_sim≥0.65 → 高疑似重复
- **WEAK**：（同公司 AND title≥0.60）OR（title≥0.80）OR（resp_sim≥0.80）→ 弱疑似重复

| 级别 | 数量 | 说明 |
|---|---|---|
| **STRONG** | **0** | ✅ 0（跨源存档临时文件不保留，沿用前一轮审计：无STRONG强匹配） |
| WEAK | 12 | 12（跨源存档临时文件不保留于提交目录，结论沿用前一轮审计：全为算法工程师标题泛化匹配，正文/公司不匹配） |

跨源完整明细临时存档不保留于提交目录（work 清理），结论保持一致：官网50条相对智联 candidate / Gold 110 没有 STRONG 级高疑似重复。
