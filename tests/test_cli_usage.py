"""CLI usage behaviour, driven through the real ``troupe`` console script."""

from support import MakeTheatre, RunCli


def test_config_help_exits_zero_from_a_yml_theatre(
    make_theatre: MakeTheatre, run_cli: RunCli
) -> None:
    theatre = make_theatre("name: demo\n", config_name="troupe.yml")
    assert (theatre / ".troupe" / "troupe.yml").read_bytes() == b"name: demo\n"

    result = run_cli("config", "--help", cwd=theatre)

    assert result.exit_code == 0
    assert result.stdout.startswith(b"usage: troupe config")
