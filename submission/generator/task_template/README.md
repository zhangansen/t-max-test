# Task scaffold

Jinja2 templates for new tasks. Fill the signature from `sampler.py`,
then instantiate:

```bash
python instantiate.py --signature 'entity_resolution|data_engineering|...' --name my-new-task --out ../tasks/
```

## Fixture contract (per task)

- `environment/data/generate_input.py` — deterministic (seeded) fixture
  generator; MUST be parameterized by `--seed`; MUST NOT write ground truth
  into the agent environment.
- `tests/verifier_env/generate_input.py` — same generator, different seed;
  writes `--gt` (clusters + all_ids) readable only by root.
- Verifier tests run the artifact as an unprivileged child (`preexec_fn`
  demote) with a hard subprocess timeout, and require exact/paired equality
  with ground truth (precision == recall == 1.0 unless the verifier is a
  threshold verifier).

## Golden rules (from the test brief)

1. Difficulty must come from real work: planning, exploration, cross-tool
   coordination, domain expertise, iterative debugging, long-horizon
   execution — never from hiding info, cutting network, or stacking steps.
2. Verifier checks core behavior, not surface files or magic strings.
3. Oracle passes, Nop fails, verifier deterministic, canary GUID present.
4. Capability transfer/combination vs the seed case, not renaming.
