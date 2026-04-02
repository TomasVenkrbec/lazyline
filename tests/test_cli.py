from typer.testing import CliRunner

from lazyline.__main__ import app

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "lazyline" in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    assert "Usage" in result.output
