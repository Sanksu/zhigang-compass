"""图算法模块（设计文档 7.1 图算法应用）。

PageRank 技能重要性 / Louvain 技能簇 / 技能最短路径 三个能力的纯计算实现。

背景：Neo4j 为社区版（docker-compose 镜像 neo4j:5，未挂 GDS 插件），
`gds.pageRank.stream()` 不可用；故 PageRank/Louvain 以纯 Python 实现，
零新增依赖（numpy 已是既有传递依赖）。技能网络以「岗位共现」构建
（图谱无技能-技能边，两技能被同一岗位 REQUIRES 即连边，见 network.py）。
"""
