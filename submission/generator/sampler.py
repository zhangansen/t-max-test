#!/usr/bin/env python3
"""T-Max signature sampler.

Samples one value per taxonomy dimension (weighted) and emits a task
"signature" — the design contract a generator prompt must implement.
Deterministic given --seed.

Example:
    python sampler.py --seed 7 --n 3
"""
import argparse
import random
import sys

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None


def load_taxonomy(path):
    if tomllib is None:
        sys.exit("needs Python >= 3.11 for tomllib (or use Python 3.12+)")
    with open(path, "rb") as f:
        return tomllib.load(f)


def weighted_choice(rng, table):
    items = list(table.items())
    weights = [v.get("weight", 1) if isinstance(v, dict) else 1 for _, v in items]
    return rng.choices([k for k, _ in items], weights=weights)[0]


def sample_signature(rng, tax):
    family = weighted_choice(rng, tax["seed_families"])
    return {
        "seed_family": family,
        "domain": weighted_choice(rng, tax["dimensions"]["domain"]),
        "persona": weighted_choice(rng, tax["dimensions"]["persona"]),
        "language": weighted_choice(rng, tax["dimensions"]["language"]),
        "complexity": weighted_choice(rng, tax["dimensions"]["complexity"]),
        "command_complexity": weighted_choice(rng, tax["dimensions"]["command_complexity"]),
        "fixture": weighted_choice(rng, tax["dimensions"]["fixture"]),
        "verifier": weighted_choice(rng, tax["dimensions"]["verifier"]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--taxonomy", default="taxonomy.toml")
    args = ap.parse_args()
    tax = load_taxonomy(args.taxonomy)
    rng = random.Random(args.seed)
    for i in range(args.n):
        sig = sample_signature(rng, tax)
        print(f"signature[{i}] " + " | ".join(f"{k}={v}" for k, v in sig.items()))


if __name__ == "__main__":
    main()
