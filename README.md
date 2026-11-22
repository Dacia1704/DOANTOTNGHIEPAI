# Rental Management — hướng dẫn chung FE, BE và AIService

Cập nhật 15/11/2026. Tài liệu chung cho ba source `DOANTOTNGHIEPFE` (FE), `RentalManagement` (BE) và `AIService`. Các lệnh dưới đây dùng PowerShell trên Windows và cấu trúc ba thư mục nằm cạnh nhau. Có thể thay `E:\SpringBoot\DoAn` bằng thư mục của bạn.

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
│   ├── scripts/          # tiện ích phát triển (test đã tạm gỡ)
│   └── docs/              # tài liệu API/UI còn dùng
├── RentalManagement/
│   ├── src/main/java/com/tamdaptit/rentalmanagement/
│   │                       # auth, identity, property, room, listing,
│   │                       # rental, chat, review, report, payment...
│   ├── src/main/resources/db/migration/  # Flyway: V1/V2 lõi, V6–V12 nâng cấp và seed cộng đồng
│   ├── src/main/resources/reports/       # mẫu hợp đồng PDF
│   └── scripts/            # setup DB, sinh seed, proxy IPN
└── AIService/
    ├── app/                # API, chat, recommendation, embedding, JWT/log
    ├── alembic/            # migration rental_ai
    ├── scripts/            # setup, seed AI, build vector, đánh giá
    ├── data/               # câu hỏi và dữ liệu đánh giá AI
    └── README.md           # tài liệu chung này
```

Giữ file lock, migration, font/license, test và script vận hành đang dùng. Không xem class được Spring tự đăng ký, migration Alembic hoặc file environment được Angular thay thế lúc build là code thừa chỉ vì không có import trực tiếp.

## 1. Kiến trúc cơ bản

```text
Frontend :4200
  ├── /api, /ws → Spring Boot :8080 → rental_db (Flyway V1, V2, V6, V7, V8, V9, V10, V11, V12)
  └── /ai       → FastAPI :8000
                    ├── chỉ đọc dữ liệu nghiệp vụ rental_db
                    ├── đọc/ghi rental_ai (Alembic)
                    ├── Ollama Docker :11434 → Qwen3 4B Instruct
                    └── Rules / câu trả lời mẫu khi cần
