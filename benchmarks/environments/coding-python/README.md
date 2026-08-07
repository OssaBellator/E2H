# Coding environment: identifier normalizer

Implement `normalize_identifier` in `src/task.py`.

Requirements:

- input is a Unicode string;
- trim leading and trailing whitespace;
- convert ASCII letters to lowercase;
- replace each run of ASCII whitespace, `_`, or `-` with one `-`;
- preserve non-ASCII characters unchanged;
- strip leading and trailing `-` from the normalized result;
- raise `ValueError` if the normalized result is empty;
- do not use network access.

Run the deterministic checker with:

```bash
python checks/check.py
```
