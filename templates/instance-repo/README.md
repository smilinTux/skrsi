# skrsi-config-example

Private SKRSI instance for the `example` cluster. Generated from the public
`skrsi-config-template`, which is generated from `templates/instance-repo/` in
the SKRSI framework. Read `framework/docs/INSTANCE-MODEL.md` first.

| Path | Holds |
|---|---|
| `framework/` | The SKRSI framework, a git submodule pinned to a release tag. Never edited here. |
| `CONFIG_SCHEMA` | Instance layout version. Equals `config_schema` in `cluster.yaml`. |
| `cluster.yaml` | Cluster name, nodes and roles, readers per node, adopted services, canon pointer, target overrides. |
| `baselines/` | Measured cluster baselines, aggregates only. |
| `evidence/<node>/<YYYY-MM-DD>.json` | Daily collector aggregates, one directory per node. |
| `evaluations/` | Evaluation records; recurrence counts in `evaluations/recurrence/`. |

Lessons are not kept here. They live in the canon named by `cluster.yaml` `canon:`.

## Rules

- Allowed: node short names and roles declared in `cluster.yaml`, service and repo
  names, aggregate counts, rates and durations.
- Not allowed: IP addresses, domains, user or home paths, prompts, responses, memory
  content, card or PR text, credentials.
- `python framework/scripts/validate_instance.py .` enforces both lists. CI runs it on
  every push and pull request, and the publish step runs it before every push.
- Each node writes only `evidence/<its name>/`, with a write deploy key scoped to this
  repo. Keys never go in any repo.
