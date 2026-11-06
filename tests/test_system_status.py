"""System metrics use an injected backend, never this computer's readings."""

from types import SimpleNamespace

import pytest


class FakePsutil:
    def __init__(self, battery=True):
        self.cpu_calls = []
        self.has_battery = battery
        self.paths = []

    def cpu_percent(self, interval=None):
        self.cpu_calls.append(interval)
        return 0.0 if len(self.cpu_calls) == 1 else 12.4

    def virtual_memory(self):
        return SimpleNamespace(percent=58.2)

    def disk_usage(self, path):
        self.paths.append(path)
        return SimpleNamespace(percent=41.3)

    def sensors_battery(self):
        if not self.has_battery:
            return None
        return SimpleNamespace(percent=87.0, power_plugged=True)


def test_primes_cpu_before_returning_real_measurement(tmp_path):
    from system_status import SystemStatusReader

    backend = FakePsutil()
    reader = SystemStatusReader(loader=lambda: backend, disk_path=tmp_path)
    assert backend.cpu_calls == [None]
    assert reader.read() == {
        "cpu_percent": 12.4,
        "memory_percent": 58.2,
        "disk_percent": 41.3,
        "battery_percent": 87.0,
        "power_plugged": True,
    }
    # A bounded fresh sample works even when to_thread chooses another thread.
    assert backend.cpu_calls == [None, 0.1]
    assert backend.paths == [str(tmp_path)]


def test_desktop_without_battery_is_explicit(tmp_path):
    from system_status import SystemStatusReader

    reader = SystemStatusReader(loader=lambda: FakePsutil(False), disk_path=tmp_path)
    result = reader.read()
    assert result["battery_percent"] is None
    assert result["power_plugged"] is None
    assert result["cpu_percent"] == 12.4


def test_backend_failure_has_a_safe_message():
    from system_status import SystemStatusReader, SystemStatusUnavailableError

    def unavailable():
        raise OSError("private OS details")

    with pytest.raises(
        SystemStatusUnavailableError, match="System metrics unavailable"
    ):
        SystemStatusReader(loader=unavailable)


def test_sample_failure_has_a_safe_message(tmp_path):
    from system_status import SystemStatusReader, SystemStatusUnavailableError

    backend = FakePsutil()
    reader = SystemStatusReader(loader=lambda: backend, disk_path=tmp_path)

    def unavailable():
        raise OSError("private OS details")

    backend.virtual_memory = unavailable
    with pytest.raises(
        SystemStatusUnavailableError, match="Could not read system metrics"
    ):
        reader.read()
