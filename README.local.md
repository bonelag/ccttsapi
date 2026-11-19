# CapCut TTS & STT Python API (`capcut-tts-api`)

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![Audio](https://img.shields.io/badge/Audio-24kHz%2016--bit%20PCM%20WAV-blue?style=flat&logo=audacity&logoColor=white)](https://github.com)
[![Pure Python](https://img.shields.io/badge/Dependency-Pure%20Python%20(Requests)-brightgreen?style=flat)](https://github.com)
[![Concurrency](https://img.shields.io/badge/Concurrency-1--5%20Threads-orange?style=flat)](https://github.com)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

Bộ thư viện Python chuẩn hoá và công cụ tự động hóa toàn diện cho hệ sinh thái tác vụ của **CapCut**:
- **Chuyển đổi văn bản thành giọng nói (Text-to-Speech - TTS)**: Tạo âm thanh studio chất lượng cao chuẩn **24kHz 16-bit Mono PCM WAV** hoặc MP3, tự động nhận diện và ghép nối `voice_type` với `resource_id`.
- **Tự động hoá tạo Dataset hàng loạt (`voice.py`)**: Đa luồng (1–5 luồng), chia batch thông minh, tự động resume tiếp tục tiến độ, kiểm soát timeout và tự sửa file lỗi.
- **Nhận diện giọng nói & trích xuất phụ đề (Speech-to-Text - STT)**: Phiên âm tự động từ audio/video với mốc thời gian chi tiết từng từ và từng câu.
- **Tải lên media VOD phân đoạn**: Upload chunked an toàn với chữ ký bảo mật AWS SigV4.
- **Thuần Python 100%**: Không phụ thuộc thư viện nhị phân C/C++ (`.dll`, `.so`, `.dylib`), không dùng `ctypes`. Toàn bộ thuật toán mã hoá RSA PKCS#1 v1.5 và chữ ký AWS SigV4 được viết trực tiếp bằng Python tiêu chuẩn.

---

## Mục lục

1. [Cài đặt](#1-cài-đặt-installation)
2. [Công cụ tạo Dataset hàng loạt (`voice.py`)](#2-công-cụ-tạo-dataset-hàng-loạt-voicepy)
3. [Danh sách giọng đọc tiếng Việt hỗ trợ](#3-danh-sách-giọng-đọc-tiếng-việt-hỗ-trợ)
4. [Hướng dẫn sử dụng Python SDK](#4-hướng-dẫn-sử-dụng-python-sdk)
   - [Tạo giọng nói TTS](#41-tạo-giọng-nói-tts)
   - [Nhận diện phụ đề STT](#42-nhận-diện-phụ-đề-stt)
   - [Tra cứu danh mục giọng đọc](#43-tra-cứu-danh-mục-giọng-đọc)
   - [Tuỳ biến cấu hình thiết bị (Device Identity)](#44-tuỳ-biến-cấu-hình-thiết-bị-device-identity)
5. [Hướng dẫn dòng lệnh CLI](#5-hướng-dẫn-dòng-lệnh-cli)
6. [Cấu trúc mã nguồn](#6-cấu-trúc-mã-nguồn)
7. [Lưu ý & Trách nhiệm](#7-lưu-ý--trách-nhiệm)

---

## 1. Cài đặt (Installation)

Yêu cầu môi trường **Python 3.9** trở lên.

```bash
# Cách 1: Cài đặt package dạng editable trực tiếp
pip install -e .

# Cách 2: Cài thư viện phụ thuộc tối thiểu
pip install requests
```

---

## 2. Công cụ tạo Dataset hàng loạt (`voice.py`)

File `voice.py` là script tự động hoá hoàn chỉnh để đọc file nhãn `metadata.csv` và tải toàn bộ các file audio tương ứng:

```bash
# Chạy với cấu hình mặc định (3 luồng, định dạng WAV 24kHz)
python voice.py

# Chạy tối đa 5 luồng song song, batch 10 câu/lần
python voice.py --voice "HoatNgon" --output-dir "HoatNgon" -t 5 --batch-size 10

# Chạy 1 luồng tuần tự (thích hợp mạng yếu hoặc kiểm thử)
python voice.py --voice "BanMai" --output-dir "BanMai" -t 1

# Kiểm tra danh sách các giọng đọc được hỗ trợ
python voice.py --list-voices
```

### Điểm nổi bật của `voice.py`:
- **Đa luồng an toàn (1 đến 5 luồng)**: Mỗi luồng xử lý 1 batch riêng biệt, cô lập phiên kết nối (thread-isolated `CapCutClient`), không trùng lặp câu, không đè file.
- **Tự động Resume**: Tự quét thư mục đích, bỏ qua các file đã tạo hợp lệ (> 0 bytes) và chỉ tải tiếp những câu còn thiếu.
- **Ghi file nguyên tử (Atomic Write)**: Tải qua file tạm `.tmp_<filename>` trước khi đổi tên, ngăn chặn file rác hoặc file hỏng khi bị ngắt đột ngột (`Ctrl+C`).
- **Kiểm soát Timeout & Tự sửa**: Tự động tính toán timeout linh hoạt theo độ dài batch, tự gom các câu bị lỗi để tạo lại ở lượt cuối, đảm bảo dữ liệu đầu ra đạt chuẩn 100%.

---

## 3. Danh sách giọng đọc tiếng Việt hỗ trợ

Hệ thống hỗ trợ chọn giọng linh hoạt bằng **Tên hiển thị**, **STT (1–10)**, **ID chữ (Voice Type)** hoặc **ID số (Resource ID)**:

| STT | Tên hiển thị | ID chữ (`voice_type`) | ID số (`resource_id`) | Đặc trưng giọng |
| :---: | :--- | :--- | :--- | :--- |
| **1** | **Nhỏ Ngọt Ngào** | `BV421_vivn_streaming` | `7252594014782755330` | Nữ miền Nam ngọt ngào, ấm áp, tự nhiên |
| **2** | **Giọng Nữ Phổ Thông** | `vi_female_huong` | `7264854897953083905` | Nữ chuẩn phát thanh viên, sách nói, tin tức |
| **3** | **Giọng Bé** | `BV074_streaming_dsp` | `7550087831092251920` | Giọng trẻ em, trong sáng, hồn nhiên |
| **4** | **Cô Gái Hoạt Ngôn** | `BV074_streaming` | `7102355709945188865` | Nữ hoạt bát, năng động, lôi cuốn |
| **5** | **Hoai My** | `vi-VN-HoaiMyNeural` | `7371666434650280464` | Nữ truyền cảm, ngữ điệu mượt mà |
| **6** | **Nam Minh** | `vi-VN-NamMinhNeural` | `7371666524727153168` | Nam chuẩn giọng phổ thông, rõ ràng, dứt khoát |
| **7** | **Việt Méo** | `BV075_streaming_vibrato_dsp` | `7569450639810465040` | Nam vui nhộn, cá tính, biểu cảm phong phú |
| **8** | **Mai** | `BV562_streaming` | `7483736254694035984` | Nữ ấm áp, chững chạc, phát thanh |
| **9** | **Ban Mai** | `multi_female_yangguangnv_uranus_bigtts` | `7637456432522218773` | Nữ dịu dàng, tự nhiên, nhịp điệu êm ái |
| **10** | **Review Phim new** | `multi_female_richgirl_uranus_bigtts` | `7637460351541447956` | Nữ phong cách review phim, kịch tính |

---

## 4. Hướng dẫn sử dụng Python SDK

### 4.1. Tạo giọng nói TTS

Tự động nhận diện `resource_id` tương ứng từ `Voice.json` khi bạn truyền `voice_type` hoặc tên giọng:

```python
from capcut_tts_api import CapCutClient

client = CapCutClient()

# 1. Tạo audio chuẩn WAV không nén (16-bit 24kHz Mono PCM)
result = client.generate_speech(
    texts="Xin chào! Đây là âm thanh chất lượng cao 24kHz được tạo bởi CapCut API.",
    voice="BV421_vivn_streaming",  # Hoặc "Nhỏ Ngọt Ngào"
    rate="1.0",
    audio_format="wav",            # "wav" hoặc "mp3"
    wait=True
)

# 2. Lấy danh sách URL âm thanh trả về và tải file
urls = client.extract_speech_urls(result)
for idx, url in enumerate(urls, start=1):
    client.download_file(url, f"output_{idx}.wav")
    print(f"Đã lưu: output_{idx}.wav")
```

#### Gửi nhiều câu trong cùng 1 Request (Batch API):
```python
texts = [
    "Câu thứ nhất trong danh sách.",
    "Câu thứ hai tiếp theo.",
    "Câu thứ ba kết thúc đoạn."
]

result = client.generate_speech(
    texts=texts,
    voice="BV074_streaming",
    audio_format="wav",
    wait=True
)
urls = client.extract_speech_urls(result)
print(f"Đã tạo thành công {len(urls)} file audio!")
```

---

### 4.2. Nhận diện phụ đề STT

Tự động tải lên file âm thanh/video, tạo tác vụ nhận diện lời nói và bóc tách phụ đề kèm mốc thời gian chính xác:

```python
from capcut_tts_api import CapCutClient

client = CapCutClient()

# Tải lên media và chờ kết quả phiên âm
res = client.transcribe_file(
    file_path="audio_sample.mp3",
    language="vi-VN",
    use_translation=False,
    wait=True
)

# Bóc tách phụ đề có cấu trúc
subtitles = client.extract_subtitles(res)

print("Toàn bộ nội dung:", subtitles.full_text)
print("-" * 50)
for item in subtitles.utterances:
    print(f"[{item.start_time}ms -> {item.end_time}ms] {item.text}")
```

---

### 4.3. Tra cứu danh mục giọng đọc

```python
from capcut_tts_api import CapCutClient

client = CapCutClient()

# Tra cứu các giọng tiếng Việt có sẵn
voices = client.list_voices(lang="vi-VN")

for v in voices:
    print(f"{v.display_name:<20} | Type: {v.voice_type:<35} | ID: {v.resource_id}")
```

---

### 4.4. Tuỳ biến cấu hình thiết bị (Device Identity)

Có thể cấu hình định danh thiết bị linh hoạt qua mã Python hoặc nạp từ file JSON:

```python
from capcut_tts_api import CapCutClient, DeviceConfig

custom_device = DeviceConfig(
    device_id="7647183892936328721",
    iid="7647185302080423697",
    appvr="8.7.0",
    loc="VN",
    lan="vi-VN"
)

# Khởi tạo client với cấu hình riêng
client = CapCutClient(device=custom_device)

# Hoặc nạp trực tiếp từ file device.json
# client = CapCutClient(device="device.json")
```

---

## 5. Hướng dẫn dòng lệnh CLI

Sau khi cài đặt, bạn có thể gọi trực tiếp lệnh `capcut-tts-api` hoặc `python -m capcut_tts_api.cli`:

```bash
# 1. Liệt kê danh sách giọng đọc tiếng Việt
capcut-tts-api list-voices --language vi-VN

# 2. Tạo tác vụ TTS mới
capcut-tts-api tts-new \
  --text "Xin chào thế giới" \
  --voice "BV421_vivn_streaming" \
  --rate 1.0

# 3. Tra vấn trạng thái tác vụ TTS
capcut-tts-api tts-query \
  --task-id "TASK_ID" \
  --token "TOKEN"

# 4. Tải lên và nhận diện phụ đề STT một bước
capcut-tts-api stt-file \
  --audio-file "audio.mp4" \
  --language vi-VN \
  --out "result.json"

# 5. Chế độ Dry-Run (Xem trước payload và chữ ký bảo mật mà không gọi API thật)
capcut-tts-api tts-new \
  --text "Kiểm tra chữ ký" \
  --voice "BV421_vivn_streaming" \
  --dry-run
```

---

## 6. Cấu trúc mã nguồn

```text
capi/
├── capcut_tts_api/              # Thư viện lõi (Core Python Package)
│   ├── __init__.py              # Export SDK & phiên bản
│   ├── client.py                # Lớp CapCutClient chính (TTS, STT, Upload)
│   ├── signer.py                # Mã hoá RSA PKCS#1 v1.5, AWS SigV4, MD5 & tạo chữ ký
│   ├── uploader.py              # Xử lý upload media chunked lên VOD CapCut
│   ├── models.py                # Dataclasses định kiểu (DeviceConfig, Utterance,...)
│   ├── config.py                # URL endpoints, hằng số VOD, public key RSA
│   ├── exceptions.py            # Hệ thống phân cấp lỗi (CapCutError, CapCutTaskError)
│   └── cli.py                   # Bộ phân tích tham số dòng lệnh CLI
├── examples/                    # Các kịch bản mẫu sẵn sàng chạy
│   ├── 01_tts_basic.py          # Mẫu tạo TTS cơ bản
│   ├── 02_stt_transcribe.py     # Mẫu nhận diện phụ đề STT
│   ├── 03_voice_catalog.py      # Mẫu tra cứu giọng đọc
│   └── 04_custom_device.py      # Mẫu tuỳ biến thiết bị
├── voice.py                     # Script tạo dataset TTS hàng loạt (Đa luồng, Resume, Repair)
├── metadata.csv                 # 1,350 câu văn bản mẫu chuẩn hoá 100% tiếng Việt
├── Voice.json                   # Danh mục thư viện giọng đọc CapCut
└── pyproject.toml               # Cấu hình cài đặt module chuẩn PEP 517
```

---

## 7. Lưu ý & Trách nhiệm

- Thư viện được phát triển phục vụ mục đích nghiên cứu, học tập, thu thập và xây dựng các bộ dữ liệu giọng nói mở.
- Vui lòng tuân thủ điều khoản dịch vụ của nhà cung cấp dịch vụ gốc khi sử dụng với tần suất lớn.
