// 智岗罗盘 — Neo4j 图谱 Schema
//
// 节点标签：Position / Skill / Tool / Education / Certification / Course / Evidence / Occupation / PositionEditLog
// 关系类型：REQUIRES / LEARNABLE_VIA / EVIDENCED_BY / HAS_EVIDENCE / BELONGS_TO_OCCUPATION /
//           PREREQUISITE_OF / SIMILAR_TO / BELONGS_TO / ALTERNATIVE_OF / EVOLVED_FROM
//
// ID 格式：{prefix}_{seq:04d}（由 Counter 节点原子生成，见 id_generator.py）
//
// 初始化：uv run python scripts/init_neo4j.py

// ============================================================
// 1. 唯一约束（节点 ID 唯一；Position/Skill 各含 id+name 两个约束）
// ============================================================
// name 唯一约束：kg_service 按 name 合并节点（MATCH+CREATE 语义），
// 并发/重复导入时防同名校对节点的产生（设计文档 5.1：8 类实体共 10 个 UNIQUE；
// PositionEditLog 为编辑日志节点，id 唯一即可）

CREATE CONSTRAINT IF NOT EXISTS FOR (n:Position) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Position) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Skill) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Skill) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Tool) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Education) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Certification) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Course) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Evidence) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Occupation) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:PositionEditLog) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Community) REQUIRE n.id IS UNIQUE;

// ============================================================
// 2. 属性索引（常用查询字段）
// ============================================================

CREATE INDEX IF NOT EXISTS FOR (n:Position) ON (n.name);
CREATE INDEX IF NOT EXISTS FOR (n:Position) ON (n.status);
CREATE INDEX IF NOT EXISTS FOR (n:Position) ON (n.level);
CREATE INDEX IF NOT EXISTS FOR (n:Skill) ON (n.name);
CREATE INDEX IF NOT EXISTS FOR (n:Skill) ON (n.category);
CREATE INDEX IF NOT EXISTS FOR (n:Skill) ON (n.normalized_name);
CREATE INDEX IF NOT EXISTS FOR (n:Course) ON (n.name);
CREATE INDEX IF NOT EXISTS FOR (n:Course) ON (n.platform);
CREATE INDEX IF NOT EXISTS FOR (n:Course) ON (n.source, n.source_id);
CREATE INDEX IF NOT EXISTS FOR (n:Evidence) ON (n.source);
CREATE INDEX IF NOT EXISTS FOR (n:Evidence) ON (n.crawled_at);
CREATE INDEX IF NOT EXISTS FOR (n:Occupation) ON (n.category);

// ============================================================
// 3. 全文索引（图谱搜索，支持中英文混合）
// ============================================================
// 用 lucene 的 cjk 分析器做中/日/韩文分词，覆盖中文技能与岗位名搜索
// 查询：CALL db.index.fulltext.queryNodes('position_search', 'Python') YIELD node, score

CREATE FULLTEXT INDEX position_search IF NOT EXISTS
FOR (n:Position) ON EACH [n.name, n.description]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } };

CREATE FULLTEXT INDEX skill_search IF NOT EXISTS
FOR (n:Skill) ON EACH [n.name, n.description]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } };

CREATE FULLTEXT INDEX course_search IF NOT EXISTS
FOR (n:Course) ON EACH [n.name, n.description, n.category]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } };

CREATE FULLTEXT INDEX occupation_search IF NOT EXISTS
FOR (n:Occupation) ON EACH [n.name, n.category, n.definition, n.aliases]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } };

CREATE FULLTEXT INDEX evidence_search IF NOT EXISTS
FOR (n:Evidence) ON EACH [n.source, n.raw_text]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } };

// ============================================================
// 4. Counter 节点初始化（ID 生成器依赖）
// ============================================================
// 每种实体类型一个 Counter 节点，next_id() 通过原子 MERGE 自增

MERGE (c:Counter {type: 'Position'}) SET c.value = coalesce(c.value, 0);
MERGE (c:Counter {type: 'Skill'}) SET c.value = coalesce(c.value, 0);
MERGE (c:Counter {type: 'Evidence'}) SET c.value = coalesce(c.value, 0);
MERGE (c:Counter {type: 'Course'}) SET c.value = coalesce(c.value, 0);
MERGE (c:Counter {type: 'Occupation'}) SET c.value = coalesce(c.value, 0);
MERGE (c:Counter {type: 'Certification'}) SET c.value = coalesce(c.value, 0);
MERGE (c:Counter {type: 'Education'}) SET c.value = coalesce(c.value, 0);
MERGE (c:Counter {type: 'Tool'}) SET c.value = coalesce(c.value, 0);
MERGE (c:Counter {type: 'PositionEditLog'}) SET c.value = coalesce(c.value, 0);

// ============================================================
// 5. 关系类型说明（Cypher 不需要预声明关系类型，此处仅作文档）
// ============================================================
// (Position)-[:REQUIRES {necessity: 'must'|'nice', level: '初级'|...}]->(Skill|Tool|Education|Certification)
// (Skill)-[:LEARNABLE_VIA]->(Course)
// (Position)-[:HAS_EVIDENCE]->(Evidence)
// (Skill)-[:MENTIONED_IN]->(Evidence)
// (Position)-[:BELONGS_TO_OCCUPATION]->(Occupation)  // 岗位归属国家职业分类（设计文档 5.1）
