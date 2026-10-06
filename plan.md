# Kế hoạch hoàn thiện voice agent trợ lý khách sạn/resort end-to-end

Mỗi chủ đề chỉ được mô tả **một lần** trong phần "Thiết kế đích" (§4); lộ trình (§6) chỉ trỏ tới đó.

0. Phạm vi phát hành và định nghĩa hoàn tất
1. Mục tiêu và bài toán
2. Hiện trạng
3. Số đo và kết luận kỹ thuật
4. Thiết kế đích
5. Nguyên tắc
6. Lộ trình
7. Rủi ro
8. Để sau
9. Kiểm tra
10. Tham khảo
11. Tiến độ đã kiểm chứng

---

## 0. Phạm vi phát hành và định nghĩa hoàn tất

### 0.1 Mục tiêu của bản phát hành này

Bản phát hành mục tiêu là **prototype production-like cho một property**, chạy trên một kiosk CPU ở edge, có thể demo và kiểm thử lặp lại với dữ liệu đã được kiểm soát. Đây chưa phải là hệ thống kết nối PMS/payment/nhà cung cấp thật.

**Môi trường: một laptop/PC phổ thông bất kỳ** (Windows/Linux/macOS, có mic + loa), **không gắn với máy cụ thể nào**. Laptop vừa đóng vai kiosk vừa là máy edge; không có kiosk, Jetson, hạ tầng mạng resort hay người duyệt thật.
- Mọi gate được nghiệm thu bằng **bản thay thế chạy trên laptop** (bảng §6.0).
- Hạng mục cần thiết bị/người thật nằm ở §0.2 và §8, **không làm**.
- **Không cố định theo máy**: cấu hình theo phần cứng được dò lúc chạy (§4.7). Số đo ở §3 là số **tham chiếu trên một máy dev**; máy khác phải đo lại bằng cùng công cụ.
- Kiểm hành vi ở **cả ba tình huống**: có mạng, mất mạng, và bật/tắt mạng giữa phiên.

**P0 — bắt buộc để gọi là hoàn tất**

- hỏi đáp chỉ dùng canonical knowledge và biết từ chối khi thiếu bằng chứng;
- hiểu và xử lý được một yêu cầu dịch vụ nhiều lượt bằng cả 4 ngôn ngữ;
- mọi hành động ghi đều có tóm tắt, xác nhận khách, idempotency và trạng thái;
- yêu cầu cần người duyệt đi qua HITL; không có đường tắt theo loại dịch vụ;
- khẩn cấp được phát hiện deterministic và chuyển đúng SOP;
- kiosk không mất an toàn khi restart, mất mạng hoặc khách rời kiosk;
- có trace, audit, metric và bộ eval có thể chạy lại từ đầu.

**P1 — chỉ làm sau khi toàn bộ P0 đạt**

- multi-agent/specialist subgraph;
- incremental STT, barge-in bằng phần cứng và tối ưu p50 voice. P0 vẫn phải đo voice; nếu chưa đạt thì phải có fallback push-to-talk/text như ghi ở bảng mục tiêu;
- OTA kéo từ xa, dashboard nhiều kiosk;
- fine-tune/LoRA hoặc thay model.

**Vector DB là P0** (theo yêu cầu): `VectorStore` + Chroma, có spike, và dùng FAISS/sqlite-vec làm dự phòng như §4.3.

### 0.2 Không nằm trong phạm vi

- thanh toán thẻ, thay đổi đặt phòng thật, cấp quyền mở cửa hoặc quyết định y tế;
- tự động đưa ra giá/chỗ trống hiện tại nếu không có tool được ủy quyền;
- thu và lưu audio thật trong production prototype;
- tối ưu cho nhiều property trước khi single-property contract ổn định.
- **Việc cần thiết bị hoặc người thật**, không làm vì không có:
  - kiosk ở sảnh (độ vang/tiếng ồn thật, khoảng cách khách);
  - Jetson/arm64 chạy thật, nhiệt khi chạy nhiều giờ, rút điện thật;
  - khôi phục backup sang máy khác;
  - HTTPS + gateway phân quyền trên mạng resort;
  - repository TUF do operator ký và máy chủ phát hành;
  - Wi-Fi resort cho trang QR trên điện thoại khách;
  - ghi âm khách thật;
  - chữ ký người duyệt nghiệp vụ/an toàn.

### 0.3 Quy ước hoàn tất một hạng mục

Một hạng mục chỉ được đánh dấu **Done** khi có đủ: thay đổi code/cấu hình, test contract, một bài eval hoặc probe phù hợp, artifact kết quả trong `reports/`, tài liệu cập nhật và rollback rõ ràng. “Endpoint chạy được” hoặc “pytest pass” riêng lẻ chưa đủ.

### 0.4 Vai trò review

- **Owner kỹ thuật**: duy trì contract, benchmark và quyết định promote/rollback.
- **Owner nghiệp vụ**: xác nhận policy, SLA, nhãn giá synthetic và luồng HITL.
- **Reviewer an toàn**: duyệt emergency, privacy, auth, retention và audit.
- **Reviewer release**: kiểm artifact, checksum, profile edge và bảng bằng chứng.

Trong prototype, một người có thể kiêm cả bốn vai trò. Mỗi release chỉ cần **một** file `reports/release/<version>/signoff.json` gồm checklist bốn mục (kỹ thuật, nghiệp vụ, an toàn, release). Mỗi mục ghi `pass|pass_with_risk|blocked`, trỏ tới artifact bằng chứng và có tên người duyệt. Mục an toàn không được `pass_with_risk` cho hạng mục P0.

---

## 1. Mục tiêu và bài toán

### 1.1 Đề bài
- **Bối cảnh**: kiosk/robot ở sảnh, chạy **edge**. Hệ thống phải chạy đúng **cả khi có mạng lẫn khi không có mạng**, và tự nhận biết trạng thái mạng lúc chạy (§4.6), không giả định cứng một chế độ.
- **Giao tiếp**: trả lời khách bằng **vi/en/zh/ko**, qua **giọng nói + màn hình**.
- **Năng lực chính**:
  - **grounded RAG** trên cẩm nang/bản đồ, không bịa giá/giờ;
  - **tool-use**: đặt bàn, dọn phòng, đặt tour, chỉ đường;
  - **lập kế hoạch nhiều bước + nhớ ngữ cảnh**;
  - **chuyển nhân viên** khi ngoài phạm vi;
  - **HITL** cho mọi yêu cầu dịch vụ.
- **Ràng buộc**: dữ liệu khách riêng tư; đo **độ trễ** và **tỷ lệ grounded**.
- **Mức cơ bản**: app kiosk, đăng nhập 2 vai trò (khách, nhân viên/quản trị), HITL, xử lý câu ngoài phạm vi, metric grounded/intent/ngôn ngữ.
- **Mức nâng cao**: SLM lượng tử hóa offline, voice E2E, multi-agent, eval RAG/hallucination, OTA cẩm nang, guardrail giá/giờ + bảo mật. Giám sát nhiều kiosk để sau (§8).

**Quyết định đã chốt**
- Phần cứng: **laptop/PC phổ thông bất kỳ** (đóng vai kiosk mini-PC). Mặc định chạy CPU; nếu máy có GPU thì được dùng khi config cho phép, nhưng chỉ tiêu nghiệm thu luôn đo ở chế độ CPU. Không có thiết bị thật nào khác.
- Mic/loa: dùng **mic + loa của laptop** để thử barge-in rồi chọn transport.
- Audio để đo: **tổng hợp bằng Piper**; ghi âm thật là tùy chọn.
- Dữ liệu: **dùng bộ hiện có, không thu thêm**. Quy ước theo `datasets/README.md`:
  - thông tin công khai ở `knowledge/canonical/` (nguồn RAG duy nhất);
  - thông tin không công bố (giá dịch vụ, chỗ trống, lịch tour/xe, SLA, tồn phòng, PMS, nhân sự) là synthetic ở `synthetic/operations/`: dùng cho tool/workflow, **không được nói như sự thật chính thức**;
  - mỗi file synthetic tự khai cách dùng (`classification`, `truth_status`, `guest_answer_policy`, `price_semantics`, `demo_booking_mode`), nên code đọc các trường này một cách tổng quát để gắn nhãn câu trả lời.

**Mục tiêu đo cho bản P0**

| Nhóm | Mục tiêu tạm thời | Cách hiểu đúng |
|---|---:|---|
| An toàn giao dịch | 0 ghi sai trong bộ regression; 0 bypass xác nhận/policy | Một lần vi phạm là chặn promote, không lấy trung bình để che lỗi |
| Grounding | ≥ 98% câu trả lời fact có evidence hợp lệ; 0 giá/giờ không có evidence | Tách `grounded`, `unsupported`, `abstained` và `not_applicable` |
| Hiểu yêu cầu | ≥ 90% command hợp lệ trên holdout 4 ngôn ngữ; **100% emergency recall** trên bộ ca khẩn cấp đã duyệt (kể cả bản có nhiễu: gõ sai, mất dấu), ≥ 95% trên câu khẩn cấp mới chưa gặp | Đo trên dữ liệu chưa dùng để train, báo cáo theo từng ngôn ngữ. Bỏ sót khẩn cấp đã duyệt là chặn promote |
| Hoàn thành nghiệp vụ | ≥ 85% journey P0 hoàn tất đúng trạng thái; pass^5 không thấp hơn baseline | Chấm state cuối, tool args, policy và câu trả lời, không chỉ HTTP 200 |
| Voice | p50 tiếng đầu ≤ 2,5 s, p95 ≤ 4,0 s sau khi warm; báo riêng cold start | Nếu phần cứng không đạt, phải fallback text/push-to-talk và ghi rõ giới hạn |
| Vận hành | 100% write request có `request_id`, idempotency key, audit và trace | Có thể dựng lại timeline mà không cần đọc PII thô |

Các ngưỡng trên là **release gate**, không phải lời hứa hiệu năng cho mọi mini-PC. Khi chưa có baseline hoặc mẫu đủ lớn, ghi `unmeasured`, không ghi `pass`.

### 1.2 Sản phẩm
> **Kiosk concierge đa ngôn ngữ giúp khách resort nói tự nhiên để hỏi thông tin và tự hoàn thành yêu cầu dịch vụ, không cần biết quy trình nội bộ.** Agent tự hiểu nhu cầu, lập kế hoạch, dùng dữ liệu/hệ thống cần thiết và hỏi lại khi thiếu. Agent chỉ thực hiện giao dịch sau khi khách xác nhận; hành động cần kiểm soát thì nhân viên duyệt.

