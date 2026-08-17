"""共享测试桩（08-14 审查：_FakeRedis 黑名单桩在多个测试文件重复实现，收敛于此）。

测试文件通过 `from tests.helpers import FakeRedis` 引用。
"""


class FakeRedis:
    """黑名单检查桩（get 返回 None = 未拉黑）。"""

    async def get(self, key):
        return None


class FakeProc:
    """模拟子进程：communicate 返回预置 stdout，returncode=0（08-17 收敛 3 处重复）。"""

    def __init__(self, lines):
        self._lines = lines
        self.returncode = 0

    def communicate(self, timeout=None):
        return ("\n".join(self._lines), "")


class SeqResult:
    """next_id 的 Counter 查询结果桩（single 返回 seq；08-17 收敛 2 处重复）。"""

    def __init__(self, seq: int):
        self._seq = seq

    def single(self):
        return {"seq": self._seq}
