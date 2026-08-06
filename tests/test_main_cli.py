from typer.testing import CliRunner

from e2h.main_cli import app

runner = CliRunner()


def test_primary_cli_exposes_genome_namespace() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "genome" in result.stdout

    result = runner.invoke(app, ["genome", "schema"])
    assert result.exit_code == 0
    assert "base_capsule_sha256" in result.stdout
