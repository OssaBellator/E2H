"""One-use lint and typing corrections for the oracle prototype."""

from pathlib import Path

path = Path("src/e2h/oracles.py")
text = path.read_text(encoding="utf-8")
replacements = (
    (
        '''        if self.min_bytes is not None and self.max_bytes is not None:
            if self.min_bytes > self.max_bytes:
                raise ValueError("min_bytes must not exceed max_bytes")
''',
        '''        if (
            self.min_bytes is not None
            and self.max_bytes is not None
            and self.min_bytes > self.max_bytes
        ):
            raise ValueError("min_bytes must not exceed max_bytes")
''',
    ),
    (
        "ORACLE_ADAPTER = TypeAdapter(OracleTemplate)\n",
        "ORACLE_ADAPTER: TypeAdapter[OracleTemplate] = TypeAdapter(OracleTemplate)\n",
    ),
)
for old, new in replacements:
    if text.count(old) != 1:
        raise RuntimeError("oracle correction anchor mismatch")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
Path(__file__).unlink()
