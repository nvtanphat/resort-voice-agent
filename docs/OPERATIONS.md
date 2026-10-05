# Vận hành

## 1. Chạy local bằng Python

Cài dependency:

```bash
python -m pip install -e ".[test,ops]"
```

Chuẩn bị biến môi trường dựa trên `.env.example` hoặc `config/local-runtime.env.example`, sau đó chạy:

```bash
python -m concierge_kiosk
```

Health endpoint:

```text
GET /healthz
GET /readyz
```

## 2. Docker local

Repository có `Dockerfile.local` và `compose.local.yaml`.

```bash
docker compose -f compose.local.yaml up --build
```

Compose local bind port `127.0.0.1:8000` và mount `data`, `config`, `knowledge`, `releases` theo mode được khai báo trong file compose.

## 3. Runtime profile

Chọn profile bằng:

```text
CONCIERGE_RUNTIME_PROFILE=test|development|edge|production
```

Profile chứa feature flags, model candidates, budget và timeout. Một số value có thể được override bằng environment variable theo `core/settings.py`.

## 4. Model runtime

Local SLM endpoint trong profile mẫu sử dụng:

```text
http://127.0.0.1:11434
```

Các model embedding/NLI/voice được tham chiếu bằng local path. Repository không đảm bảo tất cả artifact model đều có mặt trong mọi bản đóng gói.

## 5. Knowledge ingestion

Development directory ingestion:

```bash
concierge-ingest knowledge/approved/furama
```

Signed bundle ingestion dùng các tham số `--bundle`, `--manifest`, `--signature`, `--public-key`.

Không có public upload route cho knowledge trong source hiện tại.

## 6. Maintenance tools

Một số script:

```text
tools/maintenance/migrate.py
tools/maintenance/purge.py
tools/maintenance/rebuild_knowledge_index.py
tools/maintenance/reconcile_graph.py
```

Trước khi chạy trên database cần kiểm tra tham số và tạo backup phù hợp.

## 7. Backup và operation

Các tool operation gồm:

```text
tools/operations/backup.py
tools/operations/secure_backup.py
tools/operations/edge_acceptance.py
tools/operations/edge_resource_sample.py
tools/operations/edge_sustained_probe.py
tools/operations/langgraph_restart_probe.py
tools/operations/production_signoff.py
```

Các script này hỗ trợ kiểm tra hoặc tạo artifact vận hành. Kết quả cần được đọc cùng input và môi trường chạy; không nên xem tên script là bằng chứng deployment đã đạt một trạng thái cụ thể.

## 8. Frontend

Source frontend nằm trong `frontend/`. Build script:

```bash
cd frontend
npm ci
node build_offline.cjs
```

Cần kiểm tra `package.json` nếu thay đổi build flow.

## 9. Production configuration

`Settings.validate()` áp dụng thêm điều kiện khi `CONCIERGE_ENV=production`, gồm HTTPS origin, tách public/staff hostname, credential, signed/profile artifacts và local AI requirements tùy cấu hình.

Các điều kiện này là validation trong code. Deployment thực tế còn phụ thuộc reverse proxy, OS, network policy, secret provisioning, model runtime và monitoring bên ngoài process.
