# 18 条缺失 JD 正文的数据来源审计

## 审计范围与方法

处理对象为 `jd_golden_review_20_enriched.csv` 中 `detail_fetch_status=无法获取` 的 18 条记录。对每条记录以 `source_id`、岗位原名和 `source_url` 在以下位置做了只读精确检索：`backend/data/`、`backend/data/crawlers/output/`、`backend/tests/`、`backend/reports/`、`backend/scripts/`、`docs/`，以及仓库内的 JSON/JSONL/CSV/snapshot/fixture/dump/backup/cache/测试数据候选文件。

检索时排除了本轮 `review/` 产物和原始 `jd_golden_100.jsonl`，以避免把已知的元数据误报为可恢复正文。18 个 `source_id`、对应岗位名和 URL 在其余指定位置均无命中；`backend/data/crawlers/output/` 当前只有 `.gitignore` 与 `.gitkeep`，没有历史爬虫输出文件。

## 数据库与初始化结论

1. `jd_raw` 的 ORM 定义位于 `backend/app/models/raw.py`，迁移定义位于 `backend/alembic/versions/20260729_001_create_raw_tables.py`。表包含 `source`、`source_id`、`source_url`、`snapshot` 和 `raw_text` 等字段，`(source, source_id)` 唯一。
2. `docker-compose.yml` 的 PostgreSQL 服务会创建数据库卷；执行 Alembic 迁移后会创建空的 `jd_raw` 表。本地 Docker 不是随 clone 附带的历史数据副本。
3. clone 仓库不包含任何初始化 JD 数据。`backend/scripts/bootstrap.py` 明确把“已通过爬虫采集 jd_raw / course_raw 原始数据”列为前置条件，不会采集或导入 JD 种子数据。
4. 未发现 PostgreSQL dump、SQL 数据导入、JD seed、缓存或备份。存在的 `init_neo4j.py`、`import_occupations.py`、`bootstrap.py` 分别初始化图谱/导入 O*NET/处理已存在 raw 数据，不提供历史 JD 正文。
5. 若团队需要恢复这些**同一历史 source_id** 的原始版本，只能请持有历史 PostgreSQL 卷或导出的队友检查并导出 `jd_raw` 的 `snapshot.description`、`snapshot.requirements` 和 `raw_text`。但这不是尽快获得 20 条可标注样本的必经路径：智联可优先普通公开读取，BOSS 可替换为新采集的完整正文样本。

## 逐条审计结果

