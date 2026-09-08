#!/usr/bin/env python3
"""
CapCut TTS & STT API Server - Chuẩn tương thích OpenAI Audio API (/v1/audio/speech)

Tương thích hoàn toàn với:
- Thư viện chính thức: `openai.audio.speech.create(...)`
- Các phần mềm tích hợp: OpenWebUI, SillyTavern, LibreChat, Next.js, v.v.
- Chuẩn OpenAI Speech API: POST /v1/audio/speech
- Chuẩn OpenAI Transcription API: POST /v1/audio/transcriptions
- Chuẩn OpenAI Models API: GET /v1/models
"""

import argparse
import io
import math
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Literal, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import uvicorn

# Thêm đường dẫn capi vào sys.path
CAPI_DIR = Path(__file__).resolve().parent
if str(CAPI_DIR) not in sys.path:
    sys.path.insert(0, str(CAPI_DIR))

from capcut_tts_api import CapCutClient, CapCutError
from voice import SUPPORTED_VOICES, resolve_voice_info

app = FastAPI(
    title="CapCut TTS & STT - OpenAI Compatible API",
    description="Máy chủ API chuyển đổi giọng nói (TTS) và phiên âm (STT) tương thích chuẩn OpenAI Audio API.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Khởi tạo CapCut Client dùng chung
client = CapCutClient()


# ==============================================================================
# SCHEMAS (Chuẩn OpenAI API)
# ==============================================================================
class SpeechRequest(BaseModel):
    model: str = Field(
        default="tts-1",
        description="Mã mô hình hoặc tên giọng đọc (tts-1, tts-1-hd, BV421_vivn_streaming, Nhỏ Ngọt Ngào, v.v.)",
    )
    input: str = Field(..., description="Đoạn văn bản cần chuyển thành giọng nói")
    voice: Optional[str] = Field(
        default="BV421_vivn_streaming",
        description="Tên giọng đọc, STT (1-10) hoặc ID chữ/số (Ví dụ: Nhỏ Ngọt Ngào, Ban Mai, GiongBe)",
    )
    response_format: Optional[str] = Field(
        default="wav",
        description="Định dạng âm thanh đầu ra: wav, mp3, flac, aac, opus",
    )
    speed: Optional[float] = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        description="Tốc độ nói từ 0.25 đến 4.0 (chuẩn: 1.0). Tự động time-stretch chuẩn cao độ qua ffmpeg.",
    )


def adjust_audio_speed_and_format(
    raw_audio: bytes,
    input_format: str,
    output_format: str,
    speed: float = 1.0,
) -> bytes:
    """
    Điều chỉnh tốc độ (time-stretch giữ nguyên cao độ) và chuyển đổi định dạng âm thanh qua ffmpeg.
    """
    needs_speed = abs(speed - 1.0) >= 0.01
    needs_convert = input_format.lower().strip() != output_format.lower().strip()

    if not needs_speed and not needs_convert:
        return raw_audio

    # Kiểm tra xem có ffmpeg không
    if not shutil.which("ffmpeg"):
        # Nếu không có ffmpeg, trả về audio gốc
        return raw_audio

    with tempfile.NamedTemporaryFile(suffix=f".{input_format}", delete=False) as in_f:
        in_f.write(raw_audio)
        in_path = in_f.name

    with tempfile.NamedTemporaryFile(suffix=f".{output_format}", delete=False) as out_f:
        out_path = out_f.name

    try:
        cmd = ["ffmpeg", "-y", "-i", in_path]

        # Xây dựng chuỗi bộ lọc atempo nếu có đổi tốc độ
        if needs_speed:
            filters = []
            curr = speed
            while curr > 2.0:
                filters.append("atempo=2.0")
                curr /= 2.0
            while curr < 0.5:
                filters.append("atempo=0.5")
                curr /= 0.5
            filters.append(f"atempo={curr:.4f}")
            cmd.extend(["-filter:a", ",".join(filters)])

        cmd.append(out_path)
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return Path(out_path).read_bytes()
    except Exception as exc:
        print(f"[Cảnh báo] Lỗi xử lý ffmpeg: {exc}", file=sys.stderr)
        return raw_audio
    finally:
        Path(in_path).unlink(missing_ok=True)
        Path(out_path).unlink(missing_ok=True)


