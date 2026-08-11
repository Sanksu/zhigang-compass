-- ============================================================
-- glassdoor 存量 post_date 相对时间归一化回补
--
-- 背景：glassdoor 采集的 post_date 是相对时间（"3d"/"30d+"/"24h"，
-- 相对采集时刻的天数/小时数），下游 SQL（::timestamp 解析、时滞检测）
-- 无法解析会报错。清洗层 normalize_post_date 只对「新采集」生效，
-- 存量记录需一次性回补。
--
-- 基准：相对时间是采集时刻记录下的年龄，故以 crawled_at（采集时间）
-- 为基准回推，比用执行日更准确；crawled_at 不可解析时回退 now()。
-- 日期按东八区（Asia/Shanghai）计算——容器会话时区为 UTC，直接 ::date
-- 会让凌晨采集的记录早一天。
--
-- 幂等：UPDATE 仅匹配相对时间格式（^N[dwh]+?$），回补后 post_date
-- 变为绝对日期不再匹配，可安全重跑。
-- 回滚：先执行步骤 2 备份受影响记录的 id + 原始 post_date。
--
-- 执行（Windows）：
--   Get-Content scripts\backfill_glassdoor_post_date.sql -Raw |
--     docker exec -i zhigang-postgres psql -U zhigang -d zhigang
-- ============================================================

-- ── 1. 预览：受影响记录分布 ─────────────────────────────
\echo '=== 1. 预览：glassdoor 相对时间 post_date 分布 ==='
SELECT snapshot->>'post_date' AS raw_pd, count(*)
FROM jd_raw
WHERE source = 'glassdoor'
  AND snapshot->>'post_date' ~* '^[0-9]+[dwh]\+?$'
GROUP BY 1 ORDER BY 2 DESC;

-- ── 2. 备份原始值（回滚用，输出到 stdout 重定向到文件）───
-- 注：\copy 元命令不支持跨行查询，必须单行书写
\echo '=== 2. 备份原始值（id, post_date）到 stdout ==='
\copy (SELECT id, source_id, snapshot->>'post_date' AS post_date FROM jd_raw WHERE source = 'glassdoor' AND snapshot->>'post_date' ~* '^[0-9]+[dwh]\+?$' ORDER BY id) TO STDOUT WITH CSV HEADER

-- ── 3. 回补：相对时间 → 绝对日期（基于 crawled_at）───────
\echo '=== 3. 执行回补 ==='
WITH matched AS (
    SELECT id,
           lower(snapshot->>'post_date') AS pd,
           CASE WHEN crawled_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                THEN crawled_at::timestamptz ELSE now() END AS base_ts
    FROM jd_raw
    WHERE source = 'glassdoor'
      AND snapshot->>'post_date' ~* '^[0-9]+[dwh]\+?$'
), calc AS (
    SELECT id,
           ((base_ts AT TIME ZONE 'Asia/Shanghai') - CASE substring(pd FROM '[a-z]')
                        WHEN 'w' THEN make_interval(days => substring(pd FROM '[0-9]+')::int * 7)
                        WHEN 'h' THEN make_interval(hours => substring(pd FROM '[0-9]+')::int)
                        ELSE make_interval(days => substring(pd FROM '[0-9]+')::int)
                      END)::date AS new_date
    FROM matched
)
UPDATE jd_raw r
SET snapshot = jsonb_set(r.snapshot, '{post_date}', to_jsonb(c.new_date::text))
FROM calc c
WHERE r.id = c.id;

-- ── 4. 校验：不应再有相对时间残留 ────────────────────────
\echo '=== 4. 校验：剩余相对时间残留（应为 0 行） ==='
SELECT snapshot->>'post_date' AS raw_pd, count(*)
FROM jd_raw
WHERE source = 'glassdoor'
  AND snapshot->>'post_date' ~* '^[0-9]+[dwh]\+?$'
GROUP BY 1 ORDER BY 2 DESC;

\echo '=== 校验：回补后 post_date 样例 ==='
SELECT source_id, snapshot->>'post_date' AS post_date
FROM jd_raw
WHERE source = 'glassdoor'
ORDER BY id DESC LIMIT 5;
