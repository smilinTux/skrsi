# SKRSI

**SK Recursive SELF Improvement is the evidence-first improvement control plane for SK software lifecycle orchestration.** SELF means **Systematic Evaluation, Learning, and Feedback**.
**Maturity tier:** T0, N/A because this repository handles no key material. **Version phase:** Incubating v3.

SKRSI measures software delivery, evaluates bounded experiments, records lessons, and proposes safer improvements. It never grants authority, approves its own work, or turns an observation into an actuation.

## Quickstart

```bash
git clone https://github.com/smilinTux/skrsi
cd skrsi
python3 ../sk-standards/scripts/docs_check.py --repo . --tier 1
```

## The name

The readable name is **SK Recursive SELF Improvement**. The canonical expansion is:

> **SK Recursive Systematic Evaluation, Learning, and Feedback Improvement**

SELF is the operating loop:

1. **Systematic**: use declared targets, cohorts, metrics, and stop rules.
2. **Evaluation**: compare evidence without inventing causality or hiding missingness.
3. **Learning**: preserve outcomes and lessons as versioned, attributable records.
4. **Feedback**: route bounded proposals back to the responsible lifecycle agent.

### Alternatives considered

These are noncanonical alternatives retained as naming history. They must not
replace the canonical expansion above:

- **Structured Evidence, Learning, and Feedback**
- **Safe Experimentation, Learning, and Feedback**
- **Sustainable Evaluation, Learning, and Feedback**

## Honest claims

- SKRSI produces typed observations, evaluations, and proposals. It does not own workflow state or authorization.
- Improvement is never inferred from throughput alone. Quality, security, reviewer independence, and missingness remain gates.
- An inconclusive experiment stays inconclusive. A model, agent, or operator cannot relabel it as successful.
- This repository is currently documentation-first. Runtime components remain in [SKCapstone](https://github.com/smilinTux/skcapstone) until a governed extraction card moves them.

## Docs

- [SOP.md](./SOP.md): architecture, operating model, verification, and rollback.
- [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md): responsibility and evidence boundaries.
- [docs/CONTRACTS.md](./docs/CONTRACTS.md): versioned public contract catalog.
- [docs/ADOPTER-GUIDE.md](./docs/ADOPTER-GUIDE.md): minimal integration path.
- [docs/CONFORMANCE.md](./docs/CONFORMANCE.md): conformance levels and checks.
- [SECURITY.md](./SECURITY.md): threat model and private reporting.
- [CONTRIBUTING.md](./CONTRIBUTING.md): branch, test, and independent review workflow.
- [CHANGELOG.md](./CHANGELOG.md): dated project changes.

## Related projects / See also

- Depends on [SKCapstone](https://github.com/smilinTux/skcapstone) for the current registry, collector, evaluator, experiment controller, and runtime handoff implementation.
- Used by [SKDashboard](https://github.com/smilinTux/skdashboard) for bounded aggregate visibility.
- Applies to [SKWorld](https://github.com/smilinTux/skworld) software lifecycle orchestration.
- Governed by [sk-standards](https://github.com/smilinTux/sk-standards).

License: Apache-2.0.
