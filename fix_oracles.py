"""One-use lint correction for the oracle prototype."""

from pathlib import Path

path = Path("src/e2h/oracles.py")
text = path.read_text(encoding="utf-8")
old = '''        if self.min_bytes is not None and self.max_bytes is not None:
            if self.min_bytes > self.max_bytes:
                raise ValueError("min_bytes must not exceed max_bytes")
'''
new = '''        if (
            self.min_bytes is not None
            and self.max_bytes is not None
            and self.min_bytes > self.max_bytes
        ):
            raise ValueError("min_bytes must not exceed max_bytes")
'''
if text.count(old) != 1:
    raise RuntimeError("artifact bound lint anchor mismatch")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
Path(__file__).unlink()
