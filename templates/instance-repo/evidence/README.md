# evidence

Collector aggregates, published out of band by each node's publish step.

- Path: `evidence/<node>/<YYYY-MM-DD>.json`, where `<node>` is a short name declared
  in `cluster.yaml`. Any other file under `evidence/` fails validation.
- A node writes only its own directory, so nodes never conflict.
- A JSON `node` field, when present, must equal the directory name.
- Missing days are missingness, never zero.
