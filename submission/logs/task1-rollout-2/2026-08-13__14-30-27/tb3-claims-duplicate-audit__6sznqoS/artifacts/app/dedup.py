#!/usr/bin/env python3
"""Duplicate claims clustering tool.

Reads all *.csv claim files in a directory, applies the matching rules
defined in rules.yaml, and emits clusters of duplicate claims (transitive
closure of pairwise matches) as JSON.

Usage:
    python3 dedup.py /app/claims --rules /app/rules.yaml --out /app/duplicates.json

Matching rules (see rules.yaml):
    patient_name : normalized forms equal (lowercase, punctuation -> space,
                   whitespace collapse)
    patient_dob  : equal after parsing into ISO-8601 (tolerance_days: 0)
    service_code : exactly equal (case-sensitive)
    service_date : absolute difference <= tolerance_days (7)
    amount       : |a - b| / max(|a|, |b|) <= relative_tolerance (0.01)
    claim_id and provider_id are never used for matching.

Indexing strategy: records are blocked on (service_code, normalized name);
every matching pair must share that key. Within a block, records are
bucketed by (patient_dob, service_date) and amounts are compared with an
exact-integer two-pointer sweep over sorted cent values, so work is linear
in block size rather than quadratic.
"""

import argparse
import csv
import glob
import json
import os
import re
import string
import sys
from datetime import date
from fractions import Fraction

_TOKEN_PAT = re.compile(r"YYYY|MM|DD")

# Each punctuation character becomes a single space (rules.yaml: punctuation_to_space).
_PUNCT_TO_SPACE = str.maketrans(string.punctuation, " " * len(string.punctuation))


# ---------------------------------------------------------------------------
# rules.yaml (minimal YAML subset: nested mappings, scalar lists, scalars)
# ---------------------------------------------------------------------------

def _strip_inline_comment(text):
    # Remove a trailing "# ..." comment (comments start at '#' preceded by
    # whitespace or at the very start; '#' inside a quoted scalar survives).
    quoted = None
    for i, ch in enumerate(text):
        if ch in "'\"":
            quoted = ch if quoted is None else (None if quoted == ch else quoted)
        elif ch == "#" and quoted is None and (i == 0 or text[i - 1].isspace()):
            return text[:i].rstrip()
    return text