| | |
|---|---|
| **Vấn đề** | Rào cản ngôn ngữ; phải xếp hàng/gọi lễ tân; khách không biết yêu cầu thuộc bộ phận nào; phải lặp lại thông tin |
| **Input** | Nhu cầu tự nhiên, chủ yếu bằng giọng nói: câu nói; ngôn ngữ; thông tin khách đưa trong hội thoại (ngày, giờ, số người, dịch vụ, địa điểm, yêu cầu đặc biệt); ngữ cảnh các lượt trước; xác minh lưu trú (QR thẻ phòng/phòng) khi cần; xác nhận của khách trước mọi hành động làm thay đổi trạng thái |
| **Xử lý** | Hiểu mục tiêu (nhiều ý, sửa ý, ý có điều kiện) → thu thập phần còn thiếu → tra cứu/lập kế hoạch → gọi tool → kiểm policy → khách xác nhận → xác minh nếu cần → nhân viên duyệt nếu cần → thực hiện → trả kết quả + trạng thái |
| **Output** (đúng 1 trong 4) | (1) **câu trả lời có căn cứ** ("Hồ bơi mở cửa đến 21:00"); (2) **câu hỏi bổ sung** đúng phần còn thiếu ("Anh/chị muốn đặt tại nhà hàng nào?"); (3) **kết quả yêu cầu dịch vụ**: loại dịch vụ, thời gian, thông tin chính, **mã xác nhận**, **trạng thái**, **thông báo khi trạng thái đổi** ("Đặt bàn 4 khách 19:00 đã xác nhận, mã R4821"); (4) **chuyển nhân viên** kèm ngữ cảnh |
| **Khi lỗi** | Hỏi lại, nói rõ không có dữ liệu, đưa phương án khác, hoặc chuyển nhân viên — không bao giờ đoán |
| **Thành công** | Khách hoàn thành nhu cầu bằng hội thoại, không phải học cách dùng app hay biết hệ thống bên trong |

### 1.3 Nguyên tắc UX: "đọc thoải mái, ghi cẩn thận"
- **Câu chỉ đọc** (giờ mở cửa, vị trí, thực đơn, lịch shuttle, còn chỗ không): trả lời ngay, **không bắt xác nhận**.
- **Hành động ghi** (đặt bàn/tour/xe/spa, late checkout, room service, housekeeping, mang đồ): tóm tắt → **khách xác nhận** → xác minh phòng nếu cần → nhân viên duyệt nếu cần → thực thi.
- **Hỏi còn chỗ ≠ đồng ý đặt**:
  - khách chỉ hỏi → kiểm chỗ rồi *đề nghị*;
  - **ý định có điều kiện** ("nếu còn thì đặt giúp tôi") → kiểm chỗ; còn thì đi thẳng tới **tóm tắt + xác nhận**; hết thì đưa phương án khác;
  - yêu cầu chỉ được tạo sau khi khách xác nhận tóm tắt.
- **Không biến hội thoại thành form**: khách nói đủ thì không hỏi lại; chỉ hỏi đúng phần thiếu, gộp trong một câu.

### 1.4 Hành trình kiểm thử
Đây là **kịch bản đo hành vi agent**, không được cài thành luồng cố định trong code.

**Hành trình chính** — khách Hàn, nói tiếng Hàn: "Tối nay nhà hàng Ý còn bàn cho 4 người khoảng 7 giờ không?"
1. Agent tự kiểm chỗ trống (tool đọc).
2. Báo còn bàn 19:00 và *đề nghị* đặt.
3. Khách: "Có" → agent yêu cầu quét QR thẻ phòng.
4. Màn hình + giọng đọc lại tóm tắt (số phòng che bớt) → khách: "Đúng".
5. Yêu cầu vào hàng chờ HITL của nhân viên.
6. Nhân viên duyệt → kiosk báo "Đã xác nhận, mã R4821", kèm thẻ trên màn hình.
7. Khách hỏi "bao giờ có?" → agent trả lời trạng thái + thời gian dự kiến theo SLA.

**Biến thể bắt buộc**
- **Ý định có điều kiện**: "…nếu còn thì đặt giúp tôi". Hết chỗ thì đề xuất giờ/nhà hàng khác.
- **Sửa ý giữa câu**: "7 giờ… à thôi 7 rưỡi" → `SetSlot` rồi `CorrectSlot`.
- **Nhiều việc một câu**: "hồ bơi mấy giờ đóng, với đặt xe ra sân bay sáng mai" → một câu trả lời + một yêu cầu còn thiếu giờ đón.
- **Khách không biết bộ phận**: "phòng tôi hết khăn" → Housekeeping, hỏi số lượng, phòng lấy từ phiên.
- **Cùng nghiệp vụ, 4 ngôn ngữ**: dùng chung một đường xử lý.

---

## 2. Hiện trạng
| Mảng | Đã có | Thiếu |
|---|---|---|
| **Vòng agent** | `GovernedLoopSemantics` (verify → plan → execute → record); hợp đồng mục tiêu; verifier; planner model (bước kế tiếp/DAG) + fallback deterministic; ngân sách bước/đọc/planner; `failure_class`; LangGraph + checkpoint; memory/anchor; `Command[]` → hành động | Planner model **chỉ chạy khi đọc thất bại**; voice tắt hẳn; chưa multi-agent |
| **Hiểu câu** (gốc của "workflow") | Regex `classify_dialogue` chọn nhánh; chọn dịch vụ bằng khớp từ khóa `match_terms`; tách ý bằng `service_clauses` + `MULTI_CONNECTOR_PATTERNS`; slot bằng `extract_slots`; mục tiêu = if/elif theo `decision.branch` (`state.py::_goal_requirements`); `Command[]` đã được project lại thành route bounded | Chế độ `command` mới ở profile dev, `legacy` vẫn mặc định; còn `model_intent.py`/`turn_plan.py`; ~38 chỗ rẽ nhánh trong `engine.py` (1.172 dòng) |
| **Tool nghiệp vụ** | Đọc tri thức, giờ, địa điểm, chỉ đường, kế hoạch; ghi qua workflow service + policy; adapter read-only đã nối availability nhà hàng/spa/tour/xe từ synthetic operations, provenance bắt buộc | Các tool write/giữ chỗ/PMS/phí/minibar/nhân sự còn là synthetic fixture và chưa được phép nói như dữ liệu thật; chưa có upstream thật |
| **HITL** | Đồ thị LangGraph `guest_confirmation → staff_review → fulfillment` với `interrupt()` + idempotency (`agent/orchestration/graph.py`); property release có `hitl_mode=guest_confirm_all` để low-risk không bypass guest confirmation | Các profile fixture cũ mặc định `legacy_policy` để giữ compatibility test; cần promote profile/signoff trước khi gọi là production |
| **Nghiệp vụ yêu cầu** | Trạng thái `open/acknowledged/approved/in_progress/paused/completed/cancelled/rejected`; SLA tiếp nhận + xử lý; leo thang 2 cấp; giao việc + ETA; từ chối bắt buộc lý do; khách xin sửa (`guest_change`); idempotency; khung giờ phục vụ; khẩn cấp; feedback | Mã xác nhận; đồng ý dữ liệu; trạng thái chỉ xem được theo phiên; nhân viên chưa đề xuất thay đổi được; chưa giữ chỗ; chưa dịch cho nhân viên |
| **RAG** | FTS + vector JSON trong SQLite (quét tuần tự) + RRF + rerank; claim/citation binding; abstention cố định | **Chưa có vector DB** (không index, không lọc metadata ở tầng vector); chưa có KPI grounded online |
| **Voice** | Pipecat `voice/agent/*`: STT/TTS bọc adapter, SpeechGate kiểm evidence, câu đệm, barge-in; Piper vi/en/zh/ko; whisper-small | Chưa chạy thật/đo lần nào; `enable_rtvi` chưa truyền; legacy voice còn song song (`App.tsx` 816 dòng) |
| **Vai trò + web nhân viên** | Khách: cookie + CSRF; nhân viên: bearer scope `requests:read/write`; `/ops` (JS thuần, tiếng Anh): hàng chờ, chuyển trạng thái, audit, khẩn cấp, metric thô | Đăng nhập tài khoản, vai trò quản trị, màn duyệt HITL, phân công, đồng hồ SLA, lịch đặt chỗ, KPI biểu đồ, tiếng Việt, React |
| **Eval** | Bộ chấm trajectory (`agent/runtime/eval/harness.py`); `run_e2e_cases.py`; `run_tool_eval.py`; 240 ca `service_actions`; 155 journey; dữ liệu mô phỏng khách (`end_to_end/simulation`); runner đã giữ `preconditions`/`acceptable_tools` và có `--repeats` cho pass^k | Chưa chạy full API benchmark nhiều lượt trong workspace này; chưa có báo cáo voice |
| **Metric** | `/staff/metrics`: counter, histogram độ trễ, SLM throughput | Grounded rate đúng nghĩa, intent/ngôn ngữ, p50/p95 từng chặng voice |
| **OTA** | Gói tri thức ký Ed25519, có version, chống downgrade/replay | Chưa có kênh kéo; chưa chống freeze |
| **Edge** | `deploy/edge/compose.yaml` có giới hạn CPU/RAM | Chưa có profile mô phỏng Jetson + số đo |

### 2.1 Blocker hiện tại phải xử lý trước khi xây thêm agent

Theo `datasets/README.md`, runtime integration đang bị chặn bởi hai hợp đồng dữ liệu; đây là việc đầu tiên của GĐ0, không đẩy sang GĐ2:

1. `datasets/synthetic/operations/policies/service_policies.json` phải parity chính xác với checksum-pinned `config/agent-domain.json` về `service_code`, slot, risk, confirmation, verification và department.
2. Nhãn trong `datasets/evaluation/` phải map một-một sang ontology `Command` hiện tại; nhãn không map được phải bị fail-fast, không tự đoán.

**Trạng thái đã kiểm (05/10/2026)**:
- (1) Đầu phiên đã đồng bộ `agent-domain.json` theo `service_policies.json`. Kiểm lại thấy 14/14 dịch vụ khớp, **0 lệch** ở slot bắt buộc/tùy chọn, approval, risk, reversible và staff verification. Còn phải so `department` và `confirmation` rồi ghi vào artifact.
- (2) Map nhãn route trong `datasets/evaluation/` sang `Command` **vẫn mở**: dữ liệu dùng ~14 tên route, router dùng tập nhỏ hơn.

Kết quả cần có: `reports/gates/gd0-data-contract.json` gồm hash hai manifest, số service/slot, danh sách mismatch (nếu có) và trạng thái `pass|blocked`. Chưa có artifact `pass` thì không được xóa legacy NLU, không đổi mặc định runtime và không gọi benchmark agent là hợp lệ.

### 2.2 Quy tắc làm việc với worktree hiện tại

Repo có thể có thay đổi chưa commit của người khác (Codex, Claude hoặc người làm tay). Trước mỗi phase chạy `git status` và ghi danh sách file đang sửa vào `reports/gates/<phase>-worktree.json`. Không reset, checkout hoặc xóa các thay đổi đó; file đang có người sửa thì không đụng cho tới khi họ commit. Mọi bước dọn legacy phải bắt đầu bằng `git status`, kiểm tra import bằng `rg`, chạy test liên quan và tạo artifact rollback; chỉ xóa sau khi không còn consumer trong source/test/tool/docs.

---

