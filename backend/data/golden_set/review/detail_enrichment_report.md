# JD 复核样本详情正文补充报告

## 结果汇总

- 总样本：20
- 取得完整正文：2
- 仅取得部分正文：0
- 无法获取：18
- BOSS：完整 0/10（0%）
- 智联：完整 2/10（20%）

## 获取边界

本轮只复用或检查了现有项目路径：BOSS 列表/API 采集逻辑、智联详情页 SSR 解析逻辑、`scripts/backfill_jd_detail.py` 的 source_id 映射，以及本机是否有可读取的数据库服务。回填脚本会写数据库，故未运行。未发现本机可读取的 PostgreSQL 服务；未进行登录、验证码绕过、Cookie 导出或反爬规避。

两条智联详情正文来自普通公开详情页读取，并按 source_id 映射保存到 CSV；其余条目没有本地完整正文或可普通读取的详情正文，未用摘要、岗位常识或 AI 内容替代。

## 条目来源与人工标注可用性

| id | source | detail_source | detail_fetch_status | 六字段人工标注条件 |
|---|---|---|---|---|
| jd_001 | boss | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_003 | boss | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_008 | boss | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_009 | boss | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_010 | boss | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_034 | boss | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_045 | boss | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_064 | boss | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_074 | boss | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_082 | boss | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_012 | zhilian | zhilian_detail | 完整 | 具备（以 detail_raw_text 为证据） |
| jd_013 | zhilian | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_015 | zhilian | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_021 | zhilian | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_025 | zhilian | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_030 | zhilian | zhilian_detail | 完整 | 具备（以 detail_raw_text 为证据） |
| jd_036 | zhilian | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_053 | zhilian | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_084 | zhilian | unavailable | 无法获取 | 不具备（无完整详情正文） |
| jd_091 | zhilian | unavailable | 无法获取 | 不具备（无完整详情正文） |

“具备”仅表示已有职责与任职要求正文，可进入人工六字段核对；并不表示现有 `current_gold_*` 已正确。所有 `review_*`、`error_type`、`review_status`、`review_note` 在本轮保持为空，必须继续由人工填写。
