"""Run selected pytest cases serially with network/model guards and temporary DBs.

Optional --sdk uses the local dependency cache, never installs or downloads it.
The parent enforces a finite timeout and checks available RAM on Windows.
"""
from __future__ import annotations

import argparse
import builtins
import ctypes
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def _guarded_tests(args: list[str], use_sdk: bool) -> int:
    sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests/agent'), str(ROOT / 'tests')]
    if use_sdk:
        sdk = ROOT / '.cache/dependencies/langfuse'
        if not (sdk / 'langfuse').is_dir():
            raise SystemExit('Optional Langfuse dependency cache is unavailable')
        sys.path.insert(0, str(sdk))
    os.environ.update(CONCIERGE_ENV='test', CONCIERGE_RUNTIME_PROFILE='test',
                      LANGFUSE_ENABLED='false', CONCIERGE_LLM_BASE_URL='', CONCIERGE_LLM_MODEL='')
    attempts = {'real_socket_attempts_blocked': 0, 'heavy_import_attempts_blocked': 0}

    def deny_socket(*_args, **_kwargs):
        attempts['real_socket_attempts_blocked'] += 1
        raise AssertionError('Real network is forbidden in offline tests')

    original_connect = socket.socket.connect

    def guarded_connect(instance, address):
        caller = sys._getframe(1)
        if (caller.f_code.co_name == 'socketpair'
                and Path(caller.f_code.co_filename).resolve() == Path(socket.__file__).resolve()
                and address[0] in {'127.0.0.1', '::1'}):
            return original_connect(instance, address)
        return deny_socket(instance, address)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = deny_socket
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split('.')[0] in {'torch', 'transformers', 'sentence_transformers', 'onnxruntime'}:
            attempts['heavy_import_attempts_blocked'] += 1
            raise AssertionError('Heavy models are forbidden in offline tests')
        return original_import(name, *args, **kwargs)

    builtins.__import__ = guarded_import
    with tempfile.TemporaryDirectory(prefix='concierge-offline-', ignore_cleanup_errors=True) as temporary:
        os.environ['CONCIERGE_DB_PATH'] = str(Path(temporary) / 'bootstrap.sqlite3')
        from concierge_kiosk.runtime import local_http
        local_http._OPENER.open = deny_socket
        import concierge_kiosk.main  # Initialize only the private bootstrap DB.
        for key in ('CONCIERGE_DB_PATH', 'CONCIERGE_LLM_BASE_URL', 'CONCIERGE_LLM_MODEL'):
            os.environ.pop(key)
        import pytest
        try:
            return int(pytest.main([
                *args, '--basetemp=' + str(Path(temporary) / 'pytest'),
                '-o', 'cache_dir=' + str(Path(temporary) / 'cache'),
            ]))
        finally:
            print('OFFLINE_RESOURCE_GUARD', attempts, 'real_network_connections=0 heavy_model_loads=0')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', action='store_true')
    parser.add_argument('--timeout', type=int, default=150)
    parser.add_argument('--guarded', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    args = options.pytest_args
    if args[:1] == ['--']:
        args = args[1:]
    if not args or not any(arg.startswith('tests/') or arg.startswith('tests\\') for arg in args):
        parser.error('Specify selected tests after --; full pytest is not allowed')
    if any(arg.rstrip('/\\') == 'tests' for arg in args):
        parser.error('Select test files or a smaller group, not the entire tests directory')
    if any(arg.startswith(('-n', '--numprocesses', '--basetemp')) for arg in args):
        parser.error('Parallel pytest and overriding the temporary directory are not allowed')
    if not 1 <= options.timeout <= 600:
        parser.error('--timeout must be between 1 and 600 seconds')
    if options.guarded:
        return _guarded_tests(args, options.sdk)
    if os.name == 'nt':
        class Memory(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise SystemExit('RESOURCE_BLOCKED: cannot inspect available RAM')
        if memory.available < 1024**3:
            raise SystemExit('RESOURCE_BLOCKED: available RAM below 1 GiB')
    command = [sys.executable, str(Path(__file__).resolve()), '--guarded']
    if options.sdk:
        command.append('--sdk')
    command.extend(['--', *args])
    try:
        return subprocess.run(
            command, cwd=ROOT, timeout=options.timeout, check=False,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        ).returncode
    except subprocess.TimeoutExpired:
        print('RESOURCE_BLOCKED: offline test subprocess exceeded timeout', file=sys.stderr)
        return 124


if __name__ == '__main__':
    raise SystemExit(main())
