# Rental Management — hướng dẫn chung FE, BE và AIService

Cập nhật 09/11/2026. Tài liệu chung cho ba source `DOANTOTNGHIEPFE` (FE), `RentalManagement` (BE) và `AIService`. Các lệnh dưới đây dùng PowerShell trên Windows và cấu trúc ba thư mục nằm cạnh nhau. Có thể thay `E:\SpringBoot\DoAn` bằng thư mục của bạn.

## Ý tưởng và chức năng

Hệ thống kết nối người thuê với chủ cơ sở cho thuê, quản lý từ tìm phòng đến yêu cầu thuê và hợp đồng. AI hỗ trợ tìm bằng ngôn ngữ tự nhiên và cá nhân hóa gợi ý theo hồ sơ/hành vi.

- **TENANT:** tìm tin theo khu vực, giá, diện tích, tiện ích, loại phòng và bản đồ; lưu tin, cập nhật hồ sơ, chat với chủ trọ, gửi yêu cầu thuê, xác nhận hợp đồng và đánh giá/báo cáo.
- **LANDLORD:** quản lý cơ sở, phòng, ảnh và vị trí; tạo tin, xử lý yêu cầu thuê/hợp đồng; mua gói dịch vụ/Boost, theo dõi thanh toán và hóa đơn.
- **ADMIN:** quản lý tài khoản, danh mục, duyệt tin, xử lý báo cáo và vận hành gói dịch vụ/thanh toán.
- **AI:** chatbot tìm phòng, trả card qua SSE, gợi ý theo hồ sơ và embedding, ghi nhận tương tác/feedback; có quota và fallback khi model không trả được kết quả.

Thanh toán VNPay cho gói dịch vụ/Boost tách khỏi luồng đặt thuê phòng. Xác nhận hợp đồng trong ứng dụng là đồng ý điện tử, chưa phải chữ ký số chứng thực.

## Công nghệ và cấu trúc source

| Source | Công nghệ theo source hiện tại | Điểm bắt đầu |
| --- | --- | --- |
| FE | Angular 22.2, TypeScript 6.0, Angular Material, RxJS, MapLibre/MapTiler, STOMP/SockJS, PDF.js | `src/main.ts`, `src/app/app.routes.ts`, `angular.json` |
| BE | Java 21, Spring Boot 4.1.1, Spring Security/JWT, JPA, Flyway, JasperReports, MySQL, Redis, RabbitMQ, Elasticsearch | `pom.xml`, `src/main/resources/application.yaml` |
| AIService | Python, FastAPI/Uvicorn, SQLAlchemy, Alembic, sentence-transformers, Ollama | `app/main.py`, `app/config.py`, `requirements.txt` |

```text
DoAn/
├── DOANTOTNGHIEPFE/
│   ├── src/app/core/       # models, services, guards, interceptor
│   ├── src/app/features/   # auth, tenant, landlord, admin
│   ├── src/app/shared/     # components, pages, utils
│   ├── src/environments/  # API và MapTiler
│   ├── postman/, scripts/ # mock và smoke/E2E
│   └── docs/              # tài liệu API/UI còn dùng
├── RentalManagement/
│   ├── src/main/java/com/tamdaptit/rentalmanagement/
│   │                       # auth, identity, property, room, listing,
│   │                       # rental, chat, review, report, payment...
│   ├── src/main/resources/db/migration/  # Flyway V1/V2 và V6 thông báo
│   ├── src/main/resources/reports/       # mẫu hợp đồng PDF
│   └── scripts/            # setup DB, sinh seed, proxy IPN
└── AIService/
    ├── app/                # API, chat, recommendation, embedding, JWT/log
    ├── alembic/            # migration rental_ai
    ├── scripts/            # setup, seed AI, build vector, đánh giá
    ├── tests/, data/       # kiểm thử và câu hỏi đánh giá
    └── README.md           # tài liệu chung này
```

Giữ file lock, migration, font/license, test và script vận hành đang dùng. Không xem class được Spring tự đăng ký, migration Alembic hoặc file environment được Angular thay thế lúc build là code thừa chỉ vì không có import trực tiếp.

## 1. Kiến trúc cơ bản

```text
Frontend :4200
  ├── /api, /ws → Spring Boot :8080 → rental_db (Flyway V1 + V2 + V6)
  └── /ai       → FastAPI :8000
                    ├── chỉ đọc dữ liệu nghiệp vụ rental_db
                    ├── đọc/ghi rental_ai (Alembic)
                    ├── Ollama Docker :11434 → Qwen3 4B Instruct
                    └── Rules / câu trả lời mẫu khi cần
```