# ==============================================================================
# ENDPOINTS
# ==============================================================================
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Trang thông tin tổng quan & hướng dẫn kết nối API."""
    base_url = str(request.base_url).rstrip("/")
    voices_html = "".join(
        f"<li><b>{v['index']}. {v['name']}</b>: <code>{v['voice_type']}</code> (ID: <code>{v['resource_id']}</code>)</li>"
        for v in SUPPORTED_VOICES
    )
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>CapCut TTS API - OpenAI Compatible</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; line-height: 1.6; color: #1e293b; }}
            h1 {{ color: #0284c7; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; }}
            pre {{ background: #0f172a; color: #38bdf8; padding: 15px; border-radius: 8px; overflow-x: auto; }}
            code {{ font-family: monospace; }}
            .badge {{ display: inline-block; background: #e0f2fe; color: #0369a1; padding: 4px 10px; border-radius: 9999px; font-weight: bold; font-size: 0.85em; }}
            ul {{ line-height: 1.8; }}
            a {{ color: #0284c7; text-decoration: none; font-weight: 600; }}
            a:hover {{ text-decoration: underline; }}
        </style>
    </head>
    <body>
        <h1>CapCut TTS & STT API <span class="badge">OpenAI Compatible</span></h1>
        <p>Máy chủ API chuyển đổi giọng nói đạt chuẩn OpenAI Audio Speech API. Có thể kết nối trực tiếp với OpenAI SDK hoặc các phần mềm hỗ trợ OpenAI TTS.</p>
        
        <h3>1. Điểm truy cập khả dụng:</h3>
        <ul>
            <li><code>POST {base_url}/v1/audio/speech</code>: Chuyển văn bản thành giọng nói (TTS)</li>
            <li><code>POST {base_url}/v1/audio/transcriptions</code>: Nhận diện giọng nói bóc tách phụ đề (STT)</li>
            <li><code>GET {base_url}/v1/models</code>: Liệt kê danh sách giọng đọc</li>
            <li><code>GET {base_url}/docs</code>: <a href="/docs">Tài liệu Swagger tương tác (API Docs)</a></li>
        </ul>

        <h3>2. Danh sách giọng đọc tiếng Việt nổi bật:</h3>
        <ul>{voices_html}</ul>

        <h3>3. Ví dụ gọi bằng Python (OpenAI SDK chính thức):</h3>
        <pre><code>from openai import OpenAI

client = OpenAI(base_url="{base_url}/v1", api_key="sk-dummy")

response = client.audio.speech.create(
    model="tts-1",
    voice="Nhỏ Ngọt Ngào",  # hoặc "BV421_vivn_streaming", "Ban Mai", "1"
    input="Xin chào bạn! Đây là giọng đọc từ CapCut API chuẩn OpenAI.",
    response_format="wav",
    speed=1.0
)

response.stream_to_file("speech.wav")
print("Đã lưu speech.wav thành công!")</code></pre>

        <h3>4. Ví dụ gọi bằng cURL:</h3>
        <pre><code>curl {base_url}/v1/audio/speech \\
  -H "Content-Type: application/json" \\
  -d '{{
    "model": "tts-1",
    "voice": "Nhỏ Ngọt Ngào",
    "input": "Xin chào thế giới!",
    "response_format": "wav",
    "speed": 1.0
  }}' --output test.wav</code></pre>
    </body>
    </html>
    """


@app.get("/health")
def health():
    return {"status": "ok", "service": "capcut-tts-api", "version": "1.0.0"}


@app.get("/v1/models")
def list_models():
    """Liệt kê danh sách mô hình / giọng đọc chuẩn OpenAI."""
    models_data = [
        {"id": "tts-1", "object": "model", "owned_by": "capcut", "permission": []},
        {"id": "tts-1-hd", "object": "model", "owned_by": "capcut", "permission": []},
        {"id": "whisper-1", "object": "model", "owned_by": "capcut", "permission": []},
    ]
    for v in SUPPORTED_VOICES:
        models_data.append({
            "id": v["voice_type"],
            "object": "model",
            "owned_by": "capcut",
            "display_name": v["name"],
            "resource_id": v["resource_id"],
            "index": v["index"],
        })
        # Thêm alias theo tên tiếng Việt
        models_data.append({
            "id": v["name"],
            "object": "model",
            "owned_by": "capcut",
            "alias_to": v["voice_type"],
        })
    return {"object": "list", "data": models_data}


@app.get("/v1/models/{model_id}")
def get_model(model_id: str):
    return {"id": model_id, "object": "model", "owned_by": "capcut", "permission": []}


@app.post("/v1/audio/speech")
async def create_speech(req: SpeechRequest):
    """
    Tạo âm thanh TTS theo chuẩn OpenAI: POST /v1/audio/speech
    """
    text = (req.input or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Văn bản 'input' không được để trống.")

    # 1. Xác định giọng đọc từ model hoặc voice
    target_voice_input = req.voice
    if req.model and req.model.lower() not in ("tts-1", "tts-1-hd"):
        target_voice_input = req.model

    try:
        voice_type, resource_id, display_name = resolve_voice_info(target_voice_input, client)
    except Exception as e:
        voice_type, resource_id, display_name = (
            "BV421_vivn_streaming",
            "7252594014782755330",
            "Nhỏ Ngọt Ngào",
        )

    # 2. Xác định định dạng
    fmt = (req.response_format or "wav").lower().strip()
    capcut_fetch_fmt = "wav" if fmt in ("wav", "flac", "pcm") else "mp3"

    # 3. Gọi CapCut API
    try:
        # Nếu văn bản dài trên 300 ký tự, có thể cắt thành đoạn
        result = client.generate_speech(
            texts=text,
            voice=voice_type,
            resource_id=resource_id,
            rate="1.0",
            audio_format=capcut_fetch_fmt,
            wait=True,
            timeout=60.0,
        )
        urls = client.extract_speech_urls(result)
        if not urls:
            raise CapCutError("Không nhận được URL âm thanh từ máy chủ CapCut.")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Lỗi CapCut API: {exc}")

    # 4. Tải file âm thanh về bộ nhớ
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{capcut_fetch_fmt}", delete=False) as tmp_f:
            tmp_path = Path(tmp_f.name)
        client.download_file(urls[0], tmp_path)
        raw_audio_bytes = tmp_path.read_bytes()
        tmp_path.unlink(missing_ok=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Không tải được file âm thanh từ CDN: {exc}")

    # 5. Xử lý tốc độ (speed / atempo) và chuyển đổi định dạng theo yêu cầu
    final_audio = adjust_audio_speed_and_format(
        raw_audio=raw_audio_bytes,
        input_format=capcut_fetch_fmt,
        output_format=fmt,
        speed=req.speed or 1.0,
    )

    # 6. Trả về Content-Type tương ứng
    media_types = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "opus": "audio/opus",
        "aac": "audio/aac",
        "flac": "audio/flac",
        "pcm": "audio/pcm",
    }
    content_type = media_types.get(fmt, "application/octet-stream")

    from urllib.parse import quote
    return Response(
        content=final_audio,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="speech.{fmt}"',
            "X-Voice-Name": quote(display_name),
            "X-Voice-Type": voice_type,
            "X-Voice-Resource-ID": resource_id,
        },
    )


