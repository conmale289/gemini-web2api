# gemini-web2api

<p align="center">
  <img src="logo.png" width="200" alt="gemini-web2api logo">
</p>

[English](README.md) | [中文](README_CN.md)

Chuyển đổi giao diện web Google Gemini thành API tương thích OpenAI. Miễn phí, đa nền tảng, một file duy nhất.

## Tính năng

- **API Keys tuỳ chọn**: không xác thực khi `api_keys` rỗng, xác thực Bearer khi được cấu hình
- **Tương thích OpenAI**: Thay thế trực tiếp cho `/v1/chat/completions` và `/v1/models`
- **Tool Calling**: Hỗ trợ đầy đủ gọi hàm (format OpenAI)
- **Nhiều Model**: Flash, Flash Thinking (output 20k+ ký tự), Pro, Auto, Lite
- **Thinking Depth**: Điều chỉnh qua hậu tố `@think=N` (0=sâu nhất, 4=nông nhất)
- **Tìm kiếm Web**: Truy cập internet tích hợp (tìm kiếm gốc của Gemini)
- **Đa nền tảng**: Python thuần, một dependency tuỳ chọn (`httpx` cho streaming)
- **Streaming**: Hỗ trợ SSE streaming qua `httpx`
- **Codex CLI**: Responses API (`/v1/responses`) cho tích hợp OpenAI Codex
- **Gemini CLI**: Google native API (`/v1beta/models`) tương thích Gemini CLI
- **Nén prompt thông minh**: Tự động nén prompt quá dài (lấy cảm hứng từ RTK)
- **Chống ban**: Thuật toán xoay tài khoản chống phát hiện với token bucket, cooldown, health scoring

## Bắt đầu nhanh

```bash
pip install -r requirements.txt
python gemini_web2api.py
```

Server khởi động tại `http://localhost:8081/v1`.

## Cấu hình Client

### Cherry Studio / ChatBox / bất kỳ client OpenAI nào

| Trường | Giá trị |
|--------|---------|
| Base URL | `http://localhost:8081/v1` |
| API Key | để trống hoặc dùng placeholder khi `api_keys` là `[]` |
| Model | `gemini-3.5-flash-thinking` |

### curl

```bash
curl http://localhost:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-3.5-flash","messages":[{"role":"user","content":"Xin chào!"}]}'
```

### OpenAI Python SDK

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8081/v1", api_key="không-cần-khi-api-keys-rỗng")
resp = client.chat.completions.create(
    model="gemini-3.5-flash-thinking",
    messages=[{"role": "user", "content": "Giải thích máy tính lượng tử"}]
)
print(resp.choices[0].message.content)
```

### Codex CLI

```toml
model_provider = "gemini-web2api"
model = "gemini-3.5-flash"