- Chatbot: trích giá, khu vực, diện tích, tiện ích, số người → tìm phòng bằng SQL → trả card và nội dung qua SSE → lưu hội thoại/log.
- Hỗ trợ hỏi “Profile của tôi hiện tại gồm những điều kiện gì?” để xem các điều kiện đang lưu, hoặc “Gợi ý phòng dựa trên profile của tôi” để dùng bộ gợi ý theo hồ sơ. Đọc theo user ID trong JWT; không lấy ID/email trong câu chat và không gửi hồ sơ cho LLM. Sau khi xem hồ sơ có thể trả lời “Có, gợi ý đi nhé”. Nếu thiếu hồ sơ hoặc đang tắt cá nhân hóa, chatbot hướng dẫn bổ sung/bật lại, không tự đổi thiết lập. Xem hồ sơ vẫn hoạt động khi cá nhân hóa tắt.
- Gợi ý theo hồ sơ dùng khu vực, giá, diện tích, loại phòng, số người, tiện ích bắt buộc/ưu tiên, điểm mốc và mức dung sai theo bộ recommendation hiện có. Các tiêu chí được chấm điểm có thể chưa khớp hoàn toàn; câu trả lời ghi rõ chênh lệch giá, diện tích, loại phòng, sức chứa hoặc khoảng cách. Ngày dọn vào, nhóm người thuê và ghi chú được hiển thị như thông tin đã lưu, chưa phải điều kiện lọc được bảo đảm bởi recommendation.
- Chỉ dùng tin PUBLISHED còn hạn, phòng AVAILABLE, tin công khai mới nhất của cơ sở. Bộ lọc phải khớp cùng một phòng.
- Khi không có kết quả: ưu tiên phòng ngoài phường nhưng trong 3 km từ centroid phường, cùng tỉnh và đúng giá; tiếp đến nới giá trong khu vực yêu cầu tối đa 20%, cuối cùng kết hợp lân cận và nới giá. Giữ tiện ích, diện tích, loại phòng và số người. Ghi rõ phần chưa khớp và hỏi thêm; không ghi điều kiện nới vào bộ lọc gốc.
- Centroid tỉnh/phường do backend đồng bộ từ [tinhthanhpho.com](https://www.tinhthanhpho.com/en/api-docs), lưu ở `centroid_lat`, `centroid_lng`. Không có centroid thì không suy đoán lân cận. Khoảng cách tính theo đường thẳng tới tọa độ cơ sở, không phải quãng đường đi.
- AI recommendation dùng hồ sơ, embedding/hành vi, độ mới, ưu tiên và MMR. Tọa độ điểm mốc trong hồ sơ được dùng nếu có; chatbot chưa nhận vị trí hiện tại của trình duyệt.
- Embedding là `intfloat/multilingual-e5-small`, chạy CPU trong Python, độc lập với Ollama; build lại sau khi dữ liệu tin thay đổi.
- Local LLM dùng `qwen3:4b-instruct-2507-q4_K_M`. Tag chung `qwen3:4b` đã tải trước đó là bản Thinking, chậm cho tác vụ này. Prompt gọn, giới hạn EXTRACT 512 token, COMPOSE 384 token, chỉ gửi chi tiết tối đa 5 phòng để soạn lời; card frontend vẫn đầy đủ.

`ALLOW_EXTERNAL_FALLBACK=false` hiện được giữ: chuỗi **Local → Rules**. Nếu bật lại thì Local → Gemini/Anthropic → Rules. HTTP 200 của chat chỉ xác nhận mở được stream; kiểm tra provider trong `llm_call_logs` để biết local có hoàn thành hay không.

API: `GET /ai/health`, `GET /ai/me`, `GET /ai/recommendations`, `POST /ai/chat`, `POST /ai/events`, `POST /ai/feedback`. Ngoại trừ health, cần JWT access của tenant ACTIVE, cùng HS512/secret/issuer với backend. Swagger: `http://localhost:8000/docs`.

| Source | Chức năng |
| --- | --- |
| `app/main.py`, `app/config.py` | Khởi động, cấu hình, chuỗi provider |
| `app/chat/`, `app/chat/suggestions.py` | Trích bộ lọc, tìm phòng, phương án lân cận/nới giá |
| `app/db/inventory.py` | Điều kiện dữ liệu công khai dùng chung |
| `app/recommend/`, `app/embeddings/` | Gợi ý và vector |
| `app/core/`, `app/observability/` | JWT, quota, log |
| `alembic/`, `scripts/` | Migration và các công cụ setup/seed/đo thử |

## 2. Lần đầu chạy: dựng cả hai DB và dữ liệu mẫu

### 2.1. Chuẩn bị

Docker Desktop với Linux containers và Docker Compose; JDK 21 (đặt `JAVA_HOME` vào JDK, cần cả `javac`); Python 3.11 và Python launcher `py`; Node.js 26.0.0 là phiên bản đã kiểm tra trên workspace này. Angular 22 yêu cầu Node phù hợp bảng tương thích chính thức; không dùng Node 20 cho source này. Maven Wrapper đã có trong BE, không bắt buộc cài Maven riêng. Internet cần cho lần đầu tải dependency/image, đồng bộ địa chỉ, tải model Qwen và embedding. Các thư mục:

```text
E:\SpringBoot\DoAn\RentalManagement
E:\SpringBoot\DoAn\AIService
E:\SpringBoot\DoAn\DOANTOTNGHIEPFE
```

Kiểm tra công cụ:

```powershell
docker version
docker compose version
java -version
javac -version
py -3.11 --version
node --version
npm --version
```

Tại BE, nếu chưa có `.env`, sao chép `.env.docker` sang `.env`, rồi chỉnh cấu hình; giữ `.env` đã cấu hình. Backend tự đọc file này qua `optional:file:.env[.properties]` khi chạy từ thư mục BE. Compose cũng dùng `.env` cho hạ tầng.

```dotenv
MYSQL_ROOT_PASSWORD=CHANGE_ME_MYSQL
MYSQL_DATABASE=rental_db
MYSQL_PORT=3306
DB_HOST=localhost
DB_PORT=3306
DB_NAME=rental_db
DB_USERNAME=root
DB_PASSWORD=CHANGE_ME_MYSQL
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=CHANGE_ME_REDIS
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=admin
RABBITMQ_PASSWORD=CHANGE_ME_RABBITMQ
MAIL_HOST=localhost
MAIL_PORT=1025
MAIL_FROM=noreply@rental.local
JWT_SECRET=REPLACE_WITH_THE_SAME_RANDOM_SECRET_OF_AT_LEAST_64_BYTES_IN_BOTH_SERVICES
```

Thay toàn bộ placeholder; `DB_PASSWORD` phải khớp `MYSQL_ROOT_PASSWORD` nếu BE dùng root. JWT secret phải đủ ít nhất 64 byte cho HS512 và giữ đúng cùng chuỗi trong BE/AI. Cổng host MySQL đổi thì cập nhật cả `MYSQL_PORT`, `DB_PORT` và hai URL DB AI. RabbitMQ local hiện dùng cổng 5672 trong `application.yaml`, vì vậy giữ mapping 5672 hoặc chỉnh cấu hình tương ứng. Với volume MySQL/Redis/RabbitMQ đã tồn tại, sửa `.env` không tự thay credential đã lưu trong dịch vụ.

Ảnh và hợp đồng PDF dùng Cloudinary. Cấu hình qua `.env` BE bằng các property bên dưới để ghi đè giá trị trong YAML:

```dotenv
app.cloudinary.cloud-name=YOUR_CLOUD_NAME
app.cloudinary.api-key=YOUR_API_KEY
app.cloudinary.api-secret=YOUR_API_SECRET
```

Không cần Cloudinary để xem dữ liệu ảnh mẫu; cần tài khoản hợp lệ để upload/tạo PDF thật. Cấu hình VNPay và bản đồ xem mục 5.

### 2.2. Tạo rental_db, đồng bộ địa chỉ rồi seed

Tại terminal backend, khi DB mới chưa có V2:

```powershell
Set-Location E:\SpringBoot\DoAn\RentalManagement
.\scripts\setup-demo.ps1
```

Script tự bật Docker hạ tầng → Maven clean → Flyway tới V1 tạo 49 bảng → đồng bộ 34 tỉnh và 3.321 phường từ API → xuất snapshot → sinh V2 dựa trên địa chỉ vừa đồng bộ → áp dụng các migration còn lại → thoát. Sau khi merge main, migration V6 bổ sung `notification_outbox` và index thông báo; tổng cộng 50 bảng nghiệp vụ. Giữ V6 riêng để database đã có V1/V2 có thể nâng cấp; không gộp thay đổi mới vào V1 đã áp dụng. Không tạo quận/huyện riêng vì nguồn và schema dùng tỉnh → phường/xã.

Mặc định có 1.071 tài khoản (3 admin, 68 landlord, 1.000 tenant), 1.088 cơ sở/tin đăng, 7.752 phòng, hơn 26.000 ảnh, 6.000 review, 1.000 report và dữ liệu cho mọi bảng nghiệp vụ. Số lượng chi tiết ở `RentalManagement/scripts/data/demo-counts.json`. Ảnh giữ từ seed cũ.

Muốn tăng số lượng trước khi dựng DB mới:

```powershell
.\scripts\setup-demo.ps1 -Landlords 136 -Tenants 2000 -PropertiesPerLandlord 16 -RentalsPerTenant 3
```

Không chạy lại setup hoặc sửa V2 sau khi V2 đã áp dụng. Script không xóa DB; nếu đã có V2, dùng lệnh khởi động bình thường ở mục 3. Snapshot trong repo cho phép dựng offline bằng V1/V2 đã sinh: bật hạ tầng rồi chạy BE bình thường để Flyway áp dụng V1/V2, bỏ qua bước đồng bộ mới. Script đồng bộ mới nhất cần mạng. Database có lịch sử migration cũ V1–V6/V14/V15 không được coi là DB mới; sao lưu và lập phương án chuyển đổi riêng, không sửa checksum hay dùng repair để ép khớp.

Muốn xem trước bộ seed lớn hơn, chưa áp dụng vào DB:

```powershell
python scripts/generate-demo-seed.py --landlords 136 --tenants 2000 --properties-per-landlord 16 --rentals-per-tenant 3 --output demo-preview.sql
```

Generator dùng snapshot `scripts/data/administrative-units.json` và `reference-catalog.sql`; ID tỉnh/phường được tra theo mã hành chính. Landlord/tenant tối thiểu 4, cơ sở mỗi landlord chọn 8 hoặc 16, số hợp đồng lịch sử tối thiểu 1.

### 2.3. Tạo môi trường Python và cấu hình AI

```powershell
Set-Location E:\SpringBoot\DoAn\AIService
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Nếu đã có venv đúng phiên bản thì bỏ dòng tạo lại. Nếu chưa có `.env`, sao chép `.env.example`, sau đó điền cấu hình. Các giá trị chính:

```dotenv
CORE_DB_URL=mysql+pymysql://ai_svc:URL_ENCODED_PASSWORD@localhost:3306/rental_db
AI_DB_URL=mysql+pymysql://ai_svc:URL_ENCODED_PASSWORD@localhost:3306/rental_ai
JWT_ALG=HS512
JWT_SECRET=DIEN_DUNG_SECRET_CUA_BACKEND
JWT_ISSUER=rental-management
LOCAL_LLM_BASE_URL=http://localhost:11434/v1
LOCAL_LLM_MODEL=qwen3:4b-instruct-2507-q4_K_M
LOCAL_LLM_API_KEY=ollama
LOCAL_TIMEOUT_SECONDS=90
CHAT_TIMEOUT_SECONDS=240
CHAT_NEARBY_RADIUS_KM=3
CHAT_PRICE_TOLERANCE_PCT=20
ALLOW_EXTERNAL_FALLBACK=false
EMBEDDING_MODEL=intfloat/multilingual-e5-small
EMBEDDING_DIM=384
CORS_ORIGINS=["http://localhost:4200","http://localhost"]
```

Thay password/port cho đúng; URL-encode ký tự đặc biệt trong mật khẩu URL. `JWT_SECRET` là đúng chuỗi backend dùng, không tự base64-decode. Các biến môi trường của process ưu tiên hơn `.env`. Restart Uvicorn sau khi đổi cấu hình.

### 2.4. Tạo rental_ai, cấp quyền, migration và seed

Sau khi backend đã seed xong:

```powershell
Set-Location E:\SpringBoot\DoAn\AIService
.\scripts\setup_demo.ps1
```

Script dùng Python trong venv, lần lượt:

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_database
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m scripts.seed_demo_ai --users 1000
.\.venv\Scripts\python.exe -m scripts.build_embeddings
```

`prepare_database` dùng credential quản trị MySQL local từ `.env` backend để tạo schema AI và grant runtime; không in secret và không cấp quyền ghi core. Nếu user `ai_svc` đã tồn tại thì mật khẩu không tự đổi; cấu hình AI phải dùng đúng mật khẩu đó. Với môi trường khác, quản trị DB có thể áp dụng [setup_database.sql](scripts/setup_database.sql) sau khi thay placeholder.

Alembic quản lý riêng `rental_ai`, head hiện tại `20261030_ai_integration`. `seed_demo_ai` đọc tenant/tin công khai thật trong core và chỉ upsert fixture AI theo ID cố định. Bao gồm hội thoại, feedback, recommendation snapshots/events, cache, usage ngày cũ, lock, lease đã hết hạn, sync state và eval fixture. Tất cả đều gắn nhãn demo; không phải kết quả đo chất lượng model. Không gọi LLM, không tính vào quota chat hôm nay.

`build_embeddings` tạo vector thật và log đồng bộ thật, hoàn tất dữ liệu cho các bảng AI còn lại. Lần đầu có thể tải model. Không dùng vector ngẫu nhiên làm giả embedding. Dữ liệu nghiệp vụ được seed bằng V2 của BE; AI chỉ seed bảng riêng trong `rental_ai`.

### 2.5. Chạy model bằng Docker

```powershell
Set-Location E:\SpringBoot\DoAn\AIService
docker compose up -d ollama
docker compose exec ollama ollama pull qwen3:4b-instruct-2507-q4_K_M
docker compose exec ollama ollama list
docker compose exec ollama ollama run qwen3:4b-instruct-2507-q4_K_M "Chỉ trả lời: OK"
Invoke-RestMethod http://localhost:11434/api/tags
docker compose exec ollama ollama ps
```

Compose hiện bật NVIDIA GPU cho RTX 3050 trên máy này, giữ model 24 giờ, xử lý một lượt mỗi lần để hạn chế VRAM. Máy không có NVIDIA thì bỏ block `deploy.resources.reservations.devices`. Model lưu trong volume, không cần pull mỗi lần. Không chạy thêm Ollama trực tiếp trên Windows cùng cổng 11434.

### 2.6. Mở ứng dụng bằng các terminal riêng

**Terminal 1 — backend:**

```powershell
Set-Location E:\SpringBoot\DoAn\RentalManagement
.\mvnw.cmd spring-boot:run
```

**Terminal 2 — AIService:**

```powershell
Set-Location E:\SpringBoot\DoAn\AIService
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Terminal 3 — health và frontend:**

```powershell
Invoke-RestMethod http://localhost:8000/ai/health | ConvertTo-Json -Depth 6
Set-Location E:\SpringBoot\DoAn\DOANTOTNGHIEPFE
npm ci
npm start
```

Giữ các terminal phục vụ ứng dụng mở. Health cần hai DB UP, có vector và `llm.external.enabled=false`. Health không tự gọi thử Ollama. Mở `http://localhost:4200`; đăng nhập `tenant@gmail.com` / `123456`. Admin/chủ trọ mẫu: `admin@gmail.com`, `landlord@gmail.com`, cùng mật khẩu. Hỏi thử “Tìm phòng dưới 3 triệu ở phường Cầu Giấy, có WiFi”.

## 3. Từ lần thứ hai: chỉ khởi động, không seed lại

Mở Docker Desktop rồi chạy:

**Terminal 1:**

```powershell
Set-Location E:\SpringBoot\DoAn\RentalManagement
docker compose up -d
docker compose ps
.\mvnw.cmd spring-boot:run
```

Chờ MySQL healthy trước dòng Maven.

**Terminal 2:**

```powershell
Set-Location E:\SpringBoot\DoAn\AIService
docker compose up -d ollama
docker compose exec ollama ollama run qwen3:4b-instruct-2507-q4_K_M "Chỉ trả lời: OK"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Terminal 3:**

```powershell
Invoke-RestMethod http://localhost:8000/ai/health | ConvertTo-Json -Depth 6
Set-Location E:\SpringBoot\DoAn\DOANTOTNGHIEPFE
npm start
```

Không cần activate venv, cài lại thư viện, pull model, migrate hoặc seed nếu không có thay đổi. Chỉ chạy `build_embeddings` khi thêm/sửa tin; chỉ chạy Alembic khi có migration AI mới. Service định kỳ nạp lại vector từ DB, không tự chạy job build.

Dừng Uvicorn/backend/frontend bằng `Ctrl+C`. Tại AIService dùng `docker compose stop ollama`; tại backend dùng `docker compose stop` nếu muốn dừng hạ tầng. Giữ volume, không dùng `down -v` khi muốn giữ DB và model.

## 4. Đo tốc độ và xử lý lỗi

```powershell
Set-Location E:\SpringBoot\DoAn\AIService
.\.venv\Scripts\python.exe -m scripts.benchmark_local
```

Script thử local trực tiếp, không có fallback/DB; tách thời gian warmup khỏi EXTRACT/COMPOSE và kiểm tra một số điều kiện mẫu. Trước đây trên RTX 3050 đã đo EXTRACT khoảng 2–5 giây, COMPOSE khoảng 3,6 giây cho một phòng; không bảo đảm thời gian cho mọi câu hoặc nhiều người dùng. Hỏi tiếp có thể tận dụng model/cache đã nạp; nhiều lượt đồng thời sẽ xếp hàng, không tự huấn luyện model.

```sql
SELECT request_id,step,provider,status,latency_ms,error_detail
FROM rental_ai.llm_call_logs ORDER BY created_at DESC LIMIT 20;
```

Fixture seed có model `demo-fixture-v1`; loại chúng khỏi thống kê thực tế. Lượt dùng thật cần thấy `local/SUCCESS`; `rules` là fallback. Nhánh không có kết quả/gợi ý nới lọc dùng template nên có thể không gọi COMPOSE.

| Lỗi | Kiểm tra |
| --- | --- |
| Local ConnectError | Ollama, cổng 11434, `/api/tags` và log container |
| Local timeout/Thinking dài | Dùng đúng tag Instruct, làm nóng model, xem CPU/GPU bằng `ollama ps` |
| Health 503/thiếu cột centroid | Chạy đúng V1 mới của backend và migration AI; kiểm tra grant |
| 401/403 | JWT secret/issuer/HS512, tenant ACTIVE, authVersion và role |
| Không có kết quả lân cận | Centroid và tọa độ cơ sở có thật, phòng còn trống, đúng giá/tiện ích |
| Setup core báo DB đã vượt V1 | DB đã seed; dùng mục 3, không sinh lại V2 đã áp dụng |
| Vẫn thấy Gemini | Restart đúng Uvicorn; kiểm tra biến môi trường và external.enabled |

Tọa độ cơ sở/ảnh/giao dịch seed là mô phỏng để kiểm tra chức năng; centroid hành chính đến từ nguồn công khai, ghi nguồn tinhthanhpho.com / HVN Group, CC BY 4.0. Review demo không phải đánh giá người dùng thật. PDF seed chỉ có snapshot/tracking trạng thái chưa tạo tài liệu, không có upload Cloudinary hoặc giao dịch thanh toán thật.

## 5. Cấu hình FE, dịch vụ ngoài và Docker frontend

### Thông báo và cache sau khi cập nhật main

FE có chuông thông báo realtime; BE dùng Redis cache, RabbitMQ và `notification_outbox` (Flyway V6). Backend tự áp dụng V6 khi khởi động; không seed lại database đã có V2. Có thể cấu hình `CACHE_TTL=30m`, `NOTIFICATIONS_RELAY_ENABLED=true`, `NOTIFICATIONS_RELAY_DELAY_MS=2000` trong `.env` BE. Phần đồng bộ địa chỉ vẫn giữ `ADDRESS_SYNC_ENABLED=false` khi chạy bình thường. Chi tiết: [thông báo Redis/RabbitMQ](../RentalManagement/docs/REDIS_RABBITMQ_NOTIFICATIONS.md), [kiến trúc backend](../RentalManagement/docs/BE_ARCHITECTURE_AND_DATABASE.md).

### 5.1. Frontend Angular

`npm ci` cài đúng `package-lock.json`; chỉ cần cài lại khi dependency thay đổi. `npm start` dùng cấu hình development. API trong trình duyệt là đường dẫn tương đối:

| Đường dẫn | Đích local trong `proxy.conf.json` | Chức năng |
| --- | --- | --- |
| `/api` | `http://localhost:8080` | REST backend |
| `/ws` | `http://localhost:8080` với WebSocket | Chat/thông báo |
| `/ai` | `http://localhost:8000` | AI, SSE |

`environment.ts` là mặc định, `environment.development.ts` được thay khi serve; `environment.docker.ts` được thay khi build `production-docker`. Đổi cổng backend/AI thì sửa proxy; đổi đường dẫn API thì sửa environment tương ứng. Không xóa file environment có trong `fileReplacements` dù không được import trực tiếp.

```powershell
Set-Location E:\SpringBoot\DoAn\DOANTOTNGHIEPFE
npm run build
# Hoặc build cấu hình dùng cho image Nginx:
npm run build -- --configuration production-docker
```

Output nằm ở `dist/library-frontend/browser`. `npm run build` chỉ tạo frontend, không khởi động backend hoặc AIService.

### 5.2. Bản đồ và vị trí

Chỉnh key trình duyệt tại `DOANTOTNGHIEPFE/src/environments/maptiler.config.ts`, dùng MapTiler Cloud cho style/tiles/geocoding và MapLibre để render. Giới hạn allowed origins của key cho localhost và domain triển khai; build lại sau khi đổi. Giữ attribution MapTiler/OpenStreetMap. Không cần Google Maps SDK. Định vị trình duyệt cần localhost hoặc HTTPS và quyền người dùng; vẫn có thể nhập tọa độ/chọn ghim thủ công. Tọa độ thuộc cơ sở, dùng chung cho các phòng/tin. Chi tiết: [bản đồ FE](../DOANTOTNGHIEPFE/docs/maps.md).

### 5.3. VNPay và PDF

Đặt trong `.env` BE khi thử thanh toán sandbox:

```dotenv
VNPAY_TMN_CODE=YOUR_SANDBOX_TERMINAL
VNPAY_HASH_SECRET=YOUR_SANDBOX_SECRET
VNPAY_PAYMENT_URL=https://sandbox.vnpayment.vn/paymentv2/vpcpay.html
VNPAY_RETURN_URL=http://localhost:4200/thanh-toan/ket-qua
VNPAY_QUERY_URL=https://sandbox.vnpayment.vn/merchant_webapi/api/transaction
VNPAY_QUERY_IP=127.0.0.1
```

Thiếu hash secret, checkout báo 503; các module khác vẫn chạy. Đăng ký IPN URL HTTPS public kết thúc `/api/payments/vnpay/ipn` tại merchant. Return về FE không tự xác nhận thu tiền; BE kiểm tra IPN hoặc QueryDr. Công cụ tunnel/proxy và luồng kiểm thử nằm trong [VNPay sandbox](../RentalManagement/docs/VNPAY_SANDBOX.md).

Mẫu hợp đồng ở `RentalManagement/src/main/resources/reports/rental-contract.jrxml`; font NotoSans và license đi kèm phải được giữ. PDF thật cần Cloudinary hợp lệ và xử lý job; file seed chỉ chứa tracking mô phỏng. Các property `app.rental.jobs-enabled`, `app.rental.job-delay-ms`, `app.rental.cleanup-delay-ms`, `app.rental.contract-terms` có thể đặt trong `.env` BE. Chi tiết quyền truy cập, lease/job và template: [luồng thuê và PDF](../RentalManagement/docs/RENTAL_BOOKING.md).

### 5.4. Chạy frontend trong Docker (tùy chọn)

Sau khi BE và AIService chạy trên host:

```powershell
Set-Location E:\SpringBoot\DoAn\DOANTOTNGHIEPFE
docker compose up -d --build
docker compose ps
```

Mở `http://localhost` (cổng 80). Compose FE build Angular rồi serve bằng Nginx, mount `nginx.local.conf` và proxy về host qua `host.docker.internal`. Đây là lựa chọn thay cho `npm start`. Compose BE chỉ chạy hạ tầng; Compose AI chỉ chạy Ollama. Ba file Compose chưa cung cấp một lệnh dựng toàn bộ application trong container.

`nginx.conf` dành cho triển khai TLS, cần certificate mount, domain và upstream `backend`/`ai-service` có thể phân giải trong mạng Docker. Không dùng nguyên cấu hình này như một hệ thống production đã hoàn chỉnh. `application-docker.yaml` và AI `Dockerfile` được giữ cho trường hợp tự đóng gói dịch vụ.

## 6. Địa chỉ truy cập và kiểm thử

| Dịch vụ | URL/cổng mặc định |
| --- | --- |
| FE development / Docker | `http://localhost:4200` / `http://localhost` |
| BE / Swagger | `http://localhost:8080/api` / `http://localhost:8080/swagger-ui/index.html` |
| AI Swagger / health | `http://localhost:8000/docs` / `http://localhost:8000/ai/health` |
| MySQL / Redis | `localhost:3306` / `localhost:6379` |
| phpMyAdmin | `http://localhost:8081` |
| RabbitMQ management | `http://localhost:15672` |
| Elasticsearch | `http://localhost:9200` |
| MailHog web / SMTP | `http://localhost:8025` / `localhost:1025` |
| Ollama | `http://localhost:11434` |

Kiểm thử tự động từ từng thư mục:

```powershell
# FE
npm test -- --watch=false
npm run build

# BE: compile và unit test không cần DB cho mẫu PDF
.\mvnw.cmd -DskipTests package
.\mvnw.cmd '-Dtest=RentalContractPdfServiceTest' test

# AIService: pytest dùng SQLite fixture, không cần Ollama để chạy unit test
.\.venv\Scripts\python.exe -m pytest -q
```

Một số test BE cần MySQL thật. Cấp riêng các biến sau trong terminal test rồi chạy nhóm phù hợp; các test này tạo schema có tên ngẫu nhiên, tài khoản test cần quyền tạo/xóa schema test:

```powershell
$env:SUBSCRIPTION_TEST_MYSQL_URL='jdbc:mysql://127.0.0.1:3306/'
$env:SUBSCRIPTION_TEST_MYSQL_USER='YOUR_TEST_DB_USER'
$env:SUBSCRIPTION_TEST_MYSQL_PASSWORD='YOUR_TEST_DB_PASSWORD'
.\mvnw.cmd '-Dtest=SeedDataMigrationTest,AddressCentroidMigrationTest,AddressSyncTest' test
```

Kiểm tra thủ công: đăng nhập đủ ba role; tenant tìm/lưu tin và chat; landlord sửa phòng/tin; gửi và xử lý yêu cầu thuê; kiểm tra PDF khi Cloudinary đã cấu hình; thử AI với tenant ACTIVE. Email xác minh/reset mật khẩu xem trong MailHog. Các script E2E/smoke trong FE `scripts/`, `postman/` vẫn được giữ; đọc cấu hình mock/port trong script trước khi chạy, không mặc định gọi dữ liệu thật.

AI có `scripts.benchmark_local` để đo model, `scripts.evaluate_chat` và `scripts.evaluate_recsys` để đánh giá. Kết quả trên seed/fixture không chứng minh chất lượng với người dùng thật. `scripts.purge_logs` phục vụ retention; `scripts.build_embeddings` cần chạy lại khi thay tin. Công cụ `make_token`/`smoke_test` dùng `SEED_DB_URL` cấu hình riêng cho môi trường thử, không dùng credential runtime AI để nâng quyền ghi core.

## 7. Xử lý lỗi cài đặt và tài liệu chi tiết

| Hiện tượng | Cách xử lý |
| --- | --- |
| FE báo Node không được hỗ trợ | Kiểm tra `node --version`, `node_modules/@angular/cli/package.json` và bảng tương thích Angular; dùng Node phù hợp Angular 22 |
| `npm ci` báo lock không khớp | Đối chiếu `package.json`/`package-lock.json`; chỉ cập nhật lock bằng `npm install` khi chủ động đổi dependency |
| Maven dùng nhầm Java | Kiểm tra `JAVA_HOME`, `javac -version` và `.\mvnw.cmd -version`; phải dùng JDK 21 |
| BE không kết nối MySQL/Redis/RabbitMQ | Chạy `docker compose ps`, kiểm tra health, cổng, `.env`, credential thực trong volume và log container |
| Flyway checksum/version lỗi | Kiểm tra lịch sử DB; bộ V1/V2 dành cho DB mới, không repair checksum để bỏ qua khác biệt |
| Python launcher thiếu 3.11 | Cài Python 3.11 rồi tạo `.venv`; gọi đúng `.venv\Scripts\python.exe`, không phụ thuộc activate |
| PowerShell chặn script | Chạy PowerShell với execution policy được môi trường cho phép; vẫn có thể chạy từng lệnh Python/Maven tương ứng trong hướng dẫn |
| AI Access denied hoặc user đã tồn tại | Kiểm tra password `ai_svc`, URL-encode password trong URL, quyền SELECT core và quyền schema AI; setup không tự đổi password user cũ |
| Ollama báo không có GPU phù hợp | Máy không dùng NVIDIA: bỏ block reservation GPU trong Compose AI rồi tạo lại container; CPU suy luận chậm hơn |
| FE 502/AI stream bị ngắt | Kiểm tra BE/AI có chạy, proxy đúng host/port; Nginx cho SSE cần tắt buffering và timeout phù hợp |
| PDF FAILED hoặc upload ảnh lỗi | Kiểm tra Cloudinary, log job và cấu hình; bản ghi PDF demo FAILED là có chủ ý |

Tài liệu còn dùng:

- [API backend](../RentalManagement/docs/api.md), [Property API](../RentalManagement/docs/PROPERTY_API.md), [thuê phòng và PDF](../RentalManagement/docs/RENTAL_BOOKING.md), [VNPay](../RentalManagement/docs/VNPAY_SANDBOX.md).
- [API phía FE](../DOANTOTNGHIEPFE/docs/api.md), [quản lý cơ sở](../DOANTOTNGHIEPFE/docs/PROPERTY_UI.md), [cây loại phòng](../DOANTOTNGHIEPFE/docs/ROOM_TYPE_TREE.md), [UI hợp đồng](../DOANTOTNGHIEPFE/docs/RENTAL_CONTRACT_UI.md), [bản đồ](../DOANTOTNGHIEPFE/docs/maps.md).
- [Tương thích Angular/Node](https://angular.dev/reference/versions), [cấu hình Spring Boot](https://docs.spring.io/spring-boot/reference/features/external-config.html), [chạy FastAPI/Uvicorn](https://fastapi.tiangolo.com/deployment/manually/).

## 8. Kết quả dọn source và kiểm tra ngày 09/11/2026

Đã loại bỏ 44 file, gồm 11 file Markdown: màn/component FE cũ không còn route/import, file environment trùng không được build dùng, DTO/barrel không có tham chiếu, mock data không còn dùng (giữ interface card trong `room-card.model.ts`), template HTML thiết kế cũ, utility Java không có tham chiếu, POM kiểm tra tạm, SQL Boost rời cũ, seed nghiệp vụ AI đã được thay thế, log/kết quả chạy sinh ra và tài liệu kế hoạch/schema cũ. Hướng dẫn BE/AI đã gộp vào file này; tài liệu API, quy tắc phát triển, nghiệp vụ và kiểm thử đang dùng vẫn giữ. Phạm vi rà soát gồm cả file mới/chưa commit.

| Kiểm tra sau dọn | Kết quả |
| --- | --- |
| FE `npm run build` | Thành công; còn cảnh báo RouterLink dư và SCSS vượt ngưỡng cảnh báo |
| FE `npm test -- --watch=false` | 40 file test, 221 test đạt; jsdom báo canvas chưa triển khai |
| BE `.\mvnw.cmd -DskipTests package` | Thành công; compile cả test, không chạy test BE |
| AI `.\.venv\Scripts\python.exe -m pytest -q` | 38 test đạt |
| Tham chiếu TypeScript và link Markdown nội bộ | Không còn file TS mồ côi ngoài entry point/environment build; không có link nội bộ bị hỏng |

Đợt kiểm tra này không dựng lại DB/seed, không chạy giao dịch VNPay, upload Cloudinary hoặc toàn bộ luồng tích hợp với model thật. Không thay dữ liệu ứng dụng đang có.

Sau khi cập nhật `origin/main` vào `refactor` ngày 09/11/2026: FE build thành công, 42 file/229 test FE đạt; BE compile source/test và chạy 34 test kiến trúc, cache, WebSocket, liên hệ tin, review, validation thuê và PDF đều đạt. Hai conflict BE (README và application.yaml) đã được giải quyết bằng cách giữ tài liệu chung ở AIService, đồng thời giữ cả cấu hình đồng bộ địa chỉ và cache/thông báo mới. Chưa chạy nhóm test hạ tầng thông báo cần MySQL/Redis/RabbitMQ thật.
