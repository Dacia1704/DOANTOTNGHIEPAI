---
name: ai-service-development
description: >-
  Comprehensive guide and runbook for developing, evaluating, and running
  the FastAPI AI Service (AIService) in Python 3.11.
  Activate when working on natural language room search, SSE chat streaming,
  hybrid recommendation systems, multilingual-e5 embeddings, Ollama/Qwen3 local LLM inference,
  external LLM fallback (Gemini/Anthropic), Alembic migrations, or vector sync.
---

# AI Service Development Guide (AIService)

Tài liệu hướng dẫn và quy chuẩn kỹ thuật cho việc phát triển, kiểm thử, tích hợp LLM và vận hành dịch vụ Trí tuệ Nhân tạo (AIService) của Hệ thống Quản lý Thuê trọ (Rental Management).

---

## 1. Tổng quan Công nghệ & Môi trường

| Thành phần | Công nghệ / Phiên bản | Ghi chú |
| :--- | :--- | :--- |
| **Ngôn ngữ** | Python 3.11 | Dùng Python launcher `py -3.11` để tạo và chạy `.venv` |
| **Framework** | FastAPI + Uvicorn | Async ASGI server, Streaming SSE, OpenAPI Swagger |
| **ORM & Migration** | SQLAlchemy 2.0 + Alembic | Head migration hiện tại: `20261030_ai_integration` |
| **Local LLM** | Ollama (`qwen3:4b-instruct-2507-q4_K_M`) | Chạy trong Docker container, bắt buộc dùng bản **Instruct** |
| **External LLM** | Google Gemini (`gemini-3.1-flash-lite`) / Claude | Fallback khi local lỗi, quá tải hoặc cần làm rõ ý định |
| **Embedding Model** | `intfloat/multilingual-e5-small` (384 dims) | Chạy CPU độc lập với Ollama qua `sentence-transformers` |
| **Bảo mật** | JWT HS512 (`app.core.security`) | Dùng chung Secret và Issuer (`rental-management`) với Backend |
| **Hệ thống Database**| Kiến trúc 2 Database riêng biệt | Xem chi tiết mục 2 |

### Cổng & URL Truy cập Local

*   **API Base URL**: `http://localhost:8000`
*   **Health Check**: `http://localhost:8000/ai/health`
*   **Interactive Swagger UI**: `http://localhost:8000/docs`
*   **Ollama Endpoint**: `http://localhost:11434`

---

## 2. Kiến trúc Hai Database & Ranh giới Dữ liệu

Dịch vụ AI kết nối đồng thời tới 2 database MySQL với phân quyền chặt chẽ:

```text
FastAPI AIService (:8000)
├── CORE_DB_URL (rental_db)  ---> [CHỈ ĐỌC (READ-ONLY)]
│     └── properties, rooms, listings, user_profiles, administrative_units...
└── AI_DB_URL   (rental_ai)  ---> [ĐỌC & GHI (READ/WRITE)]
      └── chat_conversations, chat_messages, ai_recommendations,
          ai_feedback, ai_events, embedding_store, llm_call_logs...
```

*   `rental_db` (`CORE_DB_URL`): Chứa dữ liệu nghiệp vụ do Spring Boot sở hữu. AIService **chỉ đọc** dữ liệu tin đăng và phòng để truy vấn tìm kiếm hoặc sinh vector; không có quyền ghi hay sửa đổi bảng nghiệp vụ.
*   `rental_ai` (`AI_DB_URL`): Database chuyên biệt cho AI do Alembic quản lý schema. Dùng để lưu trữ lịch sử chat SSE, quota ngày, embedding vectors, feedback người dùng và telemetry logs.

---

## 3. Kiến trúc Chatbot & Chuỗi Xử lý Ý định (Pipeline)

Quy trình xử lý một câu chat tự nhiên từ người dùng (`POST /ai/chat`):

