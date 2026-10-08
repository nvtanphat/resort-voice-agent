# Cài đặt và chạy (máy mới)

Repo đã có sẵn code, dữ liệu (`datasets/`, `knowledge/`, `releases/`) và DB kiến thức đã nạp (`data/concierge.sqlite3`, `data/vectors/`). Chỉ cần cài môi trường và tải model.

## 1. Yêu cầu

- Python ≥ 3.11, Node.js ≥ 20, Git.
- [Ollama](https://ollama.com) (chạy local, cổng 11434).
- ~6 GB trống cho model.

## 2. Cài

```bash
git clone -b phase4-handoff https://github.com/nvtanphat/resort-voice-agent.git
cd resort-voice-agent

python -m pip install -e ".[test,ops,voice]"

ollama pull qwen2.5:3b        # SLM hiểu câu
ollama pull bge-m3            # embedding (truy hồi + router + gate khẩn cấp)

python tools/runtime/download_voice_models.py      # Whisper + Piper vào models/voice
python tools/runtime/download_reranker_model.py    # reranker vào models/reranker

cd frontend && npm ci && npm run build && cd ..
```

## 3. Chạy

```bash
set -a; . ./.env.example; set +a     # Git Bash / Linux / macOS
python -m concierge_kiosk
```

Mở đúng `http://localhost:8000` (không dùng `127.0.0.1`, server chặn origin khác). Lần đầu chờ ~30 giây để index router/khẩn cấp dựng xong.

## 4. Kiểm tra

```bash
python -m pytest -q -W error::ResourceWarning   # KHÔNG source .env.example trước khi chạy test
python tools/config/repin_configs.py --check
python datasets/schemas/validate_contracts.py
```

## Lưu ý

- Ở nguyên nhánh `phase4-handoff`. Nhánh `dev` ignore `datasets/`; chuyển sang `dev` sẽ xoá dữ liệu khỏi ổ đĩa.
- Tài liệu kiến trúc: `CLAUDE.md`; luật phát triển: `AGENT.md`; kế hoạch hiện tại: `plan.md`.
