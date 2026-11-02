# Các việc cần làm để chạy AI Service với Gemini

Cập nhật: **30/10/2026**. Hướng dẫn dưới đây ưu tiên chạy BE, AI và FE trên máy; MySQL chạy bằng Docker của BE.

## 1. Phần đã được cấu hình

File `AIService/.env` đã có key bạn cung cấp và các cấu hình:

```dotenv
ALLOW_EXTERNAL_FALLBACK=true
EXTERNAL_PROVIDER=gemini
EXTERNAL_MODEL=gemini-3.1-flash-lite
```

Key chỉ được lưu trong `.env` đã có trong `.gitignore`; không cần đưa key vào FE hoặc file hướng dẫn. Không chạy lệnh in toàn bộ `.env` khi chia sẻ log.

Giữ nguyên `EXTERNAL_DAILY_CAP=2000` từ cấu hình trước. Đây là giới hạn số lần gọi external qua Gateway mỗi ngày, không phải ngân sách tiền hoặc bảo đảm quota Google. Một lượt chat có thể gọi external cho cả EXTRACT và COMPOSE.

**Thứ tự gọi hiện tại: Ollama → Gemini → Rules.** Gemini là API ngoài dự phòng. Khi Ollama hoạt động tốt, request có thể không gọi Gemini. Nếu chưa chạy Ollama, lỗi kết nối local sẽ dẫn sang Gemini; không bắt buộc cài model Ollama để bắt đầu thử API ngoài.

Google chấp nhận key khi kiểm tra danh sách model. Model cũ `gemini-2.5-flash` trả HTTP 404 vì không còn dành cho người dùng mới. `gemini-3.8-flash` được Google gợi ý nhưng gặp timeout 30 giây khi thử tác vụ này. Cấu hình cuối dùng **`gemini-3.1-flash-lite`**, đã gọi được trích bộ lọc và streaming bằng chính adapter của dự án.

Các phép thử gọi trực tiếp provider bằng câu hỏi và card thử nghiệm, chưa kiểm chứng toàn bộ luồng có DB/JWT/proxy. Giá ước tính trong code chưa được đối chiếu riêng cho model Flash-Lite; dùng thống kê tài khoản Google để kiểm tra chi phí thực tế.

**Điểm cần kiểm thử trước khi dùng thật:** API trả JSON hợp lệ và streaming có nội dung, nhưng câu thử “dưới 3 triệu … cho 2 người” có lần bị model bỏ sót giá/số người. Vì vậy kết nối API đã được xác nhận, còn độ chính xác trích bộ lọc chưa đạt kiểm chứng; cần kiểm thử và tinh chỉnh riêng trước khi tin kết quả tìm phòng. Đây là việc ưu tiên sau khi khởi động đủ BE/AI/FE.

## 2. Khởi động MySQL và BE

- [ ] Mở terminal tại `E:\SpringBoot\DoAn\RentalManagement`.
- [ ] Chạy hạ tầng:

```powershell
docker compose up -d
```

- [ ] Chạy Spring Boot bằng IDE như trước.
- [ ] Kiểm tra BE khởi động thành công, Flyway không lỗi, API hoạt động tại cổng 8080.

Không drop/reset database để làm lại setup AI.

## 3. Cấp thêm quyền đọc cho AI

- [ ] Đăng nhập MySQL bằng tài khoản quản trị qua công cụ bạn đang dùng.
- [ ] Nếu đã có schema `rental_ai` và tài khoản `ai_svc`, cấp thêm:

```sql
GRANT SELECT ON rental_db.properties TO 'ai_svc'@'%';
GRANT SELECT ON rental_db.property_amenities TO 'ai_svc'@'%';
```

- [ ] Kiểm tra quyền của tài khoản đó:

```sql
SHOW GRANTS FOR 'ai_svc'@'%';
```

Nếu chưa có user/schema AI, dùng `scripts/setup_database.sql` sau khi thay placeholder password cho đúng. AI cần SELECT trên các bảng nghiệp vụ liên quan và quyền phù hợp trên schema riêng `rental_ai`; không cấp quyền ghi core cho tài khoản runtime.

## 4. Kiểm tra cấu hình DB và JWT

- [ ] Mở `E:\SpringBoot\DoAn\AIService\.env` bằng editor.
- [ ] `CORE_DB_URL` trỏ schema `rental_db`, `AI_DB_URL` trỏ `rental_ai`; credential khớp tài khoản đã cấp quyền.
- [ ] Khi AI chạy trên host, DB host là `localhost` với cổng MySQL đang publish.
- [ ] `JWT_SECRET` khớp BE, `JWT_ALG=HS512`, `JWT_ISSUER=rental-management`.

Key Gemini đã được điền; không cần nhập lại để chạy local. Nếu sau này thay key, cập nhật `GEMINI_API_KEY` và restart AI.

## 5. Cài dependencies và áp dụng migration AI

- [ ] Mở terminal tại `E:\SpringBoot\DoAn\AIService` và chạy:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

- [ ] Revision hiện tại cần là `20261030_ai_integration`.

Migration bổ sung snapshot kết quả recommendation và các bảng khóa/lease để quota hoạt động. Phần này **chưa được áp dụng vào MySQL thật trong các đợt sửa code trước**.

