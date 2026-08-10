"""岗位聚合 level 合并测试（设计文档 §5.5 REQUIRES 边）。

覆盖：_position_skills 携带 level、_most_common_level 众数、build_aggregates 收集 level。
"""

from types import SimpleNamespace

import pytest

from app.services.extraction.dictionary import (
    _ANALYST_SUB_FAMILIES,
    _POSITION_KEYWORDS,
)
from app.services.kg.aggregation import (
    _ALLOWED_SKILL_CATEGORIES,
    _is_must,
    _jd_decay_weight,
    _most_common_level,
    _position_skills,
    build_aggregates,
    write_aggregates,
)


class TestMustJudgment:
    """must 判定（P2-D 聚合口径）：hit≥3 样本保护 + JD 覆盖率≥15%
    + must 标注占比>1/2 三重条件。"""

    def _sa(self, hit: int, must_count: int):
        return SimpleNamespace(hit=hit, must_count=must_count)

    def test_high_coverage_is_must(self):
        # hit=10/jd_count=20（覆盖率 50%），must 占 80% → must
        assert _is_must(self._sa(hit=10, must_count=8), jd_count=20) is True

    def test_low_coverage_is_nice(self):
        # hit=3/jd_count=30（覆盖率 10% <15%），must 全标 → 覆盖率不足 → nice
        assert _is_must(self._sa(hit=3, must_count=3), jd_count=30) is False

    def test_must_ratio_at_half_is_nice(self):
        # 覆盖率达标但 must 标注恰 50%（须严格大于）→ nice
        assert _is_must(self._sa(hit=6, must_count=3), jd_count=10) is False

    def test_low_hit_protection_is_nice(self):
        # hit=1/2（样本不足），即使全标 must 也判 nice，防单条 JD 虚高
        assert _is_must(self._sa(hit=1, must_count=1), jd_count=2) is False
        assert _is_must(self._sa(hit=2, must_count=2), jd_count=3) is False

    def test_zero_jd_count_is_nice(self):
        # jd_count=0 防御：不判 must
        assert _is_must(self._sa(hit=3, must_count=3), jd_count=0) is False


class TestPositionSkills:
    def test_requirements_carry_level(self):
        ext = {
            "requirements": [
                {"skill_name": "Java", "necessity": "must", "level": "高级"},
                {"skill_name": "MySQL", "necessity": "must", "level": None},
            ]
        }
        assert _position_skills(ext) == [
            ("Java", "must", "高级"),
            ("MySQL", "must", ""),
        ]

    def test_fallback_to_skills_without_level(self):
        ext = {"skills": [{"name": "Python"}, {"name": "Go"}]}
        assert _position_skills(ext) == [("Python", "nice", ""), ("Go", "nice", "")]

    def test_skills_merged_into_requirements(self):
        # P4：requirements 存在时 skills 中未进 requirements 的技能以 nice 并入
        # （requirements 是 skills 子集，漏掉则技能频次被低估）；同技能去重保留
        # requirements 的 must/level 语义
        ext = {
            "requirements": [{"skill_name": "Java", "necessity": "must", "level": "高级"}],
            "skills": [{"name": "Java"}, {"name": "Vue3"}],
        }
        assert _position_skills(ext) == [
            ("Java", "must", "高级"),
            ("Vue.js", "nice", ""),
        ]


class TestPositionSkillsNormalization:
    """聚合技能名归一化：旧快照异构名（P1-1 前抽取）对齐到规范节点，防聚合重建旧名。"""

    def test_requirements_skill_name_normalized(self):
        ext = {"requirements": [{"skill_name": "Vue3", "necessity": "must", "level": None}]}
        assert _position_skills(ext) == [("Vue.js", "must", "")]

    def test_fallback_skills_name_normalized(self):
        ext = {"skills": [{"name": "reactjs"}]}
        assert _position_skills(ext) == [("React", "nice", "")]

    def test_whitelist_word_preserved(self):
        # 白名单词整体保护，聚合不被剥成泛词碎片
        ext = {"requirements": [{"skill_name": "操作系统", "necessity": "must", "level": None}]}
        assert _position_skills(ext) == [("操作系统", "must", "")]

    def test_stopword_dropped(self):
        # 归一化后为空的旧泛词碎片（"系统"→""）不进聚合
        ext = {"requirements": [{"skill_name": "系统", "necessity": "must", "level": None}]}
        assert _position_skills(ext) == []

    def test_stopword_preserved_after_clean_dropped(self):
        # 剥后缀剥不掉的旧泛词（"嵌入式"在 P1-2 才入黑名单，旧快照残留）按黑名单剔除
        ext = {"requirements": [{"skill_name": "嵌入式", "necessity": "must", "level": None}]}
        assert _position_skills(ext) == []


