"""Read-only one-shot edge resource sample; does not assert hardware acceptance.

Uses Linux /proc and sysfs when present. GPU telemetry is intentionally marked
unavailable unless a real local provider is configured and verified on device.
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path


def sample(*, proc: Path = Path('/proc'), thermal: Path = Path('/sys/class/thermal')) -> dict:
    result = {'at_epoch_seconds': time.time(), 'pid': os.getpid(),
              'ram_kib': None, 'available_ram_kib': None,
              'thermal_millicelsius': {}, 'gpu_memory_bytes': None,
              'gpu_measurement': 'NOT_AVAILABLE'}
    try:
        for line in (proc / 'meminfo').read_text(encoding='utf-8').splitlines():
            name, _, value = line.partition(':')
            if name in {'MemTotal', 'MemAvailable'}:
                result['ram_kib' if name == 'MemTotal' else 'available_ram_kib'] = int(value.strip().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    if thermal.is_dir():
        for zone in sorted(thermal.glob('thermal_zone*'))[:32]:
            try:
                label = (zone / 'type').read_text(encoding='utf-8').strip()[:64]
                temperature = int((zone / 'temp').read_text(encoding='utf-8').strip())
                if -40000 <= temperature <= 150000:
                    result['thermal_millicelsius'][f'{zone.name}:{label}'] = temperature
            except (OSError, ValueError):
                continue
    return result

if __name__ == '__main__':
    print(json.dumps(sample(), sort_keys=True))
