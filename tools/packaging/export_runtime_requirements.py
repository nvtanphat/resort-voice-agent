"""Export declared production dependency inputs for a trusted wheel builder.

This file is NOT a lock. Download on the target-compatible trusted builder,
review licenses/vulnerability reports, then freeze actual wheel bytes with
prepare_python_bundle. Do not substitute this input for requirements.lock.
"""
from __future__ import annotations

import argparse
import tomllib
from pathlib import Path
from tools._shared.paths import project_root

ROOT = project_root()
EXTRAS = ('voice', 'embeddings', 'nli', 'incremental-stt', 'ops')


def render(root: Path = ROOT) -> str:
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    deps = [*project['dependencies'], 'setuptools>=70']
    for extra in EXTRAS:
        deps.extend(project['optional-dependencies'][extra])
    return '\n'.join(dict.fromkeys(deps)) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error('Output must be a new file; do not overwrite a previously reviewed resolution')
    args.output.write_text(render(), encoding='utf-8')
    print('DEPENDENCY_INPUTS_EXPORTED_NOT_LOCKED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
