# Research environment: local evidence packet

Use only the files under `sources/` to answer this question:

> Which project reached general availability first, and by how many days?

Write `answer.json` with this shape:

```json
{
  "project": "...",
  "days": 0,
  "sources": ["source-a", "source-b"]
}
```

Requirements:

- no network access;
- `project` must name the project that reached general availability first;
- `days` is the whole-number difference between the two general-availability dates;
- `sources` must contain the local source IDs supporting the comparison;
- do not cite files that do not support the dates.

Run the deterministic checker with:

```bash
python checks/check.py
```
