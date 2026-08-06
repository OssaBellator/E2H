from typer.testing import CliRunner

from e2h.main_cli import app

runner = CliRunner()


def test_primary_cli_exposes_genome_and_variant_namespaces() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "genome" in result.stdout
    assert "variant" in result.stdout

    result = runner.invoke(app, ["genome", "schema"])
    assert result.exit_code == 0
    assert "base_capsule_sha256" in result.stdout

    result = runner.invoke(app, ["variant", "schema"])
    assert result.exit_code == 0
    assert "HarnessVariantDocument" in result.stdout