```

- Chatbot: trích giá, khu vực, diện tích, tiện ích, số người → tìm phòng bằng SQL → trả card và nội dung qua SSE → lưu hội thoại/log.
- Câu chào hỏi, cảm ơn, tạm biệt và giới thiệu trợ lý được trả lời nhanh theo `app/chat/small_talk.py`; có thể thêm mẫu câu/câu đáp trong `SMALL_TALK`. Chỉ khớp toàn câu để không bỏ mất điều kiện trong câu như “alo, tìm phòng dưới 3tr”. Trạng thái chờ trên giao diện là “Đang suy nghĩ…”.
- Với câu còn lại, local trích ý định trước. Nếu trả `CLARIFY`, `OUT_OF_SCOPE` hoặc yêu cầu tìm mới không có bộ lọc, Gateway chuyển ngay sang API ngoài để diễn giải và hỏi lại tự nhiên hơn; lỗi/timeout/JSON sai vẫn chuyển tầng như trước. Câu trả lời làm rõ của API ngoài được dùng trực tiếp, không thay bằng lời từ chối cố định. Nếu API ngoài tắt, lỗi, mở circuit breaker hoặc hết hạn mức, hệ thống giữ câu hỏi làm rõ của local; khi mọi model lỗi thì dùng Rules. Bộ lọc lượt trước được giữ khi chào hỏi hoặc hỏi lại.
- Muốn dùng API ngoài, đặt `ALLOW_EXTERNAL_FALLBACK=true` và cấu hình khóa của `EXTERNAL_PROVIDER` trong môi trường chạy AIService, rồi khởi động lại dịch vụ. Vẫn áp dụng `EXTERNAL_DAILY_CAP`, circuit breaker và che thông tin cá nhân trước khi gửi ra ngoài.
- Hỗ trợ hỏi “Profile của tôi hiện tại gồm những điều kiện gì?” để xem các điều kiện đang lưu, hoặc “Gợi ý phòng dựa trên profile của tôi” để dùng bộ gợi ý theo hồ sơ. Đọc theo user ID trong JWT; không lấy ID/email trong câu chat và không gửi hồ sơ cho LLM. Sau khi xem hồ sơ có thể trả lời “Có, gợi ý đi nhé”. Nếu thiếu hồ sơ hoặc đang tắt cá nhân hóa, chatbot hướng dẫn bổ sung/bật lại, không tự đổi thiết lập. Xem hồ sơ vẫn hoạt động khi cá nhân hóa tắt.
- Các câu tư vấn chung như “Bạn tư vấn phòng cho tôi nhé”, “Giúp mình tìm phòng”, “Mình muốn thuê phòng”, “Có phòng nào phù hợp với mình không?” tự dùng hồ sơ của tenant đăng nhập, không cần nhắc từ “profile”. Nhận diện có dấu/không dấu và các cách nói lịch sự trước khi gọi LLM. Câu có điều kiện cụ thể (giá, địa điểm, diện tích, tiện ích...) tiếp tục qua luồng trích bộ lọc, giá trị trong câu hỏi được ưu tiên. Hồ sơ thiếu/rỗng hoặc tắt cá nhân hóa sẽ nhận hướng dẫn bổ sung/bật lại. Đây là logic định tuyến ý định trong `app/chat/profile.py`; không thay trọng số model Ollama.
- Gợi ý theo hồ sơ dùng khu vực, giá, diện tích, loại phòng, số người, tiện ích bắt buộc/ưu tiên, điểm mốc và mức dung sai theo bộ recommendation hiện có. Các tiêu chí được chấm điểm có thể chưa khớp hoàn toàn; câu trả lời ghi rõ chênh lệch giá, diện tích, loại phòng, sức chứa hoặc khoảng cách. Ngày dọn vào, nhóm người thuê và ghi chú được hiển thị như thông tin đã lưu, chưa phải điều kiện lọc được bảo đảm bởi recommendation.
- Chỉ dùng tin PUBLISHED còn hạn, phòng AVAILABLE, tin công khai mới nhất của cơ sở. Bộ lọc phải khớp cùng một phòng.
- Khi không có kết quả: mở rộng từng bước từ một nửa đến toàn bộ mức dung sai, dừng ngay khi có phòng gần phù hợp. Khu vực lân cận tối đa 5 km từ centroid phường, cùng tỉnh; giá tối đa 20% nhưng không quá 500.000đ/tháng mỗi biên; diện tích tối đa 15% nhưng không quá 5 m² mỗi biên. Thử thay đổi một điều kiện trước rồi mới kết hợp khu vực/giá/diện tích. Ví dụ ngân sách 2 triệu: thử tới 2,2 triệu rồi 2,4 triệu; ngân sách 8 triệu: không vượt 8,5 triệu. Giữ tiện ích, loại phòng và sức chứa. Ưu tiên giá/diện tích gần yêu cầu khi xếp phòng trong mỗi bước; lân cận ưu tiên khoảng cách. Ghi rõ phần chưa khớp trong câu trả lời và trên card ở chat/màn Tìm phòng; không ghi điều kiện nới vào bộ lọc gốc.
- Centroid tỉnh/phường do backend đồng bộ từ [tinhthanhpho.com](https://www.tinhthanhpho.com/en/api-docs), lưu ở `centroid_lat`, `centroid_lng`. Không có centroid thì không suy đoán lân cận. Khoảng cách tính theo đường thẳng tới tọa độ cơ sở, không phải quãng đường đi.
- AI recommendation dùng hồ sơ, embedding/hành vi, độ mới, ưu tiên và MMR. Tọa độ điểm mốc trong hồ sơ được dùng nếu có; chatbot chưa nhận vị trí hiện tại của trình duyệt.
- Embedding là `intfloat/multilingual-e5-small`, chạy CPU trong Python, độc lập với Ollama; build lại sau khi dữ liệu tin thay đổi.
- Local LLM dùng `qwen3:4b-instruct-2507-q4_K_M`. Tag chung `qwen3:4b` đã tải trước đó là bản Thinking, chậm cho tác vụ này. Prompt gọn, giới hạn EXTRACT 512 token, COMPOSE 384 token, chỉ gửi chi tiết tối đa 5 phòng để soạn lời; card frontend vẫn đầy đủ.

Chuỗi khi `ALLOW_EXTERNAL_FALLBACK=true` là **Local → Gemini/Anthropic → Rules**; API ngoài hỗ trợ cả lỗi kỹ thuật lẫn trường hợp local chưa hiểu ý khách. Các file môi trường mẫu đặt `false` để chạy riêng local; đổi thành `true` và điền khóa khi cần API ngoài. HTTP 200 của chat chỉ xác nhận mở được stream; kiểm tra provider trong `llm_call_logs` để biết tầng nào đã xử lý.

API: `GET /ai/health`, `GET /ai/me`, `GET /ai/recommendations`, `POST /ai/chat`, `POST /ai/events`, `POST /ai/feedback`. Ngoại trừ health, cần JWT access của tài khoản ACTIVE thuộc bất kỳ role nào (TENANT, LANDLORD, ADMIN), cùng HS512/secret/issuer với backend. Swagger: `http://localhost:8000/docs`.

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

Script tự bật Docker hạ tầng → Maven clean → Flyway tới V1 → đồng bộ 34 tỉnh và 3.321 phường từ API → xuất snapshot → sinh V2 dữ liệu nghiệp vụ và V12 dữ liệu cộng đồng → áp dụng các migration còn lại → thoát. V11 tạo schema cộng đồng/kiểm duyệt, sau đó V12 tự nạp bài viết, bình luận và tương tác. Không cần mở file seed cộng đồng trong DataGrip hoặc chạy SOURCE riêng. Không tạo quận/huyện riêng vì nguồn và schema dùng tỉnh → phường/xã.

