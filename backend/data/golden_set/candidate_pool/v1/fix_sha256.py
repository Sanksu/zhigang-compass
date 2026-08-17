"""Fix _sha256 placeholder values in batch_embedded_security.json.

Run: python fix_sha256.py
"""
import json
import hashlib
import os

FILEPATH = os.path.join(os.path.dirname(__file__), "batch_embedded_security.json")

with open(FILEPATH, "r", encoding="utf-8") as f:
    data = json.load(f)

for r in data:
    r["_sha256"] = hashlib.sha256(r["detail_raw_text"].encode("utf-8")).hexdigest()

with open(FILEPATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Updated {len(data)} records with real SHA-256 hashes")

# Category breakdown
embedded = sum(1 for r in data if any(
    k in r["job_title_raw"] for k in ["嵌入式", "单片机", "驱动", "C++", "DSP", "ARM", "STM32", "Linux"]
))
security = sum(1 for r in data if any(
    k in r["job_title_raw"] for k in ["安全", "网络", "渗透", "等保"]
))
other = len(data) - embedded - security
print(f"Categories: 嵌入式/C++={embedded}, 网络/安全={security}, 其他技术岗={other}")