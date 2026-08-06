from typer.testing import CliRunner

from e2h.main_cli import app

runner = CliRunner()


def test_primary_cli_exposes_controlled_optimization_namespaces() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "genome" in result.stdout
    assert "optimizer" in result.stdout
    assert "partition" in result.stdout
    assert "variant" in result.stdout

    result = runner.invoke(app, ["genome", "schema"])
    assert result.exit_code == 0
    assert "base_capsule_sha256" in result.stdout

    result = runner.invoke(app, ["optimizer", "schema"])
    assert result.exit_code == 0
    assert "OptimizerAdapterDocument" in result.stdout

    result = runner.invoke(app, ["partition", "schema"])
    assert result.exit_code == 0
    assert "DatasetPartitionDocument" in result.stdout

    result = runner.invoke(app, ["variant", "schema"])
    assert result.exit_code == 0
    assert "HarnessVariantDocument" in result.stdout
