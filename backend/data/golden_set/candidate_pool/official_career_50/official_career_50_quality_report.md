# 企业官网 50 条正式候选数据集 — 质量报告

**生成时间（北京时间）：2026-08-28 15:44**

本报告基于 `official_career_50_clean.jsonl` 重新读取 **50** 条记录计算（所有数字均为50条真实结果）。本目录属于 `candidate_pool` 阶段的正式候选数据集，尚未进入 official Gold 110 条数据集。

## 1. 总体规模

| 指标 | 结果 | 标准 | 结论 |
|---|---|---|---|
| RAW记录数 | **50** | =50 | ✅ PASS |
| CLEAN记录数 | **50** | =50 | ✅ PASS |
| Tencent 分布 | **25** | =25 | ✅ PASS |
| ByteDance 分布 | **25** | =25 | ✅ PASS |
| Pilot20原20条完整保留 | **20/20** | =20/20 | ✅ PASS |

## 2. 字段完整性（CLEAN 50 条）

| 字段 | 完整数 | 标准 | 结论 |
|---|---|---|---|
| `source_id` | **50/50** | =50/50 | ✅ PASS |
| `source_url` | **50/50** | =50/50 | ✅ PASS |
| `job_title_raw` | **50/50** | =50/50 | ✅ PASS |
| `company_name` | **50/50** | =50/50 | ✅ PASS |
| `location` | **50/50** | =50/50 | ✅ PASS |
| `responsibilities` | **50/50** | =50/50 | ✅ PASS |
| `requirements` | **50/50** | =50/50 | ✅ PASS |
| `detail_raw_text` | **50/50** | =50/50 | ✅ PASS |
| `_sha256` | **50/50** | =50/50 | ✅ PASS |
| `_sha256` 格式合法(64位小写hex) | **50/50** | =50/50 | ✅ PASS |

## 3. 唯一性检查

- `source_id` 唯一：**50/50** → ✅ PASS
- `source_url` 唯一：**50/50** → ✅ PASS
- CLEAN 内部完全重复：**0 条** → ✅ PASS
- RAW 内部完全重复：**0 条** → ✅ PASS

## 4. Pilot20 不可变保护

- 六项（source_id / source_url / responsibilities / requirements / detail_raw_text / _sha256）
  字节级 20/20 比对结果：**✅ PASS 完全一致**

## 5. publish_time 异常专项（§五审计）

扫描规则：对所有 publish_time & crawl_time 可解析的记录，检查 publish_time > crawl_time；发现未来时间 **不猜不改不换月日**，原样保留并标注。

⚠️ 共 **2 条 source-reported future publish_time anomaly**（官方源返回了晚于 crawl_time 的 publish_time — 保持原始 source value，不伪装为正常时间）：

| # | 行号 | source_id | title | location | publish_time | crawl_time |
|---|---|---|---|---|---|---|
| 1 | L26 | `7663333383728597301` | 大模型技术服务专家 - 火山引擎 | 深圳 | 2026-11-15T00:00:00.000Z | 2026-08-26T14:05:00.000Z |
| 2 | L27 | `7660694249809692981` | Infra开发工程师-全球流量基础设施 | 杭州 | 2026-11-08T00:00:00.000Z | 2026-08-26T14:06:00.000Z |

> **处理结论**：以上异常均为官方源直接返回的未来 publish_time（checkpoint 临时存档已清理，无法回看源响应；未重新采集/未猜日期/未交换月日）。按规则保留原始 source value，不得把未来日期伪装为正常时间。

## 6. 结论
- publish_time 未来异常显式报告：**✅ 已标注（共2条）**
> 综合质量判定：**✅ READY_FOR_REVIEW（全部质量项通过；future anomaly 已按规定显式标注不删除不修改）**
