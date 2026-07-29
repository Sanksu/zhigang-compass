# 智岗罗盘

多源异构驱动的岗位能力动态演化与人岗匹配系统。

构建"新一代信息技术全景图谱"，实现"数据采集 → 新岗位发现 → 既有岗位能力更新 → 人岗匹配诊断"全流程闭环。

---

## 快速入口

| 入口 | 说明 |
|------|------|
| [AGENTS.md](AGENTS.md) | AI 智能体协作入口（铁律、模块导航、上下文加载清单） |
| [docs/README.md](docs/README.md) | 文档索引（按角色推荐阅读路径） |
| [docs/project/项目概览.md](docs/project/项目概览.md) | 项目定位、技术栈、里程碑、评分维度 |
| [docs/design/设计文档.md](docs/design/设计文档.md) | 系统设计方案（单一事实源） |

## 快速启动

```bash
# 启动基础设施（api/postgres/redis/neo4j 4 服务）
docker compose up -d

# 后端开发
cd backend && uv run uvicorn app.main:app --reload --port 8000

# 前端开发
cd frontend && pnpm install && pnpm dev
```

详细环境配置见 [docs/guides/团队启动指南.md](docs/guides/团队启动指南.md)。