Trong hai tiến trình setup, script tắt job PDF, relay thông báo và RabbitMQ listener vì bước V1 chưa có `notification_outbox`; các chức năng này dùng cấu hình bình thường khi chạy backend sau setup. DevTools restart cũng được tắt bằng JVM property để lỗi khởi động trả đúng exit code cho script.

Mặc định có 1.071 tài khoản (3 admin, 68 landlord, 1.000 tenant), 1.088 cơ sở/tin đăng, 7.752 phòng, hơn 26.000 ảnh, 6.000 review và 1.000 report nghiệp vụ. Cộng đồng có thêm **600 bài, 7.184 bình luận/trả lời, 880 ảnh**, cùng cảm xúc, lưu bài, follow, report, hàng đợi kiểm duyệt và yêu cầu xét lại. Số lượng tương tác phụ thuộc tài khoản và listing công khai thực tế. Chi tiết nằm ở `RentalManagement/scripts/data/demo-counts.json` và `RentalManagement/scripts/data/community-counts.json`. Ba URL ảnh cộng đồng được giữ đúng như dữ liệu đã cung cấp; caption viết theo ngữ cảnh chia sẻ ý tưởng không gian, không gắn nhãn dữ liệu mẫu trên UI.

Tên tài khoản, tên cơ sở, địa chỉ và nội dung hiển thị được sinh tự nhiên, không dùng “Người thuê Demo”, “Nhà trọ Demo” hoặc tiền tố `[DEMO]`. Đây vẫn là dữ liệu được tạo cho dự án, không phải thông tin cá nhân hoặc tin cho thuê đã xác minh ngoài đời. Hàm sinh tên/email và tên nhà ở [seed_profiles.py](../RentalManagement/scripts/seed_profiles.py); danh sách tài khoản ở [seed-accounts.json](../RentalManagement/scripts/data/seed-accounts.json). Ba email đăng nhập chính vẫn giữ nguyên; tài khoản còn lại có email theo tên tại domain `trotot.example.test`, mật khẩu `123456`. V12 chọn tác giả bằng ID từ manifest nên không phụ thuộc tên/email.

Muốn tăng số lượng trước khi dựng DB mới:

```powershell
.\scripts\setup-demo.ps1 -Landlords 136 -Tenants 2000 -PropertiesPerLandlord 16 -RentalsPerTenant 3 -CommunityPosts 2000
```

Chỉ đổi số lượng seed khi dựng **database mới**. Nếu V2 đã áp dụng, script giữ nguyên cả file V2/V12 và chỉ áp dụng migration còn thiếu; không sinh lại seed theo tham số mới. Không sửa V2 hoặc V12 đã áp dụng vì Flyway lưu checksum. Script không xóa DB; vận hành thường ngày dùng mục 3. Snapshot trong repo cho phép dựng offline bằng các migration đã sinh, gồm V2 và V12: bật hạ tầng rồi chạy BE bình thường để Flyway áp dụng. Script đồng bộ mới nhất cần mạng. Database có lịch sử migration cũ V1–V6/V14/V15 không được coi là DB mới; không dùng repair để ép khớp lịch sử khác source.

Nếu bạn chủ động dựng lại từ đầu bằng cách xóa cả `rental_db` và `rental_ai`: dừng BE/AI trước khi xóa, giữ nguyên `.env` đã cấu hình, rồi chạy lại **hai bước** sau. Script backend sẽ tạo lại dữ liệu nghiệp vụ và cộng đồng; script AI sẽ tạo lại schema AI, migration, dữ liệu AI và embedding:

```powershell
Set-Location E:\SpringBoot\DoAn\RentalManagement
.\scripts\setup-demo.ps1 -CommunityPosts 600

Set-Location E:\SpringBoot\DoAn\AIService
.\scripts\setup_demo.ps1 -Users 1000
```

Lần đầu cần chuẩn bị venv/cấu hình theo mục 2.3. `-CommunityPosts` hỗ trợ 60–10.000 bài; khi tăng dữ liệu tenant thì có thể tăng `-Users` bên AI tương ứng. Không cần tạo lại user MySQL hay đổi mật khẩu chỉ vì đã xóa schema; bước `prepare_database` cấp lại quyền trên hai schema theo cấu hình hiện tại.

Muốn xem trước bộ seed lớn hơn, chưa áp dụng vào DB:

```powershell
python scripts/generate-demo-seed.py --landlords 136 --tenants 2000 --properties-per-landlord 16 --rentals-per-tenant 3 --output demo-preview.sql
python scripts/generate-community-seed.py --posts 2000 --accounts demo-preview.accounts.json --output community-preview.sql
```

