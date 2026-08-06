"""One-use corrections for generated redaction integration."""

from pathlib import Path


cli_path = Path("src/e2h/cli.py")
cli_text = cli_path.read_text(encoding="utf-8")
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
    count = cli_text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one generated CLI anchor, found {count}")
    cli_text = cli_text.replace(old, new, 1)
cli_path.write_text(cli_text, encoding="utf-8")

privacy_path = Path("src/e2h/privacy.py")
privacy_text = privacy_path.read_text(encoding="utf-8")
old_callback = '''    for rule, pattern in custom_patterns:
        rendered = pattern.sub(
            lambda match, rule=rule: _replace_match(
                match,
                kind=RedactionKind.CUSTOM,
                location=location,
                records=records,
                allowed=allowed,
                rule_id=rule.id,
            ),
            rendered,
        )
'''
new_callback = '''    for rule, pattern in custom_patterns:
        def replace_custom(
            match: re.Match[str],
            current_rule: CustomRedactionRule = rule,
        ) -> str:
            return _replace_match(
                match,
                kind=RedactionKind.CUSTOM,
                location=location,
                records=records,
                allowed=allowed,
                rule_id=current_rule.id,
            )

        rendered = pattern.sub(replace_custom, rendered)
'''
count = privacy_text.count(old_callback)
if count != 1:
    raise RuntimeError(f"expected one custom callback anchor, found {count}")
privacy_path.write_text(privacy_text.replace(old_callback, new_callback, 1), encoding="utf-8")

Path(__file__).unlink()
