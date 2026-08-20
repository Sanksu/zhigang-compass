# 企业官网第二招聘源 Pilot20 — 最终归档摘要

> 归档时间：2026-08-19 18:39（北京时间，精确到分钟）

## 1. Pilot 状态

**PILOT_PASS**

- 计划样本：20
- 实际有效：20（停止采集，未扩至 50/100/200）

## 2. 来源分布

| 来源 | 计划 | 有效 | 失败 | Adapter 成功率 |
|---|---|---|---|---|
| **Tencent（腾讯官网）** | 10 | 10 | 0 | **100%** |
| **ByteDance（字节跳动官网）** | 10 | 10 | 0 | **100%** |
| 合计 | 20 | 20 | 0 | **100%** |

## 3. 岗位方向分布（8 方向均衡覆盖）

| 方向 | 数量 |
|---|---|
| Java / 后端 | 2 |
| AI / 大模型 | 4 |
| 算法 | 4 |
| 前端 | 2 |
| 测试 | 2 |
| 数据工程 / 大数据 | 2 |
| 运维 / DevOps | 2 |
| 嵌入式 / C++ | 2 |
| 合计 | 20 |

## 4. 字段完整率（20/20 全部条目）

| 字段 | 完整数 / 总数 | 完整率 | 备注 |
|---|---|---|---|
| source | 20 / 20 | **100%** | 统一值 `official_career_site` |
| source_company | 20 / 20 | **100%** | `Tencent`=10 / `ByteDance`=10 |
| source_id | 20 / 20 | **100%** | 腾讯=postId；字节=URL 路径 19 位 ID |
| source_url | 20 / 20 | **100%** | 官方职位详情页 URL |
| job_title_raw | 20 / 20 | **100%** | 官网原样标题，未 AI 改写 |
| company_name | 20 / 20 | **100%** | 腾讯/字节系名称原样提取 |
| location | 20 / 20 | **100%** | 官网明确城市/地点 |
| responsibilities | 20 / 20 | **100%** | 官网明确职责/描述段，未 AI 重拆 |
| requirements | 20 / 20 | **100%** | 官网明确要求/资格段，未 AI 重拆 |
| detail_raw_text | 20 / 20 | **100%** | 完整 JD 正文原样保留 |
| crawl_time | 20 / 20 | **100%** | ISO8601 采集时间戳 |
| _sha256 | 20 / 20 | **100%** | 64 位十六进制，口径：`SHA256(responsibilities + "\n" + requirements)` |
| salary | 0 / 20 | **0%（合法 null）** | 两官网详情页未展示薪资，未猜值 |
| source_education | 0 / 20 | **0%（合法 null）** | 两官网详情页未展示学历要求，未猜值 |
| source_experience | 10 / 20 | **50%（合法 null）** | 腾讯 10 条含经验，字节详情页不展示，未猜值 |
| publish_time | 10 / 20 | **50%（合法 null）** | 腾讯元信息行有发布/更新时间，字节无，未猜值 |

## 5. 正文质量

| 长度区间 | 数量 | 占比 |
|---|---|---|
| < 100 字 | 0 | 0% |
| 100 ~ 199 字 | 0 | 0% |
| ≥ 200 字 | **20** | **100%** |

## 6. Adapter 稳定性

| Adapter | 来源覆盖 | 解析成功 | 单条 JD 硬编码 | 稳定性 |
|---|---|---|---|---|
| **TencentCareerAdapter** | Tencent 10/10 | 10/10 (100%) | 字面 ID=0，标题 case=0 | **PASS** |
| **ByteDanceCareerAdapter** | ByteDance 10/10 | 10/10 (100%) | 字面 ID=0，标题 case=0 | **PASS** |

技术特征：
- 两 Adapter 均使用统一标签常量列表 (`REQ_LABELS` / `RESP_LABELS`) 扫描正文行做职责/要求分节，无逐条特殊分支
- source_id 来源稳定：腾讯 = URL `postId`；字节 = URL path 中 19 位数字 ID

## 7. 内部重复检查（三层精确 + 一层近似）