class TestMostCommonLevel:
    def test_empty_returns_empty(self):
        assert _most_common_level([]) == ""

    def test_majority_wins(self):
        assert _most_common_level(["初级", "中级", "中级"]) == "中级"

    def test_tie_keeps_first_seen(self):
        assert _most_common_level(["高级", "初级", "初级", "高级"]) == "高级"


class TestBuildAggregatesLevel:
    def _row(self, position: str, source: str, level: str | None):
        return SimpleNamespace(
            snapshot={
                "extraction": {
                    "position_name": position,
                    "requirements": [
                        {"skill_name": "Java", "necessity": "must", "level": level},
                    ],
                }
            },
            source=source,
        )

    def test_levels_collected_per_skill(self):
        rows = [
            self._row("Java开发工程师", "boss", "高级"),
            self._row("Java开发工程师", "zhilian", "中级"),
            self._row("Java开发工程师", "zhilian", None),  # 无 level 不计入
        ]
        agg = build_aggregates(rows)
        pa = agg["Java开发工程师"]
        assert pa.skills["Java"].levels == ["高级", "中级"]
        assert pa.skills["Java"].hit == 3

    def test_skills_outside_requirements_counted(self):
        # P4 聚合级：requirements 之外 skills 补入的技能计入 hit/来源，
        # 且不因并入而虚高 must（skills 一律 nice）
        row = SimpleNamespace(
            snapshot={
                "extraction": {
                    "position_name": "Java开发工程师",
                    "requirements": [{"skill_name": "Java", "necessity": "must", "level": None}],
                    "skills": [{"name": "Java"}, {"name": "Vue3"}],
                }
            },
            source="boss",
        )
        agg = build_aggregates([row])
        sa = agg["Java开发工程师"].skills
        assert sa["Java"].hit == 1
        assert sa["Vue.js"].hit == 1
        assert sa["Vue.js"].must_count == 0


class _FakeResult:
    """会话桩 run 的返回值：write_aggregates 需读取单行结果。"""

    def __init__(self, single: dict):
        self._single = single

    def single(self):
        return self._single

    def data(self):
        return [self._single] if self._single is not None else []


class _FakeSession:
    """write_aggregates 的会话桩：收集 run 调用参数。

    edited_positions 控制 PositionEditLog 查询返回的人工编辑岗位集合。
    """

    def __init__(self):
        self.calls = []
        self.edited_positions: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query, **params):
        self.calls.append((query, params))
        if "PositionEditLog" in query:
            return _FakeResult({"names": self.edited_positions})
        return _FakeResult({"c": 0})


class TestSoftSkillsAggregation:
    """岗位侧软技能聚合（设计文档 9.2 节：soft_skills 白名单按 JD 命中计数）。"""

    def _row(self, position: str, source: str, soft: list[str]):
        return SimpleNamespace(
            snapshot={"extraction": {"position_name": position, "soft_skills": soft}},
            source=source,
        )

    def test_soft_skills_collected_with_whitelist_filter(self):
        rows = [
            self._row("Java开发工程师", "boss", ["团队协作", "沟通能力"]),
            self._row("Java开发工程师", "zhilian", ["团队协作"]),
            self._row("Java开发工程师", "lagou", ["体力好"]),  # 白名单外剔除
        ]
        agg = build_aggregates(rows)
        pa = agg["Java开发工程师"]
        assert dict(pa.soft_skills) == {"团队协作": 2, "沟通能力": 1}

    def test_write_aggregates_embeds_soft_skills(self):
        rows = [self._row("Java开发工程师", "boss", ["团队协作"])]
        agg = build_aggregates(rows)
        fake = _FakeSession()
        write_aggregates(fake, agg, now="2026-08-04T00:00:00")
        positions_items = fake.calls[0][1]["items"]
        assert positions_items[0]["soft_skills"] == ["团队协作"]


