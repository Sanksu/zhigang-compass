"""简历证书 → 颁发机构（issuer）静态映射表。

来源：
- 图谱 Certification 节点（213 个）中可识别的主流认证，对照各机构官方名称整理
- 海外认证为主（AWS/微软/Cisco/(ISC)² 等）；国内认证（软考/华为/信安测评中心等）手工维护
- 泛化名称（"云认证""AI 认证"等）不建条目——词表无法确定 issuer 的保持留空，
  不错误补全
- 仅收录 issuer 无歧义的认证；"security"“sql”等过宽词不建条目，避免误配

匹配规则：`issuer_for` 归一化后先精确匹配，再前缀匹配（如 "AWS Certified Solutions
Architect" 以 "aws" 前缀命中）。多个 key 命中时取最长者，避免短前缀误吞
（"azure" 优先于 "az"）。key 一律小写归一化形式。
"""

CERT_ISSUER_MAP: dict[str, str] = {
    # ---------- 云厂商 ----------
    "aws": "Amazon Web Services (AWS)",
    "amazon web services": "Amazon Web Services (AWS)",
    "azure": "Microsoft",
    "az": "Microsoft",
    "microsoft": "Microsoft",
    "microsoft azure": "Microsoft",
    "gcp": "Google Cloud",
    "google cloud": "Google Cloud",
    "google certified": "Google Cloud",
    "associate google workspace": "Google Cloud",
    "oracle": "Oracle",
    "oracle cloud": "Oracle Cloud Infrastructure",
    "oci": "Oracle Cloud Infrastructure",
    "oracle database": "Oracle",
    "oracle java": "Oracle",
    "huawei": "华为",
    "华为": "华为",
    "aliyun": "阿里云",
    "alibaba cloud": "阿里云",
    "阿里云": "阿里云",
    # ---------- 网络/基础设施 ----------
    "ccna": "Cisco",
    "ccnp": "Cisco",
    "ccie": "Cisco",
    "ccda": "Cisco",
    "ccde": "Cisco",
    "cisco": "Cisco",
    "jncna": "Juniper Networks",
    "jncia": "Juniper Networks",
    "jncip": "Juniper Networks",
    "jncie": "Juniper Networks",
    "cwna": "CWNP",
    "cwnp": "CWNP",
    "mikrotik": "MikroTik",
    # ---------- 安全 ----------
    "cissp": "(ISC)²",
    "ccsp": "(ISC)²",
    "sscp": "(ISC)²",
    "csslp": "(ISC)²",
    "issap": "(ISC)²",
    "cisa": "ISACA",
    "cism": "ISACA",
    "crisc": "ISACA",
    "cgeit": "ISACA",
    "ccak": "ISACA",
    "crtm": "ISACA",
    "crma": "The Institute of Internal Auditors (IIA)",
    "cia": "The Institute of Internal Auditors (IIA)",
    "ceh": "EC-Council",
    "chfi": "EC-Council",
    "oscp": "OffSec",
    "osce": "OffSec",
    "osed": "OffSec",
    "osep": "OffSec",
    "oswp": "OffSec",
    "comptia a": "CompTIA",
    "comptia network": "CompTIA",
    "comptia security": "CompTIA",
    "comptia cya": "CompTIA",
    "comptia": "CompTIA",
    "security ce": "CompTIA",
    "cysa": "CompTIA",
    "casp": "CompTIA",
    "pcne": "Palo Alto Networks",
    "palo alto": "Palo Alto Networks",
    "ccsk": "云安全联盟 (CSA)",
    "gsec": "SANS Institute",
    "gsoc": "SANS Institute",
    "gcih": "SANS Institute",
    "gcfa": "SANS Institute",
    "gcia": "SANS Institute",
    "giac": "SANS Institute",
    "sans": "SANS Institute",
    "sans giac": "SANS Institute",
    "cpts": "Hack The Box",
    "htb": "Hack The Box",
    "crto": "Zero Point Security",
    "nist": "美国国家标准与技术研究院 (NIST)",
    "iso 27001": "ISO (国际标准化组织)",
    "iso 42001": "ISO (国际标准化组织)",
    "iso27001": "ISO (国际标准化组织)",
    "soc 2": "美国注册会计师协会 (AICPA)",
    "等保": "公安部（等级保护）",
    "信息安全等级保护": "公安部（等级保护）",
    "cisp": "中国信息安全测评中心",
    "nisp": "中国信息安全测评中心",
    # ---------- 项目管理/流程 ----------
    "pmp": "Project Management Institute (PMI)",
    "pmi acp": "Project Management Institute (PMI)",
    "pmi": "Project Management Institute (PMI)",
    "capm": "Project Management Institute (PMI)",
    "prince2": "PeopleCert (AXELOS)",
    "itil": "PeopleCert (AXELOS)",
    "itil foundation": "PeopleCert (AXELOS)",
    "togaf": "The Open Group",
    "scrum master": "Scrum Alliance",
    "scrum developer": "Scrum Alliance",
    "scrum product owner": "Scrum Alliance",
    "csm": "Scrum Alliance",
    "csd": "Scrum Alliance",
    "csp": "Scrum Alliance",
    "safe agilist": "Scaled Agile",
    "safe practitioner": "Scaled Agile",
    "safe": "Scaled Agile",
    # ---------- 云原生/工程 ----------
    "cka": "CNCF (云原生计算基金会)",
    "ckad": "CNCF (云原生计算基金会)",
    "cks": "CNCF (云原生计算基金会)",
    "kubernetes": "CNCF (云原生计算基金会)",
    "terraform": "HashiCorp",
    "hashicorp": "HashiCorp",
    "docker": "Docker",
    "openshift": "Red Hat",
    "rhcsa": "Red Hat",
    "rhce": "Red Hat",
    "red hat": "Red Hat",
    "lpic": "Linux Professional Institute (LPI)",
    "lcfs": "Linux Foundation",
    "linux foundation": "Linux Foundation",
    "mongodb": "MongoDB",
    "elasticsearch": "Elastic",
    "elastic": "Elastic",
    "splunk": "Splunk",
    "servicenow": "ServiceNow",
    "salesforce": "Salesforce",
    "databricks": "Databricks",
    "snowflake": "Snowflake",
    "snowpro": "Snowflake",
    "confluent": "Confluent",
    "k2view": "K2view",
    # ---------- 应用/行业软件 ----------
    "adobe": "Adobe",
    "aem": "Adobe",
    "epic clarity": "Epic Systems",
    "epic cogito": "Epic Systems",
    "epic": "Epic Systems",
    "sap": "SAP",
    "mcsa": "Microsoft",
    "mcse": "Microsoft",
    "mcsd": "Microsoft",
    "tricentis": "Tricentis",
    "tosca": "Tricentis",
    "siemens": "Siemens",
    "ptc": "PTC",
    "帆软": "帆软软件有限公司",
    "简道云": "帆软软件有限公司",
    "金蝶": "金蝶软件（中国）有限公司",
    # ---------- 编程语言/数据库 ----------
    "pcep": "Python Institute",
    "pcdp": "Python Institute",
    "pcpp": "Python Institute",
    "postgresql": "PostgreSQL 社区",
    "kafka": "Apache Software Foundation",
    # ---------- 金融/财务 ----------
    "cfa": "CFA Institute",
    "cfp": "CFP Board",
    "acca": "ACCA（特许公认会计师公会）",
    "frm": "GARP（全球风险专业人士协会）",
    "精算": "北美精算师协会 (SOA)",
    "actuarial": "北美精算师协会 (SOA)",
    "基金从业": "中国证券投资基金业协会",
    "证券从业": "中国证券业协会",
    "series 7": "FINRA",
    "series 63": "FINRA",
    "series 57": "FINRA",
    "series 66": "FINRA",
    "ctp": "Association for Financial Professionals (AFP)",
    # ---------- 语言/考试 ----------
    "cet": "全国大学英语四六级考试委员会",
    "大学英语四": "全国大学英语四六级考试委员会",
    "英语四": "全国大学英语四六级考试委员会",
    "toefl": "ETS（美国教育考试服务中心）",
    "ielts": "British Council / IDP",
    "jlpt": "JLPT（日本语能力测试）",
    "日语": "JLPT（日本语能力测试）",
    "教师资格": "教育部",
    # ---------- 软考/职称 ----------
    "软考": "中国计算机技术职业资格（人社部/工信部）",
    "系统分析师": "中国计算机技术职业资格（人社部/工信部）",
    "信息系统项目管理师": "中国计算机技术职业资格（人社部/工信部）",
    "软件设计师": "中国计算机技术职业资格（人社部/工信部）",
    "网络工程师": "中国计算机技术职业资格（人社部/工信部）",
    "数据库系统工程师": "中国计算机技术职业资格（人社部/工信部）",
    "信息安全工程师": "中国计算机技术职业资格（人社部/工信部）",
    "系统架构设计师": "中国计算机技术职业资格（人社部/工信部）",
    "软件评测师": "中国计算机技术职业资格（人社部/工信部）",
    "信息系统监理师": "中国计算机技术职业资格（人社部/工信部）",
    "建造师": "住建部（执业资格）",
    "一级机电": "住建部（一级建造师）",
    "注册会计师": "财政部（注册会计师）",
}


