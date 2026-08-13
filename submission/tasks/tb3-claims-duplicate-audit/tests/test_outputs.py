#!/usr/bin/env python3
"""Verifier for the claims-duplicate-audit task.

Executes the untrusted agent artifact (/app/dedup.py) as an unprivileged
child on the held-out fixture (seed 1337, incl. adversarial near-miss
decoys) and requires exact pair-set equality with the ground-truth
clusters: precision 1.0 AND recall 1.0. Reward is decided by pytest's exit
code; the artifact cannot read /logs/verifier (0700 root-only) or the
ground truth under /root.
"""
import json
import os
import pwd
import subprocess
import sys
import time

CLAIMS_DIR = "/app/claims"
RULES = "/app/rules.yaml"
OUT = "/app/duplicates.json"
GT = "/root/gt.json"
TIMEOUT = 300


def run_artifact():
    """Run the untrusted tool as 'nobody' with a hard timeout."""
    nb = pwd.getpwnam("nobody")

    def demote():
        os.setgroups([])
        os.setgid(nb.pw_gid)
        os.setuid(nb.pw_uid)

    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "/app/dedup.py", CLAIMS_DIR, "--rules", RULES, "--out", OUT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=demote,
        timeout=TIMEOUT,
    )
    return proc, time.time() - t0


# Run exactly once at module import; all tests share the result.
ARTIFACT_PROC, WALL_TIME = run_artifact()


def pair_set(clusters):
    pairs = set()
    for c in clusters:
        ids = sorted(c)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.add((ids[i], ids[j]))
    return pairs


def load_gt():
    with open(GT) as f:
        return json.load(f)


def test_no_third_party_packages():
    import importlib.util

    for pkg in ("numpy", "pandas", "scipy", "sklearn"):
        assert importlib.util.find_spec(pkg) is None, (
            f"third-party package {pkg!r} was installed in the verifier "
            f"environment; the tool must use only the standard library"
        )


def test_artifact_runs_in_time():
    assert ARTIFACT_PROC.returncode == 0, (
        f"tool failed rc={ARTIFACT_PROC.returncode}: {ARTIFACT_PROC.stderr[-500:]!r}"
    )
    assert WALL_TIME <= TIMEOUT, f"tool took {WALL_TIME:.1f}s > {TIMEOUT}s"


def test_output_schema():
    assert os.path.exists(OUT), "missing /app/duplicates.json"
    with open(OUT) as f:
        clusters = json.load(f)
    assert isinstance(clusters, list), "top-level must be a list"
    gt = load_gt()
    all_ids = set(gt["all_ids"])
    seen = set()
    for c in clusters:
        assert isinstance(c, list) and len(c) >= 2, "clusters must be lists of >= 2 ids"
        for i in c:
            assert isinstance(i, str), "cluster members must be strings"
            assert i in all_ids, f"unknown claim_id {i!r}"
            assert i not in seen, f"claim_id {i!r} appears in multiple clusters"
            seen.add(i)


def test_exact_cluster_match():
    with open(OUT) as f:
        got = json.load(f)
    gt = load_gt()
    g = pair_set(gt["clusters"])
    p = pair_set(got)
    inter = p & g
    precision = len(inter) / len(p) if p else 0.0
    recall = len(inter) / len(g) if g else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    assert p == g, (
        f"pair sets differ: precision={precision:.4f} recall={recall:.4f} "
        f"f1={f1:.4f} (false_positive_pairs={len(p - g)}, "
        f"false_negative_pairs={len(g - p)})"
    )
