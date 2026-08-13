#!/usr/bin/env python3
"""Oracle solution: exact blocking on (service_code, normalized name) + union-find.

A linked pair must share service_code AND normalized patient_name, so grouping
by (code, name) is an exact blocking — no candidate pairs are lost — and keeps
the pair count linear in the number of rows.
"""
import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y")
TOLERANCE = 0.01


def parse_date(s):
    s = s.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {s!r}")


def norm_name(name):
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z]", " ", name.lower())).strip()


def amounts_close(a, b):
    return abs(a - b) <= TOLERANCE * max(abs(a), abs(b))


def linked(x, y):
    return (
        x["n"] == y["n"]
        and x["dob"] == y["dob"]
        and x["code"] == y["code"]
        and abs((x["d"] - y["d"]).days) <= 7
        and amounts_close(x["amt"], y["amt"])
    )


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("claims_dir")
    ap.add_argument("--rules", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = []
    for path in sorted(Path(args.claims_dir).glob("*.csv")):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                rows.append({
                    "id": r["claim_id"].strip(),
                    "n": norm_name(r["patient_name"]),
                    "dob": parse_date(r["patient_dob"]),
                    "code": r["service_code"].strip(),
                    "d": parse_date(r["service_date"]),
                    "amt": float(r["amount"]),
                })

    # Exact blocking: linked pairs must share (service_code, normalized name).
    blocks = defaultdict(list)
    for r in rows:
        blocks[(r["code"], r["n"])].append(r)

    uf = UF(len(rows))
    idx = {r["id"]: i for i, r in enumerate(rows)}
    for cands in blocks.values():
        for i in range(len(cands)):
            for j in range(i + 1, len(cands)):
                if linked(cands[i], cands[j]):
                    uf.union(idx[cands[i]["id"]], idx[cands[j]["id"]])

    clusters = defaultdict(list)
    for r in rows:
        clusters[uf.find(idx[r["id"]])].append(r["id"])

    out = [sorted(v) for v in clusters.values() if len(v) >= 2]
    out.sort(key=lambda c: c[0])
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
