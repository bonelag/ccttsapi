#!/usr/bin/env python3
"""
Giao diện Web Gradio cục bộ cho CapCut TTS & STT API.
Hỗ trợ đầy đủ các tham số cấu hình: Giọng đọc, Tốc độ (Time-stretch), Định dạng (WAV 24kHz/MP3),
Âm lượng, Tự động tách câu dài, và Bóc tách phụ đề STT.
"""

import argparse
import io
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple
import wave

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import gradio as gr

# Thêm thư mục hiện tại vào sys.path
CAPI_DIR = Path(__file__).resolve().parent
if str(CAPI_DIR) not in sys.path:
    sys.path.insert(0, str(CAPI_DIR))

from capcut_tts_api import CapCutClient, CapCutError
from voice import SUPPORTED_VOICES, resolve_voice_info

client = CapCutClient()

# Tạo danh sách lựa chọn giọng đọc hiển thị thân thiện (chỉ hiển thị tên)
VOICE_CHOICES = [v["name"] for v in SUPPORTED_VOICES]


def post_process_audio(
    input_path: Path,
    output_path: Path,
    speed: float = 1.0,
    volume: float = 1.0,
    output_format: str = "wav",
) -> Path:
    """
    Xử lý hậu kỳ âm thanh bằng ffmpeg: điều chỉnh tốc độ (atempo), âm lượng và định dạng.
    """
    needs_speed = abs(speed - 1.0) >= 0.01
    needs_vol = abs(volume - 1.0) >= 0.01
    needs_convert = input_path.suffix.lower().lstrip(".") != output_format.lower()

    if not needs_speed and not needs_vol and not needs_convert:
        shutil.copy2(input_path, output_path)
        return output_path

    if not shutil.which("ffmpeg"):
        shutil.copy2(input_path, output_path)
        return output_path

    cmd = ["ffmpeg", "-y", "-i", str(input_path)]
    filters = []

    # Xử lý tốc độ (atempo)
    if needs_speed:
        curr = speed
        while curr > 2.0:
            filters.append("atempo=2.0")
            curr /= 2.0
        while curr < 0.5:
            filters.append("atempo=0.5")
            curr /= 0.5
        filters.append(f"atempo={curr:.4f}")

    # Xử lý âm lượng (volume)
    if needs_vol:
        filters.append(f"volume={volume:.2f}")

    if filters:
        cmd.extend(["-filter:a", ",".join(filters)])

    cmd.append(str(output_path))

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return output_path


def split_text_into_chunks(text: str, max_chars: int = 250) -> List[str]:
    """Tách văn bản dài thành các câu nhỏ tự nhiên theo dấu câu."""
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return [cleaned]

    # Tách theo dấu kết câu
    sentences = re.split(r"(?<=[.?!,\n])\s+", cleaned)
    chunks = []
    current = ""

    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(current) + len(s) + 1 <= max_chars:
            current = f"{current} {s}".strip() if current else s
        else:
            if current:
                chunks.append(current)
            current = s

    if current:
        chunks.append(current)

    return chunks if chunks else [cleaned]