## 3. Số đo và kết luận kỹ thuật
**Số đo tham chiếu** trên một máy dev (CPU 6 nhân/12 luồng, 15 GB RAM; Ollama tự dùng GPU rời nên mọi số dưới đây **ép CPU**, `num_gpu=0`). Đây không phải chỉ tiêu cố định. Máy khác chạy lại `tools/runtime/preflight.py` + các bộ eval ở §9 để có số của chính máy đó, và report luôn ghi kèm thông tin phần cứng.

| Thành phần | Số đo | Kết luận |
|---|---|---|
| qwen2.5:3b (CPU) | sinh **18,7 tok/s**; prefill khi cache **0,25–0,42 s**; lạnh 7,6 s + nạp 3,6 s | Lệnh 50–70 token = **2,9–3,8 s**; chi phí nằm ở **token đầu ra** |
| qwen2.5:0.5b (CPU) | sinh **75 tok/s**; prefill cache 0,1 s | ~1,2 s cho 80 token; chất lượng chưa đo |
| Cache tiền tố Ollama | cùng phần đầu prompt → prefill 0,03–0,4 s | Phần cố định để đầu, phần thay đổi để cuối |
| STT whisper-small int8 | **1,8–2,1 s** cho câu 4,4–5,8 s | Đang giải mã sau khi khách nói xong; **zh sai trên audio sạch** (几点→记点, 送→松, 毛巾→毛进) |
| TTS Piper | 0,2–0,3 s/câu | Không phải điểm nghẽn |
| Embedding bge-m3 | ~46 ms | Không phải điểm nghẽn |
| Reranker int4 OpenVINO | top_k 4: 0,29 s; 8: 0,6 s; 20: 3,4 s; nạp 12 s | Giữ top_k = 4; nạp lúc khởi động |
| Retrieval (`reports/retrieval/g-rr-a03-k4-l64.json`) | **R@1 95,4%**, R@3 98%; không rerank 91,3%; p95 0,72 s; zh yếu nhất | RAG đã tốt; lỗi chính "đúng thuộc tính, sai ngữ cảnh" |
| Router regex (`reports/nlu-robustness.json`) | **44%** câu có nhiễu (vi 32%, en 40%, ko/zh 53%); "send two bath ttowels to room 305" → hiểu thành hỏi tri thức | **Hiểu câu bằng luật là điểm yếu lớn nhất** |
| Semantic router kNN (`reports/nlu-router-calibration.json`) | precision **75%** ở coverage 75% | Chưa dùng làm đường tắt được (cần ≥ 95%) |

**Độ trễ voice** (khách nói xong → tiếng đầu tiên):
- **Thiết kế hiện tại**: VAD 0,65 + STT 2,0 + SLM 3B 1,8–4,2 + retrieval 0,4–0,7 + TTS 0,2 ≈ **5–8 s**.
- Mục tiêu p50 ≤ 2,5 s chỉ đạt được khi đổi thiết kế (§4.4) → dự kiến **1,7–2,6 s**.

### 3.1 Quy ước đo để tránh số đẹp nhưng không dùng được

- **Cố định profile**: `config/runtime-profiles/edge.json`, CPU-only, số worker, thread, model hash, dataset release và commit SHA phải nằm trong mọi report.
- **Warm/cold tách riêng**: bỏ 3 lượt warm-up đầu khỏi warm latency; cold start, model load và index load báo thành metric riêng.
- **Đơn vị thời gian**: dùng monotonic clock và trace `turn_id`; báo p50/p95/p99, không chỉ trung bình.
- **Denominator rõ ràng**: mỗi tỷ lệ ghi `numerator`, `denominator`, `excluded` và lý do loại mẫu. Không trộn câu hỏi đọc với write request.
- **Reproducibility**: seed, manifest, config, model checksum và lệnh chạy được lưu trong report; dataset evaluation không được copy sang training.
- **Human review**: mẫu fail của grounding, policy, privacy và emergency phải được lưu redacted để review; không log transcript/room number nguyên văn.

### 3.2 Định nghĩa metric sản phẩm

| Metric | Định nghĩa |
|---|---|
| `grounded_answer_rate` | Số câu fact đủ claim-evidence binding / tổng câu fact được chấm; câu abstain đúng không tính là grounded |
| `safe_abstain_rate` | Số câu thiếu evidence nhưng từ chối/chuyển người đúng / tổng câu thiếu evidence |
| `unsafe_answer_rate` | Số câu trả lời unsupported hoặc bịa giá/giờ/chỗ / tổng câu fact; release gate bằng 0 cho P0 |
| `command_valid_rate` | Command parse được schema, service hợp lệ, slot đúng kiểu / tổng lượt cần hiểu command |
| `policy_compliance` | Lượt không bypass confirmation, verification, HITL, emergency và idempotency / tổng lượt action |
| `journey_completion` | Journey đạt trạng thái DB mục tiêu, câu trả lời đúng và không vi phạm policy / tổng journey |
| `voice_first_audio_latency` | `audio_first_frame_monotonic - guest_turn_end_monotonic`; báo riêng STT, route, tool, render, TTS |

Mỗi report phải có confusion matrix theo `vi/en/zh/ko`, `read|write|emergency`, và `known|holdout|novel` để không che điểm yếu của tiếng Trung hoặc luồng khẩn cấp.

---

## 4. Thiết kế đích