| 维度 | 重复数 |
|---|---|
| `source_company + source_id` 精确 | **0** |
| `source_url` 精确 | **0** |
| `_sha256` 精确 | **0** |
| 正文 SimHash Hamming ≤ 3（近似） | **0** |

> 完全重复 = 0，近似重复 = 0，重复 sample_id = 无

## 8. 与智联数据跨源检查（只读）

**完整跨源检查已完成。**

实际参与比较：
- 智联 candidate（candidate_pool/v1）：**158** 条
- Gold（`jd_golden_110.jsonl`）：**110** 条
- 合计参考记录（去重合并）：**268** 条
- 企业官网 Pilot：**20** 条

结果：
- STRONG 疑似重复：**0**
- WEAK 疑似重复：**0**
- 跨源疑似重复总计：**0**

智联数据与 Gold 数据未做任何修改；仅只读报告，未删除/追加/覆盖。

## 9. 访问限制统计

| 类型 | 数量 |
|---|---|
| 正常访问 | **20** |
| 登录墙 | **0** |
| 验证码 | **0** |
| 访问验证（滑动/滑块/人机） | **0** |
| HTTP 异常 | **0** |
| 页面结构异常 | **0** |

## 10. SHA256 算法口径（统一）

```
_sha256 = lowercase_hex( SHA256( UTF8( responsibilities + "\n" + requirements ) ) )
```
- 64 位小写十六进制字符串
- 禁止重新解释职责/要求文本；提取后直接拼接即算哈希
- 本次 20 条全部格式合法（64 hex，100%），20 条哈希唯一（无重复职位误判）

## 11. 原始事实与 Gold 边界声明（强制）

**本 20 条仅属于「第二来源 Pilot 候选数据」，尚未进入正式 Gold。**

- ❌ 未自动标 Gold
- ❌ 未加入 `A01_FINAL`
- ❌ 未修改 `jd_golden_110.jsonl`
- ❌ 未混入现有 110 Gold 集合
- ❌ 无 AI 补全（薪资/学历/经验/发布时间官网未展示均为 null，未猜值、未改写标题、未重新解释职责/要求）
- ❌ 未修改智联任何数据（跨源比对仅只读）

后续是否进入新的 Gold 集由人工另行决定。

## 12. 仓库保护声明（强制）

- ❌ 未修改 `zhigang-compass/` 下任何生产代码（`backend/app` / `frontend` / `Prompt` / 算法 / Gold 目录）
- ❌ 未修改 `develop` / `main` 分支
- ❌ 在最初 Pilot 归档阶段未 commit
- ❌ 在最初 Pilot 归档阶段未 push
- ❌ 在最初 Pilot 归档阶段未创建 PR

## 13. 正式归档文件清单

归档根目录：`D:\du_yan\jiebang_guashuai_jingsai\第二招聘源_企业官网_Pilot20\`

| 文件 / 目录 | 说明 |
|---|---|
| `official_career_pilot20_raw.jsonl` | 原始解析 20 条，保留内部辅助字段 |
| `official_career_pilot20_clean.jsonl` | 去重后有效 20 条（标准 17 字段） |
| `official_career_pilot20_quality_report.md` | 详细质量统计报告 |
| `official_career_pilot20_duplicate_report.md` | 内部/跨源去重详细报告 |
| `official_career_pilot20_final_summary.md` | 本文件（最终归档摘要） |
| `adapters/` | `base.py` + `tencent_adapter.py` + `bytedance_adapter.py` + `router.py` + `__init__.py` |
| `snapshots/` | 20 条 JD 原始正文快照 + `_index.json`（可用于回溯/重跑 Adapter） |
| `url_list.json`（保留待人工确认） | 采集计划（20 URL + postId/19 位 ID + 方向标签），建议保留 |

## 14. 最终结论

**ARCHIVE_PASS**

- 数据完整性达标（20/20，腾讯 10/10、字节 10/10）
- 核心 12 字段 100% 完整，允许 null 字段未被篡改
- SHA256 格式 100% 合法，口径统一可复现
- 内部精确/近似重复均为 0
- Adapter 静态核验 PASS，无单条硬编码
- 访问限制为 0，具备批量扩源基础
- 仓库保护绿，未破坏生产代码、未进 Gold、未 commit/push
