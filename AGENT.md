# AGENT.md — Quy tắc phát triển và sửa lỗi

Tài liệu này là quy tắc bắt buộc khi thêm code, sửa bug, thêm ngôn ngữ hoặc thay đổi voice/RAG trong repository.

## 1. Quy tắc không hardcode

Không hardcode trong `src/` các dữ liệu có thể thay đổi theo property, ngôn ngữ, locale, model hoặc deployment:

- tên khách sạn, `property_id`, nhà hàng, dịch vụ, phòng, địa điểm và contact;
- câu trả lời hiển thị, từ khóa NLU, alias, regex ngôn ngữ và emergency copy;
- từ đọc số, đơn vị, tiền tệ, email, ngày/giờ, dấu câu và quy tắc phát âm;
- danh sách ngôn ngữ được hỗ trợ, thứ tự fallback, ngưỡng có thể tuning;
- đường dẫn model, model digest, runtime budget và feature flag;
- dữ liệu benchmark, expected fact, source id và nhãn nghiệp vụ.

Các giá trị được phép nằm trong code:

- invariant bảo mật và giới hạn an toàn không phụ thuộc property;
- tên field/schema, enum của protocol, thuật toán tổng quát và cấu trúc control flow;
- giá trị fallback trung tính khi config thiếu, miễn là fallback không giả định khách sạn hay ngôn ngữ cụ thể.

Nếu một giá trị xuất hiện vì “chỉ có một khách sạn hiện tại”, vẫn đưa nó vào profile/config. Không dùng lý do đó để thêm hardcode vào runtime.

## 2. Nơi đặt dữ liệu

- `config/agent-domain.json`: vocabulary, NLU, RAG policy, locale/voice rendering, service rules.
- `config/runtime-profiles/src/`: model path, timeout, budget, feature flag và deployment setting.
- `i18n/` và `frontend/src/i18n.ts`: text hiển thị theo ngôn ngữ.
- property profile/release: property-specific facts, contacts và enabled languages.
- `datasets/` và `knowledge/`: dữ liệu khách sạn, benchmark và corpus; không chép lại các giá trị này vào Python.

Mọi key mới phải có:

1. JSON schema tương ứng.
2. Loader/accessor typed hoặc có kiểm tra kiểu.
3. Test config hợp lệ, thiếu key và sai kiểu.
4. Cập nhật SHA-256 bằng công cụ của repo (`python tools/config/repin_configs.py`).

Không sửa tay generated release, frontend bundle hoặc file có ghi là generated.

## 3. Cách xử lý bug bắt buộc

Trước khi sửa:

1. Tạo reproduction nhỏ nhất bằng test hoặc fixture dữ liệu thật.
2. Xác định bug thuộc data/config, contract, integration, algorithm, model hay UX.
3. Đọc code gọi và code bị gọi; kiểm tra encoding, locale, cache key, boundary và fallback.
4. Tìm implementation chính thức của dependency và paper/benchmark liên quan nếu bug là model, speech, ranking hoặc localization.
5. Chọn giải pháp theo thứ tự ưu tiên:
   - sửa data/config/schema nếu hành vi phụ thuộc domain hoặc locale;
   - dùng API/option chính thức của dependency;
   - sửa algorithm với invariant và test rõ ràng;
   - chỉ đổi model khi có benchmark cùng dữ liệu, latency và điều kiện chạy.

Không sửa bằng cách thêm một `if language == ...` hoặc một string ngoại lệ để làm xanh một test đơn lẻ.

Không "học đề":

- Không thêm cụm từ vào `config/agent-domain.json` để một câu cụ thể được hiểu đúng. Ý định (dịch vụ, hỏi thông tin, huỷ/đổi, hỏi tiếp, xã giao, sở thích, kế hoạch) được học từ ví dụ trong `datasets/training/agent/`, không từ danh sách cụm từ.
- Không chép câu từ `datasets/evaluation/` hay `docs/BACKEND-TEST-PLAN.md` vào training. Khi một câu tự nhiên bị hiểu sai, thêm 2–3 ví dụ **khác cách nói** với nhãn đúng; `gold_status: GOLD` chỉ khi người đã duyệt, ví dụ do agent viết dùng `CANDIDATE`.
- Không thêm đường tắt tra cứu nguyên văn (câu khách trùng câu train → trả nhãn) hay ngưỡng đếm từ để đoán ý định.
- `tests/agent/test_no_case_specific_rules.py` chặn các điểm trên; `tests/agent_domain_keyword_budget.json` chỉ được giảm.

Không đặt tên file/hàm theo phiên bản hay giai đoạn (final, v2, after, rerun, t2…). Báo cáo ghi đè đúng một đường dẫn ổn định; so sánh trước/sau để trong cùng file; lịch sử thuộc về git.

Sau khi sửa:

