#!/usr/bin/env python3
"""Deterministic claims-fixture generator for the claims-duplicate-audit task.

Two claim records are LINKED iff ALL of the following hold:
  * normalized patient_name equal (lowercase; punctuation removed; whitespace collapsed)
  * patient_dob equal after parsing (formats: YYYY-MM-DD, MM/DD/YYYY, DD.MM.YYYY)
  * service_code exactly equal
  * service_date within 7 days (inclusive)
  * amount within 1% relative difference (inclusive)
Clusters are the transitive closure of linked pairs.

The same script generates:
  * the agent-environment fixture (--seed 42, no --gt)
  * the held-out verifier fixture (--seed 1337, --gt with adversarial decoys)
Ground truth is NEVER written into the agent environment.
"""
import argparse
import csv
import json
import random
from collections import defaultdict
from datetime import date, timedelta

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y")

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
    "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
    "Thomas", "Sarah", "Charles", "Karen", "Christopher", "Nancy", "Daniel", "Lisa",
    "Matthew", "Betty", "Anthony", "Margaret", "Mark", "Sandra", "Donald", "Ashley",
    "Steven", "Kimberly", "Paul", "Emily", "Andrew", "Donna", "Joshua", "Michelle",
    "Kenneth", "Dorothy", "Kevin", "Carol", "Brian", "Amanda", "George", "Melissa",
    "Edward", "Deborah", "Ronald", "Stephanie", "Timothy", "Rebecca", "Jason", "Sharon",
    "Jeffrey", "Laura", "Ryan", "Cynthia", "Jacob", "Kathleen", "Gary", "Amy",
    "Nicholas", "Shirley", "Eric", "Angela", "Jonathan", "Helen", "Stephen", "Anna",
    "Larry", "Brenda", "Justin", "Pamela", "Scott", "Nicole", "Brandon", "Samantha",
    "Benjamin", "Katherine", "Samuel", "Emma", "Gregory", "Ruth", "Frank", "Christine",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
    "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
    "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
    "Carter", "Roberts", "Gomez", "Phillips", "Evans", "Turner", "Diaz", "Parker",
    "Cruz", "Edwards", "Collins", "Reyes", "Stewart", "Morris", "Morales", "Murphy",
    "Cook", "Rogers", "Gutierrez", "Ortiz", "Morgan", "Cooper", "Peterson", "Bailey",
    "Reed", "Kelly", "Howard", "Ramos", "Kim", "Cox", "Ward", "Richardson", "Watson",
    "Brooks", "Chavez", "Wood", "James", "Bennett", "Gray", "Mendoza", "Ruiz",
]

SERVICE_CODES = (
    [str(99200 + i) for i in range(15)]
    + [str(80040 + i) for i in range(35)]
    + [str(93000 + i) for i in range(10)]
    + [str(71010 + i) for i in range(20)]
    + [str(36415 + i) for i in range(15)]
    + [str(85025 + i) for i in range(20)]
)

DOB_START = date(1940, 1, 1)
DOB_END = date(2010, 12, 31)
SVC_START = date(2018, 1, 1)
SVC_END = date(2026, 8, 1)


def fmt_date(d, fmt_idx):
    return d.strftime(DATE_FORMATS[fmt_idx])


def norm_name(s):
    """Reference normalization: lowercase, strip punctuation, collapse spaces."""
    out = []
    for ch in s.lower():
        out.append(ch if ch.isalnum() else " ")
    return " ".join("".join(out).split())