def _norm(name: str) -> str:
    """归一化：ASCII 小写、标点转空格、合并空白（保留中文）。"""
    import re

    s = name.lower()
    s = re.sub(r"[()\[\]{}:/,._\-–—]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# 颁发机构简写 → 全称（规范化 LLM 抽出的 issuer，如"软考"→全称）。
# 仅收录无歧义的简写；key 为归一化形式（与 _norm 输出一致）。
# 与 CERT_ISSUER_MAP 的 value 全称保持一致，避免同一机构两种写法。
ISSUER_CANONICAL_MAP: dict[str, str] = {
    # 云厂商
    "aws": "Amazon Web Services (AWS)",
    "amazon": "Amazon Web Services (AWS)",
    "azure": "Microsoft",
    "microsoft azure": "Microsoft",
    "gcp": "Google Cloud",
    "google": "Google Cloud",
    "oci": "Oracle Cloud Infrastructure",
    "oracle cloud": "Oracle Cloud Infrastructure",
    "aliyun": "阿里云",
    "阿里云": "阿里云",
    # 网络
    "cisco": "Cisco",
    "juniper": "Juniper Networks",
    "mikrotik": "MikroTik",
    # 安全
    "isc2": "(ISC)²",
    "isaca": "ISACA",
    "ec council": "EC-Council",
    "offsec": "OffSec",
    "comptia": "CompTIA",
    "palo alto": "Palo Alto Networks",
    "csa": "云安全联盟 (CSA)",
    "sans": "SANS Institute",
    "iso": "ISO (国际标准化组织)",
    # 项目管理/流程
    "pmi": "Project Management Institute (PMI)",
    "peoplecert": "PeopleCert (AXELOS)",
    "axelos": "PeopleCert (AXELOS)",
    "open group": "The Open Group",
    "scrum alliance": "Scrum Alliance",
    "scaled agile": "Scaled Agile",
    # 云原生/工程
    "cncf": "CNCF (云原生计算基金会)",
    "hashicorp": "HashiCorp",
    "red hat": "Red Hat",
    "linux foundation": "Linux Foundation",
    # 应用/行业
    "epic": "Epic Systems",
    "servicenow": "ServiceNow",
    "databricks": "Databricks",
    "snowflake": "Snowflake",
    "confluent": "Confluent",
    "帆软": "帆软软件有限公司",
    "金蝶": "金蝶软件（中国）有限公司",
    # 金融
    "aicpa": "美国注册会计师协会 (AICPA)",
    "finra": "FINRA",
    "cfa institute": "CFA Institute",
    "garp": "GARP（全球风险专业人士协会）",
    "soa": "北美精算师协会 (SOA)",
    # 语言
    "ets": "ETS（美国教育考试服务中心）",
    "jlpt": "JLPT（日本语能力测试）",
    # 国内认证/职称
    "软考": "中国计算机技术职业资格（人社部/工信部）",
    "人社部": "中国计算机技术职业资格（人社部/工信部）",
    "工信部": "中国计算机技术职业资格（人社部/工信部）",
    "信安测评中心": "中国信息安全测评中心",
    "公安部": "公安部（等级保护）",
}


def canonical_issuer(issuer: str) -> str:
    """将颁发机构简写规范化为全称；已是全称或未收录返回原值。

    仅精确匹配（归一化后），不做前缀/子串——避免把 LLM 抽出的含上下文
    的 issuer（如"PMI（中国分部）"）误规范化为通用全称。
    """
    n = _norm(issuer)
    if not n:
        return issuer
    return ISSUER_CANONICAL_MAP.get(n, issuer)


def issuer_for(name: str) -> str:
    """查证书名对应的颁发机构；未命中返回空串（不错误补全）。

    归一化后先精确匹配，再前缀匹配；多个 key 命中取最长，避免短前缀误吞
    （如 "azure" 优先于 "az"）。ASCII key 前缀要求空格边界（"csp" 不吞
    "cspm"）；中文 key 无分词空格，直接前缀匹配（"软考高级..." → 软考）。
    """
    n = _norm(name)
    if not n:
        return ""
    best = ""
    best_len = -1
    for key, issuer in CERT_ISSUER_MAP.items():
        if n == key:
            hit = True
        elif key.isascii():
            hit = n.startswith(key + " ")
        else:
            hit = n.startswith(key)
        if hit and len(key) > best_len:
            best = issuer
            best_len = len(key)
    return best
