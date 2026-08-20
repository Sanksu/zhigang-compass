# Official Career Site JD Pilot20

数据性质：
企业官方招聘网站真实JD候选数据。

来源：

- Tencent：10条
- ByteDance：10条

总数：
20条

状态：

PILOT_PASS
ARCHIVE_PASS

source统一：

official_career_site

用途：

用于验证多来源JD采集、清洗、标准化和跨源去重流程。

与现有智联数据关系：

现有 candidate_pool/v1 主要为智联招聘数据；
本目录为企业官网第二类来源数据。

重要声明：

- 尚未进入Gold
- 尚未进行新的人工Gold确认
- 不修改现有110条Gold
- 51job因访问验证被淘汰
- Liepin因登录墙被淘汰
- 腾讯/字节官网公开职位页通过Pilot验证

Adapter：

仅用于企业官网公开JD页面字段解析。

已知限制 / 可复现性：

- clean 与 raw 当前为**同源同构**（逐字节相同、均保留内部辅助字段）；"clean" 语义的精修（去内部字段、过滤页脚噪声如"相关职位/投递/申请岗位"）待后续清洗管线完成
- 数据由外部 pilot 采集/解析管线生成；本 PR **未包含**其生成脚本与原始快照（`pilot20_official_process.py`、`snapshots/`、`url_list.json` 为归档规划、未入库），字段解析以 `adapters/` 为准
- 与智联 Gold 的跨源"疑似重复 0"仅覆盖可访问的 **14/110** 条子集，完整 110 条比对待数据整合阶段执行
