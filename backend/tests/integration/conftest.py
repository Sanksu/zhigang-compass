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
def client(base_url: str) -> httpx.Client:
    """同步 httpx 客户端（集成测试不需要 pytest-asyncio）。

    超时放宽到 120s：recommend/compare 首次调用会冷加载 SBERT 模型
    （paraphrase-multilingual-MiniLM，约 30s），超时过短会误报失败。
    """
    with httpx.Client(base_url=base_url, timeout=120) as c:
        yield c