```text
Tin nhắn người dùng
  ├── 1. Khớp Small Talk (app/chat/small_talk.py)
  │      └── Chào hỏi, cảm ơn, tạm biệt, giới thiệu trợ lý -> Trả lời ngay lập tức
  ├── 2. Định tuyến Hồ sơ Tenant (app/chat/profile.py)
  │      └── "Tư vấn phòng cho tôi", "Tìm phòng phù hợp" -> Lấy hồ sơ tenant trong JWT
  └── 3. Trích xuất Bộ lọc & Tìm kiếm (Local -> External -> Rules)
         ├── EXTRACT (Tối đa 512 tokens) -> Trích giá, khu vực, diện tích, tiện ích, số người
         ├── SQL Query phòng thỏa mãn (Chỉ lấy tin PUBLISHED, còn hạn, phòng AVAILABLE)
         ├── Xử lý 0 kết quả: Nới lỏng dung sai từng bước (Tolerance Expansion)
         └── COMPOSE (Tối đa 384 tokens, tối đa 5 phòng) -> Soạn câu trả lời + Trả card qua SSE
```

### 3.1. Cơ chế Fallback 3 Tầng
1.  **Local LLM (Ollama - Qwen3 4B Instruct)**: Xử lý mặc định offline, latency thấp.
2.  **External LLM (Gemini / Anthropic)**: Kích hoạt khi `ALLOW_EXTERNAL_FALLBACK=true`. Hỗ trợ khi Local gặp lỗi kỹ thuật, timeout hoặc khi câu hỏi thuộc dạng `CLARIFY` / `OUT_OF_SCOPE` cần diễn giải mềm mại hơn.
    *   *Bảo vệ:* Có Circuit Breaker, hạn mức hàng ngày (`EXTERNAL_DAILY_CAP`), và tự động che giấu thông tin cá nhân (PII Masking).
3.  **Rules Provider**: Fallback tầng cuối cùng sử dụng mẫu câu định sẵn nếu cả Local và API ngoài đều không khả dụng.

### 3.2. Thuật toán Nới lỏng Bộ lọc khi Không có Kết quả (Tolerance)
Khi tìm kiếm không có phòng nào khớp chính xác, hệ thống tự động mở rộng theo từng nấc dung sai và dừng lại ngay khi có kết quả:
*   **Khu vực lân cận**: Tối đa $5\text{ km}$ tính từ tâm (centroid) của phường/xã, trong cùng tỉnh/thành. Tọa độ centroid được Backend đồng bộ từ `tinhthanhpho.com`. Nếu không có centroid, không tự suy đoán.
*   **Giá thuê**: Nới tối đa $20\%$ nhưng không vượt quá $500.000\text{ đ/tháng}$ mỗi biên. (Ví dụ: ngân sách 2 triệu nới lên 2,2 triệu rồi 2,4 triệu; ngân sách 8 triệu chỉ nới tối đa 8,5 triệu).
*   **Diện tích**: Nới tối đa $15\%$ nhưng không vượt quá $5\text{ m}^2$ mỗi biên.
*   **Bảo toàn điều kiện**: Luôn giữ nguyên loại phòng, tiện ích bắt buộc và sức chứa. Ghi rõ phần nới lỏng trong câu trả lời giải thích cho người dùng.

---

## 4. Hệ thống Gợi ý Cá nhân hóa & Embedding Vector

*   **Embedding Model**: `intfloat/multilingual-e5-small`.
    *   Sinh vector biểu diễn 384 chiều từ text mô tả phòng, tiện ích và vị trí.
    *   Lưu trữ trong bảng `rental_ai.embedding_store`.
    *   Phải chạy lại script `scripts.build_embeddings` mỗi khi có thay đổi lớn về dữ liệu tin đăng.
*   **Recommendation Flow (`app/recommend/`)**:
    1.  `candidates.py`: Lọc ứng viên tin đăng hợp lệ theo khu vực tỉnh/thành.
    2.  `scoring.py`: Chấm điểm đa tiêu chí: Khớp hồ sơ tenant + Độ tương đồng cosine với vector hành vi + Điểm độ mới (freshness) + Trọng số gói ưu tiên.
    3.  `rerank.py`: Áp dụng thuật toán **MMR (Maximal Marginal Relevance)** để tối ưu tính đa dạng, tránh việc tất cả phòng gợi ý đều giống hệt nhau hoặc thuộc cùng một chủ trọ.

---

## 5. Giới hạn Hạn mức & Giám sát (Observability)

*   **Quota người dùng**:
    *   Mỗi tài khoản có hạn mức chat tối đa `chat_daily_limit_per_user = 30` lượt/ngày (reset theo múi giờ `Asia/Ho_Chi_Minh`).
    *   Giới hạn `max_concurrent_per_user = 3` request đồng thời.