class TestP2CrossDomainAndLowFreq:
    """P2-C 跨域降权 + P2-D 低频边过滤（write_aggregates 生成 edges 阶段）。"""

    def _row(self, requirements: list[dict], i: int, position: str = "Java开发工程师"):
        return SimpleNamespace(
            snapshot={"extraction": {"position_name": position, "requirements": list(requirements)}},
            source=f"src{i}",
        )

    def _edges(self, rows):
        agg = build_aggregates(rows)
        fake = _FakeSession()
        write_aggregates(fake, agg, now="2026-08-08T00:00:00")
        return fake.calls[1][1]["edges"]

    def test_cross_domain_skill_downgraded_to_nice(self):
        # Java开发工程师 12 条 JD 全标 Vue.js（前端类别）为 must：
        # 命中率/标注率均满足 must 判定，但类别不在 Java 白名单 → 降权 nice；
        # Java（编程语言）不受影响保持 must
        reqs = [
            {"skill_name": "Java", "necessity": "must", "level": "熟悉"},
            {"skill_name": "Vue.js", "necessity": "must", "level": ""},
        ]
        edges = self._edges([self._row(reqs, i) for i in range(12)])
        by_skill = {e["skill"]: e for e in edges}
        assert by_skill["Java"]["necessity"] == "must"
        assert by_skill["Java"]["weight"] == 0.8
        assert by_skill["Vue.js"]["necessity"] == "nice"
        assert by_skill["Vue.js"]["weight"] == 0.4

    def test_low_freq_edge_filtered(self):
        # 大岗位（jd_count≥10）：hit=1 的一次性技能边不生成；hit 达标的保留
        reqs = [{"skill_name": "Java", "necessity": "must", "level": ""}]
        rows = [self._row(reqs, i) for i in range(12)]
        rows[0].snapshot["extraction"]["requirements"].append(
            {"skill_name": "Rust", "necessity": "must", "level": ""}
        )
        edges = self._edges(rows)
        skills = {e["skill"] for e in edges}
        assert "Java" in skills
        assert "Rust" not in skills  # hit=1 < _MIN_HIT_EDGE 被过滤

    def test_small_position_keeps_low_freq_edge(self):
        # 小岗位（jd_count<10）样本不足：hit=1 的边保留，不做低频过滤
        reqs = [{"skill_name": "Java", "necessity": "must", "level": ""}]
        rows = [self._row(reqs, i) for i in range(3)]
        rows[0].snapshot["extraction"]["requirements"].append(
            {"skill_name": "Rust", "necessity": "must", "level": ""}
        )
        edges = self._edges(rows)
        skills = {e["skill"] for e in edges}
        assert "Rust" in skills

    def test_product_manager_cross_domain_downgraded(self):
        # P5：产品经理此前未配置跨域白名单（Vue.js 全标 must 不降权）；
        # 配置后前端类技能（Vue.js）强制 nice，期望类别（编程语言 SQL）保持 must
        reqs = [
            {"skill_name": "Vue.js", "necessity": "must", "level": ""},
            {"skill_name": "SQL", "necessity": "must", "level": ""},
        ]
        edges = self._edges([self._row(reqs, i, "产品经理") for i in range(12)])
        by_skill = {e["skill"]: e for e in edges}
        assert by_skill["Vue.js"]["necessity"] == "nice"
        assert by_skill["SQL"]["necessity"] == "must"

    def test_all_families_have_cross_domain_whitelist(self):
        # P5：所有岗位族都必须配置跨域白名单，防新增族漏配导致跨域技能不降权
        families = {std for _, std in _POSITION_KEYWORDS} | {std for _, std in _ANALYST_SUB_FAMILIES}
        assert families <= set(_ALLOWED_SKILL_CATEGORIES)


