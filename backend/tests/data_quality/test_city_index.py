"""城市薪资指数与城市提取单元测试（M5 测试补充）。

覆盖 city_index.py 的两个纯函数：
- extract_city：从 location 文本提取城市名（子串匹配 + 分隔符回退）
- city_index：城市 → 薪资水平指数映射（一线基准 1.0）

设计原则：
1. extract_city：已知城市子串优先匹配、长名优先、空值安全、未知城市回退
2. city_index：已知城市返回正确指数、未知城市回退 1.0、数据完整性校验
"""

import pytest

from app.services.data_quality.city_index import (
    DEFAULT_CITY_INDEX,
    _CITY_TIER_INDEX,
    city_index,
    extract_city,
)


class TestExtractCity:
    """extract_city：从 location 文本提取城市名。"""

    def test_none_returns_empty_string(self):
        """None 输入 → 空串。"""
        assert extract_city(None) == ""

    def test_empty_string_returns_empty(self):
        """空字符串 → 空串。"""
        assert extract_city("") == ""
        assert extract_city("   ") == ""

    def test_exact_city_name(self):
        """精确城市名直接返回。"""
        assert extract_city("北京") == "北京"
        assert extract_city("上海") == "上海"
        assert extract_city("深圳") == "深圳"

    def test_city_with_district_suffix(self):
        """城市 + 区县/区域 → 提取城市名。"""
        assert extract_city("北京朝阳区") == "北京"
        assert extract_city("成都高新区") == "成都"
        assert extract_city("杭州西湖区") == "杭州"

    def test_city_with_separator(self):
        """各种分隔符分隔的城市区域 → 提取城市。"""
        assert extract_city("北京·朝阳区") == "北京"
        assert extract_city("上海,浦东") == "上海"
        assert extract_city("深圳，南山区") == "深圳"
        assert extract_city("广州/天河") == "广州"
        assert extract_city("南京|鼓楼区") == "南京"
        assert extract_city("武汉、洪山区") == "武汉"

    def test_longer_city_name_matches_first(self):
        """长城市名优先匹配（避免「广州市」被「广州」先匹配）。"""
        # 注意：_CITY_NAMES 按长度降序排列，长名优先
        # 我们的词典里有「哈尔滨」（3字），不会被短名先匹配
        assert extract_city("哈尔滨市") == "哈尔滨"
        assert extract_city("乌鲁木齐市") == "乌鲁木齐"

    def test_unknown_city_returns_first_segment(self):
        """未知城市取首个分隔片段。"""
        assert extract_city("硅谷，加州") == "硅谷"
        assert extract_city("纽约·曼哈顿") == "纽约"
        assert extract_city("新加坡/中部") == "新加坡"

    def test_unknown_city_no_separator_returns_full(self):
        """未知城市且无分隔符 → 原样返回（截断 32 字）。"""
        assert extract_city("硅谷") == "硅谷"
        assert extract_city("西雅图") == "西雅图"

    def test_unknown_city_truncated_to_32_chars(self):
        """未知城市名超长时截断到 32 字符。"""
        long_name = "A" * 40
        result = extract_city(long_name)
        assert len(result) == 32
        assert result == "A" * 32

    def test_known_city_in_long_text(self):
        """长文本中包含已知城市 → 正确提取。"""
        assert extract_city("浙江省杭州市西湖区文三路") == "杭州"
        assert extract_city("江苏省南京市鼓楼区") == "南京"

    def test_whitespace_stripped(self):
        """前后空白被剥离。"""
        assert extract_city("  北京  ") == "北京"
        assert extract_city("\t上海\n") == "上海"

    def test_multiple_cities_returns_first_match(self):
        """多个城市时返回排序中第一个命中的（按长度降序）。"""
        # "北京" 和 "上海" 都是 2 字，看排序顺序
        result = extract_city("北京上海双城")
        # 只要返回其中一个已知城市即可（具体哪个取决于 _CITY_NAMES 排序）
        assert result in _CITY_TIER_INDEX


