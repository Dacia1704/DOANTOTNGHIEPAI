# Hướng dẫn Deploy `trotot.online` lên VPS Linux & CI/CD GitHub Actions

Tài liệu này hướng dẫn triển khai trọn bộ hệ thống gồm **hạ tầng Docker như hiện tại** (`mysql`, `phpmyadmin`, `redis`, `mailhog`, `rabbitmq`, `elasticsearch`, `ollama`) và **3 service ứng dụng** (`backend`, `ai-service`, `frontend`) lên **1 VPS Linux** với tên miền **`trotot.online`**, chỉ cần **1 lệnh duy nhất**:

```bash
docker compose up -d --build
```

Các file đã được chuẩn bị sẵn trong workspace:
- [docker-compose.yml](../docker-compose.yml) (ở thư mục cha chứa 3 repo)
- [.env.example](../.env.example) (mẫu biến môi trường dùng chung)
- [RentalManagement/Dockerfile](../RentalManagement/Dockerfile) và [RentalManagement/.dockerignore](../RentalManagement/.dockerignore)
- [AIService/Dockerfile](Dockerfile)
- [DOANTOTNGHIEPFE/Dockerfile](../DOANTOTNGHIEPFE/Dockerfile) và [DOANTOTNGHIEPFE/nginx.conf](../DOANTOTNGHIEPFE/nginx.conf) (đã đổi `server_name` sang `trotot.online www.trotot.online`)

---

