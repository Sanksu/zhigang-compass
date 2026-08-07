"""MLI 媒介落差指数（设计文档 7.2.5 产业化拐点监测）。

MLI = 论文/课程/社区/招聘 四维信号的加权综合，量化「技术热点与产业
落地」之间的落差。MLI > 0.6 判定 Ready-to-industrialize（可产业化）。

信号维度归一化（[0,1]）：
- 命中阈值（论文 2σ / 课程 2σ / 社区 2σ / JD 环比 50%）→ 该维记为 1.0
- 未命中但信号非零 → 按 z/(2σ) 或 环比/50% 截断至 [0,1)
- 无信号 → 0.0

设计文档未给出维度权重默认值，此处声明为常量等权（0.25），便于运营调整。
"""

from dataclasses import dataclass

# 设计文档 §7.2.5：MLI 默认等权（未指定权重时）
MLI_WEIGHTS: dict[str, float] = {
    "paper": 0.25,
    "course": 0.25,
    "community": 0.25,
    "jd": 0.25,
}
# §7.2.5 产业化拐点阈值
MLI_THRESHOLD = 0.6


@dataclass
class MLIResult:
    """MLI 计算结果。"""

    mli: float
    dimensions: dict[str, float]  # 每维归一化信号值（[0,1]）
    ready_to_industrialize: bool  # mli > 0.6


def _normalize(value: float | None, threshold: float) -> float:
    """将原始信号值归一化到 [0,1]。

    命中阈值记为 1.0；未命中按 value/threshold 截断；无信号为 0.0。
    """
    if value is None or value <= 0:
        return 0.0
    if value >= threshold:
        return 1.0
    return max(0.0, min(1.0, value / threshold))


def compute_mli(
    z_paper: float | None = None,
    z_course: float | None = None,
    z_community: float | None = None,
    growth_jd: float | None = None,
    threshold: float = MLI_THRESHOLD,
    weights: dict[str, float] | None = None,
) -> MLIResult:
    """计算 MLI（媒介落差指数）。

    Args:
        z_paper: 论文源 z 偏离（2σ 阈值）
        z_course: 课程源 z 偏离（2σ 阈值）
        z_community: 社区源 z 偏离（2σ 阈值）
        growth_jd: JD 源 3 月移动平均环比增长率（50% 阈值）
        threshold: 产业化拐点阈值（默认 0.6）
        weights: 四维权重（默认等权 0.25，总和应为 1.0）

    Returns:
        MLIResult：mli 加权和、每维归一化值、是否 Ready-to-industrialize。
    """
    w = weights or MLI_WEIGHTS
    dims = {
        "paper": _normalize(z_paper, 2.0),
        "course": _normalize(z_course, 2.0),
        "community": _normalize(z_community, 2.0),
        "jd": _normalize(growth_jd, 0.5),
    }
    mli = sum(dims[k] * w[k] for k in dims)
    return MLIResult(
        mli=round(mli, 4),
        dimensions=dims,
        ready_to_industrialize=mli > threshold,
    )
