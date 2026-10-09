# Cài đặt và chạy local

Chạy từ root repository. Hướng dẫn này cấu hình môi trường; không tự chạy benchmark, download hoặc inference.

## Chuẩn bị

- Python ≥3.11; môi trường kiểm tra gần nhất dùng Python 3.12.
- Node.js/npm nếu cần build source frontend; Git.
- Ollama và model đã provision nếu chọn profile dùng SLM/embedding.
- Dung lượng/RAM phụ thuộc bộ model bật thực tế; không có ngân sách cố định bảo đảm mọi profile chạy được.

`datasets/`, `knowledge/`, `releases/` và SQLite knowledge được theo dõi trong repo. Model lớn và secrets không được đóng gói đầy đủ. Giữ dữ liệu và sửa đổi local khi đổi nhánh.

## Python trên Windows PowerShell

```powershell
git clone -b phase4-handoff https://github.com/nvtanphat/resort-voice-agent.git
cd resort-voice-agent
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test,ops]"
```

Nếu đã có working tree, dùng thư mục hiện tại và bỏ bước clone. Trên Linux dùng `python3 -m venv .venv` và `source .venv/bin/activate`.

| Nhu cầu | Extra |
|---|---|
| Whisper/Piper/Pipecat | `voice` |
| OpenVINO reranker | `reranker` |
| Local sentence-transformers embedding | `embeddings` |
| ONNX embedding | `embeddings-onnx` |
| Langfuse SDK | `observability` |

Chỉ cài extra cần dùng, ví dụ `python -m pip install -e ".[test,ops,voice,reranker]"`. Ollama embedding là một provider khác với sentence-transformers. Cài extra không tải/provision model.

## Cấu hình development

Tạo `.env` từ `.env.example` nếu chưa có; không ghi đè file local. Mẫu chứa cấu hình/hashes của checkout, không phải credentials production. Giữ Langfuse tắt trừ khi operator đã cho phép export.

Chọn rõ `CONCIERGE_ENV=development` và `CONCIERGE_RUNTIME_PROFILE=development`. Khi thiếu environment, loader chọn production an toàn, không tự chuyển sang demo. `load_settings()` thử đọc `.env` qua python-dotenv; environment của process cũng có thể override profile. Đừng dùng môi trường development đã export model settings để chạy test.

Khởi động với env file được chỉ định:

```powershell
python -m uvicorn concierge_kiosk.main:app --env-file .env --host 127.0.0.1 --port 8000
```

Hoặc dùng `python -m concierge_kiosk` sau khi cấu hình environment. Entrypoint này đọc `CONCIERGE_BIND_HOST/PORT`, mặc định `0.0.0.0:8000`; đây khác với binding local ở lệnh trên.

Mở đúng `CONCIERGE_PUBLIC_ORIGIN` (mẫu là `http://localhost:8000`). Origin `http://127.0.0.1:8000` khác origin mẫu, dù server bind loopback. Server không tự reload code/config; restart sau khi thay đổi.

## Model và frontend

Profile quyết định model; xem [Operations](docs/OPERATIONS.md). Development tham chiếu Qwen 3B và BGE-M3 qua Ollama; production có yêu cầu manifest/digest/NLI/voice khác. Không tải model rồi coi pin hiện tại tự động hợp lệ.

Công cụ provision có sẵn trong `tools/runtime/download_voice_models.py`, `download_reranker_model.py` và `tools/packaging/`. Chỉ chạy có chủ đích sau khi kiểm tra source/tham số, RAM và cấu hình target.

Build frontend khi đã sửa source:

```powershell
Push-Location frontend
npm ci
npm run build
Pop-Location
```

Build dùng TypeScript và `build_offline.cjs`, tạo assets phục vụ trong `web/`. Không cần build để sửa tài liệu.

## Kiểm tra nhẹ

```powershell
python tools/config/repin_configs.py --check
python tools/runtime/run_offline_tests.py -- tests/agent/test_pending_read_boundaries.py -q
```

Runner dùng SQLite tạm, chặn network/model và timeout hữu hạn. Với server được operator khởi động, kiểm tra `/healthz` và `/readyz`; health PASS không chứng minh model/voice sẵn sàng. Xem [Testing](docs/TESTING.md) và [Limitations](docs/LIMITATIONS.md).
