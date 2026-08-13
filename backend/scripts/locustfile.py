"""Locust 性能压测脚本（TE-M5-01）。

压测对象（设计文档 §1.4.1 性能指标，P95 < 2s @ 100 并发）：
- `GET /api/v1/graph/panorama`（图谱全景，Redis 30s TTL 缓存）
- `GET /api/v1/graph/search?q=`（Neo4j cjk 全文检索）
- `POST /api/v1/match/compare`（单点比对，语义引擎 + 三维评分）

限流注意：普通接口 100 req/min/IP/path（middleware.RateLimitMiddleware，
HTTP 429 + 响应体业务码 4290）——单机压测 100 并发必触发限流。脚本将
429 计为"限流命中"（不算失败），但 429 仍计入响应时间百分位（快速拒绝，
方向乐观）——真实性能口径须在分布模式（--master/--worker 多 IP）或
关闭限流的环境下解读；429 命中率可从 `--csv` 报告按请求名的
"请求总数 − 成功数（429 已吸收）"差值观察，或用 `--statistics-history`
对 429 状态码单独过滤。

用法：
    cd backend
    uv run locust -f scripts/locustfile.py --host http://localhost:8000 \
        -u 100 -r 20 -t 5m --headless --csv reports/perf_$(date +%Y%m%d)
    # compare 任务需有效凭据与简历/岗位（环境变量注入，不入库不落盘）：
    #   LOCUST_USERNAME / LOCUST_PASSWORD / LOCUST_RESUME_ID / LOCUST_POSITION_ID
"""

import os
import random

from locust import HttpUser, between, task

# 限流命中：HTTP 429（业务码 4290 在响应体 code 字段）——设计预期，不计失败。
# 注意：429 仍计入响应时间百分位（快速拒绝，拉低 P95，方向乐观），
# 且被 success() 吸收后不出现在失败数——命中率需按请求名差值/状态码过滤观察
GENERAL_LIMIT_CODE = 429

# 搜索关键词池（真实图谱岗位名，cjk 全文索引命中面广）
_SEARCH_QUERIES = [
    "Python", "Java", "前端", "后端", "算法", "大模型",
    "数据分析", "测试", "运维", "嵌入式", "Docker", "React",
]

_USERNAME = os.environ.get("LOCUST_USERNAME", "admin")
_PASSWORD = os.environ.get("LOCUST_PASSWORD", "")
_RESUME_ID = os.environ.get("LOCUST_RESUME_ID", "")
_POSITION_ID = os.environ.get("LOCUST_POSITION_ID", "")


def judge_status(resp) -> None:
    """响应判定：429 限流命中（设计预期）不计失败；5xx/4xx 分别归类。"""
    if resp.status_code == GENERAL_LIMIT_CODE:
        resp.success()  # 限流命中为预期设计，不计失败
    elif resp.status_code >= 500:
        resp.failure(f"server error {resp.status_code}")
    elif resp.status_code >= 400:
        resp.failure(f"client error {resp.status_code}")


class GraphUser(HttpUser):
    """图谱读链路用户：panorama + 全文检索（匿名可测，限流按 IP）。"""

    wait_time = between(0.5, 2.0)
    weight = 3

    @task(3)
    def panorama(self):
        with self.client.get(
            "/api/v1/graph/panorama", name="panorama", catch_response=True
        ) as resp:
            judge_status(resp)

    @task(2)
    def search(self):
        q = random.choice(_SEARCH_QUERIES)
        with self.client.get(
            "/api/v1/graph/search", params={"q": q, "size": 10},
            name="search", catch_response=True,
        ) as resp:
            judge_status(resp)


class MatchUser(HttpUser):
    """匹配链路用户：login 取 token → compare（需凭据与简历/岗位 id）。"""

    wait_time = between(1.0, 3.0)
    weight = 1

    def on_start(self):
        if not _PASSWORD or not _RESUME_ID or not _POSITION_ID:
            raise RuntimeError(
                "compare 任务需要 LOCUST_USERNAME/LOCUST_PASSWORD/"
                "LOCUST_RESUME_ID/LOCUST_POSITION_ID 环境变量"
            )
        with self.client.post(
            "/api/v1/auth/login",
            json={"username": _USERNAME, "password": _PASSWORD},
            name="login", catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"login 失败: {resp.status_code}")
            data = resp.json().get("data") or {}
            self.token = data.get("access_token", "")
        if not self.token:
            raise RuntimeError("login 未返回 access_token")

    @task
    def compare(self):
        with self.client.post(
            "/api/v1/match/compare",
            json={"resume_id": _RESUME_ID, "position_id": _POSITION_ID},
            headers={"Authorization": f"Bearer {self.token}"},
            name="compare", catch_response=True,
        ) as resp:
            judge_status(resp)