| id | source | source_id | source_url | repo_has_full_text | possible_location | database_required | recommended_action |
|---|---|---|---|---|---|---|---|
| jd_001 | boss | 22c64d465b4b5d030nJ709u5F1tQ | https://www.zhipin.com/job_detail/22c64d465b4b5d030nJ709u5F1tQ.html | 否 | 无；当前黄金集仅有列表/API 元数据。若历史库曾人工补正文，可能在 `jd_raw.snapshot`/`raw_text`。 | 否；仅恢复同一历史记录时需队友库 | 建议替换为新的完整 JD 样本 |
| jd_003 | boss | ec6ac1027510acc90nF43N-1F1pW | https://www.zhipin.com/job_detail/ec6ac1027510acc90nF43N-1F1pW.html | 否 | 同上 | 否；仅恢复同一历史记录时需队友库 | 建议替换为新的完整 JD 样本 |
| jd_008 | boss | d5c7c9a54364b5d90nZ42di-EFdW | https://www.zhipin.com/job_detail/d5c7c9a54364b5d90nZ42di-EFdW.html | 否 | 同上 | 否；仅恢复同一历史记录时需队友库 | 建议替换为新的完整 JD 样本 |
| jd_009 | boss | ca5a8bba6ccb41e10nJ609m7GFRR | https://www.zhipin.com/job_detail/ca5a8bba6ccb41e10nJ609m7GFRR.html | 否 | 同上 | 否；仅恢复同一历史记录时需队友库 | 建议替换为新的完整 JD 样本 |
| jd_010 | boss | 28d105edd8ffb1b903183du4E1ZY | https://www.zhipin.com/job_detail/28d105edd8ffb1b903183du4E1ZY.html | 否 | 同上 | 否；仅恢复同一历史记录时需队友库 | 建议替换为新的完整 JD 样本 |
| jd_034 | boss | 8211ffbe14fe025f0nZ_3tm1GVJR | https://www.zhipin.com/job_detail/8211ffbe14fe025f0nZ_3tm1GVJR.html | 否 | 同上 | 否；仅恢复同一历史记录时需队友库 | 建议替换为新的完整 JD 样本 |
| jd_045 | boss | 7fad28ddcd93fe880nB829y6FldU | https://www.zhipin.com/job_detail/7fad28ddcd93fe880nB829y6FldU.html | 否 | 同上 | 否；仅恢复同一历史记录时需队友库 | 建议替换为新的完整 JD 样本 |
| jd_064 | boss | 984e871b2bd2a0550nFy29q7FlVZ | https://www.zhipin.com/job_detail/984e871b2bd2a0550nFy29q7FlVZ.html | 否 | 同上 | 否；仅恢复同一历史记录时需队友库 | 建议替换为新的完整 JD 样本 |
| jd_074 | boss | 34f4af06bf69c6850nF_3N65FVRQ | https://www.zhipin.com/job_detail/34f4af06bf69c6850nF_3N65FVRQ.html | 否 | 同上 | 否；仅恢复同一历史记录时需队友库 | 建议替换为新的完整 JD 样本 |
| jd_082 | boss | 676594a6ac367db30nJ62ty0EFtS | https://www.zhipin.com/job_detail/676594a6ac367db30nJ62ty0EFtS.html | 否 | 同上 | 否；仅恢复同一历史记录时需队友库 | 建议替换为新的完整 JD 样本 |
| jd_013 | zhilian | CCL1516918430J40789275606 | http://www.zhaopin.com/jobdetail/CCL1516918430J40789275606.htm | 否 | 不在 clone；现有 `zhilian_detail.py` 可由 `source_id` 映射公开详情 URL，历史正文若存在则在队友 `jd_raw.snapshot`/`raw_text`。 | 否；先尝试公开详情 | 可从公开详情页重新获取 |
| jd_015 | zhilian | CC192921310J40831468409 | http://www.zhaopin.com/jobdetail/CC192921310J40831468409.htm | 否 | 同上 | 否；先尝试公开详情 | 可从公开详情页重新获取 |
| jd_021 | zhilian | CC303218880J40962177002 | http://www.zhaopin.com/jobdetail/CC303218880J40962177002.htm | 否 | 同上 | 否；先尝试公开详情 | 可从公开详情页重新获取 |
| jd_025 | zhilian | CC258760917J90250298000 | http://www.zhaopin.com/jobdetail/CC258760917J90250298000.htm | 否 | 同上 | 否；先尝试公开详情 | 可从公开详情页重新获取 |
| jd_036 | zhilian | CC000544460J40670242116 | http://www.zhaopin.com/jobdetail/CC000544460J40670242116.htm | 否 | 同上 | 否；先尝试公开详情 | 可从公开详情页重新获取 |
| jd_053 | zhilian | CC385622410J40787862106 | http://www.zhaopin.com/jobdetail/CC385622410J40787862106.htm | 否 | 同上 | 否；先尝试公开详情 | 可从公开详情页重新获取 |
| jd_084 | zhilian | CCL1480117890J40603130605 | http://www.zhaopin.com/jobdetail/CCL1480117890J40603130605.htm | 否 | 同上 | 否；先尝试公开详情 | 可从公开详情页重新获取 |
| jd_091 | zhilian | CC135794170J41002479902 | http://www.zhaopin.com/jobdetail/CC135794170J41002479902.htm | 否 | 同上 | 否；先尝试公开详情 | 可从公开详情页重新获取 |

## 最终回答

1. 18 条中，仓库内可直接恢复完整正文：**0 条**。
2. 18 条中，必须依赖队友数据库才能恢复：**0 条**（若必须复原相同历史记录，则需队友确认并导出历史 `jd_raw`；这属于可选的溯源恢复路径）。
3. 18 条中，可按现有 `source_id → https://www.zhaopin.com/jobdetail/{source_id}.htm` 映射重新普通公开获取：**8 条**（智联）。
4. 18 条中，建议直接替换为新的完整 JD 样本：**10 条**（BOSS）。
5. 为尽快形成 20 条可人工标注样本，最省时间的方案是：保留已有 2 条完整智联正文；对其余 8 条智联按现有详情解析逻辑做一次不写库的普通公开读取；并以 10 条已取得完整职责与任职要求正文的新样本替换 BOSS 10 条。若智联公开读取仍失败，再将失败条目替换，而不是等待未知是否存在的历史数据库。

本轮未填写任何 `review_*` 字段，未修改 `jd_golden_100.jsonl`，未运行 `backfill_jd_detail.py` 或其他写数据库脚本。