@app.post("/v1/audio/transcriptions")
async def create_transcription(
    file: UploadFile = File(...),
    model: str = Form(default="whisper-1"),
    language: str = Form(default="vi-VN"),
    response_format: str = Form(default="json"),
):
    """
    Nhận diện giọng nói STT theo chuẩn OpenAI: POST /v1/audio/transcriptions
    """
    # Lưu file tạm
    suffix = Path(file.filename).suffix or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        lang_code = "vi-VN" if "vi" in language.lower() else language
        res = client.transcribe_file(
            file_path=tmp_path,
            language=lang_code,
            wait=True,
            timeout=90.0,
        )
        subtitles = client.extract_subtitles(res)

        if response_format.lower() in ("text", "srt", "vtt"):
            if response_format.lower() == "text":
                return Response(content=subtitles.full_text, media_type="text/plain")
            elif response_format.lower() == "srt":
                # Xuất định dạng SRT
                srt_lines = []
                for idx, u in enumerate(subtitles.utterances, start=1):
                    s_ms, e_ms = u.start_time, u.end_time
                    s_str = f"{s_ms//3600000:02d}:{(s_ms%3600000)//60000:02d}:{(s_ms%60000)//1000:02d},{s_ms%1000:03d}"
                    e_str = f"{e_ms//3600000:02d}:{(e_ms%3600000)//60000:02d}:{(e_ms%60000)//1000:02d},{e_ms%1000:03d}"
                    srt_lines.extend([str(idx), f"{s_str} --> {e_str}", u.text, ""])
                return Response(content="\n".join(srt_lines), media_type="text/plain")

        return {
            "text": subtitles.full_text,
            "segments": [
                {
                    "id": idx,
                    "start": u.start_time / 1000.0,
                    "end": u.end_time / 1000.0,
                    "text": u.text,
                }
                for idx, u in enumerate(subtitles.utterances)
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi bóc tách phụ đề: {exc}")
    finally:
        tmp_path.unlink(missing_ok=True)


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================
def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Kiểm tra cổng mạng có đang bị ứng dụng khác sử dụng hay không."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def find_available_port(start_port: int = 8080, max_attempts: int = 50) -> int:
    """Tìm cổng mạng trống tiếp theo."""
    for port in range(start_port, start_port + max_attempts):
        if not is_port_in_use(port):
            return port
    return start_port


def main():
    parser = argparse.ArgumentParser(description="Chạy API Server CapCut TTS tương thích OpenAI")
    parser.add_argument("--host", default="0.0.0.0", help="Địa chỉ IP lắng nghe (Mặc định: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Cổng chạy máy chủ (Mặc định: 8080, tự dò cổng trống)")
    parser.add_argument("--reload", action="store_true", help="Tự động tải lại khi code thay đổi")

    args = parser.parse_args()

    port = args.port
    if is_port_in_use(port):
        new_port = find_available_port(port + 1)
        print(f"[CẢNH BÁO] Cổng {port} đang bận! Tự động chuyển sang cổng khả dụng: {new_port}")
        port = new_port

    print("=" * 70)
    print("MÁY CHỦ CAPCUT TTS API (CHUẨN OPENAI /v1/audio/speech)")
    print("=" * 70)
    print(f"Địa chỉ lắng nghe : http://{args.host}:{port}")
    print(f"Tài liệu Swagger  : http://localhost:{port}/docs")
    print(f"Endpoint TTS      : http://localhost:{port}/v1/audio/speech")
    print(f"Endpoint STT      : http://localhost:{port}/v1/audio/transcriptions")
    print(f"Endpoint Models   : http://localhost:{port}/v1/models")
    print("=" * 70)

    uvicorn.run("api:app", host=args.host, port=port, reload=args.reload)


if __name__ == "__main__":
    main()

