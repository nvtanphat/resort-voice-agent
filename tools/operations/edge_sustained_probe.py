"""On-device CPU/RAM/thermal/disk/health sampling with explicit missing-evidence states.

Run on the TARGET appliance (Linux), with the actual stack and workload alive:
  python -m tools.operations.edge_sustained_probe --duration-seconds 1800 \
    --interval-seconds 5 --report /secure/reports/edge-sustained.json
A host-side probe does not certify offline operation, physical audio or power
loss/restore. Thresholds are deployment choices, not measurements.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from tools.operations.edge_resource_sample import sample


def cpu_counters(proc: Path = Path('/proc/stat')) -> tuple[int, int] | None:
    try:
        line = proc.read_text(encoding='ascii').splitlines()[0].split()
        if line[0] != 'cpu' or len(line) < 5:
            return None
        values = [int(value) for value in line[1:]]
        return sum(values), values[3] + (values[4] if len(values) >= 5 else 0)
    except (OSError, ValueError, IndexError):
        return None


def cpu_percent(before, after) -> float | None:
    if before is None or after is None:
        return None
    total = after[0]-before[0]
    idle = after[1]-before[1]
    if total <= 0 or idle < 0 or idle > total:
        return None
    return round(100*(total-idle)/total, 2)


def check_health(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in ('http','https') or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password:
        raise ValueError('Health endpoint must be an explicit loopback URL')
    try:
        with urlopen(url, timeout=3) as response:  # nosec B310  # scheme/host validated by caller
            return response.status == 200
    except (OSError, TimeoutError):
        return False


def probe(*, duration_seconds: int, interval_seconds: int, report_disk: Path = Path('/'),
          health_url: str = 'http://127.0.0.1:8000/healthz',
          max_cpu_percent: float = 90, max_ram_percent: float = 90,
          max_thermal_c: float = 85, min_disk_free_gib: float = 2,
          max_health_failures: int = 0,
          sample_fn=sample, cpu_fn=cpu_counters, health_fn=check_health,
          sleep_fn=time.sleep, disk_fn=shutil.disk_usage) -> dict:
    if not 1 <= duration_seconds <= 86400 or not 1 <= interval_seconds <= duration_seconds:
        raise ValueError('Invalid finite probe duration or interval')
    if (any(not math.isfinite(x) or x <= 0 for x in
            (max_cpu_percent,max_ram_percent,max_thermal_c,min_disk_free_gib))
            or max_health_failures < 0):
        raise ValueError('Invalid evaluation thresholds')
    # Reject bad URL before monitoring begins, including when health_fn is mocked.
    parsed = urlsplit(health_url)
    if parsed.scheme not in ('http','https') or parsed.hostname not in ('127.0.0.1','localhost','::1'):
        raise ValueError('Only loopback app-health probe supported')
    attempts = max(1, math.ceil(duration_seconds / interval_seconds))
    previous_cpu = cpu_fn()
    cpu_values, ram_values, temp_values, disk_values = [], [], [], []
    health_failures = 0
    for index in range(attempts):
        sleep_fn(min(interval_seconds, duration_seconds-index*interval_seconds))
        current_cpu = cpu_fn()
        load = cpu_percent(previous_cpu, current_cpu)
        previous_cpu = current_cpu
        if load is not None:
            cpu_values.append(load)
        snapshot = sample_fn()
        total, available = snapshot.get('ram_kib'), snapshot.get('available_ram_kib')
        if isinstance(total, int) and total > 0 and isinstance(available, int) and 0 <= available <= total:
            ram_values.append(round(100*(total-available)/total,2))
        temperatures = snapshot.get('thermal_millicelsius') or {}
        valid = [v/1000 for v in temperatures.values() if isinstance(v, int) and -40000 <= v <= 150000]
        if valid:
            temp_values.append(max(valid))
        try:
            disk_values.append(disk_fn(report_disk).free / (1024**3))
        except OSError:
            pass
        if not health_fn(health_url):
            health_failures += 1
    def field(values, limit, *, high=True):
        if not values:
            return {'status':'NOT_RUN','observations':0,'peak':None}
        value = max(values) if high else min(values)
        return {'status':'PASS' if ((value <= limit) if high else (value >= limit)) else 'FAIL',
                'observations':len(values),'peak':round(value,2)}
    checks = {
        'cpu':field(cpu_values,max_cpu_percent),
        'ram':field(ram_values,max_ram_percent),
        'thermal':field(temp_values,max_thermal_c),
        'disk_free':field(disk_values,min_disk_free_gib,high=False),
        'health':{'status':'PASS' if health_failures<=max_health_failures else 'FAIL',
                  'observations':attempts,'failures':health_failures},
    }
    statuses = {row['status'] for row in checks.values()}
    return {'type':'target_host_sustained_probe_not_physical_release',
            'device_host':os.uname().nodename if hasattr(os,'uname') else 'unknown',
            'duration_requested_seconds':duration_seconds,
            'samples':attempts, 'thresholds':{'max_cpu_percent':max_cpu_percent,
                'max_ram_percent':max_ram_percent, 'max_thermal_c':max_thermal_c,
                'min_disk_free_gib':min_disk_free_gib,'max_health_failures':max_health_failures},
            'checks':checks,
            'result':'FAIL' if 'FAIL' in statuses else ('BLOCKED' if 'NOT_RUN' in statuses else 'MEASURED_PASS'),
            'not_tested':['offline_disconnection','power_loss_reboot','backup_restore',
                          'microphone_loudspeaker','signed_hotel_policy']}


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--duration-seconds',type=int,default=1800)
    p.add_argument('--interval-seconds',type=int,default=5)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--report-disk',type=Path,default=Path('/'))
    p.add_argument('--health-url',default='http://127.0.0.1:8000/healthz')
    p.add_argument('--max-cpu-percent',type=float,default=90)
    p.add_argument('--max-ram-percent',type=float,default=90)
    p.add_argument('--max-thermal-c',type=float,default=85)
    p.add_argument('--min-disk-free-gib',type=float,default=2)
    p.add_argument('--max-health-failures',type=int,default=0)
    args=p.parse_args()
    result=probe(duration_seconds=args.duration_seconds,interval_seconds=args.interval_seconds,
        report_disk=args.report_disk,health_url=args.health_url,max_cpu_percent=args.max_cpu_percent,
        max_ram_percent=args.max_ram_percent,max_thermal_c=args.max_thermal_c,
        min_disk_free_gib=args.min_disk_free_gib,max_health_failures=args.max_health_failures)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['result']=='MEASURED_PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
