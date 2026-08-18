"""LinkedIn 技术岗标题白名单过滤测试（08-18 聚焦治理）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

from crawlers.spiders.linkedin_public import _is_tech_title


def test_tech_titles_match():
    """技术岗标题（中英文）命中白名单。"""
    matches = [
        "Software Engineer (all teams)",
        "Applied AI Software Engineer",
        "Senior Backend Developer",
        "Data Scientist - Machine Learning",
        "DevOps Engineer / SRE",
        "前端开发工程师",
        "算法工程师（大模型方向）",
        "嵌入式软件工程师",
        "网络安全工程师",
        "数据分析师",
        "Product Manager (Technical)",
        "QA Automation Engineer",
    ]
    for title in matches:
        assert _is_tech_title(title), f"应命中: {title}"


def test_non_tech_titles_rejected():
    """非技术基础岗（治理目标样本）不命中。"""
    rejected = [
        "Crew Member",
        "Pharmacist",
        "Department Manager",
        "Cashier",
        "Sales Associate",
        "Registered Nurse",
        "Driver - Delivery",
        "Barista",
        "Receptionist",
        "Head of Systematic Macro Strategy Team (USA)",
        "Recruiter at Jobot - Work 100% Remote!",
        "Electrical Estimator",
        "Busser",
        "Store Manager Trainee",
        "",
        "  ",
    ]
    for title in rejected:
        assert not _is_tech_title(title), f"应拒绝: {title!r}"


def test_case_insensitive():
    """大小写不敏感。"""
    assert _is_tech_title("SOFTWARE ENGINEER")
    assert _is_tech_title("Machine Learning Engineer")
