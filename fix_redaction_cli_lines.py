"""One-use formatting corrections for generated ingestion calls."""

from pathlib import Path


path = Path("src/e2h/cli.py")
text = path.read_text(encoding="utf-8")
replacements = {
    "        bundle = ingest_transcript_file(source, capsule_id=capsule_id, redact=redact, redaction_policy=redaction_policy)\n": (
        "        bundle = ingest_transcript_file(\n"
        "            source,\n"
        "            capsule_id=capsule_id,\n"
        "            redact=redact,\n"
        "            redaction_policy=redaction_policy,\n"
        "        )\n"
    ),
    "        bundle = ingest_otlp_file(source, capsule_id=capsule_id, redact=redact, redaction_policy=redaction_policy)\n": (
        "        bundle = ingest_otlp_file(\n"
        "            source,\n"
        "            capsule_id=capsule_id,\n"
        "            redact=redact,\n"
        "            redaction_policy=redaction_policy,\n"
        "        )\n"
    ),
}
for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one generated CLI anchor, found {count}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
Path(__file__).unlink()
