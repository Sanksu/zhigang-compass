"""LLM Provider 封装。

支持 OpenAI 兼容 API 的主/备/三 provider 同步调用。
实际调用走 app.services.llm 模块（M3 实现），当前骨架仅定义接口。
"""

from typing import Optional, TypeVar, Type
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMExtractionError(Exception):
    """LLM 抽取失败（超时/格式错误等）。"""


class LLMProvider:
    """LLM Provider 轻封装。

    当前为骨架占位。M3 集成时在此实现：
    - 主/备/三 provider 轮换
    - Instructor/Pydantic 结构化输出
    - 超时/重试/降级
    """

    def __init__(self):
        self._model = "spark-v2"

    def extract_structured(
        self,
        prompt: str,
        response_model: Type[T],
        max_retries: int = 1,
    ) -> T:
        """使用 LLM 从文本中抽取结构化数据。

        当前返回空实例（骨架阶段）。M3 集成 Instructor 后替换。
        """
        raise NotImplementedError("LLM 集成将在 M3 实现，当前为骨架占位")
