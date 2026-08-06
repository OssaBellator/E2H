from pathlib import Path

from e2h.genome import apply_genome, load_genome
from e2h.loader import load_capsule


def test_committed_genome_example_applies_to_committed_base() -> None:
    base = load_capsule(Path("examples/genome/base.yaml"))
    genome = load_genome(Path("examples/genome/candidate.yaml"))

    application = apply_genome(genome, base)

    assert application.capsule.goal == "Verify the candidate harness."
    command = application.capsule.success.commands[0]
    assert command.env["MODE"] == "candidate"
    assert command.timeout_seconds == 10
