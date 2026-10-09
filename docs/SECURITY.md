# Security và privacy

## Trust boundaries

| Boundary | Kiểm tra hiện có |
|---|---|
| Guest | Cookie `ck_session` + `X-CSRF-Token`, expiry và ownership |
| HTTP ingress | Origin/Fetch Metadata, CIDR khi cấu hình, security headers |
| Staff | Bearer identity với `requests:read`/`requests:write`; production còn yêu cầu staff gateway |
| Internal agent | `X-Agent-Token`, không dùng guest/staff token thay thế |
| Model/knowledge | Untrusted proposals/evidence; schema, semantic gate và citation checks |
| Business | Explicit confirmation, authorization, receipts và idempotency |
| Telemetry | Optional export, allowlist, masking và keyed correlation |

Origin chỉ là một lớp ingress; không thay session authorization. Public kiosk không có quyền staff chỉ vì biết bearer. Session A không được dùng request/proposal/anchor của session B.

## Configuration và secrets

Settings đọc môi trường/profile có pin. Thiếu environment chọn production; production validate origin HTTPS, staff boundary, credentials, model/signature/manifest requirements theo cấu hình. Startup PASS không chứng minh triển khai đã đủ an toàn.

Không commit secrets. Các secret được hỗ trợ qua environment hoặc `*_FILE` tại nơi contract cho phép; không cấu hình đồng thời hai nguồn. File phải được provision và process cần restart khi rotation. Không in credential, token, raw state hoặc guest PII vào log/report.

SLM HTTP endpoint được giới hạn loopback; Langfuse base URL là endpoint operator cấu hình riêng. Không auto-register Cloud hay export chỉ vì tìm thấy credentials.

## Business safety

Semantic Gate không tin confidence/rank hoặc room number như bằng chứng ý định. Negation, quoted speech, completed reports và informational mentions không tự cấp service authority.

Draft/proposal khác committed write. Confirm kiểm tra owned proposal và policy; request replay không nhân đôi action. DND, guest verification, price disclosure và staff transitions vẫn thuộc server/domain contract.

Emergency safety route không cần Qwen; Tier2 lỗi vẫn để Tier1 hoạt động. Queue receipt khác với staff acknowledgement/dispatch/completion; không tuyên bố đã liên hệ thành công khi thiếu receipt.

## Langfuse privacy

Mặc định disabled: không tạo SDK exporter/sender. Enabled dùng sanitized metadata và export-stage masking; không auto-capture arbitrary LangGraph state.

Không export raw utterances/audio/transcripts, guest identity/contact/room-linked identity, tokens, full model prompt/output, tool payload hoặc database records. Session/proposal links dùng HMAC/pseudonym thay identity thô. Không tự upload training/gold/holdout.

Lỗi telemetry không cấp/revoke business authority và không trigger retry action. SDK/mock privacy checks có bằng chứng ở [Testing](TESTING.md); Cloud ingestion chưa xác minh ở [Limitations](LIMITATIONS.md).

## Artifacts và deployment

Hashes/signatures bảo vệ property/model/knowledge/map/planning/release contracts tại các điểm loader kiểm tra. Chúng cần keys/artifacts thực tế; không tự chứng minh source freshness.

Production signed knowledge update khác directory ingestion development. Container hardening, TLS/proxy, OS patches, credential rotation, backup/restore và network isolation phải được nghiệm thu trên target. Xem [Operations](OPERATIONS.md).