*   **Kiểm tra Telemetry**:
    ```sql
    SELECT request_id, step, provider, status, latency_ms, error_detail
    FROM rental_ai.llm_call_logs ORDER BY created_at DESC LIMIT 20;
    ```
    *Dấu hiệu hệ thống hoạt động tốt: Thấy `local/SUCCESS` trong bảng log. Nếu thấy `rules` xuất hiện nhiều, cần kiểm tra lại container Ollama.*

---

## 6. Hướng dẫn Vận hành & Runbooks

Tất cả các lệnh thực thi từ thư mục `E:\SpringBoot\DoAn\AIService`:

### 6.1. Thiết lập Môi trường Lần đầu
```powershell
Set-Location E:\SpringBoot\DoAn\AIService

# Tạo virtualenv Python 3.11
py -3.11 -m venv .venv

# Cài đặt toàn bộ thư viện phụ thuộc
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 6.2. Khởi tạo Database AI, Migration & Build Vector
> [!NOTE]
> Yêu cầu Backend đã hoàn tất setup database nghiệp vụ `rental_db` trước khi chạy script này.

```powershell
Set-Location E:\SpringBoot\DoAn\AIService
# Chạy script tự động: tạo schema rental_ai, alembic upgrade, seed fixture, build vector
.\scripts\setup_demo.ps1
```
*Hoặc thực hiện thủ công từng bước:*
```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_database
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m scripts.seed_demo_ai --users 1000
.\.venv\Scripts\python.exe -m scripts.build_embeddings
```

### 6.3. Khởi động Container Ollama & Tải Model
```powershell
Set-Location E:\SpringBoot\DoAn\AIService

# Khởi chạy Ollama container
docker compose up -d ollama

# Tải model Qwen3 4B Instruct (bắt buộc đúng tag)
docker compose exec ollama ollama pull qwen3:4b-instruct-2507-q4_K_M

# Làm nóng model trước khi phục vụ
docker compose exec ollama ollama run qwen3:4b-instruct-2507-q4_K_M "Chỉ trả lời: OK"
```

### 6.4. Chạy AIService (Uvicorn)
```powershell
Set-Location E:\SpringBoot\DoAn\AIService
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 6.5. Kiểm tra Tình trạng & Đo Tốc độ (Benchmark)
```powershell
# 1. Kiểm tra Health Check (Yêu cầu cả 2 DB đều UP và vector đã sẵn sàng)
Invoke-RestMethod http://localhost:8000/ai/health | ConvertTo-Json -Depth 6

# 2. Biên dịch kiểm tra lỗi cú pháp Python
.\.venv\Scripts\python.exe -m compileall -q app scripts

# 3. Chạy unit test suite
.\.venv\Scripts\python.exe -m pytest -q

# 4. Đo tốc độ suy luận của model Ollama local
.\.venv\Scripts\python.exe -m scripts.benchmark_local
```

---

## 7. Các lỗi thường gặp & Cách xử lý

1.  **Lỗi `ConnectError: Failed to connect to localhost:11434`**:
    *   Container Ollama chưa được bật. Chạy `docker compose up -d ollama` và kiểm tra lại `http://localhost:11434/api/tags`.
2.  **Model phản hồi rất lâu hoặc sinh text suy nghĩ dài**:
    *   Đang dùng nhầm model Thinking thay vì Instruct. Kiểm tra lại tên model trong `.env` và `ollama list`, đảm bảo dùng đúng `qwen3:4b-instruct-2507-q4_K_M`.
3.  **Lỗi `Access denied for user 'ai_svc'@'localhost'`**:
    *   Mật khẩu trong `CORE_DB_URL` hoặc `AI_DB_URL` không khớp với user đã tạo. Chú ý URL-encode nếu mật khẩu chứa các ký tự đặc biệt (`@`, `:`, `/`).
4.  **Health Check báo 503 hoặc thiếu cột centroid**:
    *   Backend chưa chạy migration đồng bộ địa chỉ `tinhthanhpho.com` hoặc migration Flyway V1 chưa hoàn tất. Chạy lại setup demo ở Backend.
5.  **Lỗi xác thực JWT 401/403**:
    *   Đảm bảo `JWT_SECRET` trong `.env` của AIService giống từng ký tự với `JWT_SECRET` của Spring Boot backend.