### 4.1 Kiến trúc AI agent (không phải workflow)
Theo [Anthropic — Building effective agents](https://www.anthropic.com/research/building-effective-agents):
- **workflow**: LLM và tool nối bằng đường code định sẵn;
- **agent**: LLM tự điều khiển quy trình và việc dùng tool dựa trên quan sát.

Vòng agent **đã có**; cái còn là workflow là **khâu hiểu câu và dựng mục tiêu**. Việc chính là thay khâu đó, không phải viết lại vòng.

```
Nghe (STT) → Hiểu (Command[]) → Lập kế hoạch (DAG tool) → Hành động (tool có kiểu) → Quan sát
     ↑                                                                                │
     └──── Ghi nhớ ← Trả lời grounded ← Tự kiểm (đủ mục tiêu? có bằng chứng?) ←───────┘
                                          └─ chưa đủ → lập kế hoạch lại (có ngân sách)
```

**Phần agent tự quyết** (đo bằng hotel-bench):

| Bước | Agent quyết | Code tái dùng |
|---|---|---|
| Hiểu | Ý định, nhiều ý, slot, sửa slot, ý có điều kiện; **dịch vụ nào**: top-k bằng đối sánh ngữ nghĩa bge-m3 trên `service_catalog.json` → model chọn trong tập đó → server kiểm với registry. Bỏ `match_terms` | `agent/understanding/commands.py`, `semantic_router.py` |
| Lập kế hoạch | Tool nào, thứ tự, chạy song song gì; kế hoạch có điều kiện | `agent/runtime/planner.py` (đã có DAG), `loop_semantics.py` |
| Hành động | Tham số tool | `agent/tools/registry.py`, `policies.py` |
| Tự kiểm / lập lại | Đủ mục tiêu chưa, có bằng chứng không, hỏi khách hay chuyển người, hết chỗ thì tìm phương án khác | `agent/runtime/verifier.py`, `tool_contracts.py` |
| RAG chủ động | Tách câu ghép, viết lại truy vấn, tra nhiều lần, biết dừng và từ chối | `hotel_info_search` + `rag/grounding/relevance.py::answerable` |
| Hỏi lại | Câu hỏi sinh từ schema slot (`slot_labels`, slot bắt buộc trong `service_policies.json`) qua template i18n có tham số | — |
| Ghi nhớ | Tóm tắt phiên sau ~6 lượt, sở thích khách, gợi ý chủ động (chỉ gợi ý, không ghi) | `agent/memory/*`, `agent/proactive.py` |
| Multi-agent (**P1**, chỉ khi benchmark chứng minh lợi ích — xem "Chiến lược agent") | Điều phối → agent hỏi–đáp (tool đọc) / agent dịch vụ (tool ghi + HITL) / chuyển người | `langgraph_loop.py`, checkpoint SQLite |

**Ranh giới agent ↔ môi trường** (theo cách τ-bench mô hình hóa):
- **Môi trường** (deterministic, agent không đổi được): máy trạng thái + SLA (`domain/`); policy cần xác nhận/xác minh/duyệt; HITL `interrupt()`; giữ chỗ; mã xác nhận; phát hiện khẩn cấp; kiểm grounding; lọc PII.
- **Tool trả quan sát có cấu trúc**: `ok`, `needs_slot(field)`, `needs_confirmation(summary)`, `needs_verification`, `pending_staff`, `unavailable(alternatives)`, `denied(reason)`, `error(hint)`. Agent phải phản ứng đúng với các ràng buộc này, được chấm như "tuân thủ policy".
- **Cấm** (dấu hiệu của workflow):
  - cây hội thoại theo dịch vụ;
  - thứ tự câu hỏi viết sẵn;
  - bảng intent → handler;
  - router regex chọn nhánh;
  - code kiểu "nếu còn chỗ thì hỏi xác nhận" trong `engine.py`;
  - câu trả lời soạn sẵn theo tình huống.
  
  `engine.py` chỉ còn: khẩn cấp → hiểu → vòng agent → trả lời.
- **Giới hạn cho CPU** ("governed agent"):
  - đầu ra JSON schema, tập lệnh/tool đóng, ngân sách bước;
  - mỗi lượt **tối đa 1 lần gọi SLM để hiểu câu**;
  - planner chỉ gọi khi có ≥ 2 mục tiêu phụ thuộc hoặc khi tool lỗi;
  - agent con là đồ thị con chia theo tập tool, không phải mỗi agent một lần gọi model.
- **Khẩn cấp bất đối xứng**: luật bắt khẩn cấp chạy trước. Model được *nâng* một lượt lên khẩn cấp, không bao giờ được *hạ*.
- `classify_dialogue` chỉ còn làm dự phòng khi SLM lỗi/quá hạn, rồi bị xóa khi số đo cho phép.

**Hợp đồng bắt buộc giữa agent và hệ thống**

1. `Command`: `command_id`, `intent`, `service_code?`, `slots`, `condition?`, `language`, `confidence`, `source_turn_id`.
2. `ToolObservation`: `status`, `tool_name`, `request_id?`, `evidence_ids[]`, `missing_slots[]`, `alternatives[]`, `retryable`, `safe_to_speak`, `redaction_level`.
3. `ActionRequest`: `request_id`, `idempotency_key`, `session_id`, `guest_confirmation_id`, `verification_id?`, `policy_snapshot`, `state`, `created_at`, `expires_at`.
4. `AnswerCard`: `kind=answer|clarification|request_status|handoff|emergency`, `spoken_text`, `display_blocks`, `evidence_ids[]`, `next_action`, `sensitivity`.

Các hợp đồng này phải có JSON Schema và version. Agent không được tự sửa `state`, `policy_snapshot`, `evidence_ids` hoặc `request_id`; chỉ domain/workflow service được phép ghi. Lỗi schema là lỗi không retry và phải chuyển fallback an toàn.

**Chiến lược agent**: P0 dùng **một governed agent** với tool registry đóng. Specialist subgraph chỉ được thêm khi một benchmark chứng minh lợi ích rõ ràng về chất lượng hoặc latency; không coi “multi-agent chạy được” là tiêu chí bắt buộc của bản đầu. Mỗi specialist phải dùng chung `Command`, `ToolObservation`, budget, trace và policy boundary.

### 4.2 Nghiệp vụ và vận hành
Đây là **hành vi của môi trường**. Agent chỉ thấy qua quan sát của tool.

**Vòng đời yêu cầu** (thêm trạng thái `awaiting_guest_reconfirm`):
```
nháp → chờ khách xác nhận → [xác minh phòng] → chờ nhân viên duyệt
   ├─ duyệt → đang xử lý → hoàn tất (ghi chú đóng, mời đánh giá)
   ├─ từ chối (bắt buộc lý do) → báo khách + phương án khác
   └─ đề xuất thay đổi → chờ khách xác nhận lại → đồng ý / hết hạn → hủy, nhả chỗ
khách hủy/sửa (guest_change) · quá hạn SLA → leo thang 2 cấp
```

**Chuẩn nghiệp vụ** — SLA tách tiếp nhận/xử lý theo mức ưu tiên (khẩn: tiếp nhận ≤ 15 phút):
- **Bộ phận**: theo `department_id` trong dữ liệu (Housekeeping, Engineering, F&B, Concierge, Front Office, Spa, Transport, Security).
- **Nguồn dữ liệu đặt chỗ** (slot/giá/chỗ/chính sách đều lấy từ dữ liệu):
  - nhà hàng: `restaurants.json`;
  - tour: `tours/products.json`, `operations.json`;
  - xe: `transport/products.json`, `shuttle_schedule.json`;
  - spa: `spa/operations.json`;
  - room service: `room_service_menu.json`, `minibar.json`;
  - late checkout: `rooms/inventory.jsonl` + phí `billing/charge_rules.json`;
  - housekeeping: `housekeeping/catalog.json` + DND.
- **Xác minh**: QR thẻ phòng (đã có) hoặc phòng + họ đối chiếu `pms/stays.jsonl`. Chưa xác minh thì chỉ hỏi đáp.
- **Thanh toán**: không nhận thẻ; chỉ "tính vào phòng" hoặc "trả tại quầy". Tóm tắt trước khi xác nhận luôn có phí/giá kèm nhãn demo theo dữ liệu.
- **Biên giới integration**: P0 chỉ triển khai `OperationsPort` + simulator deterministic từ `datasets/synthetic/operations/`. Adapter PMS/booking/payment thật chỉ là interface stub có `NotConfigured`, không được để credential hoặc network call ngầm lọt vào tool. Khi sau này nối hệ thống thật, mỗi adapter phải có contract test, timeout, circuit breaker, reconciliation và quyền riêng.
- **Dữ liệu cá nhân** ([Nghị định 13/2023](https://vietnamlawmagazine.vn/conditions-for-consent-under-decree-13-on-personal-data-protection-70818.html)):
  - xin đồng ý theo từng mục đích trước khi thu tên/SĐT/phòng (im lặng không phải đồng ý);
  - lưu bản ghi đồng ý, in được;
  - thu tối thiểu, có hạn lưu, xóa theo yêu cầu;
  - không lưu audio.
- **Khẩn cấp**: SOP cố định, không qua model (đã có).

**Ma trận quyền thực thi**

| Loại hành động | Guest confirm | Xác minh phòng | Staff review | Offline |
|---|---|---|---|---|
| Hỏi đáp/RAG | Không | Không | Không | Có |
| Ghi yêu cầu reversible (khăn, housekeeping) | Có | Theo property policy | Mặc định có; chỉ auto-fulfill khi profile cho phép | Queue cục bộ, chưa tuyên bố đã hoàn tất |
| Đặt chỗ/xe/tour/spa/charge/late checkout | Có | Có | Có | P0: bộ mô phỏng `OperationsPort` chạy cục bộ nên kiểm chỗ/giữ chỗ vẫn chạy khi mất mạng. `pending_sync` chỉ áp dụng khi nối adapter hệ thống thật (sau P0); khi đó không giữ chỗ thật lúc offline |
| Emergency | Không chờ model/xác nhận | Không chặn cảnh báo | Theo SOP người trực | Gọi fallback cục bộ, hiển thị hướng dẫn rõ ràng |

Như vậy “HITL cho mọi yêu cầu dịch vụ” nghĩa là **mọi write đều có đường audit và policy**, còn một số low-risk chỉ được tự thực thi sau guest confirmation khi `property_profile.json` bật rõ. Không được quyết định low-risk bằng tên service hardcode. Mọi queue offline phải có TTL, idempotency, retry backoff và màn hình nói rõ “đã tiếp nhận/chờ đồng bộ”, không nói “đã đặt”.

**Tình huống vận hành** (kiosk dùng chung ở sảnh, khách sẽ rời đi):

| Tình huống | Cách xử lý |
|---|---|
| Khách rời kiosk khi chờ duyệt (hiện trạng thái gắn cookie phiên) | **Mã xác nhận + QR** mở trang trạng thái (mạng nội bộ, token theo yêu cầu, không PII); kiosk tra bằng mã + xác minh phòng; báo "thường duyệt trong X phút" theo SLA |
| Nhân viên đề xuất thay đổi | `awaiting_guest_reconfirm`, có hạn, quá hạn thì hủy và nhả chỗ |
| Hai khách cùng đặt chỗ cuối | Giữ chỗ tạm khi khách xác nhận, hết hạn khi bị từ chối/quá hạn |
| Nhân viên đọc tiếng Hàn/Trung | Slot có cấu trúc đã trung lập ngôn ngữ; ghi chú tự do hiển thị nguyên văn + bản dịch máy (gắn nhãn) |
| Yêu cầu trùng | Kiểm theo dịch vụ + phòng trong `dedupe_window_minutes`; trùng thì trả yêu cầu cũ + trạng thái |
| Ngoài giờ phục vụ | Báo khi nào được phục vụ (`response_windows_local`) trước khi khách xác nhận |
| Khách không lưu trú | Chỉ hỏi đáp; dịch vụ cần phòng thì chuyển lễ tân |
| Chuyển nhân viên | Phiếu bàn giao kèm tóm tắt (đã lọc PII), nói khách đi đâu/ai tới; nút gọi nhân viên |
| Riêng tư màn hình | Tự reset sau khi xong/hết giờ chờ; che số phòng |
| "Bao giờ có?" | Trả lời theo `ack_due_at`/`sla_due_at`, bằng giọng |

### 4.3 RAG
- **Vector DB thật — Chroma embedded** (FAISS là phương án thay thế cùng giao diện):
  - **Giao diện**: `rag/vectorstore/{base,chroma,faiss}.py` (`upsert`, `query(vector, k, filters)`, `delete_release`, `stats`); chọn bằng `retrieval.dense_backend: chroma|faiss`.
  - **Cấu hình Chroma**: `PersistentClient` ở `data/vectors/`, cosine, HNSW; truyền embedding bge-m3 tự tính (không dùng hàm embedding mặc định vì nó tải model qua mạng); `anonymized_telemetry=False`.
  - **Metadata lọc**: `property_id`, `language`, `classification`, `active`, `effective_from/to` (yyyymmdd), `embedding_model`, `entity_id`, `fact_type`, `fact_context`, `revision`, `release_version`.
  - **Nhất quán**: SQLite là nguồn sự thật. Collection dựng theo `release_version`, chỉ dùng sau khi SQLite commit. Vector chỉ trả id; nội dung/citation/hiệu lực đọc từ SQLite. `/readyz` báo lệch.
  - **Luồng truy vấn**: structured → FTS + dense (có lọc) → RRF → rerank → conflict/grounding.
  - **Dọn**: xóa đường quét vector JSON (`decoded_embedding`, cột `embedding`) khi Chroma đạt chỉ tiêu.
- **Spike trước khi chốt backend**: trên cùng corpus và profile edge đo cold start, RSS, kích thước index, p95 query, metadata filter, rebuild sau OTA và khả năng arm64. Chroma chỉ là mặc định sau khi pass; nếu không pass thì giữ FTS + backend nhẹ hơn (FAISS/sqlite-vec) qua cùng `VectorStore`, không để lựa chọn thư viện chặn P0.
- **Freshness và release**: mỗi chunk có `source_uri`, `source_hash`, `review_status`, `effective_at`, `expires_at`, `release_version`. Fact hết hạn hoặc pending review bị loại khỏi answer path, nhưng vẫn giữ trong audit. Ingest phải atomic: validate → build index → consistency check → activate pointer → rollback pointer khi fail.
- **Sửa chỗ yếu**: lỗi "sai ngữ cảnh" (context label/alias), tiếng Trung, câu ghép (qua RAG chủ động §4.1).
- **Guardrail grounding**:
  - bộ kiểm claim–evidence học được (MiniCheck/AlignScore) chạy online **chỉ cho câu do model viết lại**; câu trích xuất + template thì dùng làm thước đo offline;
  - mọi số/giờ/giá phải có trong evidence (chuẩn hóa qua `voice/runtime/rendering.py`);
  - giá synthetic luôn có nhãn;
  - không làm danh sách từ khóa "giá/giờ".

### 4.4 Voice
- **Pipeline**:
  - bật/kiểm `enable_rtvi` để client nhận transcript, sự kiện, answer card;
  - resample Piper 22,05 kHz → 16 kHz;
  - Silero VAD + smart-turn chạy offline, kiểm trong `/readyz`.
- **Ngắt lượt**: smart-turn v3.2 (model âm thanh, có tiếng Việt). Chỉ chỉnh `voice_vad_stop_secs` / `voice_user_turn_stop_timeout_seconds`; kém thì thử bản mới hoặc fine-tune, không thêm luật từ nối.
- **STT theo ngôn ngữ** qua `voice_stt_models` (đã hỗ trợ khóa ngôn ngữ):
  - vi: whisper-small vs PhoWhisper;
  - zh/ko/en: whisper-small vs SenseVoice-Small (cần thêm backend, ví dụ sherpa-onnx);
  - bỏ `CONCIERGE_WHISPER_MODEL_PATH` trong `.env.example` vì nó ghi đè mọi ngôn ngữ;
  - prompt từ vựng khách sạn sinh từ dữ liệu (đã có).
- **Giảm độ trễ**, theo thứ tự tác động:
  1. STT giải mã tăng dần trong lúc khách nói (phần còn lại ≤ 0,6 s);
  2. lệnh SLM ≤ 25 token theo JSON schema, phần đầu prompt cố định để trúng cache, `keep_alive` + làm ấm;
  3. model nhỏ fine-tune LoRA (0.5B/1.5B) cho bước sinh lệnh;
  4. `hotel_info_search` chạy suy đoán song song với bước hiểu câu (tool đọc không tác dụng phụ);
  5. phát chunk đầu sớm + câu đệm ở 0,7 s (đã có);
  6. semantic router chỉ làm đường tắt khi precision ≥ 95%.
- **Barge-in**: Pipecat ghi rõ WebSocket không có khử vọng. Đo tỷ lệ ngắt nhầm trên kiosk với: (a) WebSocket hiện tại; (b) `SmallWebRTCTransport` tự host; (c) mic có AEC phần cứng. Cả ba kém thì khi bot đang phát chỉ cho ngắt bằng nút.

**Fail-safe voice**

- Không coi transcript partial là ý định cuối; chỉ commit turn khi smart-turn/VAD kết thúc hoặc khách bấm gửi.
- Khi STT confidence thấp, ngôn ngữ không chắc, audio méo hoặc model quá hạn: hiển thị transcript để khách sửa, chuyển sang push-to-talk/text hoặc gọi nhân viên; không gọi write tool.
- TTS phải được hủy idempotently khi barge-in; mỗi turn có `audio_generation_id` để audio cũ không phát sau câu mới.
- Audio tổng hợp dùng cho regression phải ghi model/voice/seed/manifest; file audio thật (nếu có) lưu ngoài repo, mã hóa, có consent và retention riêng.

### 4.5 Web nhân viên/quản trị (React)
`frontend/src/staff/`, entry `/staff`, build bằng `build_offline.cjs`, đủ 4 ngôn ngữ, mặc định tiếng Việt. Thay `/ops`.
- **Đăng nhập** tài khoản + mật khẩu băm → token ngắn hạn theo scope (thêm scope `admin:*`).
- **Nhân viên trực**:
  - hàng chờ theo bộ phận + đồng hồ SLA + cảnh báo leo thang;
  - **màn duyệt HITL** (tóm tắt, slot, nguồn, ghi chú + bản dịch máy; duyệt / đề xuất thay đổi / từ chối kèm lý do);
  - nhận/giao việc, hoàn tất kèm ghi chú đóng;
  - lịch đặt chỗ giả lập;
  - khẩn cấp đặt trên cùng, có âm báo;
  - chi tiết + audit + phản hồi khách.
- **Quản trị**: KPI có biểu đồ; phát hành cẩm nang OTA; tài khoản/vai trò; bản ghi đồng ý.
- Cập nhật thời gian thực bằng SSE/poll ngắn.

**API và security contract**

- Guest, staff và admin dùng audience/scope khác nhau; staff không được dùng cookie guest để duyệt request.
- Mọi mutation yêu cầu `request_id` + idempotency key; trả `202 pending` khi queue/HITL, không giả `200 completed`.
- Endpoint trạng thái qua mã/QR chỉ trả dữ liệu tối thiểu, token ngắn hạn, one-time hoặc có TTL; không nhúng số phòng, tên, transcript vào QR.
- SSE/poll chỉ phát event đã lọc theo tenant/property/session; reconnect phải bắt đầu từ cursor và không tạo duplicate action.
- Audit lưu actor, scope, before/after state, policy version, evidence ids và reason; secret/token/transcript/room number phải redacted.
- Các thao tác admin (OTA, đổi policy, xoá dữ liệu, cấp credential) cần re-auth và audit riêng.

### 4.6 Kết nối mạng: nhận biết lúc chạy, không cố định
Hiện code chỉ phản ứng theo từng lần gọi (adapter upstream lỗi → `pending_sync` trong `integrations/hotel_ops.py`). Chưa có trạng thái mạng chung, và nhiều chỗ trong plan/tài liệu ngầm giả định "luôn offline". Thiết kế đích:

- **Trạng thái mạng là dữ liệu lúc chạy, không phải hằng số**: một `ConnectivityMonitor` thăm dò định kỳ các endpoint khai trong runtime profile (danh sách và chu kỳ nằm trong config, không viết cứng). Kết quả là `online | degraded | offline` theo **từng đích** (upstream ops, máy chủ OTA, gateway nhân viên…), có độ trễ chuyển trạng thái (hysteresis) để không chập chờn. Công bố qua `/readyz` và hiển thị trên web nhân viên.
- **Mỗi chức năng tự khai mức phụ thuộc mạng** trong config: `network: none | optional | required` + hành vi khi thiếu mạng (`queue`, `pending_sync`, `disable_with_notice`). Code đọc khai báo này; **không rải `if offline` trong code**.
  - `none`: hỏi đáp RAG, SLM, STT/TTS, khẩn cấp hiển thị tại chỗ, bộ mô phỏng `OperationsPort`. Luôn chạy, có mạng hay không.
  - `optional`: báo khẩn cấp ra ngoài, đồng bộ yêu cầu lên upstream, OTA kéo cẩm nang, telemetry. Có mạng thì làm ngay; mất mạng thì vào outbox bền vững (idempotency, TTL, retry backoff) và tự đẩy khi có mạng lại.
  - `required`: adapter hệ thống thật khi được bật. Mất mạng thì trả `pending_sync`/`unavailable` kèm thông báo đúng, không giả thành công.
- **Hai lớp cùng chạy**: monitor cho biết trạng thái *dự kiến* để UI/agent nói trước ("đang mất kết nối, yêu cầu sẽ được gửi khi có mạng"); lỗi thực tế của từng lần gọi vẫn là nguồn sự thật (giữ hành vi `pending_sync` hiện có).
- **Agent thấy mạng qua quan sát của tool**, không qua code rẽ nhánh: tool trả `queued(reason=offline)` hoặc `unavailable(retry_after)`, agent tự chọn cách nói/đề nghị.
- **Provider từ xa là tùy chọn có kiểm soát**: mặc định mọi model (SLM, embedding, STT, TTS) chạy cục bộ và endpoint model chỉ loopback như hiện tại. Nếu sau này muốn dùng provider qua mạng thì phải bật rõ trong config + đánh giá quyền riêng tư, luôn có fallback cục bộ khi mất mạng. Không đổi provider ngầm theo trạng thái mạng.
- **Test** (trên laptop, bật/tắt Wi-Fi):
  1. khởi động có mạng;
  2. khởi động không mạng;
  3. mất mạng giữa lúc tạo yêu cầu (vào outbox, không trùng);
  4. có mạng lại (outbox tự đồng bộ đúng một lần, trạng thái cập nhật);
  5. mạng chập chờn (hysteresis, không spam retry);
  6. khẩn cấp lúc mất mạng (vẫn hiển thị và lưu, đẩy ra ngoài khi có mạng).

### 4.7 Phần cứng: dò lúc chạy, không cố định theo máy
- **Dò phần cứng khi khởi động**: mở rộng `tools/runtime/preflight.py` + `/readyz` để báo số nhân/luồng CPU, RAM trống, có GPU không, hệ điều hành/kiến trúc (x86_64/arm64), mic/loa (phía trình duyệt). Kết quả dùng để chọn **hồ sơ phần cứng**.
- **Hồ sơ phần cứng là dữ liệu trong config**, không viết trong code: ví dụ `hardware_tiers` trong runtime profile, mỗi mức khai ngưỡng RAM/CPU tối thiểu và lựa chọn tương ứng:
  - model SLM (3B/1.5B/0.5B);
  - model STT;
  - có bật reranker không, `rerank_top_k`;
  - số luồng của Whisper/ONNX/OpenVINO;
  - `num_ctx`.
  
  Máy yếu tự hạ mức; operator có thể ép một mức cụ thể bằng env.
- **Số luồng và bộ nhớ tính theo máy**: thread = theo số nhân dò được (có trần trong config), không ghi số cố định. Giữ `stt_threads_max` và các trần hiện có nhưng mặc định là "tự động".
- **GPU là tùy chọn**: dùng khi có và config cho phép; mất GPU hoặc không có thì chạy CPU, không lỗi. Chỉ tiêu nghiệm thu đo ở chế độ CPU để công bằng giữa các máy.
- **Không phụ thuộc hệ điều hành**: đường dẫn qua `pathlib`; probe/test dùng Python thay cho lệnh shell riêng của OS (`taskkill`, `kill`…); script hướng dẫn có bản cho Windows và Linux/macOS. Có lỗi riêng của Windows đã gặp (`Path("ollama://…")`) thì sửa trong code chứ không ghi chú cho riêng máy.
- **Yêu cầu tối thiểu được đo và ghi trong `README`**: RAM tối thiểu cho từng mức (cộng dung lượng model đang nạp: SLM, bge-m3, reranker, Whisper, Piper), dung lượng đĩa, Python ≥ 3.11, Ollama.
- **Report luôn kèm thông tin máy** (CPU, RAM, OS, GPU dùng hay không, mức phần cứng) để so sánh được giữa các máy.
- **Kiểm**:
  - CI chạy test trên cả Windows và Linux;
  - một lần chạy với giới hạn tài nguyên thấp (`compose.jetson-sim.yaml` hoặc giới hạn luồng/RAM) để chứng minh hồ sơ thấp vẫn chạy đúng.

---

## 5. Nguyên tắc
- **Không cố định theo máy**: chạy được trên laptop/PC phổ thông bất kỳ; cấu hình theo phần cứng dò lúc chạy (§4.7), không ghi số luồng/model/đường dẫn/lệnh riêng của một máy hay một hệ điều hành.
- **Không cố định trạng thái mạng**: không giả định luôn có hay luôn mất mạng; hành vi theo §4.6 (trạng thái lúc chạy + khai báo mức phụ thuộc mạng trong config).
- **Không hardcode theo trường hợp**: không thêm regex, danh sách cụm từ, `if language == …` để vá một ca. Thứ tự sửa hợp lệ:
  1. thành phần học được, tổng quát;
  2. sửa dữ liệu (canonical, alias, ví dụ huấn luyện trong `datasets/training/`; không copy từ `datasets/evaluation/`);
  3. tham số cấu hình đo được.
  
  Code deterministic chỉ cho bất biến an toàn và kiểm tra tổng quát.
- **Code sạch**:
  - module > ~400 dòng hoặc gom > 1 việc thì tách;
  - code không dùng thì xóa ngay trong giai đoạn thay thế, cùng test/tài liệu;
  - shim không sống quá một giai đoạn; cờ gỡ khi giá trị mới thành mặc định;
  - CI có `tests/test_module_size.py` (allowlist chỉ được thu nhỏ), kiểm module mồ côi, `vulture`, `ruff`, báo script `tools/` không ai dùng;
  - mỗi giai đoạn kết thúc bằng bước dọn.
- **Đo trước/sau** mọi thay đổi. Thay đổi lớn đi sau cờ (`voice_transport`, `understanding_mode`, `hitl_mode`, `retrieval.dense_backend`).
- **An toàn**: khẩn cấp deterministic chạy trước; ghi chỉ qua workflow service; câu "không có bằng chứng" là template; model không tự sinh giá/giờ.
- **Làm việc**:
  - commit nhỏ, không `git checkout` nhánh khác;
  - không sửa `datasets/` trừ khi số đo chỉ ra thiếu (khi đó chạy `validate_contracts.py` + cập nhật manifest);
  - audio/báo cáo để ở `reports/`;
  - mỗi giai đoạn chỉ một bên (Codex hoặc Claude) sửa; xem `git status` trước lệnh ghi nhiều file.
- **Promote có điều kiện**:
  - giữ `legacy/shadow` khi thay NLU, voice transport hoặc retrieval; so sánh cùng input và cùng trace trước khi bật mặc định;
  - không promote nếu P0 safety gate fail, nếu một ngôn ngữ tụt quá 5 điểm phần trăm, hoặc p95 tăng quá 20% mà chưa có quyết định chấp nhận;
  - mỗi feature flag có owner, ngày tạo, metric thành công, rollback và ngày xóa; flag quá một phase phải được review;
  - không xóa file/route chỉ vì “không còn thấy dùng”: phải chứng minh bằng import scan, test coverage, runtime smoke và docs scan.
- **Không nhầm prototype với production**: synthetic availability/price/SLA chỉ là mô phỏng. UI, voice và report phải hiển thị `demo`/`synthetic` ở nơi phù hợp; chỉ tool được ủy quyền mới được nói “đã đặt/đã xác nhận”.

---

## 6. Lộ trình
Thứ tự: GĐ0 → GĐ1 → GĐ2 → đo lại voice → GĐ3 → GĐ4 → GĐ5 → GĐ6.

### 6.0 Cổng chuyển pha

| Cổng | Điều kiện vào | Artifact bắt buộc | Điều kiện ra / quyết định |
|---|---|---|---|
| G0 | Worktree đã ghi nhận; biết model/config/dataset hiện dùng | baseline manifest, data-contract report, test inventory, rollback note | `pass` mới được thay NLU/agent; `blocked` thì chỉ sửa contract |
| G1 | Voice baseline tái lập được | voice latency/ASR report trên **1.162 WAV tổng hợp**; barge-in thử bằng **loa + mic laptop** (Chrome, WebSocket vs `SmallWebRTCTransport`); tùy chọn tự ghi âm giọng mình ~20 câu/ngôn ngữ | đủ p50/p95 hoặc ghi rõ fallback push-to-talk |
| G2 | Command ontology và tool contract đã ổn | hotel-bench report, trajectory traces, tool/policy report | policy violation = 0; pass^5 không thấp hơn baseline |
| G3 | Workflow state machine và auth contract ổn | HITL E2E, API/security report, redacted audit samples | mọi write có confirm/idempotency; staff xử lý được queue |
| G4 | Canonical release và metadata ổn | retrieval/grounding report, index consistency, privacy scan | unsupported price/time = 0; rollback index được |
| G5 | Gói runtime reproducible | Đo trên laptop ép CPU, giới hạn bằng `compose.jetson-sim.yaml` (6 CPU/8 GB); chạy 6 test mạng ở §4.6 (có mạng, không mạng, mất/có lại giữa phiên, chập chờn, khẩn cấp lúc mất mạng) bằng bật/tắt mạng; **dừng cưỡng bức tiến trình** giữa lúc ghi yêu cầu thay cho rút điện, làm bằng probe Python (`os.kill`/`Process.kill`) để chạy được trên mọi hệ điều hành, không dùng lệnh riêng của từng OS; OTA qua repository TUF **cục bộ** (fixture tự ký) | boot/restart/mất mạng an toàn trong budget |
| G6 | Tất cả gate trước đã pass | demo matrix trên laptop, known limitations, signoff do chính người làm đồ án ký (một người kiêm 4 vai trò, §0.4) | bản demo trên laptop hoặc quay về phase lỗi |

Mỗi gate có ba trạng thái `pass`, `pass_with_risk`, `blocked`. `pass_with_risk` chỉ dành cho P1 và phải có người chấp nhận rủi ro; không dùng để bỏ qua P0.

### GĐ0 — Nền và đo baseline
1. Chụp baseline an toàn: lưu `git status`, commit SHA, runtime profile, dataset/release hash và các file đang sửa vào `reports/gates/gd0-worktree.json`; không reset hoặc commit thay đổi không thuộc phase.
2. Xử lý hai blocker parity/mapping ở §2.1. Chạy `datasets/schemas/validate_contracts.py`, semantic/domain validators và tạo `reports/gates/gd0-data-contract.json`.
3. Sửa 3 import (`voice/agent/speech_gate.py`, `agent/understanding/semantic_router.py`, `agent/understanding/semantic.py`) rồi **chỉ xóa** shim `rag/common.py`, `rag/claims.py`, `voice/session/speech_text.py` và script mồ côi nếu import scan + test chứng minh không còn consumer. Không xóa các file đang có thay đổi trong worktree.
4. Thêm kiểm tra code sạch vào CI (§5).
5. **Bộ audio tổng hợp** `tools/evaluation/synthesize_voice_set.py`:
   - nguồn: 350 câu vi từ `voice_text/vi_asr_robustness.jsonl`; en/zh/ko từ `challenges/natural.jsonl`, `end_to_end/service_actions.jsonl`, `end_to_end/scenarios/production.jsonl` (`gold/*` chỉ có vi);
   - đầu ra WAV 16 kHz + manifest (`synthetic: true`) ở `reports/voice-set/`.
6. **Client headless**: mở rộng `tools/evaluation/run_voice_eval.py` để chọn transport (`/api/audio/stream` legacy hoặc `/api/voice/agent` Pipecat), phát WAV và đo từng chặng, WER/CER, route/tool đúng, ghi sai.
7. **KPI** trong `runtime/metrics.py`: grounded rate, faithfulness, abstain đúng, intent, ngôn ngữ (GlotLID), p50/p95 từng chặng, ghi sai.
8. Chạy baseline (ép CPU) → `reports/voice-eval/baseline.json`; nếu voice chưa khởi động được thì report phải là `blocked`, không tạo số 0 giả.

**Xong khi**: có baseline 4 ngôn ngữ với số đo từng chặng.

### GĐ1 — Đường ống voice (§4.4)
1. Pipeline: RTVI, resample, offline VAD/smart-turn.
2. Ngắt lượt.
3. STT theo ngôn ngữ.
4. STT giải mã tăng dần.
5. Barge-in.
6. Các kỹ thuật giảm độ trễ thuộc phần voice (1, 5).
7. **Dọn**: gỡ voice legacy (`api/voice/streaming.py`, `capture.py`, `recognition.py`, `voice/session/partials.py`, `incremental.py`, `frontend/src/continuousVoice.ts`, `web/pcm-worklet.js`) **sau khi đo lại sau GĐ2**. Route `/api/audio/speak|proof|played` chỉ gỡ khi `frontend/src/api.ts::speakChunk` hết dùng. Tách `voice/session/turns.py` (577 dòng); gỡ cờ `voice_transport`.

**Xong khi**: đường ống voice đạt WER và độ trễ phần ống trên bộ audio; đã chọn được cấu hình barge-in.

### GĐ2 — Lõi AI agent (§4.1) + tool giả lập
1. **Hotel-bench kiểu τ-bench** trên nền `agent/runtime/eval/harness.py` + `tools/run_e2e_cases.py`:
   - user mô phỏng bằng SLM local từ `end_to_end/simulation/`;
   - mục tiêu từ `service_actions.jsonl` (`preconditions`/`acceptable_tools`) + journeys + ASR;
   - chấm trạng thái DB cuối, vi phạm policy, ghi sai, **pass^k**;
   - sửa `run_tool_eval.py` dùng `preconditions`/`acceptable_tools`.
2. **Tool giả lập từ dữ liệu synthetic**:
   - đăng ký file trong `core/dataset_layout.py`; bộ nạp chung gắn nhãn theo trường dữ liệu;
   - tool đọc: chỗ trống nhà hàng/tour/spa/xe, thực đơn, báo phí, trạng thái phòng/DND, xác minh PMS;
   - tool ghi qua workflow + policy + HITL, giữ chỗ trong bộ nhớ mô phỏng, có mã xác nhận;
   - kiểm bằng `tools/validate_synthetic_operations.py`.
3. **Quan sát chuẩn của tool** (§4.1) trong `tool_contracts.py` + registry.
4. **Thay khâu hiểu câu và dựng mục tiêu**: chọn dịch vụ bằng ngữ nghĩa; nhiều ý; slot verbatim; `_goal_requirements` dựng từ `Command`. Khớp từ khóa chỉ chạy khi SLM lỗi, xóa khi hotel-bench không tụt.
5. **Chọn SLM** (ép CPU): 3B prompt vs 1.5B vs 0.5B vs LoRA 0.5B/1.5B; so Command JSON với tool-calling gốc của Ollama. Chỉ tiêu ≥ 90% trên bộ robustness 2.520 câu + hotel-bench, p95 ≤ 1,3 s.
6. **Mọi lượt không khẩn cấp qua vòng agent**: xóa định tuyến nhánh trong `engine.py`; RAG chủ động; hỏi lại từ schema; ghi nhớ; governed single-agent dùng chung cho text/voice. Chỉ thử specialist subgraph nếu có benchmark trước/sau chứng minh lợi ích.
7. Đổi mặc định sang chế độ agent khi đạt chỉ tiêu; chuyển `state.py`/`runtime.py` sang `Command`.
8. **Dọn**:
   - xóa nhánh legacy, cờ `understanding_mode`, `model_intent.py`, `turn_plan.py`, `commands_from_turn_plan` + test;
   - tách `engine.py` (→ `turn_runtime.py`, `understanding.py`, `contract_guard.py`, `build.py`), `answers.py` (761), `service_actions.py` (762), `state.py` (595), `planner.py` (488), `memory/conversation.py` (558), `concierge.py` (414).
9. **Đo lại voice E2E** với lõi mới (mục tiêu p50 ≤ 2,5 s, p95 ≤ 4 s).

**Xong khi**:
- hotel-bench pass^k không kém `legacy`; ghi sai = 0; vi phạm policy = 0;
- `engine.py` không còn định tuyến nhánh;
- cùng một Command/tool/policy contract chạy cho cả text và voice; multi-agent nếu có phải là tối ưu P1, không phải điều kiện P0;
- voice đạt chỉ tiêu độ trễ, **hoặc** có fallback push-to-talk/text đã ghi rõ giới hạn (giống cổng G1); tối ưu tiếp là P1.

### GĐ3 — Nghiệp vụ, HITL, web nhân viên (§4.2, §4.5)
1. Mã xác nhận; thông báo trạng thái; trả lời "bao giờ có"; đồng ý dữ liệu.
2. Vận hành khi khách rời kiosk: QR trang trạng thái, tra bằng mã, `awaiting_guest_reconfirm`, giữ chỗ, trùng, phí, khung giờ, dịch cho nhân viên, reset kiosk.
3. HITL cho mọi dịch vụ:
   - thêm `hitl_mode` (mặc định `guest_confirm_all`);
   - 3 dịch vụ low-risk đi qua `guest_confirmation`;
   - sửa `application/service_actions.py`;
   - bỏ ghi cứng `low_risk_requires_verified_room` trong `tools/build_furama_releases.py` (đọc từ `property_profile.json`).
4. Vai trò + đăng nhập (`api/shared/auth.py`, dựa trên `tools/operations/create_staff_credentials.py`).
5. Web nhân viên React; chuỗi UI đủ 4 `locales/*.json`.
6. **Dọn**: xóa `web/ops.html`, `web/ops.js`; tách `main.py` (542), `core/settings.py` (735), `domain/requests/submissions.py` (580), `staff_ops.py` (454), `core/operational_policy.py` (458), `frontend/src/App.tsx`.
7. Thêm contract/API tests cho `202 pending`, reconnect SSE, auth scope, replay idempotency, expiry của QR/status token và redaction audit trước khi dọn UI cũ.

**Xong khi**:
- mọi yêu cầu qua xác nhận của khách (và duyệt với dịch vụ cần duyệt), có mã + bản ghi đồng ý;
- nhân viên đăng nhập và duyệt HITL trên web React tiếng Việt.

### GĐ4 — RAG + guardrail (§4.3)
1. Chạy storage spike theo §4.3; chọn Chroma/FAISS/sqlite-vec qua `VectorStore`; đo R@k/độ trễ không tụt; `pip_audit` + `bandit`; kiểm tra arm64.
2. Sửa chỗ yếu RAG.
3. **Eval hallucination bằng dữ liệu có sẵn**: `gold/vi_hard_negatives.jsonl`, `challenges/natural.jsonl`, `end_to_end/edge_cases.jsonl`, nhóm `knowledge_abstain`/`policy_guard`, `retrieval/grounded_fact_holdout.jsonl`.
4. Guardrail grounding.
5. KPI online lên web quản trị.
6. Riêng tư: lọc PII khỏi log/metric, `tools/maintenance/purge.py` theo lịch, sao lưu mã hóa.
7. **Dọn**: xóa đường quét vector JSON; xóa kiểm tra theo cụm từ được bộ kiểm claim thay thế (kèm mục allowlist); tách `agent/tools/scheduling.py` (475), `agent/understanding/intent.py` (462).

**Xong khi**:
- giá/giờ bịa = 0, giá synthetic luôn có nhãn;
- faithfulness và abstain không tụt;
- log/metric không còn PII.

### GĐ5 — Edge, OTA, mất mạng
1. `deploy/edge/compose.jetson-sim.yaml`:
   - giới hạn ~6 CPU / 8 GB để **đo**;
   - arm64 qua QEMU **chỉ kiểm tương thích**;
   - không mô phỏng GPU.
2. (**P1**) OTA cẩm nang theo **TUF** (`python-tuf`): kéo khi online, kiểm chữ ký, chống rollback (cùng `release_version`) và freeze (timestamp hết hạn), ingest nguyên tử.
3. Kiểm khi rút mạng: hỏi đáp canonical, đặt chỗ qua bộ mô phỏng cục bộ, voice, khẩn cấp; với adapter upstream thật (sau P0) thì không nói đã giữ chỗ/đặt thành công khi chưa đồng bộ.
4. **Dọn**: gom `tools/` thành `build/`, `validate/`, `manifest/`, `release/` (sửa CI, `CLAUDE.md`, import trong test); allowlist kích thước về rỗng hoặc có lý do.

**Xong khi**:
- đạt chỉ tiêu trong giới hạn tài nguyên kiểu Jetson;
- OTA có test;
- restart/mất mạng không mất request, không tạo duplicate và không báo sai trạng thái; chức năng phụ thuộc upstream phải hiện `pending_sync` rõ ràng.

### GĐ6 — Nghiệm thu
1. Hành trình §1.4 và các biến thể, bằng giọng, 4 ngôn ngữ.
2. KPI sản phẩm: tỷ lệ tự hoàn thành, số lượt hỏi lại, thời gian từ câu đầu đến mã xác nhận.
3. Demo thêm:
   - giá spa (grounded);
   - chỉ đường + bản đồ;
   - mang khăn (đọc lại số);
   - câu ngoài phạm vi → chuyển nhân viên;
   - "có người chảy máu" → khẩn cấp không qua SLM;
   - đổi ngôn ngữ giữa phiên;
   - rút mạng giữa chừng.
4. **Kiểm "đúng là agent"**: dùng câu mới chưa từng có trong dữ liệu. Trace phải cho thấy agent tự chọn/nối tool, tự hỏi lại, tự lập lại kế hoạch khi tool lỗi.
5. (Tùy chọn) Ghi âm thật ~50 câu/ngôn ngữ.
6. Chạy production signoff: redacted audit review, privacy/retention check, emergency drill, restart/offline drill, rollback drill và security scan.
7. Bảng đối chiếu đề bài → bằng chứng; cập nhật `CLAUDE.md`, `docs/`, `reports/release/<version>/signoff.json`.

---

## 7. Rủi ro
| Rủi ro | Dấu hiệu | Xử lý |
|---|---|---|
| SLM CPU chậm | p95 voice > 4 s | Các kỹ thuật §4.4 (2)–(4) |
| Model nhỏ sinh lệnh sai | intent/pass^k thấp hơn `legacy` | Không bật mặc định; thêm ví dụ ICL; thử tool-calling gốc; LoRA; regex chỉ là dự phòng |
| Benchmark sai vì dùng GPU | số đo nhanh bất thường | Luôn `num_gpu=0`; profile `edge` cố định CPU |
| STT chậm/sai (zh) | WER/CER cao | STT theo ngôn ngữ + giải mã tăng dần |
| Smart-turn cắt lời tiếng Việt | cắt nhầm cao | Chỉnh ngưỡng → bản mới → fine-tune |
| Vọng loa | ngắt nhầm | So 3 cấu hình; dự phòng nút ngắt |
| Nói giá synthetic như thật | eval bắt câu thiếu nhãn | Nhãn sinh từ dữ liệu; test hợp đồng tool |
| Data contract lệch giữa policy và agent-domain | validator/hash mismatch; tool không map service | Chặn GĐ2; sửa một nguồn chuẩn, rebuild manifest, review parity |
| Offline queue trùng hoặc mất yêu cầu | restart tạo 2 request; sync trả sai trạng thái | idempotency key, outbox/inbox, TTL, reconciliation report và property policy |
| Knowledge hết hạn/xung đột | citation inactive hoặc hai giờ mở cửa khác nhau | effective/expiry, conflict fail-closed, staff review trước activate |
| Lộ PII qua log/QR/SSE | room/name/transcript xuất hiện trong report hoặc URL | redaction ở boundary, token ngắn hạn, privacy tests và purge drill |
| Xóa legacy quá sớm | import runtime/test ẩn; rollback không được | shadow mode, import scan, smoke test, rollback artifact trước khi xóa |
| Codex và Claude cùng sửa một file | `git status` lạ | Một bên mỗi giai đoạn; commit nhỏ |

## 8. Để sau
**Giám sát nhiều kiosk**: heartbeat (phiên bản, `/readyz`, KPI, không dữ liệu khách) + trang "đội kiosk" và trạng thái OTA từng kiosk. Thiết kế hiện tại không chặn việc thêm sau.

**Khi có thiết bị/người thật** (hiện không làm, xem §0.2): nghiệm thu barge-in và tiếng ồn ở sảnh; Jetson/arm64, nhiệt, rút điện, khôi phục backup sang máy khác; HTTPS + gateway phân quyền; repository TUF do operator ký; Wi-Fi cho QR; ghi âm khách thật; người duyệt nghiệp vụ/an toàn.

## 9. Kiểm tra (mỗi giai đoạn)
```bash
python -m compileall -q src tools
python tools/repin_configs.py --check
python datasets/schemas/validate_contracts.py
python tools/validate_agent_domain.py
python tools/validate_synthetic_operations.py
python tools/validate_furama_schemas.py
python tools/validate_furama_semantics.py
python -m pytest -q -W error::ResourceWarning        # không nạp .env.example
npm --prefix frontend run build
python tools/evaluation/run_voice_eval.py --manifest reports/voice-set/manifest.jsonl --output reports/voice-eval/<phase>.json
python tools/evaluation/run_retrieval_eval.py --suite grounded --output reports/retrieval/<phase>.json
python tools/evaluation/run_tool_eval.py --tasks datasets/evaluation/end_to_end/service_actions.jsonl --base-url http://localhost:8000 --output reports/tool-eval/<phase>.json
```
So với baseline GĐ0. Chỉ đổi mặc định khi không tụt chỉ số và ghi sai = 0.

## 10. Tham khảo
- **Hiểu câu**: Rasa CALM — [arXiv 2402.12234](https://arxiv.org/abs/2402.12234), [Command Generator](https://rasa.com/docs/pro/customize/command-generator).
- **Agent vs workflow**: [Anthropic — Building effective agents](https://www.anthropic.com/research/building-effective-agents).
- **Lập kế hoạch song song**: [LLMCompiler (ICML 2024)](https://arxiv.org/abs/2312.04511).
- **Eval agent**: [τ-bench (arXiv 2406.12045)](https://arxiv.org/abs/2406.12045) — pass^k; [BFCL](https://gorilla.cs.berkeley.edu/leaderboard.html).
- **Multi-agent + HITL**: [LangGraph multi-agent](https://docs.langchain.com/oss/javascript/multi-agent-custom), `interrupt()`.
- **Ngắt lượt**: [Smart Turn v3](https://github.com/pipecat-ai/smart-turn) — 23 ngôn ngữ có tiếng Việt, 12–60 ms CPU.
- **Transport/khử vọng**: [Pipecat — Choosing a transport](https://docs.pipecat.ai/client/concepts/choosing-a-transport).
- **STT**: [PhoWhisper (arXiv 2406.02555)](https://arxiv.org/abs/2406.02555), [Vietnamese Open ASR Leaderboard](https://vietaudio-team-vietnamese-asr-leaderboard.hf.space/), SenseVoice-Small (FunAudioLLM).
- **Grounding**: [MiniCheck (EMNLP 2024)](https://arxiv.org/abs/2404.10774), [NeMo Guardrails fact-checking](https://docs.nvidia.com/nemo/guardrails/latest/configure-guardrails/guardrail-catalog/fact-checking), [RAGAS (arXiv 2309.15217)](https://arxiv.org/abs/2309.15217).
- **Nhận dạng ngôn ngữ**: [GlotLID (arXiv 2310.16248)](https://arxiv.org/abs/2310.16248).
- **Vector store**: Chroma (`PersistentClient`, HNSW, lọc metadata), FAISS, [sqlite-vec](https://github.com/asg017/sqlite-vec).
- **OTA**: [TUF](https://theupdateframework.io/security/).
- **Nghiệp vụ khách sạn**: [SOP work order](https://setupmyhotel.com/hotel-sop-standard-operating-procedures/engineering-sop/sop-engineering-handling-maintenance-work-order-request-by-housekeeping/), [SLA yêu cầu khách](https://oxmaint.com/industries/hospitality/hotel-guest-request-response-time-sla-cmms-guide).
- **Dữ liệu cá nhân**: [Nghị định 13/2023](https://vietnamlawmagazine.vn/conditions-for-consent-under-decree-13-on-personal-data-protection-70818.html).
- **Ví dụ voice khách sạn**: LiveKit Agents "hotel receptionist".

## 11. Tiến độ đã kiểm chứng — 06/10/2026

Mục này thay cho các nhật ký §11–§16 cũ (lưu nguyên văn ở `%TEMP%\plan.before-progress-0610.md`). Mọi số dưới đây đã được kiểm lại trực tiếp từ code, test và `reports/`.

### 11.1 Tình trạng chung
- **Test toàn bộ** trên worktree hiện tại: **589 passed, 1 skipped, 0 fail**. Bài bị skip là fixture TUF thật, chỉ chạy khi có extra `ota-tuf`.
- **⚠ Chưa commit**:
  - commit cuối là `4eb8c44` (05/10 19:35);
  - từ đó có **70 file sửa** (+1.644/−237 dòng) và **~35 file mới**: vectorstore, synthetic operations, status token, staff console, OTA/TUF, probe, test.
  - Đây là rủi ro lớn nhất hiện tại (mất việc, khó review, khó rollback). **Việc đầu tiên: commit theo nhóm logic**, mỗi nhóm kèm test của nó.
- **Release `0.1.0-local`**: `reports/release/0.1.0-local/signoff.json` = `BLOCKED` (technical `pass_with_risk`; business, safety, release `blocked`). Người duyệt là "Codex automated audit", chưa có người thật ký.

### 11.2 Đã làm (theo giai đoạn)
| Giai đoạn | Đã có | Bằng chứng |
|---|---|---|
| GĐ0 | Gate dữ liệu pass (parity `service_policies` ↔ `agent-domain`); ghi worktree; bộ audio tổng hợp **1.162 WAV** (vi 350, en 299, zh 240, ko 273); `run_voice_eval.py --transport legacy\|pipecat` | `gates/gd0-data-contract.json` (pass), `gates/gd0-worktree.json` (pass_with_risk), `voice-set/manifest.jsonl` |
| GĐ1 | Pipecat route đăng ký đúng `cfg=`; sửa race STT sau timeout; TTS latency/RTF | `voice-eval/tts-final.json`, `voice-eval/synthetic-stt.json` |
| GĐ2 | `Command[]` vào vòng agent; `ToolObservation`/answer card có version, fail-closed; tool đọc chỗ trống nhà hàng/spa/tour/xe từ synthetic (có provenance, không ghi); evaluator dùng `preconditions`/`acceptable_tools` + `--repeats` | `gates/gd2-tool-contract.json` (pass_with_risk), `tool-eval/service-actions-full-local2.json` |
| GĐ3 | `hitl_mode=guest_confirm_all` cho property release; mã xác nhận `STAY-<12 hex>`; status token HMAC có TTL, `/api/status/{token}`, SSE có `Last-Event-ID`, trang `/status/{token}`; confirm trả `202` khi chưa xong; bảng `guest_consents` (bắt buộc ở production); staff console React `/staff` (vi mặc định + en/zh/ko), KPI; `/ops` chỉ còn redirect | `gates/gd3-staff-console.json` (pass_with_scope) |
| GĐ4 | `rag/vectorstore/` (Chroma + FAISS) dựng từ SQLite, citation vẫn kiểm qua SQLite; benchmark ngữ nghĩa; KPI grounded/abstain trên `/staff/metrics` | `gates/vector-{chroma,faiss}-semantic.json` |
| GĐ5 | `compose.jetson-sim.yaml` (6 CPU/8 GB, không GPU); OTA Ed25519 có freshness/anti-rollback; adapter TUF (`knowledge_tuf.py`, fail-closed); restart probe; offline probe 6 check; backup mã hóa AES-256-GCM | `gates/gd5-restart.json`, `offline-acceptance.json`, `privacy-backup.json` (PASS); `knowledge-tuf.json` (BLOCKED) |

### 11.3 Số đo hiện có
| Chỉ số | Kết quả | Đọc đúng |
|---|---|---|
| Vector dense (bge-m3, 220 câu `grounded`, 55/ngôn ngữ) | R@1 **88,2%**, R@3 99,5%, R@5 **100%**, MRR 0,936; truy vấn p95 FAISS **0,9 ms**, Chroma **9,4 ms** | Chỉ đo nhánh dense, chưa đo hybrid + rerank + citation. Hai backend cho kết quả như nhau, FAISS nhanh hơn 10× |
| Tool eval 240 ca (1 lượt lặp) | chọn tool **100%**, tham số 94,6%, **DB 83,3%**, vi phạm policy **0**, khẩn cấp ngoài ý muốn 0, pass^1 0,833 | 40 ca "sửa yêu cầu" không có nội dung sửa nên agent hỏi lại (đúng), nhưng oracle chấm sai → cần sửa oracle hoặc làm bộ nhiều lượt. Chưa chạy k > 1 |
| Route trên `service_actions` | 240/240 | Đây là câu **sạch**; **chưa đo lại** bộ robustness 2.520 câu có nhiễu (lần trước 44%) |
| STT tổng hợp (whisper-small) | WER en 14,6%, vi 25,4%, ko 28%; CER zh 18,6%; p50 ~1,8–2,1 s | **Chỉ 5 câu/ngôn ngữ**, chưa có ý nghĩa thống kê; 1.162 WAV đã có nhưng chưa chạy hết |
| Bandit | 0 high, 0 medium, 159 low | pip-audit bị chặn do môi trường Python → chưa có scan sạch |

### 11.4 Chưa làm / lệch so với plan (review)
**Lõi agent — mục tiêu chính chưa đạt, còn đi ngược:**
- `understanding_mode` mặc định vẫn `legacy` (chỉ dev là `command`).
- `engine.py` **dài thêm**: 1.172 → **1.280 dòng**, rẽ nhánh `decision.branch` 38 → **39**.
- `model_intent.py`, `turn_plan.py` vẫn còn; chọn dịch vụ vẫn khớp từ khóa (`match_terms`, cộng thêm logic khớp catalog trong `intent.py`).
- **Vi phạm nguyên tắc**: thêm danh sách từ khóa mới `nlu.read_intent.availability_service_terms` (vd `"spa_reservation": ["spa", "massage", "mát-xa"]`) và `schedule_cue_terms` vào `config/agent-domain.json`.
  - Đây là **tên dịch vụ trong `agent-domain.json`** (`CLAUDE.md` cấm) và là định tuyến bằng cụm từ (plan cấm).
  - Cần thay bằng đối sánh ngữ nghĩa trên `service_catalog.json` (§4.1) rồi xóa hai khóa này.

**Code sạch:**
- Chưa có `tests/test_module_size.py`, kiểm module mồ côi, `vulture`.
- Shim `rag/common.py`, `rag/claims.py` và `voice/session/speech_text.py` vẫn còn.
- Voice legacy (`api/voice/streaming.py`, `continuousVoice.ts`) và `web/ops.*` vẫn còn (`/ops` đã thành redirect).
- Chưa tách `engine.py`, `answers.py`, `service_actions.py`…

**RAG:**
- `retrieval.dense_backend` mặc định vẫn `legacy` (quét vector JSON).
- Đường quét JSON chưa xóa.
- Chưa có eval hallucination/faithfulness và bộ kiểm claim (MiniCheck).

**Voice:**
- Chưa đo voice E2E qua Pipecat.
- Chưa STT theo ngôn ngữ (PhoWhisper/SenseVoice), chưa giải mã tăng dần.
- Chưa thử barge-in trên thiết bị thật.
- `enable_rtvi` chưa xác nhận.

**Vận hành:**
- Package `qrcode` chưa cài, nên endpoint QR báo thiếu renderer.
- Đã kiểm trong code: **chưa có** trạng thái `awaiting_guest_reconfirm` (nhân viên đề xuất thay đổi), **chưa có** giữ chỗ tạm, **chưa có** bản dịch máy cho nhân viên. `dedupe_window_minutes` đã được nạp vào `core/operational_policy.py`.

**Gate cần thiết bị hoặc người thật (không làm được trong workspace):**
- G1: audio người thật có đồng ý, thiết bị Pipecat, barge-in;
- G5: Jetson/arm64, nhiệt, mất điện, khôi phục backup ngoài máy, repository TUF do operator ký;
- G6: chữ ký người duyệt (nghiệp vụ, quyền riêng tư, an toàn, rollback).

### 11.5 Trạng thái cổng
| Cổng | Trạng thái | Còn thiếu |
|---|---|---|
| G0 | pass (có việc dọn kèm theo) | Kiểm tra code sạch trong CI; xóa shim |
| G1 voice | chưa đạt — **làm được trên laptop** | Đo E2E Pipecat trên đủ 1.162 WAV (ép CPU); thử PhoWhisper/SenseVoice; barge-in bằng loa + mic laptop; xác nhận RTVI |
| G2 agent/tool | pass_with_risk | **Thay khâu hiểu câu** (vẫn là workflow); robustness 2.520 câu; pass^k với k > 1; sửa oracle ca "sửa yêu cầu" |
| G3 nghiệp vụ/web | pass_with_scope | Chạy web nhân viên trên Chrome laptop qua `localhost`; thiếu `awaiting_guest_reconfirm`, giữ chỗ, dịch cho nhân viên |
| G4 RAG | pass_with_scope | Promote `dense_backend` (đề xuất FAISS vì nhanh hơn 10×, cùng chất lượng); eval faithfulness; xóa đường JSON |
| G5 edge/OTA | chưa đạt — **làm được trên laptop** | Làm `ConnectivityMonitor` + khai báo mức phụ thuộc mạng (§4.6); chạy 6 test mạng (có/không mạng, chuyển giữa phiên, chập chờn) và kill tiến trình trên laptop; OTA qua repository TUF cục bộ (fixture đã pass 7/7 trong môi trường riêng → cài `tuf` vào môi trường chính) |
| G6 | chưa đạt | Demo matrix + signoff tự ký sau khi G0–G5 đạt |

Các mục "blocked" trong `reports/gates/*` và `reports/release/0.1.0-local/signoff.json` mà chỉ do thiếu thiết bị/người thật (Jetson, nhiệt, rút điện, TUF của operator, HTTPS resort, người duyệt) được **xếp ngoài phạm vi** theo §0.2, không còn tính là blocker của đồ án.

### 11.6 Ưu tiên tiếp theo
1. **Commit** toàn bộ việc đang mở theo nhóm (data/config, vectorstore, synthetic ops, public status + consent, staff console, OTA/TUF + probe, evaluator), mỗi nhóm có test xanh.
2. **GĐ2 cốt lõi**:
   - gỡ `availability_service_terms`/`schedule_cue_terms`, thay bằng chọn dịch vụ theo ngữ nghĩa;
   - đo lại robustness 2.520 câu với chế độ `command`;
   - nếu ≥ 90% thì đổi mặc định sang `command` và bắt đầu xóa nhánh legacy.
3. **Promote vector backend** (FAISS hoặc Chroma) sau khi đo hybrid + rerank không tụt; xóa đường quét JSON.
4. **Voice**:
   - chạy `run_voice_eval.py --transport pipecat` trên đủ 1.162 WAV (ép CPU);
   - thử PhoWhisper (vi) và SenseVoice (zh/ko/en);
   - xác nhận RTVI.
5. **Code sạch**: thêm `test_module_size.py` + `vulture`; xóa shim; tách `engine.py` sau khi GĐ2 xong.
6. Cài `qrcode`; chạy pip-audit trong môi trường sạch.
