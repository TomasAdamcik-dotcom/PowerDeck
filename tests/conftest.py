import pytest

from powerdeck import cpu


@pytest.fixture(autouse=True)
def no_host_cpu(monkeypatch, tmp_path):
    """Tests must never depend on the machine they run on."""
    monkeypatch.setattr(cpu, "CPU_ROOT", str(tmp_path / "no-cpu"))
