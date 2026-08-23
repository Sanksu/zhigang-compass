# 性能压测基线报告（TE-M5-01 前置，2026-08-15）

> 压测工具：Locust 2.46，100 并发 / 20 ramp-up / 3 分钟，headless。
> 目标（设计文档 §1.4.1 / 执行计划）：API P95 < 2s @ 100 并发。
> 环境：本机 Docker Compose 5 服务（api 重建于 08-15），限流临时放宽（还原生产值 100/min）。

## 一、结论

**P95 < 2s 目标达成**（实测 < 500ms）：

| 场景 | P50 | P90 | P95 | 目标 | 判定 |
|---|---|---|---|---|---|
| GET /graph/panorama | 60ms | 290ms | **430ms** | <2s | ✅ |
| GET /graph/search | 45ms | 270ms | **390ms** | <2s | ✅ |
| 聚合（12931 req） | 54ms | 280ms | **410ms** | <2s | ✅ |

0% 失败（限流放宽口径）。压测 CSV：`backend/reports/perf_20260815c_*.csv`（gitignore）。

## 二、压测中发现并修复的问题（按收益排序）

### 1. panorama 路由装饰器错位（生产 bug，压测首轮 8290 个 422）
- **现象**：`GET /api/v1/graph/panorama` 返回 4000"参数校验失败: scope Field required"
- **根因**：08-14 审查重构（同步 Neo4j 查询抽线程池）时，`@router.get("/panorama")` 装饰器被**错误挂在内部函数 `_query_panorama(scope, focus, min_weight, limit)` 上**——内部函数参数全部暴露成必填 Query；真实端点 async `panorama()`（含缓存逻辑）失去路由成为死代码
- **修复**：装饰器移回 async `panorama()`，内部函数去装饰器（`graph.py`）

### 2. Neo4j 连接池过小（30 → 100）
- 30 连接在 100 并发（panorama 缓存 miss / search 无缓存）下排队严重
- `database.py` max_connection_pool_size 30→100，对齐 100 并发目标

### 3. search 无缓存（每次打 Neo4j 全文索引）
- 搜索词重复度高（真实用户/压测同词），60s Redis TTL 缓存（key 含 scope/type/q/page/size），缓存命中后 search P95 3.9s→860ms

### 4. panorama 缓存穿透风暴（single-flight 合并）
- **现象**：30s TTL 失效瞬间 100 并发同时 miss → 全部打 Neo4j（to_thread 线程池饱和）→ P95 20s
- **修复**：模块级 in-flight Future 表——同 key 并发 miss 只放行 1 个查库，其余 await 同 future（`graph.py`）
- P95 20s→430ms（本轮最大单项收益）

## 三、优化前后对比

| 阶段 | panorama P95 | search P95 | 说明 |
|---|---|---|---|
| 首轮（bug 未修） | 422（8290 次） | 5.5s | panorama 路由 bug |
| bug 修复 + 连接池 | 20s | 3.9s | 缓存 miss 风暴暴露 |
| + search 缓存 | 20s | 860ms | search 大幅改善 |
| + single-flight | **430ms** | **390ms** | 全达标 |

## 四、遗留说明

- **compare 任务**（POST /match/compare）未跑：需要有效凭据 + 简历/岗位 id（环境变量注入），M5 正式窗口补测
- **99.9% 百分位 3.7s**：极长尾（线程池瞬时饱和），100 并发下可接受；如需消除可扩 anyio 线程池上限
- **限流口径**：本压测临时放宽限流（GENERAL_LIMIT 100→100000 后还原）——真实生产 100 req/min/IP/path 下 100 并发会触发 429（快速拒绝，方向乐观）；验收建议分布式多 IP 或按文档口径解读
- 运行命令：`cd backend && uv run locust -f scripts/locustfile.py --host http://localhost:8000 -u 100 -r 20 -t 3m --headless --csv reports/perf_xxx GraphUser`

## 五、复现

```bash
docker compose build api && docker compose up -d api   # 含全部修复
cd backend
uv run locust -f scripts/locustfile.py --host http://localhost:8000 \
    -u 100 -r 20 -t 3m --headless --csv reports/perf_20260815 GraphUser
```

---

## 08-18 治理后复测（#300~#302 性能治理）

> 治理背景：100 并发压测对比发现 P99/P99.9 尾部劣化（panorama P99 910→5100ms vs 08-15 基线），
> 根因为三层叠加：冷查询映射阻塞事件循环、30s/60s TTL 到期风暴、search 冷键无并发合并与
> 大载荷序列化积压。治理修复见 PR #300（映射移出事件循环）/ #301（TTL 300s + 管理端写路径
> 即时失效）/ #302（search single-flight + panorama 预序列化响应）。

### 复测结果（2026-08-18，100 并发 / 3 分钟 / 全量热键预热 / 0 失败）

| 场景 | P50 | P90 | P95 | P99 | P99.9 | Max |
|---|---|---|---|---|---|---|
| GET /graph/panorama | 5ms | 6ms | 8ms | 290ms | 390ms | 420ms |
| GET /graph/search | 5ms | 6ms | 7ms | 250ms | 340ms | 378ms |

- 吞吐 58.9 req/s（10567 请求）；对比 08-15 基线：**P95 430/390 → 8/7ms（约 55 倍），P99.9 3700 → 390/340ms（约 10 倍）**
- 全面超越目标（P95 < 2s）与 08-15 基线；后续对比以此表为准