class TestJdDecayWeight:
    """时滞/通胀降权系数读取与合并（设计文档 §4.7/§4.8 聚合消费）。"""

    def test_no_detection_records_returns_one(self):
        # 无检测记录（validation/inflation 缺失）不武断降权
        assert _jd_decay_weight({}) == 1.0
        assert _jd_decay_weight({"extraction": {}}) == 1.0

    def test_temporal_decay_weight(self):
        # 内容时滞 stale：降权 ×0.5
        assert _jd_decay_weight({"validation": {"decay_weight": 0.5}}) == 0.5

    def test_inflation_decay_weight(self):
        # 高通胀 severe：降权 ×0.4
        assert _jd_decay_weight({"inflation": {"decay_weight": 0.4}}) == 0.4

    def test_strictest_wins(self):
        # 时滞 stale ×0.5 + 通胀 severe ×0.4 并存 → 取更严格者 0.4
        # （与 temporal_detector.apply_temporal_decay 内部取最严重者一致）
        snap = {
            "validation": {"decay_weight": 0.5},
            "inflation": {"decay_weight": 0.4},
        }
        assert _jd_decay_weight(snap) == 0.4

    def test_detection_record_without_decay_defaults_fresh(self):
        # 检测记录存在但缺 decay_weight（异常数据）不武断降权
        assert _jd_decay_weight({"validation": {"sai": {}}}) == 1.0


class TestDecayConsumption:
    """聚合层消费时滞/通胀降权：obsolete 归档跳过、非零系数加权技能贡献。"""

    def _row(self, position: str, source: str, snap_extra: dict | None = None):
        snap = {
            "extraction": {
                "position_name": position,
                "requirements": [{"skill_name": "Java", "necessity": "must", "level": None}],
            }
        }
        if snap_extra:
            snap.update(snap_extra)
        return SimpleNamespace(snapshot=snap, source=source)

    def test_stale_jd_weighted_skill_contribution(self):
        # stale ×0.5：技能 hit/must_count 贡献减半；jd_count 仍计真实 JD 条数
        rows = [
            self._row("Java开发工程师", "boss"),
            self._row("Java开发工程师", "zhilian", {"validation": {"decay_weight": 0.5}}),
        ]
        agg = build_aggregates(rows)
        pa = agg["Java开发工程师"]
        sa = pa.skills["Java"]
        assert pa.jd_count == 2
        assert sa.hit == pytest.approx(1.5)
        assert sa.must_count == pytest.approx(1.5)

    def test_obsolete_jd_excluded_from_aggregation(self):
        # obsolete ×0（归档不入聚合）：jd_count 与技能贡献都不计
        rows = [
            self._row("Java开发工程师", "boss"),
            self._row("Java开发工程师", "zhilian", {"validation": {"decay_weight": 0.0}}),
        ]
        agg = build_aggregates(rows)
        pa = agg["Java开发工程师"]
        assert pa.jd_count == 1
        assert pa.skills["Java"].hit == 1

    def test_severe_inflation_weighted_skill_contribution(self):
        # 高通胀 ×0.4：技能贡献降为 0.4
        rows = [
            self._row("Java开发工程师", "boss", {"inflation": {"decay_weight": 0.4}}),
        ]
        agg = build_aggregates(rows)
        sa = agg["Java开发工程师"].skills["Java"]
        assert sa.hit == pytest.approx(0.4)
        assert sa.must_count == pytest.approx(0.4)

    def test_decayed_jd_lowers_must_eligibility(self):
        # 3 条 stale（×0.5）+ 1 条 fresh，Java 全标 must：
        # 加权 hit=2.5 < 3 样本保护线 → 降权 JD 的贡献不足以让该技能判 must；
        # 对照 4 条 fresh（hit=4）→ 满足三重条件判 must
        stale = self._row("Java开发工程师", "boss", {"validation": {"decay_weight": 0.5}})
        fresh = self._row("Java开发工程师", "boss")
        agg = build_aggregates([stale] * 3 + [fresh])
        pa = agg["Java开发工程师"]
        sa = pa.skills["Java"]
        assert sa.hit == pytest.approx(2.5)
        assert _is_must(sa, jd_count=pa.jd_count) is False

        agg2 = build_aggregates([fresh] * 4)
        pa2 = agg2["Java开发工程师"]
        assert _is_must(pa2.skills["Java"], jd_count=pa2.jd_count) is True