class TestCityIndex:
    """city_index：城市 → 薪资水平指数。"""

    def test_first_tier_cities_are_around_one(self):
        """一线城市指数接近 1.0（基准）。"""
        assert city_index("北京") == pytest.approx(1.00)
        assert city_index("上海") == pytest.approx(1.00)
        assert city_index("深圳") == pytest.approx(1.02)
        assert city_index("广州") == pytest.approx(0.98)

    def test_new_first_tier_range(self):
        """新一线城市指数在 0.82~0.90 区间。"""
        new_first_tier = ["杭州", "成都", "南京", "苏州", "武汉", "天津", "重庆", "西安"]
        for city in new_first_tier:
            idx = city_index(city)
            assert 0.80 <= idx <= 0.92, f"{city} 指数 {idx} 不在新一线区间"

    def test_second_tier_range(self):
        """二线城市指数在 0.68~0.80 区间。"""
        second_tier = ["大连", "福州", "济南", "东莞", "佛山", "哈尔滨", "南昌", "贵阳"]
        for city in second_tier:
            idx = city_index(city)
            assert 0.66 <= idx <= 0.82, f"{city} 指数 {idx} 不在二线区间"

    def test_unknown_city_returns_default(self):
        """未知城市返回默认值 1.0。"""
        assert city_index("硅谷") == DEFAULT_CITY_INDEX
        assert city_index("纽约") == DEFAULT_CITY_INDEX
        assert city_index("") == DEFAULT_CITY_INDEX
        assert city_index("随便一个地方") == DEFAULT_CITY_INDEX

    def test_default_is_one_point_zero(self):
        """默认指数 = 1.0（不改变原有判定）。"""
        assert DEFAULT_CITY_INDEX == 1.0

    def test_all_known_cities_have_positive_index(self):
        """所有收录城市指数均为正数。"""
        for city, idx in _CITY_TIER_INDEX.items():
            assert idx > 0, f"{city} 指数 {idx} 非正"
            assert isinstance(idx, float), f"{city} 指数不是 float"

    def test_index_between_zero_and_one(self):
        """所有城市指数在 (0, 1.1] 范围内（深圳最高 1.02）。"""
        for city, idx in _CITY_TIER_INDEX.items():
            assert 0 < idx <= 1.1, f"{city} 指数 {idx} 超出合理范围"

    def test_shenzhen_highest_index(self):
        """深圳指数最高（1.02）。"""
        max_idx = max(_CITY_TIER_INDEX.values())
        assert city_index("深圳") == pytest.approx(max_idx)
        assert max_idx > 1.0

    def test_beijing_shanghai_are_baseline(self):
        """北京、上海均为基准 1.0。"""
        assert city_index("北京") == pytest.approx(city_index("上海"))
        assert city_index("北京") == pytest.approx(1.0)


class TestCityIndexDataIntegrity:
    """城市指数数据完整性校验。"""

    def test_dictionary_not_empty(self):
        """城市词典非空。"""
        assert len(_CITY_TIER_INDEX) > 30

    def test_all_keys_are_strings(self):
        """所有城市名都是字符串。"""
        for city in _CITY_TIER_INDEX:
            assert isinstance(city, str)
            assert len(city) > 0

    def test_all_values_are_finite_floats(self):
        """所有指数都是有限浮点数。"""
        import math
        for city, idx in _CITY_TIER_INDEX.items():
            assert isinstance(idx, float), f"{city} 不是 float"
            assert math.isfinite(idx), f"{city} 指数非有限值"

    def test_no_duplicate_city_names(self):
        """城市名无重复（dict key 天然唯一）。"""
        assert len(_CITY_TIER_INDEX) == len(set(_CITY_TIER_INDEX.keys()))

    def test_extract_and_index_consistency(self):
        """extract_city 提取的已知城市，city_index 都能在表中查到。"""
        test_locations = [
            "北京朝阳区", "上海·浦东", "深圳南山区",
            "杭州西湖区", "成都高新区", "南京市鼓楼区",
        ]
        for loc in test_locations:
            city = extract_city(loc)
            assert city in _CITY_TIER_INDEX, \
                f"{loc} → {city} 未命中城市词典"
            idx = city_index(city)
            assert idx == _CITY_TIER_INDEX[city], \
                f"{city} 指数不一致"
