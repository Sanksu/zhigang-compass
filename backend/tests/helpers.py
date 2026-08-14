"""共享测试桩（08-14 审查：_FakeRedis 黑名单桩在多个测试文件重复实现，收敛于此）。

测试文件通过 `from tests.helpers import FakeRedis` 引用。
"""


class FakeRedis:
    """黑名单检查桩（get 返回 None = 未拉黑）。"""

    async def get(self, key):
        return None
