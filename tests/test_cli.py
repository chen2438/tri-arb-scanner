import pytest

from tri_arb.cli import main


def test_doctor_reports_scaffold_state(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor"]) == 0

    output = capsys.readouterr().out
    assert "configuration: ok" in output
    assert "market data: not implemented" in output
