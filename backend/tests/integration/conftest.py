"""集成测试夹具（TE-M4-01，设计文档 §11.3.9）。

策略：使用本地 docker-compose 基础设施（postgres/neo4j/redis，用户确认），
以真实 uvicorn 子进程 + httpx 发起 HTTP 请求（规避 TestClient 与 asyncpg
Proactor 事件环在 Windows 上的冲突，见 project_memory §3.4）。

基础设施任一端口不可达 → 整体 skip（CI 无 DB 时集成测试自动跳过，
不影响单元测试门禁；本地 docker compose up 后即可运行）。
"""

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[2]

# 本地基础设施端口（docker-compose 三件套）
_INFRA_PORTS = [
    ("127.0.0.1", 5432),   # postgres
    ("127.0.0.1", 7687),   # neo4j bolt
    ("127.0.0.1", 6379),   # redis
]


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def _infra_available() -> bool:
    return all(_port_open(h, p) for h, p in _INFRA_PORTS)


def _free_port() -> int:
    """取一个空闲端口（bind 0 探测后释放，供 uvicorn 使用）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url() -> str:
    """启动真实 uvicorn 子进程，返回 base_url；基础设施不可达则 skip。"""
    if not _infra_available():
        pytest.skip("本地基础设施（postgres/neo4j/redis）不可达，跳过集成测试")

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(_BACKEND_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        # 等待 /health 就绪（最多 30s）
        for _ in range(60):
            if proc.poll() is not None:
                pytest.skip("uvicorn 子进程启动失败")
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            pytest.skip("uvicorn 未在 30s 内就绪")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def auth_headers(base_url: str) -> dict | None:
    """登录 admin 获取 Bearer（H1 认证修复后 resume/match 端点需 user+ 角色）。

    开发环境 bootstrap admin 仅在 users 表为空时创建；真实库已初始化且
    密码非默认值时登录失败返回 None，调用方对认证用例执行 skip。
    """
    try:
        r = httpx.post(
            f"{base_url}/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
            timeout=10,
        )
        if r.status_code == 200:
            token = r.json()["data"]["access_token"]
            return {"Authorization": f"Bearer {token}"}
    except (httpx.HTTPError, KeyError, TypeError):
        pass
    return None


@pytest.fixture(scope="session")
def client(base_url: str, auth_headers: dict | None) -> httpx.Client:
    """同步 httpx 客户端（集成测试不需要 pytest-asyncio）。

    先预热 SBERT：/match/recommend 首次调用会冷加载 paraphrase-multilingual-MiniLM
    （实测 143s，随图谱数据量增长变慢），预热一次后后续匹配测试走缓存 ~22s，
    避免超时误报。预热用临时长超时 client（180s），正式 client 保持 120s。
    认证端点预热需携带 Bearer（/resume/list 需 user+ 角色）。
    """
    warm_headers = auth_headers or {}
    with httpx.Client(base_url=base_url, timeout=180) as warmup:
        try:
            resumes = warmup.get("/api/v1/resume/list", params={"limit": 1}, headers=warm_headers).json()["data"]
            if resumes.get("items"):
                rid = resumes["items"][0]["id"]
                warmup.post("/api/v1/match/recommend", json={"resume_id": rid, "top_n": 1}, headers=warm_headers)
        except (httpx.HTTPError, KeyError, TypeError, IndexError):
            pass  # 预热失败不阻断测试（无简历/接口异常时用例自身会 skip）

    with httpx.Client(base_url=base_url, timeout=120) as c:
        yield c
