import pytest

from powerdeck import cpu


@pytest.fixture(autouse=True)
def no_host_cpu(monkeypatch, tmp_path):
    """Tests must never depend on the machine they run on."""
    monkeypatch.setattr(cpu, "CPU_ROOT", str(tmp_path / "no-cpu"))
    monkeypatch.setattr(cpu, "POWERCAP_ROOT", str(tmp_path / "no-powercap"))
    monkeypatch.setattr(cpu, "THERMAL_ROOT", str(tmp_path / "no-thermal"))
    monkeypatch.setattr(cpu, "THERMAL_STATE_DIR", str(tmp_path / "no-thermal-state"))
    # ... nor touch the user's real state/config (active.json, backups, logs, profiles)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