class TestInflationAggregationStrategy:
    """岗位级通胀排除 + 平台级源降权（设计文档 §4.8 聚合消费）。

    岗位级：岗位内通胀 JD 占比 ≥30% 时，通胀 JD 完全剔除（jd_count 与
    技能贡献均不计）；平台级：源内通胀 JD 占比 >50% 时，该源全部 JD 额外
    降权 ×0.5（normal JD 由 1.0 → 0.5，通胀 JD 取 min(decay, 0.5)）。
    """

    def _row(self, position: str, source: str, inflated: bool):
        snap = {
            "extraction": {
                "position_name": position,
                "requirements": [{"skill_name": "Java", "necessity": "must", "level": None}],
            }
        }
        if inflated:
            snap["inflation"] = {"label": "severe_inflation", "decay_weight": 0.4}
        return SimpleNamespace(snapshot=snap, source=source)

    def test_position_inflation_excluded_when_ratio_ge_30(self):
        # 岗位内 3 normal + 2 通胀（40% ≥30%）→ 通胀 JD 完全剔除：
        # jd_count 只计正常 3 条，技能贡献 3（通胀贡献为 0）；
        # 源占比 40% <50% → 无源降权干扰
        rows = (
            [self._row("Java开发工程师", "boss", False) for _ in range(3)]
            + [self._row("Java开发工程师", "boss", True) for _ in range(2)]
        )
        pa = build_aggregates(rows)["Java开发工程师"]
        assert pa.jd_count == 3
        assert pa.skills["Java"].hit == 3

    def test_position_below_30_no_exclusion(self):
        # 岗位内 3 normal + 1 通胀（25% <30%）→ 不排除，通胀 JD 按
        # 自身 decay_weight 0.4 参与：jd_count=4，hit=3+0.4=3.4
        rows = (
            [self._row("Java开发工程师", "boss", False) for _ in range(3)]
            + [self._row("Java开发工程师", "boss", True)]
        )
        pa = build_aggregates(rows)["Java开发工程师"]
        assert pa.jd_count == 4
        assert pa.skills["Java"].hit == pytest.approx(3.4)

    def test_source_inflation_downgrades_all_jds(self):
        # 源 boss 内通胀占比 6/11≈54.5% >50% → 该源全部 JD ×0.5。
        # 岗位 A（5 normal，占比 0% 无岗位排除）：hit=5×0.5=2.5；
        # 岗位 B（6 全通胀，占比 100% ≥30%）→ 通胀 JD 全剔除，岗位消失
        rows = (
            [self._row("Java开发工程师", "boss", False) for _ in range(5)]
            + [self._row("数据开发工程师", "boss", True) for _ in range(6)]
        )
        agg = build_aggregates(rows)
        assert "数据开发工程师" not in agg
        pa = agg["Java开发工程师"]
        assert pa.jd_count == 5
        assert pa.skills["Java"].hit == pytest.approx(2.5)

    def test_source_below_50_no_downgrade(self):
        # 源 boss 内 5 normal + 2 通胀（28.6% <50%）→ 无源降权；
        # 岗位占比 28.6% <30% → 无岗位排除；hit=5+2×0.4=5.8
        rows = (
            [self._row("Java开发工程师", "boss", False) for _ in range(5)]
            + [self._row("Java开发工程师", "boss", True) for _ in range(2)]
        )
        pa = build_aggregates(rows)["Java开发工程师"]
        assert pa.jd_count == 7
        assert pa.skills["Java"].hit == pytest.approx(5.8)

    def test_position_exclusion_does_not_affect_other_positions(self):
        # 岗位 A 40% 通胀触发排除；岗位 B 全 normal 不受影响；
        # 源占比 2/9≈22% <50% → 无源降权
        rows = (
            [self._row("Java开发工程师", "boss", False) for _ in range(3)]
            + [self._row("Java开发工程师", "boss", True) for _ in range(2)]
            + [self._row("Python开发工程师", "boss", False) for _ in range(4)]
        )
        agg = build_aggregates(rows)
        pa_a = agg["Java开发工程师"]
        assert pa_a.jd_count == 3
        assert pa_a.skills["Java"].hit == 3
        pa_b = agg["Python开发工程师"]
        assert pa_b.jd_count == 4
        assert pa_b.skills["Java"].hit == 4