Generator lõi dùng snapshot `scripts/data/administrative-units.json` và `reference-catalog.sql`; ID tỉnh/phường được tra theo mã hành chính. Landlord/tenant tối thiểu 4, cơ sở mỗi landlord chọn 8 hoặc 16, số hợp đồng lịch sử tối thiểu 1. Bản xem trước tạo kèm `.counts.json` và `.accounts.json`; truyền file tài khoản đó cho generator cộng đồng bằng `--accounts`. Đầu ra mặc định cộng đồng là `V12__seed_community_demo_data.sql`; dùng `--output` ngoài thư mục migration khi chỉ xem trước. Xem [seed cộng đồng](../RentalManagement/scripts/seed-community.README.md).

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
CHAT_NEARBY_RADIUS_KM=5
CHAT_PRICE_TOLERANCE_PCT=20
CHAT_PRICE_TOLERANCE_MAX_VND=500000
CHAT_AREA_TOLERANCE_PCT=15
CHAT_AREA_TOLERANCE_MAX_M2=5
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

Alembic quản lý riêng `rental_ai`, head hiện tại `20261030_ai_integration`. `seed_demo_ai` đọc tenant/tin công khai trong core và chỉ upsert fixture AI theo ID cố định. Bao gồm hội thoại, feedback, recommendation snapshots/events, cache, usage ngày cũ, lock, lease đã hết hạn, sync state và eval fixture. Nội dung hội thoại/feedback tự nhiên, không có tiền tố Demo; metadata kỹ thuật vẫn đánh dấu fixture để không bị hiểu là kết quả đo chất lượng model. Không gọi LLM, không tính vào quota chat hôm nay.

`build_embeddings` tạo vector thật và log đồng bộ thật, hoàn tất dữ liệu cho các bảng AI còn lại. Lần đầu có thể tải model. Không dùng vector ngẫu nhiên làm giả embedding. Dữ liệu nghiệp vụ được seed bằng V2 và cộng đồng bằng V12 của BE; AI chỉ seed bảng riêng trong `rental_ai`, không tạo thêm bài viết hay đánh giá listing.

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

Giữ các terminal phục vụ ứng dụng mở. Health cần hai DB UP, có vector; `llm.external.enabled` phản ánh cấu hình API ngoài. Health không tự gọi thử Ollama. Mở `http://localhost:4200`; đăng nhập `tenant@gmail.com` / `123456`. Admin/chủ trọ mẫu: `admin@gmail.com`, `landlord@gmail.com`, cùng mật khẩu. Hỏi thử “Tìm phòng dưới 3 triệu ở phường Cầu Giấy, có WiFi”.

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
| 401/403 | JWT secret/issuer/HS512, tài khoản ACTIVE và authVersion |
| Không có kết quả lân cận | Centroid và tọa độ cơ sở có thật, phòng còn trống, đúng giá/tiện ích |
| Setup core báo DB đã vượt V1 | DB đã seed; dùng mục 3, không sinh lại V2 đã áp dụng |
| Vẫn thấy Gemini | Restart đúng Uvicorn; kiểm tra biến môi trường và external.enabled |

Tọa độ cơ sở/ảnh/giao dịch seed là mô phỏng để kiểm tra chức năng; centroid hành chính đến từ nguồn công khai, ghi nguồn tinhthanhpho.com / HVN Group, CC BY 4.0. Review demo không phải đánh giá người dùng thật. PDF seed chỉ có snapshot/tracking trạng thái chưa tạo tài liệu, không có upload Cloudinary hoặc giao dịch thanh toán thật.

## 5. Cấu hình FE, dịch vụ ngoài và Docker frontend

### Thông báo và cache sau khi cập nhật main