- thêm regression test cho bug gốc và ít nhất một case không liên quan;
- kiểm tra cả bốn ngôn ngữ nếu thay đổi locale/voice;
- chạy schema/data validation và test đầy đủ;
- kiểm tra cache invalidation nếu thay đổi model, config, normalizer hoặc pronunciation;
- cập nhật tài liệu nếu behavior hoặc contract thay đổi.

## 4. Quy tắc voice/STT/TTS

### STT

- Khi UI đã biết ngôn ngữ, truyền `language` rõ ràng vào decoder để tránh lượt language detection dư thừa.
- Nếu cần tự phát hiện ngôn ngữ, đo riêng latency và độ chính xác; không chạy detect rồi decode lại mặc định.
- Phát hiện lệch script chỉ được dùng để gợi ý đổi ngôn ngữ, không tự ý sửa transcript.
- Không chọn model STT mới chỉ vì paper báo WER tốt hơn. Phải có benchmark giọng người thật, theo từng ngôn ngữ và dialect.
- Ghi nhận ít nhất CER/WER, p50/p95 latency, timeout, hallucination và tỷ lệ câu rỗng.

### TTS

- Text normalization phải chạy sau authorization và chỉ thay đổi chuỗi đưa vào TTS, không thay đổi câu hiển thị hay evidence.
- Số, tiền, ngày/giờ, phần trăm, đơn vị, URL/email, acronym và số điện thoại phải đi qua rule/locale config.
- Cache key phải bao gồm model/voice revision, executable/config revision và pronunciation policy digest.
- `length_scale`, pause, punctuation và voice option phải là config; không chôn trong Python.
- Khi thêm rule normalization, test cả giá trị đọc đúng và giá trị hiển thị không bị thay đổi.

## 5. Quy tắc benchmark và model

- Không dùng điểm end-to-end để kết luận riêng retrieval hoặc STT.
- Retrieval phải có qrels/chunk labels và đo Recall@k, MRR/nDCG theo từng ngôn ngữ.
- STT phải dùng audio người thật cho quyết định model; audio tổng hợp chỉ dùng smoke test.
- Tách development, validation và hidden holdout theo concept, không để bản dịch của cùng một câu lọt sang split khác.
- Mọi model change phải có before/after report, per-language breakdown và latency budget.
- Khi không có dữ liệu đủ mạnh, giữ model hiện tại và ghi rõ gap; không tối ưu theo phỏng đoán.

## 6. Hardcode exception

Nếu thật sự chưa thể chuyển một giá trị ra config:

1. Ghi rõ lý do và phạm vi trong code comment.
2. Thêm một entry tạm thời vào `tests/hardcode_allowlist.txt`.
3. Thêm issue/next step để loại bỏ entry đó.
4. Không tăng allowlist cho tiện. Allowlist phải giảm dần.

`tests/agent/test_no_hardcode.py` là gate bắt buộc. Entry allowlist dùng line number, nên sau khi refactor phải xử lý stale entry.

## 7. Nguồn tham khảo kỹ thuật

Các nguồn dưới đây được dùng làm chuẩn tham khảo, không phải lý do để copy mù quáng:

- Unicode LDML/CLDR: locale data, number/date/time formatting và locale inheritance — <https://www.unicode.org/reports/tr35/>
- Piper preprocessing: language/phonemization và voice config — <https://github.com/rhasspy/piper/blob/master/src/python/piper_train/preprocess.py>
- Piper runtime: phonemize theo voice config và synthesize theo sentence — <https://github.com/rhasspy/piper/blob/master/src/cpp/piper.cpp>
- faster-whisper: truyền `language` rõ ràng hoặc để model detect, cùng các decoding options — <https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py>
- Whisper: robust multilingual ASR từ weak supervision — <https://arxiv.org/abs/2212.04356>
- PhoWhisper: ASR tối ưu cho tiếng Việt — <https://arxiv.org/abs/2406.02555>
- Non-Standard Vietnamese Word Detection and Normalization for TTS: tách phát hiện NSW và rule-based verbalization — <https://arxiv.org/abs/2209.02971>
- VietNormalizer: hướng rule-based, dependency-free cho số, ngày, giờ, tiền, phần trăm và từ ngoại lai — <https://arxiv.org/abs/2603.04145>

Khi tham khảo paper/project, phải kiểm tra license, version, điều kiện benchmark và khả năng chạy trên CPU hiện tại trước khi đưa dependency hoặc model vào production.

## 8. Gate trước khi hoàn thành

```bash
python -m compileall -q src tools
python tools/config/repin_configs.py --check
python tools/validate/audit_data.py
python -m pytest -q -W error::ResourceWarning
bandit -q -r src/concierge_kiosk tools -ll
```

Nếu thay đổi frontend hoặc web bundle, chạy thêm build frontend và `node --check` cho các file web theo `CLAUDE.md`.
