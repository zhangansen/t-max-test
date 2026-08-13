#!/usr/bin/env python3
"""Naive O(n^2) baseline for difficulty evidence (NOT part of the task)."""
import csv, json, sys, time
from datetime import datetime
from pathlib import Path

FMTS = ("%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y")

def pd(s):
    for f in FMTS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue

def norm(s):
    return " ".join("".join(c if c.isalnum() else " " for c in s.lower()).split())

t0 = time.time()
rows = []
for p in sorted(Path(sys.argv[1]).glob("*.csv")):
    for r in csv.DictReader(open(p)):
        rows.append((r["claim_id"], norm(r["patient_name"]), pd(r["patient_dob"]),
                     r["service_code"], pd(r["service_date"]), float(r["amount"])))
n = len(rows)
linked = 0
for i in range(n):
    for j in range(i + 1, n):
        a, b = rows[i], rows[j]
        if a[1] == b[1] and a[2] == b[2] and a[3] == b[3] \
           and abs((a[4] - b[4]).days) <= 7 \
           and abs(a[5] - b[5]) <= 0.01 * max(abs(a[5]), abs(b[5])):
            linked += 1
print(f"rows={n} naive_links={linked} time={time.time()-t0:.1f}s")