## 6. Chuẩn bị dữ liệu thử và embedding

- [ ] Có tài khoản TENANT đăng nhập được qua BE.
- [ ] Có Listing `PUBLISHED`, thời hạn còn hiệu lực.
- [ ] Property của Listing có ít nhất một Room `AVAILABLE`, có giá và diện tích hợp lệ.

Seed tham chiếu của BE không tự tạo Listing công khai. Nếu đã có dữ liệu phù hợp, không cần chạy seed AI.

- [ ] Tạo embedding để gợi ý có thể dùng ngữ nghĩa/hành vi:

```powershell
.\.venv\Scripts\python.exe -m scripts.build_embeddings
```

Lần đầu có thể tải model embedding. **Gemini là LLM ngoài; embedding hiện vẫn chạy local bằng sentence-transformers**, không dùng Gemini embedding API. Nếu chưa có vector, recommendation vẫn có thể chạy theo profile/cold start; chatbot tìm bằng SQL.

## 7. Chạy AI Service

- [ ] Tại thư mục AIService:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- [ ] Mở `http://localhost:8000/ai/health`.
- [ ] Kiểm tra `status=UP`, hai DB đều UP và `llm.external` có provider gemini, model gemini-3.1-flash-lite, enabled true.

Health cho biết DB/schema và cấu hình; không tự gọi model để xác nhận quota Google.

Nếu AI đang chạy từ trước lúc chỉnh `.env`, dừng process đó rồi chạy lại để nhận key/model mới.

## 8. Chạy FE và thử bằng tài khoản thật

- [ ] Tại `E:\SpringBoot\DoAn\DOANTOTNGHIEPFE`, dùng Node đáp ứng yêu cầu Angular CLI đã cài rồi chạy:

```powershell
npm start
```

- [ ] Mở `http://localhost:4200` và đăng nhập TENANT.
- [ ] Kiểm tra phần “Gợi ý phòng dành cho bạn” trên landing.
- [ ] Mở “Trợ lý tìm phòng”, hỏi bằng địa điểm có trong DB, ví dụ: “Tìm phòng dưới 3 triệu ở phường Cầu Giấy, có Wifi”.
- [ ] Kiểm tra trả lời từng phần, card đúng giá/diện tích, mở đúng tin đăng, gửi đánh giá được.
- [ ] Kiểm tra câu có đủ giá và số người: bộ lọc/kết quả phải giữ đúng những điều kiện đó, không chỉ đúng địa điểm. Có thể đối chiếu `ai_messages.filters_json` của lượt thử.
- [ ] Thử câu nối tiếp và nút Dừng/Hội thoại mới.

Nếu muốn quan sát Gemini thực sự được gọi trong cấu hình fallback hiện tại, có thể thử lúc Ollama chưa chạy. Không cần chỉnh FE để chứa key hoặc gọi thẳng Google.

## 9. Nếu gặp lỗi

| Dấu hiệu | Việc cần kiểm tra |
| --- | --- |
| AI health trả 503 | MySQL đang chạy, URLs/quyền đọc đúng, migration AI đã lên head |
| Unknown column hoặc table | BE/schema đang dùng đúng bản hiện tại; đã áp dụng migration và grant bổ sung |
| HTTP 401 từ `/ai/*` | Đăng nhập lại, JWT secret/issuer/algorithm phải khớp BE |
| HTTP 403 từ `/ai/*` | Tài khoản có ROLE_TENANT và đang ACTIVE |
| Gemini HTTP 403 | Quyền key/project, giới hạn key và điều kiện truy cập API của Google |
| Gemini HTTP 404 với model cũ | Kiểm tra `EXTERNAL_MODEL=gemini-3.1-flash-lite`, restart AI |
| Gemini HTTP 429 | Quota/rate limit Google; chờ hoặc kiểm tra project/quota trên Google AI Studio |
| Câu trả lời dạng template | Có thể đã fallback Rules; kiểm tra provider/status trong `llm_call_logs` |
| Không thấy Gemini call | Ollama có thể đã xử lý thành công; hoặc trần external trong ngày đã đạt |
| Gợi ý trống | Có Listing công khai còn hạn và Room AVAILABLE khớp nhu cầu hay chưa |
| `node.exe` không tìm thấy | Chọn Node qua NVM hoặc sửa PATH; Angular CLI hiện yêu cầu bản Node phù hợp engines |

Không chia sẻ log có Authorization, API key hoặc URL DB chứa password.

## 10. Nếu chuyển sang Docker sau này

- [ ] Hoàn tất và thử thành công luồng host trước.
- [ ] Chuẩn bị `.env.docker` từ mẫu, điền credential/JWT đúng; bật external và chọn model/key giống `.env` local.
- [ ] Với MySQL publish trên host, URL DB trong AI container dùng `host.docker.internal`, không dùng localhost.
- [ ] Chạy Alembic bằng container trước khi khởi động API mới.
- [ ] Nếu FE Docker gọi AI host, AI cần bind địa chỉ container truy cập được, ví dụ `--host 0.0.0.0` trong môi trường local phù hợp.

Chi tiết topology, migration và các việc còn lại trước production: `AI_SERVICE_ARCHITECTURE.md`, mục 10–12.
