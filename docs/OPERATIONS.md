# Vận hành và cấu hình

## Profile và settings

`core/settings.py` nạp environment, runtime profile và domain profile có checksum. `CONCIERGE_ENV` xác định môi trường; `CONCIERGE_RUNTIME_PROFILE` chọn profile. Có thể chỉ định profile path/SHA-256; explicit overrides chỉ áp dụng theo settings contract. Thiếu environment mặc định production.

Bảng này đọc từ generated profiles hiện tại, không khẳng định máy đã có model:

| Profile | SLM chính | Embedding candidates | GPU setting của SLM |
|---|---|---|---|
| test | Không cấu hình SLM | local E5, rồi hash stub | -1; không inference trong test |
| development | qwen2.5:7b; có fallback model trong config; NLU budget 12 s | Ollama BGE-M3, rồi hash | -1, auto |
| edge | qwen2.5:1.5b | Ollama BGE-M3, rồi hash | 0, CPU |
| production | qwen2.5:7b, strict; NLU budget 12 s | local multilingual-e5-small | 0, CPU |

qwen2.5:7b (Q4, ~5 GB RAM khi chạy) cần máy còn ≥6 GB RAM trống cùng BGE-M3 và ứng dụng; trên CPU 6 nhân một lượt cần model mất 7–12 s. Primary/fallback được cấu hình không chứng minh mỗi lượt tự retry qua mọi model. Kiểm tra transport thực tế. Model digest/manifest/assets production cần operator provision; template không tự là cấu hình deploy hoàn chỉnh.

Development bật semantic understanding/generation/planner và Pipecat; test tắt các feature đó; edge/production dùng legacy voice transport ở profile hiện tại. Production còn yêu cầu independent NLI và incremental STT. Process overrides và hardware thực tế có thể khác profile.

Similarity fast path đang bật; evidence path tắt (`nlu.service_selector.fast_path_evidence_enabled=false`). Domain/schema/pins là nguồn chính cho thresholds, service và language policy; không chép chúng thành constants trong runtime.

## Startup, health và shutdown

Chạy theo [Setup](../SETUP.md). `main.py` tạo app ở import time, mở store và dựng dependency; production kiểm tra strict assets. Lifespan warm SLM/voice/selector/emergency gate có thể tiêu tốn tài nguyên và gọi model. Không import/start app để kiểm tra tài liệu.

`/healthz` kiểm tra storage còn truy cập được. `/readyz` kiểm tra các dependency theo profile và trả 503 khi lớp hiểu ý định còn warm-up (prefix lệnh, index selector, emergency classifier); `/api/config.understanding_ready` báo cùng trạng thái và kiosk chờ nó trước khi nhận lượt khách; đọc chi tiết mode/model/index, không chỉ HTTP status. Không hứa thời gian warm-up cố định.

Restart khi đổi settings/profile/assets. Shutdown xử lý task warm-up, store/graph và telemetry theo lifecycle; export thành công không phải điều kiện hoàn thành business transaction.

## Model và deadline

Giữ model URI như chuỗi (`ollama://bge-m3`), không biến thành Windows Path. Embedding/reranker/index phải khớp manifest; hash fallback không dùng để tuyên bố learned quality.

Deadline nằm trong runtime/domain profile và voice caps. Không nâng production timeout để làm xanh smoke test. Khi đo CPU, xác minh provider thực dùng `num_gpu=0`.

Provider duration fields ở Ollama là nanosecond; harness quy đổi sang millisecond và tách loading/prompt evaluation/generation. Thiếu final event/counter báo NOT_RETURNED; không ước tính từ tổng HTTP latency.

## SQLite và knowledge maintenance

Luồng thử nghiệm dùng DB tạm/copy và checkpoint tương ứng; không chạy tool maintenance/evaluation ghi trực tiếp lên tracked hoặc live DB.

Canonical knowledge thay đổi có thể cần build vocabulary, localized knowledge, releases, refresh manifests và repin configs. Các công cụ nằm trong `tools/knowledge/`, `tools/manifest/`, `tools/config/`. Rebuild runtime knowledge có `--database`, `--model`, `--manifest`, `--require-learned`; kiểm tra source/tham số trước khi chạy vì có ghi DB và embedding calls.

Production ingestion dùng `concierge-ingest --bundle ... --manifest ... --signature ... --public-key ...`; directory ingestion chỉ cho môi trường được phép. Backup, OTA, reconcile và signoff nằm trong `tools/operations/`; không coi tên tool là proof đã nghiệm thu.

## Langfuse optional

Extra hiện tại: `observability` pin SDK `langfuse==4.17.0`.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `LANGFUSE_ENABLED` | false | Không tạo exporter khi tắt |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | rỗng | Secrets; không log/report |
| `LANGFUSE_BASE_URL` | rỗng | Endpoint operator chỉ định, Cloud hoặc self-host |
| `LANGFUSE_TRACING_ENVIRONMENT` | development | Environment của trace |
| `LANGFUSE_SAMPLE_RATE` | 0.1 | Sampling một lần/turn; zero/invalid tắt |
| `LANGFUSE_PSEUDONYM_KEY` | rỗng | Secret ≥32 ký tự khi cần correlation qua restart/workers |

Enabled nhưng thiếu credentials/URL, SDK hoặc init lỗi sẽ safe-disable. Không startup auth probe; invalid credentials có thể chỉ lộ khi async export. Không tự bật Cloud bằng việc cài SDK/có keys.

Mặc định pseudonym key ngẫu nhiên theo process; muốn liên kết session/proposal qua restart phải provision key chung theo contract. Xoay key cắt liên kết cũ. Frontend không gọi Langfuse trực tiếp; không thêm exporter thứ hai.

Scores lấy từ existing evaluation, giữ semantics/units và association; offline chưa có real trace ID giữ unlinked. Xem [Architecture](ARCHITECTURE.md), [Security](SECURITY.md) và [Testing](TESTING.md).

## Docker, frontend và output

`compose.local.yaml` bind host loopback và mount runtime data/config. Endpoint loopback trong container khác host; Compose local không tự provision Ollama host hoặc model mounts.

`frontend/package.json` build bằng TypeScript và offline bundler. `web/` là runtime asset, không xóa như cache.

`reports/` dùng output có tên theo chức năng; `.cache/dependencies/` giữ dependency local. Test runner dùng temp directory và không tạo repo pytest/bytecode cache; fixture transcript dùng tmp_path. Không tạo helpers trong reports hoặc tạo file theo WP/phiên bản.