def generate_tts_ui(
    text: str,
    voice_select: str,
    custom_voice: str,
    audio_format: str,
    speed: float,
    volume: float,
    auto_split: bool,
):
    """Xử lý yêu cầu Text-to-Speech từ giao diện Gradio."""
    text = (text or "").strip()
    if not text:
        return None, "⚠️ Vui lòng nhập văn bản cần đọc!", ""

    start_time = time.time()

    # 1. Xác định giọng đọc
    if custom_voice and custom_voice.strip():
        target_voice_input = custom_voice.strip()
    else:
        target_voice_input = (voice_select or "").strip()

    try:
        voice_type, resource_id, display_name = resolve_voice_info(target_voice_input, client)
    except Exception as exc:
        return None, f"❌ Lỗi nhận diện giọng đọc: {exc}", ""

    # 2. Tách câu nếu văn bản dài
    chunks = split_text_into_chunks(text) if auto_split else [text]
    fmt = "wav" if "wav" in audio_format.lower() else "mp3"

    try:
        # Gọi CapCut API
        result = client.generate_speech(
            texts=chunks,
            voice=voice_type,
            resource_id=resource_id,
            rate="1.0",
            audio_format=fmt,
            wait=True,
            timeout=60.0,
        )
        urls = client.extract_speech_urls(result)
        if not urls:
            return None, "❌ Không nhận được URL âm thanh từ máy chủ CapCut.", ""

        # Tải các đoạn audio về
        temp_dir = Path(tempfile.mkdtemp())
        raw_files = []
        for idx, url in enumerate(urls):
            part_path = temp_dir / f"part_{idx:03d}.{fmt}"
            client.download_file(url, part_path)
            raw_files.append(part_path)

        # Ghép các đoạn nếu có nhiều chunk
        merged_raw = temp_dir / f"merged_raw.{fmt}"
        if len(raw_files) == 1:
            shutil.copy2(raw_files[0], merged_raw)
        else:
            # Ghép audio bằng ffmpeg concat
            concat_list_file = temp_dir / "concat_list.txt"
            with open(concat_list_file, "w", encoding="utf-8") as f:
                for rf in raw_files:
                    f.write(f"file '{rf.resolve().as_posix()}'\n")

            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_list_file), "-c", "copy", str(merged_raw)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # 3. Xử lý hậu kỳ (Tốc độ & Âm lượng)
        final_output = temp_dir / f"output_{int(time.time())}.{fmt}"
        post_process_audio(
            input_path=merged_raw,
            output_path=final_output,
            speed=speed,
            volume=volume,
            output_format=fmt,
        )

        elapsed = time.time() - start_time
        file_size_kb = final_output.stat().st_size / 1024

        # Đọc thời lượng chính xác
        duration_sec = 0.0
        sample_rate_str = "24,000 Hz"
        if fmt == "wav":
            try:
                with wave.open(str(final_output), "rb") as wf:
                    duration_sec = wf.getnframes() / wf.getframerate()
                    sample_rate_str = f"{wf.getframerate():,} Hz"
            except Exception:
                pass

        stats_md = f"""
### 📊 Thông Số Âm Thanh Tạo Ra:
- **Giọng đọc**: `{display_name}` (`{voice_type}`)
- **Thời lượng**: **{duration_sec:.2f} giây**
- **Tần số lấy mẫu**: **{sample_rate_str} (Mono 16-bit PCM)**
- **Dung lượng file**: **{file_size_kb:.1f} KB**
- **Tốc độ áp dụng**: **{speed:.2f}x** | **Âm lượng**: **{volume:.1f}x**
- **Thời gian xử lý API**: **{elapsed:.2f}s**
"""
        return str(final_output), "✅ Tạo giọng đọc thành công!", stats_md

    except Exception as exc:
        return None, f"❌ Lỗi trong quá trình tạo giọng: {exc}", ""


def transcribe_stt_ui(audio_file: str, language: str):
    """Xử lý nhận diện phụ đề STT từ file audio/video."""
    if not audio_file:
        return "⚠️ Vui lòng tải lên file âm thanh hoặc video!", None

    try:
        lang_code = "vi-VN" if "vi" in language.lower() else language
        res = client.transcribe_file(
            file_path=audio_file,
            language=lang_code,
            wait=True,
            timeout=90.0,
        )
        subtitles = client.extract_subtitles(res)

        # Tạo file SRT
        srt_lines = []
        rows = []
        for idx, u in enumerate(subtitles.utterances, start=1):
            s_ms, e_ms = u.start_time, u.end_time
            s_str = f"{s_ms//3600000:02d}:{(s_ms%3600000)//60000:02d}:{(s_ms%60000)//1000:02d},{s_ms%1000:03d}"
            e_str = f"{e_ms//3600000:02d}:{(e_ms%3600000)//60000:02d}:{(e_ms%60000)//1000:02d},{e_ms%1000:03d}"
            srt_lines.extend([str(idx), f"{s_str} --> {e_str}", u.text, ""])
            rows.append([idx, f"{u.start_time/1000:.2f}s", f"{u.end_time/1000:.2f}s", u.text])

        srt_content = "\n".join(srt_lines)
        temp_srt = Path(tempfile.gettempdir()) / f"subtitles_{int(time.time())}.srt"
        temp_srt.write_text(srt_content, encoding="utf-8")

        summary = f"**Toàn bộ văn bản:**\n\n{subtitles.full_text}\n\n---\n**Số câu nhận diện được:** {len(subtitles.utterances)}"
        return summary, str(temp_srt)

    except Exception as exc:
        return f"❌ Lỗi phiên âm: {exc}", None


def search_voice_catalog(query: str, lang_filter: str):
    """Tra cứu kho giọng đọc trong Voice.json."""
    try:
        all_voices = client.list_voices(lang=None if lang_filter == "Tất cả" else lang_filter)
        q = (query or "").lower().strip()
        data = []
        for v in all_voices:
            if not q or (q in v.display_name.lower() or q in v.voice_type.lower() or q in v.resource_id):
                data.append([v.display_name, v.voice_type, v.resource_id, v.lang])
        return data
    except Exception as exc:
        return [[f"Lỗi: {exc}", "", "", ""]]


