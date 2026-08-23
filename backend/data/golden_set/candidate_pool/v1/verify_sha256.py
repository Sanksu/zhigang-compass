#!/usr/bin/env python3
"""Verify and fix SHA-256 hashes in batch_frontend_test_ops.jsonl."""
import json
import hashlib
import os

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

jsonl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "batch_frontend_test_ops.jsonl")

with open(jsonl_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

records = [json.loads(line) for line in lines if line.strip()]

fixed = 0
for rec in records:
    expected = sha256(rec["detail_raw_text"])
    if rec["_sha256"] != expected:
        print(f"Fixing SHA-256 for {rec['source_id']}: {rec['_sha256']} -> {expected}")
        rec["_sha256"] = expected
        fixed += 1

if fixed > 0:
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Fixed {fixed} SHA-256 hashes.")
else:
    print("All SHA-256 hashes are correct.")

# Summary
cats = {"前端开发": 0, "测试": 0, "运维/DevOps": 0}
for i, rec in enumerate(records):
    if i < 9:
        cats["前端开发"] += 1
    elif i < 19:
        cats["测试"] += 1
    else:
        cats["运维/DevOps"] += 1

print(f"Total records: {len(records)}")
print(f"Category distribution: {cats}")
print(f"Output: {jsonl_path}")