FE có chuông thông báo realtime; BE dùng Redis cache, RabbitMQ và `notification_outbox` (Flyway V6). Backend tự áp dụng V6 khi khởi động; không seed lại database đã có V2. Có thể cấu hình `CACHE_TTL=30m`, `NOTIFICATIONS_RELAY_ENABLED=true`, `NOTIFICATIONS_RELAY_DELAY_MS=2000` trong `.env` BE. Phần đồng bộ địa chỉ vẫn giữ `ADDRESS_SYNC_ENABLED=false` khi chạy bình thường. Chi tiết: [thông báo Redis/RabbitMQ](../RentalManagement/docs/OPERATIONS.md#thong-bao), [kiến trúc backend](../RentalManagement/docs/RULE.md).

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

Chỉnh key trình duyệt tại `DOANTOTNGHIEPFE/src/environments/maptiler.config.ts`, dùng MapTiler Cloud cho style/tiles/geocoding và MapLibre để render. Giới hạn allowed origins của key cho localhost và domain triển khai; build lại sau khi đổi. Giữ attribution MapTiler/OpenStreetMap. Không cần Google Maps SDK. Định vị trình duyệt cần localhost hoặc HTTPS và quyền người dùng; vẫn có thể nhập tọa độ/chọn ghim thủ công. Tọa độ thuộc cơ sở, dùng chung cho các phòng/tin. Chi tiết: [bản đồ FE](../DOANTOTNGHIEPFE/docs/UI.md#ban-do).

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

Thiếu hash secret, checkout báo 503; các module khác vẫn chạy. Đăng ký IPN URL HTTPS public kết thúc `/api/payments/vnpay/ipn` tại merchant. Return về FE không tự xác nhận thu tiền; BE kiểm tra IPN hoặc QueryDr. Công cụ tunnel/proxy và luồng kiểm thử nằm trong [VNPay sandbox](../RentalManagement/docs/OPERATIONS.md#vnpay).

Mẫu hợp đồng ở `RentalManagement/src/main/resources/reports/rental-contract.jrxml`; font NotoSans và license đi kèm phải được giữ. PDF thật cần Cloudinary hợp lệ và xử lý job; file seed chỉ chứa tracking mô phỏng. Các property `app.rental.jobs-enabled`, `app.rental.job-delay-ms`, `app.rental.cleanup-delay-ms`, `app.rental.contract-terms` có thể đặt trong `.env` BE. Chi tiết quyền truy cập, lease/job và template: [luồng thuê và PDF](../RentalManagement/docs/BUSINESS.md).

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

Các bộ kiểm thử tự động, smoke/E2E, mock và dependency chỉ phục vụ test đã được tạm gỡ ngày 15/11/2026. Các kết quả tại mục 8 là lịch sử triển khai. Hiện kiểm tra biên dịch tại từng repo:

```powershell
# FE
npm run build

# BE
.\mvnw.cmd compile

# AIService
.\.venv\Scripts\python.exe -m compileall -q app scripts
```

Kiểm tra thủ công: đăng nhập đủ ba role; tenant tìm/lưu tin và chat; landlord sửa phòng/tin; gửi và xử lý yêu cầu thuê; kiểm tra PDF khi Cloudinary đã cấu hình; thử AI với tenant ACTIVE, community và kiểm duyệt. Email xác minh/reset mật khẩu xem trong MailHog.

AI giữ `scripts.benchmark_local` để đo model, `scripts.evaluate_chat` và `scripts.evaluate_recsys` để đánh giá. Kết quả trên seed/fixture không chứng minh chất lượng với người dùng thật. `scripts.purge_logs` phục vụ retention; `scripts.build_embeddings` cần chạy lại khi thay tin.

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

- [API backend](../RentalManagement/docs/api.md), [Property API](../RentalManagement/docs/api.md#property-api), [thuê phòng và PDF](../RentalManagement/docs/BUSINESS.md), [VNPay](../RentalManagement/docs/OPERATIONS.md#vnpay).
- [API phía FE](../RentalManagement/docs/api.md), [quản lý cơ sở](../DOANTOTNGHIEPFE/docs/UI.md#co-so), [cây loại phòng](../DOANTOTNGHIEPFE/docs/UI.md#loai-phong), [UI hợp đồng](../DOANTOTNGHIEPFE/docs/UI.md#hop-dong), [bản đồ](../DOANTOTNGHIEPFE/docs/UI.md#ban-do).
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

## 9. Thuật toán xếp hạng tin landing và bài viết cộng đồng

### 9.1. Trạng thái triển khai

Hai thuật toán ở mục 9.2 và 9.3 **đã được triển khai phiên bản `listing-v1` và `community-v1` tại BE ngày 15/11/2026**. Landing mặc định dùng “Đề xuất cho bạn”; lựa chọn Mới nhất/Giá thấp đến cao/Giá cao đến thấp nằm trong “Bộ lọc nâng cao” và được thực hiện trên toàn bộ kết quả trước khi phân trang. Trọng số và tỷ lệ vị trí là cấu hình khởi đầu để đo thử, chưa phải cam kết lượt hiển thị của gói dịch vụ.

| Luồng | Cách xếp hạng hiện có trong source |
| --- | --- |
| Danh sách tin công khai của BE, dùng cho landing | Điểm tổng hợp + phân bổ 5/3/2 theo ưu tiên + đa dạng chủ trọ + lượt hiển thị; xem [ListingDiscoveryService.java](../RentalManagement/src/main/java/com/tamdaptit/rentalmanagement/discovery/ListingDiscoveryService.java), [FeedRanking.java](../RentalManagement/src/main/java/com/tamdaptit/rentalmanagement/discovery/FeedRanking.java). Điều kiện công khai và quyền lợi còn hiệu lực tiếp tục lấy từ [ListingRepository.java](../RentalManagement/src/main/java/com/tamdaptit/rentalmanagement/listing/repository/ListingRepository.java) và `ListingServiceImpl` |
| Bảng tin cộng đồng của BE | Bảng tin chung và “Đang theo dõi” dùng điểm hồ sơ/chủ đề + tương tác + theo dõi + độ mới + uy tín, sau đó đa dạng tác giả; xem [CommunityDiscoveryService.java](../RentalManagement/src/main/java/com/tamdaptit/rentalmanagement/discovery/CommunityDiscoveryService.java). “Đã lưu”, “Bài của tôi” và hồ sơ tác giả giữ thứ tự thời gian với quyền đọc riêng |
| API gợi ý AI theo hồ sơ | Đã có lọc cứng, chấm điểm hồ sơ + vector hành vi + độ mới + ưu tiên, chọn một phòng phù hợp cho mỗi tin rồi sắp lại bằng MMR; xem [service.py](app/recommend/service.py), [scoring.py](app/recommend/scoring.py), [rerank.py](app/recommend/rerank.py) |

Bộ gợi ý AI theo hồ sơ vẫn là luồng riêng. Hai bảng tin mới được BE tính trực tiếp từ dữ liệu nghiệp vụ, không cần gọi LLM/Ollama để sắp thứ tự. Kiến trúc chung là **lọc ứng viên → chấm điểm → sắp lại để bảo đảm đa dạng và phân bổ hiển thị**, tham khảo [Recommendation systems overview](https://developers.google.com/machine-learning/recommendation/overview/types) và [Re-ranking](https://developers.google.com/machine-learning/recommendation/dnn/re-ranking) của Google.

### 9.2. Landing: phân bổ theo quyền lợi, chấm điểm và luân phiên

**Mục tiêu:** phối hợp tin được ưu tiên từ gói tháng và boost với tin thường; ưu tiên tin phù hợp, đủ thông tin và còn khả năng cho thuê.

**Bước 1 — Lọc ứng viên.** Chỉ lấy tin `PUBLISHED`, còn hạn, chủ trọ `ACTIVE`, đúng điều kiện công khai và tin công khai mới nhất của cơ sở. Với luồng tìm phòng, các điều kiện giá, loại phòng, diện tích và tiện ích phải khớp trên cùng một phòng còn khả dụng. Tin đã hết phòng phải được loại khỏi luồng đề xuất phòng trống.

**Bước 2 — Xác định quyền lợi còn hiệu lực.** Dùng quy tắc hiện có của BE:

```text
priority = 0 nếu tin bị tắt ưu tiên
priority = max(ưu tiên gói tháng hợp lệ, ưu tiên boost hợp lệ) trong các trường hợp còn lại
```

Kiểm tra thời hạn, trạng thái gói, kỳ gói tháng và phân bổ thực tế cho tin. Chỉ mua gói không có nghĩa mọi tin đều được ưu tiên cao; áp dụng đúng mức mặc định và các suất đã chọn. Một tin có cả gói tháng và boost vẫn là một ứng viên, không cộng hai mức ưu tiên hoặc hiển thị trùng. Khi quyền lợi hết hạn, tính lại mức còn hợp lệ; nếu không còn quyền lợi thì về nhóm thường, với điều kiện bản thân tin vẫn đủ điều kiện công khai.

**Bước 3 — Chấm điểm trong từng nhóm.** Chuẩn hóa mỗi thành phần về `[0, 1]`, rồi điều chỉnh theo lượt đã hiển thị:

```text
S_landing = 0.35 * relevance
          + 0.25 * quality
          + 0.25 * engagement
          + 0.15 * freshness

freshness = exp(-age_days / 30)
engagement = min(1, (contacts_7d + 1) / (max(views_7d, contacts_7d) + 10))
S_serving = S_landing / (1 + 0.15 * seen_by_user_7d)
          - 0.025 * log(1 + impressions_30d)
```

| Thành phần | Cách xác định đề xuất |
| --- | --- |
| `relevance` | Trong các phòng phù hợp bộ lọc, lấy phòng có điểm cao nhất: 45% khu vực + 35% giá + 10% diện tích + 10% loại phòng. Hồ sơ chỉ dùng khi `allow_personalization=true`; điều kiện tìm kiếm chủ động được ưu tiên hơn hồ sơ |
| `quality` | Mức đầy đủ của giá, diện tích, ảnh, địa chỉ, tiện ích và mô tả; dữ liệu thiếu làm giảm điểm |
| `engagement` | Tỷ lệ liên hệ/xem có làm trơn từ `listing_daily_stats` trong 7 ngày; giới hạn về `[0,1]`. V1 chưa thêm tín hiệu lưu tin vào điểm landing |
| `freshness` | Tuổi tin tính từ lần xuất bản hợp lệ; sửa mô tả không làm mới tuổi tin |

Không cộng trực tiếp số lượt xem thô vào điểm: tin đã được hiển thị nhiều sẽ có lợi thế tích lũy quá lớn. Tin chưa có thống kê nhận điểm tương tác có làm trơn là 0,1. Các bộ đếm xem/liên hệ hiện có là dữ liệu tổng hợp nghiệp vụ, chưa bảo đảm mỗi lượt là một người khác nhau; tín hiệu hiển thị mới có khử lặp theo tài khoản và thời gian.

**Bước 4 — Phân bổ và luân phiên.** Mẫu mức ưu tiên trong mỗi 10 vị trí là `[2,1,2,0,2,1,2,0,2,1]`, tương ứng 5 tin cao, 3 tin thấp và 2 tin thường khi đủ nguồn. Trong mỗi nhóm, ưu tiên điểm sau điều chỉnh lượt hiển thị. Vị trí thứ 10 là lượt khám phá, chọn theo `0.6 * freshness + 0.4 / (1 + impressions_30d)` trong nhóm đủ điều kiện. Không xoay ngẫu nhiên giữa các lần tải thêm của cùng phiên.

Giới hạn khoảng 2 tin/chủ trọ trong 10 vị trí khi đủ nguồn tin. Nhóm thiếu ứng viên sẽ nhường vị trí cho nhóm khác. Nếu vẫn thiếu, có thể nới giới hạn cùng chủ trọ; không nới điều kiện công khai, thời hạn hoặc khả dụng. Gắn nhãn ưu tiên theo quyền lợi thực tế. Gói tháng và boost cùng mức được tham gia cùng nhóm, không tự đặt boost luôn cao hơn gói tháng.

### 9.3. Cộng đồng: kết hợp sở thích, tương tác, theo dõi và độ mới

**Mục tiêu:** bài hữu ích, phù hợp vẫn có thể được đề xuất sau khi đăng; bài mới và tác giả mới vẫn có cơ hội tiếp cận.

**Bước 1 — Lọc ứng viên.** Luồng đề xuất chỉ lấy bài `VISIBLE`, chưa xóa, tác giả `ACTIVE`. Bài hạn chế/chờ duyệt/ẩn không được đưa vào nguồn đề xuất. Trong mục “Đang theo dõi”, giới hạn ứng viên theo tác giả đang theo dõi. Mục “Đã lưu” và “Bài của tôi” tiếp tục áp dụng quyền truy cập tương ứng.

Tìm kiếm chỉ dùng từ khóa/hashtag; không yêu cầu khu vực. Khi có truy vấn, lọc mức khớp trước rồi dùng điểm xếp hạng để phân biệt các bài có mức khớp tương đương.

**Bước 2 — Chấm điểm.** Chuẩn hóa các thành phần về `[0, 1]`:

```text
S_community = 0.30 * interest
            + 0.25 * engagement
            + 0.20 * following
            + 0.15 * freshness
            + 0.10 * trust

freshness = exp(-age_hours / 48)
E_raw = log(1 + U_reactions + 3 * U_commenters + 4 * U_saves)
S_serving = S_community / (1 + 0.25 * seen_by_user_7d)
```

`U_reactions`, `U_commenters`, `U_saves` là số tài khoản khác nhau có tương tác hợp lệ trong cửa sổ gần đây, ban đầu đề xuất 24 giờ. Loại tương tác tự tạo của tác giả và giới hạn mỗi tài khoản đóng góp một lần cho mỗi loại tín hiệu trên một bài. Chuẩn hóa `E_raw` bằng ngưỡng thống kê cố định trong kỳ xếp hạng, ví dụ percentile 95, rồi chặn về `[0, 1]`. Không lấy số bình luận thô để tránh một tài khoản gửi nhiều bình luận làm tăng điểm.

| Thành phần | Ý nghĩa |
| --- | --- |
| `interest` | Khi có khu vực mong muốn: 75% mức khớp khu vực + 25% chủ đề. Khu vực xét phường chính, các phường đã chọn, tỉnh/thành trong hồ sơ; đối chiếu `area`, nội dung và vị trí tin phòng đính kèm, chuẩn hóa dấu tiếng Việt. Đúng phường được 1; chỉ đúng tỉnh khi đã chọn phường được 0,4; khác khu vực được 0. Hashtag lấy từ bài đã lưu/thả cảm xúc/bình luận trong 90 ngày, tối đa 200 bài, chưa dùng embedding cộng đồng |
| `engagement` | Tương tác hợp lệ gần đây; bài cũ nhận tương tác mới có thể tăng điểm trở lại |
| `following` | Bằng 1 khi đang theo dõi tác giả, ngược lại bằng 0; trong nguồn chỉ theo dõi, thành phần này là hằng số |
| `freshness` | Giảm dần từ thời điểm tạo; chỉnh sửa không đặt lại tuổi bài |
| `trust` | `1 / (1 + số nội dung từng bị xử lý HIDDEN/REMOVED đã xác nhận)`; không có vi phạm thì bằng 1. Báo cáo chưa xác minh không tự động làm giảm uy tín |

Thiếu khu vực thì chỉ dùng điểm chủ đề; thiếu cả lịch sử chủ đề thì dùng hằng số trung lập 0,5 cho `interest`. Khách chưa đăng nhập hoặc người tắt cá nhân hóa không dùng hồ sơ/lịch sử hashtag; thứ tự dựa vào các tín hiệu chung. Theo dõi là lựa chọn chủ động, vẫn được áp dụng cho người đăng nhập. Quyền lợi gói tháng/boost của tin phòng không tự chuyển sang bài viết cộng đồng.

Ví dụ hồ sơ muốn ở Cầu Giấy: các bài có khu vực/tin đính kèm phù hợp được cộng điểm trước khi phối hợp các tín hiệu còn lại. Bài Quận 7/Bình Tân không còn đứng đầu chỉ vì vừa đăng. Đây là ưu tiên theo điểm, không phải bộ lọc cứng: bài ngoài khu vực vẫn có thể xuất hiện do tương tác, theo dõi hoặc lượt khám phá.

**Bước 3 — Sắp lại danh sách.** Dành khoảng 10% vị trí để khám phá bài mới ít lượt hiển thị nhưng vẫn đủ điều kiện. Hạn chế hai bài liền nhau cùng tác giả và tối đa khoảng 2 bài/tác giả trong 10 vị trí khi đủ nguồn. Giảm ưu tiên bài đã xem nhiều lần, khử trùng nội dung và giữ đa dạng chủ đề. Khi thiếu ứng viên, nới giới hạn đa dạng trước; không đưa bài không đủ điều kiện vào để lấp danh sách.

### 9.4. Dữ liệu, phân trang và đánh giá khi triển khai

- FE dùng [FeedImpressionDirective](../DOANTOTNGHIEPFE/src/app/shared/directives/feed-impression.directive.ts) để ghi nhận thẻ hiển thị ít nhất 50% liên tục 1 giây khi tab đang mở. Gom tối đa 50 ID/request, gửi `POST /api/discovery/impressions` với `surface=LISTING|COMMUNITY`. Chỉ lưu tín hiệu tài khoản đã đăng nhập; khách chưa đăng nhập chưa có lịch sử xem cá nhân.
- Migration V13 thêm thời điểm cảm xúc và `feed_impressions`, V14 ước lượng tuổi cảm xúc lịch sử theo bài/bình luận để tránh tất cả tương tác cũ cùng trở thành xu hướng khi triển khai. Lượt hiển thị lưu theo người dùng, ngày, loại bảng tin và ID; các lần ghi lặp trong 30 phút chỉ tính một lượt. Hiển thị tin phòng hợp lệ đồng thời tăng `listing_daily_stats.view_count`; mở số điện thoại thành công của người khác tăng `contact_count` theo ngày (bao gồm khách, loại lượt tự xem số của chủ tin). Request tải API không được coi là lượt hiển thị. Người đã tắt cá nhân hóa không được ghi lịch sử hiển thị mới và không bị điều chỉnh điểm theo lịch sử xem cá nhân. V1 không dùng lượt sao chép liên kết để tính điểm.
- BE xác thực quyền lợi gói, trạng thái tài khoản/tin, quyền đọc và tính điểm. Quyền DB và bộ recommendation riêng của AIService giữ nguyên; không thêm quyền ghi DB nghiệp vụ cho AIService.
- [RankingSessions](../RentalManagement/src/main/java/com/tamdaptit/rentalmanagement/discovery/RankingSessions.java) giữ thứ tự ID trong Redis với hạn logic 30 phút, kèm dự phòng bộ nhớ tối đa 256 phiên mỗi instance. Khóa phiên gắn với tài khoản, nguồn, bộ lọc và hồ sơ để không dùng chéo giữa người dùng. `GET /api/listings` trả `rankingSession`; tải trang tiếp theo gửi lại `rankingSession` cùng `page`, `size`, bộ lọc và `sort`. Cursor cộng đồng dạng `r:<session>:<offset>` thay cho cursor thời gian ở hai nguồn được xếp hạng.
- Khi phục vụ từng trang, kiểm tra lại tập ứng viên công khai; bỏ tin/bài vừa mất hiệu lực mà không dịch chuyển offset đã lưu. Thay đổi mức ưu tiên gói hoặc hồ sơ làm phiên không còn tương thích. Phiên hết hạn/không tương thích trả `FEED_SESSION_EXPIRED` (409); FE tự bắt đầu lại trang đầu. Không sử dụng quyền lợi gói đã hết hạn từ snapshot cũ.
- Theo dõi mức phân bổ thực tế cho từng mức ưu tiên, tỷ lệ liên hệ/tin, tương tác có ý nghĩa, tỷ lệ bỏ qua/báo cáo đã xác nhận, độ đa dạng tác giả và tỷ lệ bài mới được hiển thị. Thử nghiệm trọng số trên dữ liệu thực; không chỉ tối ưu tổng lượt xem.
- V1 đang lấy toàn bộ ứng viên phù hợp để xếp hạng trước khi phân trang, phù hợp quy mô dữ liệu hiện tại; khi dữ liệu tăng cần tách truy hồi ứng viên và tổng hợp tín hiệu nền. Muốn sửa trọng số phải cập nhật BE và phiên bản phạm vi snapshot. Khởi động BE để Flyway áp dụng migration mới trước khi dùng API này.