[model_providers.gemini-web2api]
name = "gemini-web2api"
base_url = "http://localhost:8081/v1"
wire_api = "responses"
```

### Gemini CLI

```bash
export GEMINI_API_KEY=none
export GOOGLE_GEMINI_BASE_URL=http://localhost:8081
gemini
```

### Hermes Agent

Hỗ trợ tương thích Ollama API cho Hermes Agent:
- `POST /api/show` — thông tin model
- `GET /api/v1/models` — danh sách model
- `GET /api/tags` — danh sách tags

## Model có sẵn

| Model | Mô tả | Output |
|-------|--------|--------|
| `gemini-3.5-flash` | Đa dụng nhanh | ~12k ký tự |
| `gemini-3.5-flash-thinking` | Suy nghĩ sâu, output dài nhất | **~20k ký tự** |
| `gemini-3.5-flash-thinking-lite` | Thinking tự điều chỉnh độ sâu | ~15k ký tự |
| `gemini-3.1-pro` | Pro (cần cookie cho routing thật) | ~12k ký tự |
| `gemini-auto` | Tự chọn model | tuỳ biến |
| `gemini-flash-lite` | Nhẹ, nhanh | ~10k ký tự |

### Thinking Depth

Thêm `@think=N` vào tên model:

```
gemini-3.5-flash-thinking@think=0   # sâu nhất (mặc định)
gemini-3.5-flash-thinking@think=2   # trung bình
gemini-3.5-flash-thinking@think=4   # nông nhất
```

## Nén Prompt Thông Minh

Server tự động nén prompt quá dài bằng 4 chiến lược (lấy cảm hứng từ [RTK](https://github.com/rtk-ai/rtk)):

1. **Lọc thông minh** — Xoá comment HTML, khoảng trắng thừa, boilerplate
2. **Khử trùng lặp** — Gộp kết quả tool lặp lại liên tiếp
3. **Cắt ngắn** — Giữ tin nhắn gần đây, tóm tắt tin cũ
4. **Mã hoá gọn** — JSON tối giản cho tool definitions

Giới hạn: **80.000 ký tự** (tối đa cho Gemini Web). Prompt 186K ký tự từ Hermes Agent được tự động nén xuống còn ~80K và hoạt động bình thường.

## Thuật toán Xoay Tài khoản Chống Ban

Khi cấu hình nhiều tài khoản, server sử dụng thuật toán chống phát hiện:

| Lớp bảo vệ | Cơ chế | Tác dụng |
|-------------|--------|----------|
| **Token Bucket** | 5 token burst, nạp 1/phút | Ngăn sử dụng tần suất cao liên tục |
| **Cooldown** | Tối thiểu 3 giây giữa cùng tài khoản | Mô phỏng hành vi con người |
| **Health Score** | Cửa sổ trượt 5 phút | Giảm ưu tiên tài khoản có lỗi |
| **Backoff + Jitter** | Exponential 2^n ± 30% ngẫu nhiên | Thời gian retry không đoán được |
| **Warmup** | 3 request ở 50% ưu tiên sau phục hồi | Không đánh mạnh tài khoản vừa hồi |
| **Hồi phục dần** | Success giảm failure 1 (không reset) | Cần thành công liên tục để hồi hoàn toàn |

## Cookie cho Pro (tuỳ chọn)

Truy cập ẩn danh hoạt động cho tất cả model, nhưng `gemini-3.1-pro` sẽ route sang Flash nếu không có xác thực. Để có routing Pro thật, bạn cần cookie từ tài khoản **Gemini Advanced** (đăng ký trả phí):

```bash
python gemini_web2api.py --cookie-file cookie.txt
```

### Cách lấy cookie

1. Mở Chrome, vào [gemini.google.com](https://gemini.google.com) và đăng nhập tài khoản **Gemini Advanced**
2. Mở DevTools (F12) → Application → Cookies → `https://gemini.google.com`
3. Copy các giá trị: `SID`, `HSID`, `SSID`, `APISID`, `SAPISID`, `__Secure-1PSID`
4. Tạo `cookie.txt`:

```
SID=giá_trị; HSID=giá_trị; SSID=giá_trị; APISID=giá_trị; SAPISID=giá_trị; __Secure-1PSID=giá_trị
```

## Xoay nhiều tài khoản (round-robin)

```json
{
  "accounts": [
    {"id": "acc1", "cookie_file": "/app/cookie1.txt", "auth_user": "0", "enabled": true, "weight": 1},
    {"id": "acc2", "cookie_file": "/app/cookie2.txt", "auth_user": "1", "enabled": true, "weight": 2}
  ]
}
```

`weight` cao hơn = được chọn nhiều hơn. Tài khoản bị lỗi sẽ tự động backoff và hồi phục dần.

## Cấu hình

Tạo `config.json` cùng thư mục:

```json
{
  "port": 8081,
  "host": "0.0.0.0",
  "retry_attempts": 3,
  "max_account_retry_attempts": 12,
  "retry_delay_sec": 2,
  "request_timeout_sec": 180,
  "api_keys": [],
  "accounts": [],
  "cookie_file": null,
  "proxy": null,
  "log_requests": true,
  "log_level": "INFO",
  "log_file": "logs/gemini-web2api.log",
  "log_rotation": "20 MB",
  "log_retention": "7 days"
}
```

### Logging và giám sát

Đặt `"log_level": "DEBUG"` để xem chi tiết request/response body (cắt ở 2KB). Đặt `"INFO"` cho production.

## Docker

```bash
cp config.example.json config.json
docker compose up -d
```

## Proxy

Nếu không truy cập trực tiếp `gemini.google.com`:

```json
{"proxy": "http://127.0.0.1:7890"}
```

Hoặc biến môi trường:
```bash
export HTTPS_PROXY=http://127.0.0.1:7890
```

## Giới hạn

- **Không hỗ trợ ảnh**: Gemini yêu cầu giao thức RPC riêng cho upload ảnh
- **Không phải Pro/Ultra thật**: Không có cookie trả phí thì `gemini-3.1-pro` route sang Flash
- **Đơn lượt**: Mỗi request là cuộc hội thoại độc lập
- **Giới hạn tốc độ**: Google có thể throttle request tần suất cao
- **Giới hạn prompt**: Tối đa ~80.000 ký tự (tự động cắt nếu vượt)

## Yêu cầu

- Python 3.8+
- `httpx` (`pip install httpx`) — cho streaming
- Truy cập mạng tới `gemini.google.com` (có thể cần proxy/VPN)

## Giấy phép

MIT