class Generator:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.rows = []
        self.clusters = {}          # cluster_id -> [claim_ids]
        self.cluster_rows = {}      # cluster_id -> [row dicts]
        self.used_names = set()     # normalized names already assigned to patients
        self.claim_seq = 0
        self.cluster_seq = 0
        self.link_index = defaultdict(list)  # (norm_name, code) -> rows

    # -- identity -----------------------------------------------------------
    def new_patient_name(self):
        for _ in range(100000):
            first = self.rng.choice(FIRST_NAMES)
            last = self.rng.choice(LAST_NAMES)
            if self.rng.random() < 0.5:
                last = last + "-" + self.rng.choice(LAST_NAMES)
            middle = self.rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            base = f"{last} {first} {middle}"
            n = norm_name(base)
            if n not in self.used_names:
                self.used_names.add(n)
                return base, n
        raise RuntimeError("name space exhausted")

    def name_variant(self, base):
        """Return a string whose normalized form equals norm_name(base).

        Only case and BETWEEN-word punctuation/separators are varied; the word
        sequence itself is preserved. Punctuation is replaced by a space
        during normalization, so nothing may be inserted inside a word.
        """
        words = base.split()
        parts = []
        for w in words:
            r = self.rng.random()
            if r < 0.3:
                w = w.lower()
            elif r < 0.3:
                w = w.lower().capitalize()
            elif r < 0.5:
                w = w.upper()
            parts.append(w)
        sep = self.rng.choice([" ", "  ", ", ", ". ", "-", " - "])
        s = sep.join(parts)
        if self.rng.random() < 0.2:
            s += "."
        assert norm_name(s) == norm_name(base), f"variant broke normalization: {s!r}"
        return s

    def typo_name(self, base):
        """One-character mutation that changes the normalized form."""
        for _ in range(200):
            chars = list(base)
            i = self.rng.randrange(len(chars))
            if not chars[i].isalpha():
                continue
            old = chars[i]
            pool = "abcdefghijklmnopqrstuvwxyz"
            chars[i] = self.rng.choice(pool.replace(old.lower(), ""))
            n = norm_name("".join(chars))
            if n != norm_name(base) and n not in self.used_names:
                return "".join(chars)
        raise RuntimeError("typo generation failed")

    def random_dob(self):
        return DOB_START + timedelta(days=self.rng.randrange((DOB_END - DOB_START).days))

    def random_svc_date(self):
        return SVC_START + timedelta(days=self.rng.randrange((SVC_END - SVC_START).days))

    # -- records -------------------------------------------------------------
    def add_record(self, name_variant, dob, dob_fmt, code, svc_date, amount, provider):
        self.claim_seq += 1
        rec = {
            "claim_id": f"C{self.claim_seq:07d}",
            "patient_name": name_variant,
            "patient_dob": fmt_date(dob, dob_fmt),
            "service_code": code,
            "service_date": fmt_date(svc_date, self.rng.randrange(3)),
            "amount": f"{amount:.2f}",
            "provider_id": f"P{provider:03d}",
            # internal (never written to CSV): parsed values for decoy math
            "_d": svc_date,
            "_dob": dob,
        }
        return self.append_record(rec)

    def append_record(self, rec):
        self.rows.append(rec)
        self.link_index[(norm_name(rec["patient_name"]), rec["service_code"])].append(rec)
        return rec

    # -- event types ---------------------------------------------------------
    def gen_singleton(self):
        base, _ = self.new_patient_name()
        n = self.rng.randint(1, 3)
        dob = self.random_dob()
        codes = self.rng.sample(SERVICE_CODES, n)
        for c in codes:
            self.add_record(
                self.name_variant(base), dob, self.rng.randrange(3), c,
                self.random_svc_date(),
                round(self.rng.uniform(20, 5000), 2), self.rng.randrange(1, 100),
            )

    def gen_duplicate_cluster(self):
        base, _ = self.new_patient_name()
        k = self.rng.randint(2, 5)
        dob = self.random_dob()
        code = self.rng.choice(SERVICE_CODES)
        d0 = self.random_svc_date()
        amt0 = round(self.rng.uniform(20, 5000), 2)
        ids = []
        # Tight window: any TWO members stay within tolerance directly
        # (dates: max spread 4 <= 7; amounts: max spread 0.6% <= 1%).
        for _ in range(k):
            d = d0 + timedelta(days=self.rng.randint(-2, 2))
            amt = amt0 * (1 + self.rng.uniform(-0.003, 0.003))
            rec = self.add_record(
                self.name_variant(base), dob, self.rng.randrange(3), code,
                d, round(amt, 2), self.rng.randrange(1, 100),
            )
            ids.append(rec["claim_id"])
        self.clusters[self.cluster_seq] = ids
        self.cluster_rows[self.cluster_seq] = [r for r in self.rows[-k:]]
        self.cluster_seq += 1

    def gen_chain(self):
        """Consecutive records match, endpoints do not (forces transitive closure)."""
        base, _ = self.new_patient_name()
        k = self.rng.randint(3, 6)
        dob = self.random_dob()
        code = self.rng.choice(SERVICE_CODES)
        d = self.random_svc_date()
        amt = round(self.rng.uniform(20, 3000), 2)
        ids = []
        for _ in range(k):
            rec = self.add_record(
                self.name_variant(base), dob, self.rng.randrange(3), code,
                d, round(amt, 2), self.rng.randrange(1, 100),
            )
            ids.append(rec["claim_id"])
            d = d + timedelta(days=4)      # adjacent: +4 <= 7; endpoints: >= 8
            amt = amt * 1.006              # adjacent: 0.6% <= 1%; endpoints: >= 1.2%
        self.clusters[self.cluster_seq] = ids
        self.cluster_rows[self.cluster_seq] = [r for r in self.rows[-k:]]
        self.cluster_seq += 1

    def would_link(self, rec):
        """True if rec would be linked to any existing row under the rules."""
        n = norm_name(rec["patient_name"])
        dob = rec["_dob"]
        code = rec["service_code"]
        d = rec["_d"]
        amt = float(rec["amount"])
        for r in self.link_index.get((n, code), ()):
            if r["_dob"] != dob:
                continue
            if abs((r["_d"] - d).days) > 7:
                continue
            if abs(float(r["amount"]) - amt) > 0.01 * max(abs(float(r["amount"])), abs(amt)):
                continue
            return True
        return False

    def build_decoy(self):
        """Randomized near-miss construction (see gen_decoy for the contract)."""
        cluster_id = self.rng.choice(list(self.clusters.keys()))
        members = self.cluster_rows[cluster_id]
        typ = self.rng.randrange(5)
        if typ == 0:  # amount: +5% over the cluster's max -> >1% from every member
            anchor = max(members, key=lambda r: float(r["amount"]))
            rec = dict(anchor)
            rec["amount"] = f"{float(anchor['amount']) * 1.05:.2f}"
        elif typ == 1:  # service_date: +8 days over the cluster's latest date
            anchor = max(members, key=lambda r: r["_d"])
            rec = dict(anchor)
            d = anchor["_d"] + timedelta(days=8)
            rec["service_date"] = fmt_date(d, self.rng.randrange(3))
            rec["_d"] = d
        elif typ == 2:  # name typo
            anchor = self.rng.choice(members)
            rec = dict(anchor)
            rec["patient_name"] = self.typo_name(anchor["patient_name"])
        elif typ == 3:  # dob off by one day (exact-match rule)
            anchor = self.rng.choice(members)
            rec = dict(anchor)
            d = anchor["_dob"] + timedelta(days=1)
            rec["patient_dob"] = fmt_date(d, self.rng.randrange(3))
            rec["_dob"] = d
        else:  # different service_code
            anchor = self.rng.choice(members)
            rec = dict(anchor)
            rec["service_code"] = self.rng.choice(
                [c for c in SERVICE_CODES if c != anchor["service_code"]]
            )
        return rec

    def gen_decoy(self):
        """Near-miss: violates EXACTLY one rule by a margin; never links.

        The anchor member is chosen so the violation holds relative to EVERY
        cluster member (date decoys extend the cluster's latest date by 8
        days, amount decoys inflate the cluster's max amount by 5%). A
        link-check against all existing rows rejects collisions between
        decoys themselves (e.g. two code-swap decoys of the same anchor
        picking the same code); after 30 attempts we fall back to an amount
        far from every other row, which cannot link.
        """
        for _ in range(30):
            rec = self.build_decoy()
            if not self.would_link(rec):
                self.claim_seq += 1
                rec["claim_id"] = f"C{self.claim_seq:07d}"
                self.append_record(rec)
                return
        # fallback: guaranteed unlinkable amount
        rec = self.build_decoy()
        rec["amount"] = f"{float(rec['amount']) * (2 + self.rng.random()):.2f}"
        self.claim_seq += 1
        rec["claim_id"] = f"C{self.claim_seq:07d}"
        self.append_record(rec)

    # -- driver --------------------------------------------------------------
    def generate(self, target_rows, decoy_multiplier):
        weights = [("single", 0.55), ("dup", 0.30), ("chain", 0.15)]
        types = [t for t, _ in weights]
        ws = [w for _, w in weights]
        while len(self.rows) < target_rows:
            t = self.rng.choices(types, weights=ws)[0]
            {"single": self.gen_singleton, "dup": self.gen_duplicate_cluster,
             "chain": self.gen_chain}[t]()
        for _ in range(int(len(self.clusters) * decoy_multiplier)):
            self.gen_decoy()

    def write(self, out_dir, n_files, gt_path=None):
        import os
        os.makedirs(out_dir, exist_ok=True)
        per_file = [[] for _ in range(n_files)]
        for r in self.rows:
            per_file[self.rng.randrange(n_files)].append(r)
        fieldnames = ["claim_id", "patient_name", "patient_dob", "service_code",
                      "service_date", "amount", "provider_id"]
        for i, rows in enumerate(per_file):
            path = os.path.join(out_dir, f"claims_{i + 1}.csv")
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(
                    {k: v for k, v in r.items() if k in fieldnames} for r in rows
                )
        if gt_path:
            clusters = [sorted(ids) for ids in self.clusters.values() if len(ids) >= 2]
            clusters.sort(key=lambda c: c[0])
            with open(gt_path, "w") as f:
                json.dump(
                    {"clusters": clusters, "all_ids": [r["claim_id"] for r in self.rows]},
                    f, indent=1,
                )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--files", type=int, default=6)
    ap.add_argument("--decoy-multiplier", type=float, default=1.5)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gt", default=None)
    args = ap.parse_args()
    g = Generator(args.seed)
    g.generate(args.rows, args.decoy_multiplier)
    g.write(args.out, args.files, args.gt)
    print(f"generated {len(g.rows)} rows, {len(g.clusters)} clusters "
          f"(members >=2: {sum(1 for c in g.clusters.values() if len(c) >= 2)})")


if __name__ == "__main__":
    main()