class TestAggregatesAlignedDeletion:
    """P1-1 衰退技能移除（设计文档 §7.1.1）：write_aggregates 按聚合输出
    对齐删除 REQUIRES 边，人工编辑岗位（PositionEditLog）跳过。

    聚合输出之外的边来源：SimHash 重复 JD 独有技能、大岗位 hit<2 一次性
    噪声、以及 JD 已消失的衰退技能。仅 MERGE 会永久保留这些边，导致
    图谱技能集与聚合口径漂移。
    """

    def _rows(self, n: int, position: str, extra_req: dict | None = None):
        rows = [
            SimpleNamespace(
                snapshot={
                    "extraction": {
                        "position_name": position,
                        "requirements": [
                            {"skill_name": "Java", "necessity": "must", "level": ""},
                        ],
                    }
                },
                source=f"src{i}",
            )
            for i in range(n)
        ]
        if extra_req:
            rows[0].snapshot["extraction"]["requirements"].append(extra_req)
        return rows

    def _delete_params(self, fake):
        query, params = fake.calls[-1]
        assert "DELETE r" in query, "最后一个 run 应为对齐删除查询"
        return params

    def test_stale_edges_removed_via_aligned_deletion(self):
        # 大岗位 12 条 JD：Rust 仅 1 条命中被低频过滤（不在聚合输出），
        # 图谱残留的 REQUIRES-Rust 边应由对齐删除清除；excluded 无人工岗位
        rows = self._rows(
            12, "Java开发工程师",
            {"skill_name": "Rust", "necessity": "must", "level": ""},
        )
        fake = _FakeSession()
        write_aggregates(fake, build_aggregates(rows), now="2026-08-09T00:00:00")
        params = self._delete_params(fake)
        kept = {item["pos"]: item["kept"] for item in params["kept_by_pos"]}
        assert kept == {"Java开发工程师": ["Java"]}  # Rust 不在 kept，将被删除
        assert params["excluded"] == []

    def test_manually_edited_position_excluded_from_deletion(self):
        # 人工编辑岗位（有 PositionEditLog）跳过对齐删除，防聚合打回人工调整
        rows = self._rows(
            12, "Java开发工程师",
            {"skill_name": "Rust", "necessity": "must", "level": ""},
        )
        fake = _FakeSession()
        fake.edited_positions = ["Java开发工程师"]
        write_aggregates(fake, build_aggregates(rows), now="2026-08-09T00:00:00")
        params = self._delete_params(fake)
        assert params["excluded"] == ["Java开发工程师"]

    def test_multi_position_kept_groups(self):
        # 多岗位时 kept_by_pos 按岗位分组；跨岗位技能互不干扰
        rows = self._rows(3, "Python开发工程师")
        fake = _FakeSession()
        write_aggregates(fake, build_aggregates(rows), now="2026-08-09T00:00:00")
        params = self._delete_params(fake)
        assert params["kept_by_pos"] == [
            {"pos": "Python开发工程师", "kept": ["Java"]}
        ]

    def test_result_includes_removed_count(self):
        rows = self._rows(12, "Java开发工程师")
        fake = _FakeSession()
        result = write_aggregates(fake, build_aggregates(rows), now="2026-08-09T00:00:00")
        assert result["removed_edges"] == 0
