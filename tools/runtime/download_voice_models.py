"""Download the local voice models used by the kiosk (development/demo).

Installs a multilingual faster-whisper STT model and one Piper voice per
supported language into ``models/voice`` (git-ignored), using the file names
the runtime expects (``<lang>.onnx`` + ``<lang>.onnx.json``). Then set:

    CONCIERGE_WHISPER_MODEL_PATH=models/voice/whisper-small
    CONCIERGE_PIPER_MODELS_DIR=models/voice/piper

Requires: pip install -e ".[voice]" huggingface_hub
Production deployments should pin these assets with the voice manifest instead.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

PIPER_REPO = "rhasspy/piper-voices"
# Pinned upstream revisions: a moved branch can never swap the models silently.
PIPER_REVISION = "c10ece1aade47bb51c153c893d14e5bf8e5b7117"
PIPER_VOICES = {
    "en": "en/en_US/lessac/medium/en_US-lessac-medium",
    "vi": "vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium",
    "zh": "zh/zh_CN/huayan/medium/zh_CN-huayan-medium",
    "ko": "ko/ko_KR/kss/medium/ko_KR-kss-medium",
}
WHISPER_REPOS = {
    "small": ("Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120"),
    "base": ("Systran/faster-whisper-base", "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", type=Path, default=Path("models/voice"))
    parser.add_argument("--whisper", choices=sorted(WHISPER_REPOS), default="small",
                        help="small: better vi/ko/zh accuracy; base: faster on weak CPUs")
    args = parser.parse_args()

    from huggingface_hub import hf_hub_download, snapshot_download

    piper_dir = args.target / "piper"
    piper_dir.mkdir(parents=True, exist_ok=True)
    for language, stem in PIPER_VOICES.items():
        for suffix in (".onnx", ".onnx.json"):
            destination = piper_dir / f"{language}{suffix}"
            if not destination.is_file():
                shutil.copyfile(hf_hub_download(PIPER_REPO, stem + suffix, revision=PIPER_REVISION), destination)
        print(f"piper {language}: {piper_dir / (language + '.onnx')}")

    whisper_dir = args.target / f"whisper-{args.whisper}"
    repo, revision = WHISPER_REPOS[args.whisper]
    snapshot_download(repo, revision=revision, local_dir=str(whisper_dir),
                      allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.*"])
    print(f"whisper: {whisper_dir}")
    print("\nSet in your environment:")
    print(f"CONCIERGE_WHISPER_MODEL_PATH={whisper_dir.as_posix()}")
    print(f"CONCIERGE_PIPER_MODELS_DIR={piper_dir.as_posix()}")


if __name__ == "__main__":
    main()
