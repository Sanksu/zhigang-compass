# 企业官网 50 条数据集 — 质量报告

**生成时间（北京时间）：2026-08-27 19:52**

本报告基于 `official_career_50_clean.jsonl` 重新读取 **50** 条记录计算，覆盖此前旧37条统计（所有数字均为50条真实结果）。

## 1. 总体规模

| 指标 | 结果 | 标准 | 结论 |
|---|---|---|---|
| RAW记录数 | **50** | =50 | ✅ PASS |
| CLEAN记录数 | **50** | =50 | ✅ PASS |
| 腾讯(Tencent)分布 | **25** | =25 | ✅ PASS |
| 字节跳动(ByteDance)分布 | **25** | =25 | ✅ PASS |
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

## 5. 结论
> 综合质量判定：**✅ READY_FOR_REVIEW（全部质量项通过）**
