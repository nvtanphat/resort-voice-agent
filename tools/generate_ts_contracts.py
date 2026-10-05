"""Generate stable guest TypeScript aliases from the FastAPI OpenAPI document.

This intentionally emits only the stable transport types consumed directly by
``frontend/src/api.ts``.  Additive agent payloads remain separately typed in the
frontend until they are promoted to stable response models.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _enum(schema: dict, name: str) -> list[str]:
    values = schema.get('enum')
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise RuntimeError(f'OpenAPI field {name} is not a string enum')
    return values


def _literal(values: list[str]) -> str:
    return ' | '.join(repr(value) for value in values)


def generate() -> str:
    # main.py constructs the ASGI app at import. Force an isolated development
    # runtime so contract generation never opens the operator's production DB.
    temp = tempfile.TemporaryDirectory(prefix='concierge-openapi-')
    os.environ['CONCIERGE_ENV'] = 'development'
    os.environ['CONCIERGE_PROPERTY_ID'] = 'OPENAPI'
    os.environ['CONCIERGE_ORCHESTRATOR'] = 'direct'
    os.environ['CONCIERGE_DB_PATH'] = str(Path(temp.name) / 'contracts.sqlite3')
    os.environ.setdefault('CONCIERGE_WEB_DIR', str(Path(__file__).resolve().parents[1] / 'web'))

    from concierge_kiosk.main import app
    from concierge_kiosk.api.shared.contracts import LANGUAGES, REQUEST_KINDS

    fastapi_app = app.instance() if hasattr(app, 'instance') else app
    components = fastapi_app.openapi()['components']['schemas']
    prepare = components['Prepare']['properties']
    ask = components['Ask']['properties']
    payload = components['ServicePayload']['properties']
    language = list(LANGUAGES)
    kinds = list(REQUEST_KINDS)

    def optional_type(name: str, schema: dict) -> str:
        branch = schema.get('anyOf')
        core = next((item for item in branch or [] if item.get('type') != 'null'), schema)
        t = core.get('type')
        if t == 'integer':
            return 'number'
        if t == 'string':
            return 'string'
        if t == 'boolean':
            return 'boolean'
        raise RuntimeError(f'Unsupported ServicePayload field: {name}')

    payload_lines = []
    required = set(components['ServicePayload'].get('required', []))
    for name, schema in payload.items():
        marker = '' if name in required else '?'
        payload_lines.append(f'  {name}{marker}: {optional_type(name, schema)};')

    return '''// AUTO-GENERATED from FastAPI OpenAPI. Do not edit by hand.\n''' + \
        f"export type LanguageCode = {_literal(language)};\n" + \
        f"export type RequestKind = {_literal(kinds)};\n" + \
        "export type RequestStatus = 'pending_staff' | 'approved' | 'in_progress' | 'paused' | 'rejected' | 'completed';\n" + \
        "export interface ServicePayload {\n" + '\n'.join(payload_lines) + "\n}\n"


if __name__ == '__main__':
    destination = Path(__file__).resolve().parents[1] / 'frontend' / 'src' / 'generated' / 'api-contracts.ts'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(generate(), encoding='utf-8')
    print(destination)
