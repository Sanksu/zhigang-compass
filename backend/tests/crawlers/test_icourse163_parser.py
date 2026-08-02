"""icourse163 课程解析测试（parse_course_list）。

覆盖：type=301（在线课程）与 type=306（专业/培训课程）均可解析入库，
type=308（教材）跳过。修复背景：Vue/React/HTML/JavaScript 等关键词
搜索结果卡片为 type=306，此前被 `type != 301` 过滤导致整批课程丢失。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

from crawlers.icourse163_crawler import parse_course_list


def _entry(entry_type: int, course_id: str, name: str) -> dict:
    """构造 icourse163 搜索 API 的单个课程卡片。"""
    return {
        "type": entry_type,
        "courseId": course_id,
        "mocCourseKyCardBaseInfoDto": {
            "courseId": course_id,
            "courseName": name,
            "termId": f"{course_id}t1",
            "teacherName": "测试讲师",
            "tags": [{"name": "前端"}],
            "enrollNum": 100,
        },
        "mocCourseCard": {
            "mocCourseCardDto": {
                "name": name,
                "termPanel": {
                    "lectorPanels": [],
                    "duration": "term",
                    "startTime": 0,
                    "endTime": 0,
                    "enrollCount": 100,
                },
                "schoolPanel": {"name": "测试大学"},
            }
        },
    }


def test_parse_type_301_online_course():
    """type=301 在线课程正常解析。"""
    api_data = {"result": {"list": [_entry(301, "1001", "C语言程序设计")]}}
    items = parse_course_list(api_data, "C")
    assert len(items) == 1
    assert items[0]["title"] == "C语言程序设计"
    assert items[0]["source_id"] == "1001"


def test_parse_type_306_professional_course():
    """type=306 专业/培训课程（Vue/React/HTML 等关键词主要返回此类型）同样入库。"""
    api_data = {"result": {"list": [_entry(306, "2001", "HTML5移动应用开发")]}}
    items = parse_course_list(api_data, "HTML")
    assert len(items) == 1
    assert items[0]["title"] == "HTML5移动应用开发"


def test_parse_skips_type_308_textbook():
    """type=308 教材卡片跳过。"""
    api_data = {"result": {"list": [_entry(308, "3001", "教材")]}}
    items = parse_course_list(api_data, "C")
    assert items == []


def test_parse_mixed_types():
    """混合列表：301 与 306 入库，308 跳过。"""
    api_data = {
        "result": {
            "list": [
                _entry(301, "1001", "Java程序设计"),
                _entry(306, "2001", "Vue.js实战"),
                _entry(308, "3001", "教材"),
            ]
        }
    }
    items = parse_course_list(api_data, "Java")
    assert [i["title"] for i in items] == ["Java程序设计", "Vue.js实战"]