## Mục lục
1. [Kiến trúc triển khai trên VPS](#1-kiến-trúc-triển-khai-trên-vps)
2. [Chuẩn bị VPS Linux & Tên miền](#2-chuẩn-bị-vps-linux--tên-miền)
3. [Cấu hình `.env` & Đổi key VNPay / Secret](#3-cấu-hình-env--đổi-key-vnpay--secret)
4. [Xin chứng chỉ SSL cho `trotot.online`](#4-xin-chứng-chỉ-ssl-cho-trototonline)
5. [Chạy lần đầu bằng 1 lệnh & Khởi tạo DB/AI](#5-chạy-lần-đầu-bằng-1-lệnh--khởi-tạo-dbai)
6. [Quy trình CI/CD với GitHub Actions](#6-quy-trình-cicd-với-github-actions)
7. [Kiểm tra sau deploy & Xử lý sự cố](#7-kiểm-tra-sau-deploy--xử-lý-sự-cố)

---

## 1. Kiến trúc triển khai trên VPS

```text
Internet (HTTPS :443 / HTTP :80)
  │
  ▼
frontend (Nginx + Angular build) — domain: trotot.online
  ├── /          → Angular production-docker
  ├── /api/      → http://backend:8080/api/      (Spring Boot 4.1.1 / Java 21)
  ├── /ws/       → http://backend:8080/ws/       (WebSocket STOMP/SockJS)
  └── /ai/       → http://ai-service:8000/ai/    (FastAPI / Uvicorn + SSE)
                      │
                      ├── Hạ tầng Docker giữ nguyên:
                      │   mysql, redis, rabbitmq, elasticsearch, mailhog, phpmyadmin, ollama
                      └── (Tùy chọn) API ngoài Gemini/Anthropic nếu VPS không có GPU
```

> **Lưu ý phần cứng VPS:**
> - Toàn bộ stack (MySQL + Redis + RabbitMQ + Elasticsearch + Spring Boot + FastAPI embedding CPU + Nginx) cần tối thiểu **4 vCPU, 8 GB RAM, 40 GB SSD**.
> - Các cổng hạ tầng (`3306`, `6379`, `5672`, `15672`, `9200`, `8081`, `8025`, `11434`) trong [docker-compose.yml](../docker-compose.yml) đều bind về `127.0.0.1` để bảo mật trên VPS; chỉ mở công khai cổng `80` và `443`.

---

## 2. Chuẩn bị VPS Linux & Tên miền

### 2.1. Trỏ DNS tên miền `trotot.online`
Tại trang quản lý DNS của nhà cung cấp tên miền, thêm 2 bản ghi `A` (thay `IP_VPS_CUA_BAN` bằng IPv4 public của VPS):

| Loại | Host / Name | Giá trị (Value) | TTL |
| --- | --- | --- | --- |
| `A` | `@` | `IP_VPS_CUA_BAN` | Auto / 300 |
| `A` | `www` | `IP_VPS_CUA_BAN` | Auto / 300 |

Kiểm tra DNS:
```bash
nslookup trotot.online
nslookup www.trotot.online
```

### 2.2. Cài đặt Docker, Docker Compose, Firewall trên VPS (Ubuntu 22.04 / 24.04)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl ufw certbot

# Cài Docker & Docker Compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

docker --version
docker compose version
```

Mở tường lửa (chỉ mở SSH, HTTP, HTTPS):
```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

*(Khuyên dùng nếu VPS RAM ≤ 8 GB)* Tạo 4 GB Swap:
```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 2.3. Đưa mã nguồn và file Compose lên VPS
Tạo thư mục `/opt/trotot` trên VPS và clone 3 repo + copy 2 file ở thư mục cha (`docker-compose.yml`, `.env.example`):

```bash
sudo mkdir -p /opt/trotot
sudo chown -R $USER:$USER /opt/trotot
cd /opt/trotot

git clone <URL_REPO_DOANTOTNGHIEPFE> DOANTOTNGHIEPFE
git clone <URL_REPO_RentalManagement> RentalManagement
git clone <URL_REPO_AIService> AIService
```

Đảm bảo cấu trúc `/opt/trotot` giống hệt thư mục `DoAn/` ở local:
```text
/opt/trotot/
├── docker-compose.yml
├── .env.example
├── .env                  # (tạo ở bước 3)
├── DOANTOTNGHIEPFE/
├── RentalManagement/
└── AIService/
```

---

## 3. Cấu hình `.env` & Đổi key VNPay / Secret

Tạo file `.env` từ mẫu:
```bash
cd /opt/trotot
cp .env.example .env
nano .env
```

### 3.1. Những chỗ cần đổi khi thay key VNPay mới
Backend đọc cấu hình VNPay tại [VnPayConfig.java](../RentalManagement/src/main/java/com/tamdaptit/rentalmanagement/payment/config/VnPayConfig.java) thông qua các biến trong `/opt/trotot/.env`:

| Biến trong `.env` | Giá trị mặc định | Cần đổi thành gì cho `trotot.online` |
| --- | --- | --- |
| `VNPAY_TMN_CODE` | `NQC9OREC` | **Mã Terminal (`vnp_TmnCode`)** mới của bạn |
| `VNPAY_HASH_SECRET` | *(rỗng)* | **Secret Key (`vnp_HashSecret`)** mới của bạn |
| `VNPAY_PAYMENT_URL` | `https://sandbox.vnpayment.vn/paymentv2/vpcpay.html` | Giữ nguyên nếu dùng Sandbox; đổi sang URL Production nếu dùng merchant thật |
| `VNPAY_RETURN_URL` | `http://localhost:4200/thanh-toan/ket-qua` | **`https://trotot.online/thanh-toan/ket-qua`** |
| `VNPAY_QUERY_URL` | `https://sandbox.vnpayment.vn/merchant_webapi/api/transaction` | Giữ nguyên nếu Sandbox; đổi sang URL Production nếu dùng merchant thật |
| `VNPAY_QUERY_IP` | `127.0.0.1` | **IPv4 public của VPS** |

**Ngoài file `.env`, cần cập nhật thêm 2 chỗ trên trang quản trị Merchant VNPay:**
1. **IPN URL:** `https://trotot.online/api/payments/vnpay/ipn`
2. **Return URL (nếu có whitelist):** `https://trotot.online/thanh-toan/ket-qua`

> Frontend (`DOANTOTNGHIEPFE`) không chứa key VNPay; FE chỉ redirect theo URL do BE trả về và xử lý kết quả tại `/thanh-toan/ket-qua`.

### 3.2. Các secret/cấu hình khác cần đổi trong `.env` & dịch vụ ngoài
1. **JWT Secret:** Sinh chuỗi >= 64 bytes (`openssl rand -base64 64`) và điền vào `JWT_SECRET` (cả Backend và AIService tự động dùng chung biến này từ `.env`).
2. **Mật khẩu hạ tầng:** Đổi `MYSQL_ROOT_PASSWORD`, `DB_PASSWORD`, `REDIS_PASSWORD`, `RABBITMQ_PASSWORD`, và mật khẩu `ai_svc` trong `CORE_DB_URL` / `AI_DB_URL`.
3. **CORS AIService:** Giữ `CORS_ORIGINS=["https://trotot.online","https://www.trotot.online"]`.
4. **Cloudinary:** Điền `APP_CLOUDINARY_CLOUD_NAME`, `APP_CLOUDINARY_API_KEY`, `APP_CLOUDINARY_API_SECRET`.
5. **MapTiler (`DOANTOTNGHIEPFE/src/environments/maptiler.config.ts`):** Vào dashboard MapTiler Cloud, thêm `https://trotot.online/*` và `https://www.trotot.online/*` vào **Allowed Origins** của API key.

---

## 4. Xin chứng chỉ SSL cho `trotot.online`

Chạy trước khi bật container `frontend` (để port 80 trống cho Certbot):

```bash
sudo certbot certonly --standalone -d trotot.online -d www.trotot.online --agree-tos -m email_cua_ban@gmail.com --non-interactive
```

Thiết lập cron tự động gia hạn SSL mỗi ngày lúc 03:00:
```bash
sudo crontab -e
```
Thêm dòng:
```cron
0 3 * * * certbot renew --quiet --pre-hook "docker stop rental-frontend" --post-hook "docker start rental-frontend"
```

---

## 5. Chạy lần đầu bằng 1 lệnh & Khởi tạo DB/AI

Từ `/opt/trotot`, build và khởi động **toàn bộ** hạ tầng + Backend + AIService + Frontend chỉ bằng 1 lệnh:

```bash
cd /opt/trotot
docker compose up -d --build
```

Kiểm tra trạng thái các container:
```bash
docker compose ps
```

### 5.1. Cấp quyền `ai_svc` và tạo `rental_ai` (chỉ làm 1 lần đầu)
Sau khi `rental-mysql` đã `healthy` và `rental-backend` đã chạy xong Flyway cho `rental_db`:

```bash
# Tạo database rental_ai và user ai_svc (thay CHANGE_ME_AI_SVC_PASS khớp với .env)
docker exec -i rental-mysql mysql -uroot -p"$(grep MYSQL_ROOT_PASSWORD .env | cut -d '=' -f2)" << 'EOF'
CREATE DATABASE IF NOT EXISTS rental_ai CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'ai_svc'@'%' IDENTIFIED BY 'CHANGE_ME_AI_SVC_PASS';
GRANT SELECT ON rental_db.* TO 'ai_svc'@'%';
GRANT ALL PRIVILEGES ON rental_ai.* TO 'ai_svc'@'%';
FLUSH PRIVILEGES;
EOF
```

### 5.2. Chạy Alembic, Seed AI & Build Embeddings
```bash
# Chạy migration rental_ai
docker exec -it rental-ai-service python -m alembic upgrade head

# (Nếu dùng dữ liệu seed) Seed fixture AI & build vector embedding
docker exec -it rental-ai-service python -m scripts.seed_demo_ai --users 1000
docker exec -it rental-ai-service python -m scripts.build_embeddings
```

*(Tùy chọn nếu dùng Ollama local trên VPS)* Pull model Qwen:
```bash
docker exec -it rental-ollama ollama pull qwen3:4b-instruct-2507-q4_K_M
```

Từ những lần sau, mỗi khi sửa code hoặc đổi `.env`, tại `/opt/trotot` bạn chỉ cần chạy lại đúng 1 lệnh:
```bash
docker compose up -d --build
```

---

## 6. Quy trình CI/CD với GitHub Actions

Vì 3 service nằm ở 3 repo riêng (`DOANTOTNGHIEPFE`, `RentalManagement`, `AIService`), mỗi repo có 1 file `.github/workflows/deploy.yml`. Khi bạn `git push origin main` ở repo nào, GitHub Actions sẽ:
1. Kiểm tra build/compile trên GitHub runner.
2. SSH vào VPS → `git pull origin main` → chạy `docker compose up -d --build <service>` tại `/opt/trotot` → dọn image thừa.

### 6.1. Tạo SSH Key trên VPS (chạy 1 lần)
```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
cat ~/.ssh/github_deploy
```

### 6.2. Thêm GitHub Secrets cho cả 3 repo
Vào từng repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret Name | Giá trị |
| --- | --- |
| `VPS_HOST` | `IP_VPS_CUA_BAN` (hoặc `trotot.online`) |
| `VPS_PORT` | `22` |
| `VPS_USER` | User SSH trên VPS (vd: `ubuntu` hoặc `root`) |
| `VPS_SSH_KEY` | Toàn bộ nội dung `~/.ssh/github_deploy` |

### 6.3. Workflow cho `DOANTOTNGHIEPFE/.github/workflows/deploy.yml`
```yaml
name: Deploy Frontend to VPS

on:
  push:
    branches: [ main ]

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: 'npm'
      - name: Build check
        run: |
          npm ci
          npm run build -- --configuration production-docker
      - name: Deploy to VPS
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          port: ${{ secrets.VPS_PORT }}
          script: |
            set -e
            cd /opt/trotot/DOANTOTNGHIEPFE && git pull origin main
            cd /opt/trotot && docker compose up -d --build frontend
            docker image prune -f
```

### 6.4. Workflow cho `RentalManagement/.github/workflows/deploy.yml`
```yaml
name: Deploy Backend to VPS

on:
  push:
    branches: [ main ]

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: 'temurin'
          java-version: '21'
          cache: 'maven'
      - name: Compile check
        run: |
          chmod +x mvnw
          ./mvnw compile -B
      - name: Deploy to VPS
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          port: ${{ secrets.VPS_PORT }}
          script: |
            set -e
            cd /opt/trotot/RentalManagement && git pull origin main
            cd /opt/trotot && docker compose up -d --build backend
            docker image prune -f
```

### 6.5. Workflow cho `AIService/.github/workflows/deploy.yml`
```yaml
name: Deploy AIService to VPS

on:
  push:
    branches: [ main ]

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Syntax check
        run: python -m compileall -q app scripts
      - name: Deploy to VPS
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          port: ${{ secrets.VPS_PORT }}
          script: |
            set -e
            cd /opt/trotot/AIService && git pull origin main
            cd /opt/trotot && docker compose up -d --build ai-service
            docker exec rental-ai-service python -m alembic upgrade head
            docker image prune -f
```

---

## 7. Kiểm tra sau deploy & Xử lý sự cố

### Checklist kiểm tra
- Truy cập `https://trotot.online` → SSL hợp lệ, load được danh sách tin và bản đồ MapTiler.
- `curl https://trotot.online/ai/health` → `200 OK`, `core_db` và `ai_db` đều `UP`.
- Thử thanh toán VNPay → kiểm tra redirect với `vnp_TmnCode` mới, trả về `https://trotot.online/thanh-toan/ket-qua` và log IPN ở `rental-backend`.

### Lỗi thường gặp
| Hiện tượng | Nguyên nhân & Cách xử lý |
| --- | --- |
| `502 Bad Gateway` | `rental-backend` hoặc `rental-ai-service` đang khởi động hoặc lỗi. Xem `docker logs -f rental-backend` / `docker logs -f rental-ai-service`. |
| VNPay báo sai chữ ký (Code `97`) | Kiểm tra `VNPAY_TMN_CODE` / `VNPAY_HASH_SECRET` trong `.env` (không dư khoảng trắng), sau đó chạy `docker compose up -d --force-recreate backend`. |
| Thanh toán xong không kích hoạt gói | VNPay chưa gọi được `https://trotot.online/api/payments/vnpay/ipn`. Kiểm tra cấu hình IPN trên portal VNPay và log Nginx (`docker logs rental-frontend`). |
| AI trả `401/403` | `JWT_SECRET` giữa BE và AI chưa khớp hoặc thiếu. Kiểm tra `.env`. |
| Bản đồ không hiện tile | Chưa thêm `https://trotot.online/*` vào Allowed Origins trên MapTiler Cloud. |
