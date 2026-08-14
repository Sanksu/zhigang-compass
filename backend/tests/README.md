# tests

测试目录（08-14 审查：目录结构按模块组织，声明同步实际布局）。

- 按模块组织（与 `backend/app` 对应）：`admin/ auth/ core/ crawlers/ data_quality/ discovery/
  embeddings/ evaluate/ evolution/ extraction/ graph/ kg/ learning_path/ matching/ rag/ resume/ workers/`
- `integration/` — 集成测试（需真实后端 + PG/Neo4j/Redis，默认 `-m not integration` 跳过）
- `evaluate/` — 准确率评测脚本（黄金集 100 条 JD + 100 岗位匹配 + 50 简历）
- 性能压测脚本：`scripts/locustfile.py`（TE-M5-01，Locust）
- 覆盖率基线（设计文档 §13.1）：后端 ≥ 70%（核心模块 ≥ 80%），前端核心 ≥ 95%
