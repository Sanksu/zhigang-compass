# backend

智岗罗盘后端 Python monorepo。整合 FastAPI 服务、算法引擎、数据管线、爬虫、测试与评测于单一模块。

## 目录结构

```
backend/
├── app/                              # FastAPI 应用（马兴达 + 张恺天）
│   ├── main.py                       # FastAPI 入口
│   ├── api/                          # API 路由
│   │   └── v1/
│   ├── core/                         # 配置 / 安全 / 中间件
│   ├── models/                       # SQLAlchemy ORM 模型
│   ├── schemas/                      # Pydantic 请求/响应模型
│   ├── services/                     # 业务逻辑 + 算法引擎（张恺天）
│   │   ├── matching/                 # 人岗匹配引擎
│   │   ├── discovery/                # 新岗位发现
│   │   ├── evolution/                # 动态演化
│   │   └── extraction/               # LLM 实体抽取
│   └── workers/                      # ARQ 异步任务
├── data/                             # 数据管线（刘琪）
│   ├── crawlers/                     # 爬虫（14 源，A/B/C 分级）
│   ├── pipeline/                     # ETL 清洗 / 去重 / 交叉验证
│   └── golden_set/                   # 黄金集标注
├── alembic/                          # 数据库迁移
│   └── versions/
├── openapi/                          # API 契约（OpenAPI 3.0）
│   └── openapi.yaml
├── scripts/                          # 工具脚本（Neo4j 建库 / 评测）
├── tests/                            # 所有测试（王鹏羽）
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   ├── evaluate/                     # 准确率评测
│   └── performance/                  # 性能压测（Locust）
├── pyproject.toml                    # 项目依赖 (uv)
├── Dockerfile                        # 生产镜像
├── Dockerfile.dev                    # 开发镜像
└── README.md                         # 本文件
```

## 快速开始

```bash
cd backend

# 安装依赖
uv sync

# 启动开发服务器
uv run uvicorn app.main:app --reload --port 8000

# 运行测试
uv run pytest --cov=app
```

## 团队归属

| 子模块 | 负责人 | 说明 |
|--------|--------|------|
| `app/api/` `app/core/` `app/models/` `app/schemas/` | 马兴达 | FastAPI 服务 + 数据库 |
| `app/services/` | 张恺天 | 算法引擎（匹配/发现/演化/抽取） |
| `data/` | 刘琪 | 数据管线 + 爬虫 + 黄金集 |
| `tests/` | 王鹏羽 | 测试 + 评测 + 压测 |
| `openapi/` | 马兴达 | API 契约定义 |
