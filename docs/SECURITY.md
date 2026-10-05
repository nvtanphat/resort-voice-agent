# Security boundaries

## 1. Phạm vi tài liệu

Tài liệu này liệt kê các kiểm soát đang có trong code/config. Nó không phải chứng nhận bảo mật và không thay thế threat model hoặc security assessment độc lập.

## 2. Guest, staff và internal agent

API được chia thành các nhóm access khác nhau:

```text
guest/public
staff
internal agent
```

Staff và internal agent dùng dependency xác thực riêng. Production settings yêu cầu staff origin tách khỏi public origin.

## 3. Production origin validation

Khi `environment=production`, settings validation kiểm tra các điều kiện như:

- public origin dùng HTTPS;
- staff origin dùng HTTPS;
- public và staff hostname khác nhau;
- credential tối thiểu theo rule của code;
- CIDR/ingress configuration khi được yêu cầu;
- runtime/property/release hashes và trust material theo cấu hình.

Các check này xảy ra trong application startup/config loading.

## 4. Secret input

Settings hỗ trợ đọc secret từ environment hoặc file provisioned. Không nên commit credential thật vào repository hoặc ZIP release.

`.env.example` và `config/local-runtime.env.example` chỉ nên chứa placeholder/cấu hình mẫu.

## 5. Model endpoint

`llm_base_url` được validation để dùng HTTP trên loopback cho local SLM. Production strict mode còn kiểm tra model digest và NLI/manifest requirement theo settings.

## 6. Signed artifacts

Source có logic cho:

- property profile hash/signature;
- production signoff receipt/signature;
- knowledge signed bundle;
- model/voice artifact manifest;
- map/planning release hash.

Việc signature có giá trị hay không phụ thuộc key provisioning, artifact thực tế và quy trình vận hành.

## 7. Knowledge là untrusted input đối với model

RAG/grounding code tách evidence khỏi instruction path và có claim/citation validation. Đây là một phần của defense against prompt-injection-like content trong knowledge; vẫn cần test các mẫu tấn công phù hợp với deployment.

## 8. Container configuration

Các Docker/compose file hiện có các option như non-root user, read-only filesystem, dropped capabilities và `no-new-privileges` ở một số profile.

Host security, reverse proxy, firewall, TLS key management và OS patching nằm ngoài phạm vi Python process.

## 9. Logging và telemetry

Telemetry endpoint hiện ghi latency event và không cần lưu raw transcript/audio tại call site đó. Cần kiểm tra logging configuration toàn hệ thống nếu có yêu cầu privacy cụ thể.