# ==============================================================================
# GIAO DIỆN GRADIO BLOCKS
# ==============================================================================
custom_css = """
/* Khung cố định kích thước đồng nhất, không co giãn giữa các tab */
.gradio-container {
    width: 100% !important;
    max-width: 1100px !important;
    min-width: 820px !important;
    margin: 0 auto !important;
    box-sizing: border-box !important;
}

/* Đảm bảo nội dung các tab có chiều cao và độ rộng tối thiểu bằng nhau */
.tabitem {
    width: 100% !important;
    min-height: 580px !important;
    padding-top: 15px !important;
    box-sizing: border-box !important;
}

/* Thanh tab luôn giữ nguyên dạng nằm ngang, không bị co lại thành [...] */
.tab-nav {
    display: flex !important;
    flex-direction: row !important;
    flex-wrap: nowrap !important;
    gap: 8px !important;
    margin-bottom: 15px !important;
}
.tab-nav button {
    font-size: 15px !important;
    font-weight: 600 !important;
    padding: 10px 18px !important;
    white-space: nowrap !important;
}

.header-box { text-align: center; margin-bottom: 20px; }
.header-box h1 { background: linear-gradient(135deg, #0284c7, #6366f1); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-size: 2.2em; font-weight: 800; }
.btn-primary { background: linear-gradient(135deg, #0284c7, #4f46e5) !important; color: white !important; font-weight: 600 !important; }
"""