def _parse_scalar(text):
    text = _strip_inline_comment(text.strip())
    if text.startswith("[") and text.endswith("]"):
        items = [p.strip() for p in text[1:-1].split(",") if p.strip()]
        return [p.strip("'\"") for p in items]
    if text in ("true", "false"):
        return text == "true"
    if text in ("null", "~"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def load_rules(path):
    """Parse the rules.yaml subset into nested dicts/lists."""
    root = {}
    stack = [(-1, root)]  # (indent, node)
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            while stack and indent <= stack[-1][0]:
                stack.pop()
            _, parent = stack[-1]
            if stripped.endswith(":") and not stripped.startswith("-"):
                key = stripped[:-1].strip()
                node = {}
                if isinstance(parent, list):
                    parent.append(node)
                else:
                    parent[key] = node
                stack.append((indent, node))
            elif stripped.startswith("- "):
                item = _parse_scalar(stripped[2:])
                if isinstance(parent, list):
                    parent.append(item)
                else:
                    parent.setdefault("_items", []).append(item)
            elif ":" in stripped:
                key, value = stripped.split(":", 1)
                parent[key.strip()] = _parse_scalar(value)
    return root


# ---------------------------------------------------------------------------
# field parsing / normalization
# ---------------------------------------------------------------------------

def build_date_parsers(formats):
    """Compile the input date formats into (regex, token-order) pairs."""
    parsers = []
    for fmt in formats:
        parts = []
        tokens = []
        i = 0
        for m in _TOKEN_PAT.finditer(fmt):
            parts.append(re.escape(fmt[i:m.start()]))
            token = m.group()
            parts.append(r"(\d{4})" if token == "YYYY" else r"(\d{2})")
            tokens.append(token)
            i = m.end()
        parts.append(re.escape(fmt[i:]))
        parsers.append((re.compile("^" + "".join(parts) + "$"), tokens))
    return parsers


def parse_date(text, parsers):
    """Parse a date string; returns datetime.date, trying formats in order.

    If a format matches but yields an invalid calendar date, later formats
    are still tried (harmless for unambiguous input, robust otherwise).
    """
    text = text.strip()
    for pat, tokens in parsers:
        m = pat.match(text)
        if not m:
            continue
        parts = {tok: int(val) for tok, val in zip(tokens, m.groups())}
        try:
            return date(parts["YYYY"], parts["MM"], parts["DD"])
        except ValueError:
            continue
    raise ValueError("unparseable date: %r" % text)


def normalize_name(text, steps):
    for step in steps:
        if step == "lowercase":
            text = text.lower()
        elif step == "punctuation_to_space":
            text = text.translate(_PUNCT_TO_SPACE)
        elif step == "collapse_whitespace":
            text = " ".join(text.split())
        else:
            raise ValueError("unknown name normalization step: %r" % step)
    return text


def parse_cents(text):
    """Exact parse of a decimal amount string into integer cents.

    Values with more than two decimal places are rounded half-up to the
    nearest cent.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty amount")
    d = 0
    sign = 1
    s = text
    if s[0] in "+-":
        if s[0] == "-":
            sign = -1
        s = s[1:]
    if "." in s:
        whole, frac = s.split(".", 1)
    else:
        whole, frac = s, ""
    if not whole.isdigit() or not all(c.isdigit() for c in frac):
        raise ValueError("invalid amount: %r" % text)
    if len(frac) > 2 and frac[2] >= "5":
        d = 1  # round half-up on the extra digits
    cents = int(whole) * 100 + int((frac + "00")[:2]) + d
    return sign * cents


# ---------------------------------------------------------------------------
# union-find
# ---------------------------------------------------------------------------

class DSU:
    __slots__ = ("parent", "rank")

    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x):
        parent = self.parent
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


# ---------------------------------------------------------------------------
# matching within a block
# ---------------------------------------------------------------------------

def _pairwise_same(pairs, num, den, dsu):
    """Union all matching pairs inside one sorted (cents, index) list."""
    n = len(pairs)
    j_lo = j_hi = 1
    for i in range(n):
        a, ia = pairs[i]
        if num >= den:  # tolerance >= 100%: everything matches
            for j in range(i + 1, n):
                dsu.union(ia, pairs[j][1])
            continue
        lo = a * (den - num)   # need b * den >= lo
        hi = a * den           # need b * (den - num) <= hi
        if j_lo < i + 1:
            j_lo = i + 1
        while j_lo < n and pairs[j_lo][0] * den < lo:
            j_lo += 1
        if j_hi < j_lo:
            j_hi = j_lo
        while j_hi < n and pairs[j_hi][0] * (den - num) <= hi:
            j_hi += 1
        for j in range(j_lo, j_hi):
            dsu.union(ia, pairs[j][1])


def _pairwise_cross(a_list, b_list, num, den, dsu):
    """Union all matching pairs between two sorted (cents, index) lists."""
    n_b = len(b_list)
    j_lo = j_hi = 0
    for a, ia in a_list:
        if num >= den:
            for c, ib in b_list:
                dsu.union(ia, ib)
            continue
        lo = a * (den - num)
        hi = a * den
        while j_lo < n_b and b_list[j_lo][0] * den < lo:
            j_lo += 1
        if j_hi < j_lo:
            j_hi = j_lo
        while j_hi < n_b and b_list[j_hi][0] * (den - num) <= hi:
            j_hi += 1
        for j in range(j_lo, j_hi):
            dsu.union(ia, b_list[j][1])


def process_block(recs, dob_tol, date_tol, num, den, dsu):
    """recs: list of (row_index, dob_ordinal, service_date_ordinal, cents).

    Buckets by (dob, service_date); pairs buckets within the tolerances;
    sweeps sorted amount lists with two pointers (exact integer math).
    """
    groups = {}
    for idx, dob, sdate, cents in recs:
        groups.setdefault((dob, sdate), []).append((cents, idx))
    keys = sorted(groups)
    for gi, (dob1, sdate1) in enumerate(keys):
        first = sorted(groups[(dob1, sdate1)])
        _pairwise_same(first, num, den, dsu)
        for gj in range(gi + 1, len(keys)):
            dob2, sdate2 = keys[gj]
            if dob2 - dob1 > dob_tol:
                break  # keys are sorted by (dob, sdate): later dobs only grow
            if abs(sdate2 - sdate1) > date_tol:
                continue
            _pairwise_cross(first, sorted(groups[(dob2, sdate2)]), num, den, dsu)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Cluster duplicate insurance claims per rules.yaml.")
    parser.add_argument("claims_dir", help="directory containing *.csv claim files")
    parser.add_argument("--rules", required=True, help="path to rules.yaml")
    parser.add_argument("--out", required=True, help="path to output duplicates.json")
    args = parser.parse_args(argv)

    rules = load_rules(args.rules)
    matching = rules.get("matching")
    if not isinstance(matching, dict):
        raise ValueError("rules.yaml has no 'matching:' section")

    name_steps = matching["patient_name"]["normalize"]
    if isinstance(name_steps, dict):
        name_steps = name_steps.get("_items") or []
    dob_formats = matching["patient_dob"]["input_formats"]
    dob_tol = matching["patient_dob"]["tolerance_days"]
    if matching["service_code"]["mode"] != "exact":
        raise ValueError("only exact service_code matching is supported")
    date_tol = matching["service_date"]["tolerance_days"]
    amount_tol = Fraction(str(matching["amount"]["relative_tolerance"]))
    # claim_id / provider_id listed under ignore: never used for matching.
    if "claim_id" in matching.get("ignore", []) or "provider_id" in matching.get("ignore", []):
        sys.stderr.write("note: claim_id/provider_id ignored per rules.yaml\n")
    num, den = amount_tol.numerator, amount_tol.denominator
    if den <= 0 or num < 0:
        raise ValueError("invalid relative_tolerance in rules: %r" % (amount_tol,))

    date_parsers = build_date_parsers(dob_formats)

    # -- read records -------------------------------------------------------
    paths = sorted(glob.glob(os.path.join(args.claims_dir, "*.csv")))
    if not paths:
        raise ValueError("no CSV files found in %s" % args.claims_dir)

    records = []          # (claim_id, service_code, norm_name, dob_ord, sdate_ord, cents)
    block_index = {}      # (service_code, norm_name) -> list of (row_index, dob_ord, sdate_ord, cents)
    for path in paths:
        with open(path, "r", newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is not None:
                header = [h.strip() for h in header]
                expected = ["claim_id", "patient_name", "patient_dob",
                            "service_code", "service_date", "amount",
                            "provider_id"]
                if header != expected:
                    sys.stderr.write(
                        "warning: unexpected header %r in %s; parsing by position\n"
                        % (header, path))
            for row in reader:
                if not row:
                    continue
                if len(row) != 7:
                    raise ValueError(
                        "expected 7 columns, got %d in %s: %r" % (len(row), path, row))
                cid, pname, pdob, code, sdate, amt, _prov = (c.strip() for c in row)
                if not cid:
                    raise ValueError("empty claim_id in %s" % path)
                norm_name = normalize_name(pname, name_steps)
                dob = parse_date(pdob, date_parsers).toordinal()
                sdate_o = parse_date(sdate, date_parsers).toordinal()
                cents = parse_cents(amt)
                idx = len(records)
                records.append((cid, code, norm_name, dob, sdate_o, cents))
                block_index.setdefault((code, norm_name), []).append(
                    (idx, dob, sdate_o, cents))

    # -- cluster ------------------------------------------------------------
    dsu = DSU(len(records))
    for block in block_index.values():
        process_block(block, dob_tol, date_tol, num, den, dsu)

    clusters = {}
    for idx, rec in enumerate(records):
        clusters.setdefault(dsu.find(idx), []).append(rec[0])

    result = []
    for members in clusters.values():
        if len(members) >= 2:
            result.append(sorted(members))
    result.sort(key=lambda members: members[0])

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
        fh.write("\n")

    sys.stderr.write(
        "dedup: %d records, %d duplicate clusters (%d records involved), %d singletons\n"
        % (len(records), len(result),
           sum(len(m) for m in result), len(records) - sum(len(m) for m in result)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
