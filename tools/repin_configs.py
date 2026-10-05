"""Refresh or verify SHA-256 pins for checked-in configuration artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_TEMPLATES = (ROOT / ".env.example", ROOT / "config" / "local-runtime.env.example")


def _targets() -> tuple[Path, ...]:
    paths = [ROOT / "config" / "agent-domain.json"]
    paths.extend(sorted((ROOT / "config" / "runtime-profiles").glob("*.json")))
    paths.extend(sorted((ROOT / "releases").glob("*.json")))
    return tuple(paths)


def _sha256(path: Path) -> str:
    raw = path.read_bytes()
    if path.suffix == ".json":
        raw = raw.replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _display(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _read_env(path: Path) -> list[str]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{_display(path)} is not UTF-8") from exc
    return text.splitlines(keepends=True)


def _env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _env_bindings(path: Path, digests: dict[Path, str]) -> dict[str, str]:
    lines = _read_env(path)
    line_keys = _line_keys(lines)
    paths: dict[str, Path] = {}
    for line in lines:
        body = line.rstrip("\r\n")
        stripped = body.strip()
        if not stripped or stripped.startswith("#") or "=" not in body:
            continue
        key, value = body.split("=", 1)
        key = key.strip()
        if key.endswith("_PATH"):
            candidate = Path(_env_value(value))
            if not candidate.is_absolute():
                candidate = ROOT / candidate
            paths[key] = candidate.resolve()

    bindings: dict[str, str] = {}
    for key, target in paths.items():
        sha_key = key.removesuffix("_PATH") + "_SHA256"
        if sha_key in line_keys and target in digests:
            bindings[sha_key] = digests[target]
    return bindings


def _line_keys(lines: list[str]) -> set[str]:
    keys: set[str] = set()
    for line in lines:
        body = line.rstrip("\r\n")
        if "=" not in body or body.lstrip().startswith("#"):
            continue
        keys.add(body.split("=", 1)[0].strip())
    return keys


def _update_env(path: Path, bindings: dict[str, str], *, check: bool) -> list[str]:
    lines = _read_env(path)
    issues: list[str] = []
    updated: list[str] = []
    for line in lines:
        body = line.rstrip("\r\n")
        newline = line[len(body):]
        if "=" not in body or body.lstrip().startswith("#"):
            updated.append(line)
            continue
        key, current = body.split("=", 1)
        key = key.strip()
        expected = bindings.get(key)
        if expected is None:
            updated.append(line)
            continue
        if _env_value(current) != expected:
            issues.append(f"{_display(path)}:{key}")
            updated.append(body[: body.index("=") + 1] + expected + newline)
        else:
            updated.append(line)

    if issues and not check:
        path.write_text("".join(updated), encoding="utf-8", newline="")
    return issues


def _check_or_write_sidecars(digests: dict[Path, str], *, check: bool) -> list[str]:
    issues: list[str] = []
    for path, expected in digests.items():
        sidecar = path.with_suffix(".sha256")
        try:
            actual = sidecar.read_text(encoding="ascii").strip() if sidecar.is_file() else ""
        except (OSError, UnicodeError):
            actual = ""
        if actual != expected:
            issues.append(f"{_display(sidecar)}")
            if not check:
                sidecar.write_text(expected + "\n", encoding="ascii", newline="")
    return issues


def _update_embedded_domain_vocab_pin(digests: dict[Path, str], *, check: bool) -> list[str]:
    """Keep the profile's nested vocabulary pin aligned with the release.

    The profile is JSON rather than an env file, so its release checksum is
    intentionally updated as part of the same repin operation.  Restrict the
    replacement to the declared ``domain_vocab`` object; unrelated checksums
    in the large profile must never be touched.
    """
    profile = ROOT / "config" / "agent-domain.json"
    vocab = ROOT / "releases" / "domain-vocab.json"
    if profile not in digests or vocab not in digests:
        return []
    expected = digests[vocab]
    text = profile.read_text(encoding="utf-8")
    pattern = re.compile(
        r'("domain_vocab"\s*:\s*\{[^{}]*?"sha256"\s*:\s*")([0-9a-f]{64})(")',
        re.DOTALL)
    match = pattern.search(text)
    if match is None:
        return []
    if match.group(2) == expected:
        return []
    if not check:
        profile.write_text(text[:match.start(2)] + expected + text[match.end(2):],
                           encoding="utf-8", newline="")
    return ["config/agent-domain.json:domain_vocab.sha256"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare sidecars and env pins without modifying any files",
    )
    args = parser.parse_args()

    targets = _targets()
    missing = [path for path in targets if not path.is_file()]
    if missing:
        parser.error("missing hash target(s): " + ", ".join(_display(path) for path in missing))
    digests = {path.resolve(): _sha256(path) for path in targets}
    embedded_issues = _update_embedded_domain_vocab_pin(digests, check=args.check)
    if embedded_issues and not args.check:
        digests = {path.resolve(): _sha256(path) for path in targets}

    issues = _check_or_write_sidecars(digests, check=args.check)
    issues.extend(embedded_issues)
    for env_path in ENV_TEMPLATES:
        bindings = _env_bindings(env_path, digests)
        issues.extend(_update_env(env_path, bindings, check=args.check))

    if issues:
        action = "out of date" if args.check else "updated"
        for item in sorted(set(issues)):
            print(f"{action}: {item}")
        return 1 if args.check else 0

    print("configuration hashes are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