with gr.Blocks(title="CapCut TTS & STT Studio") as demo:
    with gr.Column(elem_classes=["header-box"]):
        gr.Markdown("# 🎙️ CapCut TTS & STT Studio")
        gr.Markdown("**Giao diện tạo giọng nói chuẩn Studio 24kHz Mono PCM & Bóc tách phụ đề tự động**")

    with gr.Tabs():
        # TAB 1: TEXT TO SPEECH
        with gr.Tab("🗣️ Tạo Giọng Nói (TTS)"):
            with gr.Row():
                with gr.Column(scale=3, min_width=320):
                    text_input = gr.Textbox(
                        label="Văn bản cần đọc",
                        placeholder="Nhập đoạn văn bản tiếng Việt cần đọc vào đây...",
                        lines=6,
                        value="Chào bạn! Chào mừng bạn đến với hệ thống chuyển văn bản thành giọng nói chất lượng cao 24kHz của CapCut.",
                    )

                    with gr.Row():
                        gr.Markdown("**Mẫu câu thử nghiệm nhanh:**")
                    with gr.Row():
                        btn_sample_1 = gr.Button("🍲 Ẩm thực & Đời sống", size="sm")
                        btn_sample_2 = gr.Button("📰 Bản tin trang trọng", size="sm")
                        btn_sample_3 = gr.Button("⚔️ Kịch tính phim kiếm hiệp", size="sm")
                        btn_sample_4 = gr.Button("💬 Trò chuyện thường ngày", size="sm")

                    with gr.Row():
                        voice_dropdown = gr.Dropdown(
                            label="Chọn giọng đọc tiếng Việt",
                            choices=VOICE_CHOICES,
                            value=VOICE_CHOICES[0],
                        )
                        custom_voice_input = gr.Textbox(
                            label="Hoặc nhập ID giọng riêng (Tuỳ chọn)",
                            placeholder="Ví dụ: BV421_vivn_streaming hoặc 7252594014782755330",
                        )

                    with gr.Row():
                        format_radio = gr.Radio(
                            label="Định dạng âm thanh đầu ra",
                            choices=["WAV (Chất lượng cao)", "MP3 (Nén tiện chia sẻ)"],
                            value="WAV (Chất lượng cao)",
                        )
                        auto_split_check = gr.Checkbox(
                            label="Tự động chia nhỏ văn bản dài (chống timeout)",
                            value=True,
                        )

                    with gr.Row():
                        speed_slider = gr.Slider(
                            label="Tốc độ đọc (Time-stretch giữ nguyên cao độ)",
                            minimum=0.5,
                            maximum=2.0,
                            value=1.0,
                            step=0.05,
                        )
                        volume_slider = gr.Slider(
                            label="Âm lượng (Volume)",
                            minimum=0.2,
                            maximum=2.0,
                            value=1.0,
                            step=0.1,
                        )

                    btn_generate = gr.Button("🚀 Tạo Giọng Đọc Ngay", variant="primary", elem_classes=["btn-primary"], size="lg")

                with gr.Column(scale=2, min_width=300):
                    audio_output = gr.Audio(label="Âm thanh nghe thử", type="filepath")
                    status_text = gr.Markdown(value="*Sẵn sàng tạo giọng đọc.*")
                    stats_box = gr.Markdown(value="")

            # Cài đặt mẫu câu nhanh
            btn_sample_1.click(
                fn=lambda: "Thế nhưng nó lại có mặt ở mọi ngóc ngách, ngã đường và người dân Việt coi phở như một món ăn thân thuộc.",
                inputs=None,
                outputs=text_input,
            )
            btn_sample_2.click(
                fn=lambda: "Kính chào quý vị và các bạn! Bản tin thời sự hôm nay sẽ cập nhật những diễn biến kinh tế và công nghệ nổi bật nhất trong tuần qua.",
                inputs=None,
                outputs=text_input,
            )
            btn_sample_3.click(
                fn=lambda: "Đột phá rồi! Sư phụ, cuối cùng đồ nhi cũng đã luyện thành tầng thứ chín! Mau nhận lấy một kiếm này!",
                inputs=None,
                outputs=text_input,
            )
            btn_sample_4.click(
                fn=lambda: "Alo, cuối tuần này cậu có rảnh không? Tụi mình cùng đi uống cà phê rồi ghé qua phố sách nhé!",
                inputs=None,
                outputs=text_input,
            )

            # Xử lý nút bấm tạo giọng
            btn_generate.click(
                fn=generate_tts_ui,
                inputs=[
                    text_input,
                    voice_dropdown,
                    custom_voice_input,
                    format_radio,
                    speed_slider,
                    volume_slider,
                    auto_split_check,
                ],
                outputs=[audio_output, status_text, stats_box],
            )

        # TAB 2: SPEECH TO TEXT
        with gr.Tab("📝 Bóc Tách Phụ Đề (STT)"):
            with gr.Row():
                with gr.Column(scale=1, min_width=320):
                    media_input = gr.File(
                        label="Tải lên file âm thanh/video (.mp3, .wav, .mp4, .m4a)",
                        file_types=["audio", "video"],
                        height=210,
                    )
                    stt_lang = gr.Dropdown(
                        label="Ngôn ngữ nhận diện",
                        choices=["vi-VN (Tiếng Việt)", "en-US (Tiếng Anh)", "zh-CN (Tiếng Trung)"],
                        value="vi-VN (Tiếng Việt)",
                    )
                    btn_stt = gr.Button("⚡ Bóc Tách Phụ Đề", variant="primary", elem_classes=["btn-primary"], size="lg")

                with gr.Column(scale=1, min_width=320):
                    stt_result = gr.Markdown(
                        label="Kết quả phiên âm",
                        value="""### 📄 Kết Quả Phiên Âm
*Tải lên file âm thanh/video ở cột bên trái và nhấn **Bóc Tách Phụ Đề** để hiển thị nội dung và tải file .srt.*
""",
                    )
                    srt_download = gr.File(label="Tải file phụ đề (.srt)", height=150)

            btn_stt.click(
                fn=transcribe_stt_ui,
                inputs=[media_input, stt_lang],
                outputs=[stt_result, srt_download],
            )

        # TAB 3: VOICE CATALOG EXPLORER
        with gr.Tab("🔍 Thư Viện Giọng Đọc (Catalog)"):
            with gr.Row():
                search_box = gr.Textbox(label="Tìm giọng đọc (tên, ID hoặc ngôn ngữ)", placeholder="Nhập từ khóa cần tìm...")
                lang_select = gr.Dropdown(label="Lọc theo ngôn ngữ", choices=["Tất cả", "vi-VN", "en-US", "th-TH", "id-ID"], value="vi-VN")
                btn_search = gr.Button("Tìm kiếm")

            voice_table = gr.Dataframe(
                headers=["Tên hiển thị", "Mã giọng (voice_type)", "Resource ID", "Ngôn ngữ"],
                datatype=["str", "str", "str", "str"],
                value=search_voice_catalog("", "vi-VN"),
            )

            btn_search.click(
                fn=search_voice_catalog,
                inputs=[search_box, lang_select],
                outputs=voice_table,
            )


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Kiểm tra cổng mạng có đang bị ứng dụng khác sử dụng hay không."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def find_available_port(start_port: int = 7860, max_attempts: int = 50) -> int:
    """Tìm cổng mạng trống tiếp theo."""
    for port in range(start_port, start_port + max_attempts):
        if not is_port_in_use(port):
            return port
    return start_port


def main():
    parser = argparse.ArgumentParser(description="Chạy ứng dụng Web Gradio CapCut TTS")
    parser.add_argument("--port", type=int, default=7860, help="Cổng chạy ứng dụng (Mặc định: 7860, tự dò cổng trống)")
    parser.add_argument("--share", action="store_true", help="Tạo link công khai qua Gradio Share")
    args = parser.parse_args()

    port = args.port
    if is_port_in_use(port):
        new_port = find_available_port(port + 1)
        print(f"[CẢNH BÁO] Cổng {port} đang bận! Tự động chuyển sang cổng khả dụng: {new_port}")
        port = new_port

    print("=" * 70)
    print("ỨNG DỤNG WEB CAPCUT TTS & STT (GRADIO)")
    print("=" * 70)
    print(f"Địa chỉ cục bộ : http://localhost:{port}")
    print("=" * 70)

    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        share=args.share,
        theme=gr.themes.Soft(primary_hue="sky"),
        css=custom_css,
    )


if __name__ == "__main__":
    main()

