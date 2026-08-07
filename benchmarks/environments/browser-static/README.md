# Browser environment: static release lookup

Serve the local site with:

```bash
python -m http.server 8000 --directory site
```

Starting from `http://127.0.0.1:8000/index.html`, use the visible navigation to find the current release code.

Write `result.json` with this shape:

```json
{
  "status": "complete",
  "target": "...",
  "path": ["home", "details"]
}
```

Requirements:

- use only the localhost site;
- `target` must be the release code shown on the details page;
- `path` must describe the visible navigation path used to reach it;
- no external network access is required.

Validate the result with:

```bash
python checks/check.py
```
