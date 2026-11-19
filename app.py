#!/usr/bin/env python3
"""Responsive Gradio studio for CapCut speech, subtitles, and translation."""

import argparse
import json
from pathlib import Path
import socket
import sys

import gradio as gr

from capcut_tts_api import __version__
from web_workflows import (
    LANGUAGES, VOICE_CHOICES, build_action_ui, digital_human_metadata,
    generate_tts_ui, inspect_srt_ui, post_process_audio, query_task_ui,
    save_connection_settings, search_voice_catalog, split_text_into_chunks,
    srt_to_audio_ui, transcribe_media_ui, transcribe_stt_ui,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CAPI_DIR = Path(__file__).resolve().parent
custom_css = (CAPI_DIR / "studio.css").read_text(encoding="utf-8")
THEME = gr.themes.Soft(
    primary_hue="teal", secondary_hue="cyan", neutral_hue="slate",
    font=["Segoe UI", "Arial", "sans-serif"], font_mono=["Consolas", "monospace"],
).set(
    body_background_fill="#f5f7fa", body_background_fill_dark="#101820",
    block_background_fill="#ffffff", block_background_fill_dark="#18232d",
    block_border_color="#e4e9ef", block_border_color_dark="#2a3946",
    block_radius="14px", button_large_radius="12px",
    block_label_background_fill="transparent", block_label_background_fill_dark="transparent",
    block_label_text_color="#526674", block_label_text_color_dark="#b3c5cf",
    block_label_shadow="none",
    button_primary_background_fill="#087f79", button_primary_background_fill_hover="#06665f",
    button_primary_text_color="#ffffff",
)
SAMPLES = {
    "Đời sống": "Giữa nhịp sống bận rộn, đôi khi chỉ cần một tách cà phê ấm và một bản nhạc quen là đủ để bắt đầu ngày mới.",
    "Bản tin": "Kính chào quý vị và các bạn. Cùng điểm qua những tin tức công nghệ nổi bật trong ngày hôm nay.",
    "Kể chuyện": "Chiều hôm ấy, khi nắng vừa nghiêng qua khung cửa, cô tìm thấy một lá thư cũ nằm giữa những trang sách.",
}
MEDIA_TYPES = ["audio", "video"]
SUBTITLE_HEADERS = ["Bắt đầu", "Kết thúc", "Nội dung gốc", "Bản dịch"]


def section_header(number, title, description):
    gr.HTML(f'<div class="section-heading"><span class="step-index">{number}</span>'
            f'<div><h2>{title}</h2><p>{description}</p></div></div>')


def voice_controls(prefix):
    voice = gr.Dropdown(choices=VOICE_CHOICES, value=VOICE_CHOICES[0], label="Giọng đọc tiếng Việt",
                        elem_id=f"{prefix}-voice", min_width=0)
    with gr.Accordion("Dùng một giọng khác", open=False):
        custom = gr.Textbox(label="Tên hoặc mã giọng", placeholder="Tên giọng, voice_type hoặc resource_id",
                            elem_id=f"{prefix}-custom")
        gr.Markdown("Tra cứu thêm giọng trong mục **Giọng đọc**.")
    return voice, custom


def language_dropdown(label, value="vi-VN"):
    return gr.Dropdown(choices=LANGUAGES, value=value, label=label, allow_custom_value=True, min_width=0)


def apply_connection(base_url, timeout, headers, previous):
    try:
        return save_connection_settings(base_url, timeout, headers)
    except ValueError as exc:
        return previous, f"Chưa áp dụng cấu hình: {exc}"


def recognize(media, language, settings):
    original, _, source_file, _, rows, status = transcribe_media_ui(media, language, settings=settings)
    return original, source_file, rows, status


def translate(media, source, target, settings):
    return transcribe_media_ui(media, source, target, translate=True, settings=settings)


with gr.Blocks(title="CapCut · Audio Studio", analytics_enabled=False, delete_cache=(3600, 86400)) as demo:
    connection = gr.State({})
    gr.HTML(f"""
    <header class="studio-header">
      <a class="brand" href="/" aria-label="CapCut Audio Studio">
        <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span>
        <span>CapCut<span class="brand-sub">AUDIO STUDIO</span></span>
      </a>
      <div class="header-status"><span class="status-dot"></span> Không gian làm việc <span class="version">v{__version__}</span></div>
    </header>
    <section class="studio-hero">
      <div><span class="eyebrow">TỪ Ý TƯỞNG ĐẾN ÂM THANH</span>
        <h1>Một nơi cho giọng nói<br>và mọi câu chuyện.</h1>
        <p>Tạo giọng, nhận diện và dịch phụ đề.<br class="mobile-break"> Chọn công cụ, thêm nội dung và bắt đầu.</p>
      </div>
      <div class="hero-side" aria-hidden="true"><div class="waveform">{''.join('<i></i>' for _ in range(27))}</div>
        <div class="hero-label"><span>GIỌNG NÓI</span><b>129 giọng · Đa ngôn ngữ</b></div>
      </div>
    </section>
    """)
    workspace_navigation = gr.Radio(
        [("Tạo giọng", "speech"), ("Phụ đề", "subtitles"), ("Dịch phụ đề", "translation"),
         ("SRT → Audio", "srt-audio"), ("Giọng đọc", "voices"), ("Công cụ", "tools")],
        value="speech", show_label=False, container=False, elem_id="studio-navigation",
    )
    with gr.Tabs(elem_classes="studio-tabs", elem_id="workspace-tabs") as workspace:
        with gr.Tab("Tạo giọng", id="speech"):
            section_header("01", "Biến văn bản thành giọng nói", "Chọn chất giọng, điều chỉnh nhịp đọc và nghe câu chuyện của bạn.")
            with gr.Row(elem_classes="workspace-row"):
                with gr.Column(scale=3, min_width=0, elem_classes="studio-card"):
                    text_input = gr.Textbox(label="Nội dung cần đọc", lines=7, max_lines=14,
                        placeholder="Viết hoặc dán câu chuyện của bạn tại đây…", elem_id="speech-text")
                    with gr.Row(elem_classes="sample-row"):
                        for label, sample in SAMPLES.items():
                            gr.Button(label, size="sm", min_width=0).click(
                                lambda text=sample: text, outputs=text_input, queue=False, api_name=False)
                    voice_dropdown, custom_voice_input = voice_controls("speech")
                    format_radio = gr.Radio([("WAV · chất lượng cao", "wav"), ("MP3 · gọn nhẹ", "mp3")],
                                            value="wav", label="Định dạng", min_width=0)
                    with gr.Accordion("Tốc độ & âm lượng", open=False):
                        with gr.Row():
                            speed_slider = gr.Slider(.5, 2, value=1, step=.05, label="Tốc độ đọc", min_width=0)
                            volume_slider = gr.Slider(.2, 2, value=1, step=.1, label="Âm lượng", min_width=0)
                        auto_split_check = gr.Checkbox(value=True, label="Tự chia đoạn văn bản dài")
                    with_subtitles = gr.Checkbox(value=False, label="Tạo thêm phụ đề SRT cho audio")
                    speech_language = language_dropdown("Ngôn ngữ phụ đề")
                    btn_generate = gr.Button("Tạo âm thanh", variant="primary", size="lg", elem_id="generate-speech")
                with gr.Column(scale=2, min_width=0, elem_classes=["studio-card", "result-card"]):
                    gr.Markdown("### Bản thu của bạn")
                    gr.HTML('<div class="result-intro"><span class="result-symbol">♫</span><p>Âm thanh sẽ xuất hiện ở đây.<br>Nghe thử và tải về khi hoàn tất.</p></div>')
                    audio_output = gr.Audio(label="Nghe thử & tải audio", type="filepath", interactive=False)
                    status_text = gr.Markdown("Sẵn sàng khi bạn sẵn sàng.", elem_classes="status-box")
                    stats_box = gr.Markdown()
                    speech_srt = gr.File(label="Phụ đề SRT", interactive=False)
                    with gr.Accordion("Xem nội dung phụ đề", open=False):
                        speech_srt_preview = gr.Textbox(label="Phụ đề của audio", lines=8, interactive=False)
            btn_generate.click(generate_tts_ui,
                [text_input, voice_dropdown, custom_voice_input, format_radio, speed_slider, volume_slider,
                 auto_split_check, with_subtitles, speech_language, connection],
                [audio_output, speech_srt, status_text, stats_box, speech_srt_preview],
                api_name="studio_speech", concurrency_limit=2)
        with gr.Tab("Phụ đề", id="subtitles"):
            section_header("02", "Để mọi lời nói thành câu chữ", "Tải lên audio hoặc video để nhận nội dung và phụ đề có mốc thời gian.")
            with gr.Row(elem_classes="workspace-row"):
                with gr.Column(scale=2, min_width=0, elem_classes="studio-card"):
                    media_input = gr.File(label="Audio hoặc video", file_types=MEDIA_TYPES, type="filepath", height=180)
                    stt_lang = language_dropdown("Ngôn ngữ trong file")
                    btn_stt = gr.Button("Tạo phụ đề", variant="primary", size="lg")
                    stt_status = gr.Markdown("Thêm file để bắt đầu.", elem_classes="status-box")
                with gr.Column(scale=3, min_width=0, elem_classes="studio-card"):
                    stt_preview = gr.Textbox(label="Phụ đề nhận diện", lines=12, interactive=False,
                                              placeholder="Nội dung phụ đề sẽ xuất hiện ở đây…")
                    stt_download = gr.File(label="Tải phụ đề gốc (.srt)", interactive=False)
            stt_table = gr.Dataframe(headers=SUBTITLE_HEADERS, interactive=False, wrap=True,
                                     label="Các câu & mốc thời gian", max_height=320)
            btn_stt.click(recognize, [media_input, stt_lang, connection],
                          [stt_preview, stt_download, stt_table, stt_status],
                          api_name="studio_transcribe", concurrency_limit=2)
        with gr.Tab("Dịch phụ đề", id="translation"):
            section_header("03", "Đưa câu chuyện đến ngôn ngữ mới", "Nhận diện lời nói từ audio/video, dịch và tải phụ đề theo mốc thời gian gốc.")
            with gr.Row(elem_classes="workspace-row"):
                with gr.Column(scale=2, min_width=0, elem_classes="studio-card"):
                    translate_input = gr.File(label="Audio hoặc video cần dịch", file_types=MEDIA_TYPES,
                                               type="filepath", height=180)
                    with gr.Row():
                        translate_source = language_dropdown("Từ ngôn ngữ")
                        translate_target = language_dropdown("Sang ngôn ngữ", "en-US")
                    gr.Markdown("Dịch từ audio hoặc video gốc. File SRT hiện dùng để tạo giọng ở mục **SRT → Audio**.")
                    btn_translate = gr.Button("Dịch & tạo phụ đề", variant="primary", size="lg")
                    translate_status = gr.Markdown("Bản gốc và bản dịch sẽ được xuất riêng.", elem_classes="status-box")
                with gr.Column(scale=3, min_width=0, elem_classes="studio-card"):
                    with gr.Tabs():
                        with gr.Tab("Bản dịch"):
                            translated_preview = gr.Textbox(label="SRT đã dịch", lines=11, interactive=False)
                            translated_download = gr.File(label="Tải bản dịch (.srt)", interactive=False)
                        with gr.Tab("Bản gốc"):
                            original_preview = gr.Textbox(label="SRT gốc", lines=11, interactive=False)
                            original_download = gr.File(label="Tải bản gốc (.srt)", interactive=False)
            translation_table = gr.Dataframe(headers=SUBTITLE_HEADERS, interactive=False, wrap=True,
                                             label="Đối chiếu bản gốc & bản dịch", max_height=360)
            btn_translate.click(translate, [translate_input, translate_source, translate_target, connection],
                [original_preview, translated_preview, original_download, translated_download, translation_table, translate_status],
                api_name="studio_translate", concurrency_limit=2)
        with gr.Tab("SRT → Audio", id="srt-audio"):
            section_header("04", "Cho phụ đề một giọng đọc", "Chuyển nội dung file SRT thành một bản đọc hoặc những file audio riêng cho từng câu.")
            with gr.Row(elem_classes="workspace-row"):
                with gr.Column(scale=2, min_width=0, elem_classes="studio-card"):
                    srt_input = gr.File(label="Phụ đề SRT", file_types=[".srt"], type="filepath", height=150)
                    srt_info = gr.Markdown("Chọn file SRT để xem trước nội dung.")
                    srt_voice, srt_custom = voice_controls("srt")
                    srt_mode = gr.Radio([("Từng câu · tải ZIP", "parts"), ("Một file liên tục", "combined")],
                                         value="parts", label="Cách xuất audio")
                    gr.Markdown("Xuất định dạng MP3. Mốc thời gian gốc được lưu kèm; audio chưa được căn khớp vào thời lượng từng câu.")
                    btn_srt = gr.Button("Tạo audio từ SRT", variant="primary", size="lg")
                with gr.Column(scale=3, min_width=0, elem_classes="studio-card"):
                    srt_audio = gr.Audio(label="Nghe thử", interactive=False, type="filepath")
                    srt_package = gr.File(label="Tải kết quả · ZIP hoặc MP3", interactive=False)
                    srt_status = gr.Markdown("Thêm phụ đề và chọn giọng đọc.", elem_classes="status-box")
                    srt_result_table = gr.Dataframe(headers=["Câu", "Bắt đầu", "Kết thúc", "File audio"],
                                                    interactive=False, label="Các file đã tạo", max_height=280, wrap=True)
            with gr.Accordion("Nội dung SRT đầu vào", open=False):
                srt_input_table = gr.Dataframe(headers=SUBTITLE_HEADERS, interactive=False, max_height=280, wrap=True)
            srt_input.change(inspect_srt_ui, [srt_input], [srt_info, srt_input_table], api_name=False)
            btn_srt.click(srt_to_audio_ui, [srt_input, srt_voice, srt_custom, srt_mode, connection],
                          [srt_audio, srt_package, srt_result_table, srt_status],
                          api_name="studio_srt_audio", concurrency_limit=2)
        with gr.Tab("Giọng đọc", id="voices"):
            section_header("05", "Tìm chất giọng phù hợp", "Tra cứu 129 giọng TTS theo tên, ngôn ngữ hoặc mã để dùng trong các công cụ tạo giọng.")
            with gr.Column(elem_classes="studio-card"):
                with gr.Row():
                    catalog_search = gr.Textbox(label="Tìm kiếm", placeholder="Tên giọng, mã hoặc ngôn ngữ…", scale=3, min_width=0)
                    catalog_language = gr.Dropdown(["Tất cả", "vi-VN", "en-US", "zh-CN", "ja-JP", "ko-KR", "th-TH", "id-ID"],
                                                   value="vi-VN", label="Ngôn ngữ", min_width=0)
                    catalog_button = gr.Button("Tìm giọng", scale=0, min_width=110)
                catalog_table = gr.Dataframe(headers=["Tên giọng", "Mã giọng", "Resource ID", "Ngôn ngữ"],
                    value=search_voice_catalog("", "vi-VN"), interactive=False, wrap=True,
                    label="Danh mục TTS", max_height=490)
                catalog_button.click(search_voice_catalog, [catalog_search, catalog_language], catalog_table, api_name=False)
                catalog_search.submit(search_voice_catalog, [catalog_search, catalog_language], catalog_table, api_name=False)
                catalog_language.change(search_voice_catalog, [catalog_search, catalog_language], catalog_table, api_name=False)
            with gr.Accordion("Metadata digital-human · tham khảo", open=False):
                gr.Markdown("73 bản ghi từ danh mục digital-human. Chưa xác nhận dùng được với dịch vụ TTS; chúng không nằm trong danh sách chọn giọng.")
                gr.Dataframe(headers=["Tên", "Mã", "Ngôn ngữ", "Khả năng tương thích"], value=digital_human_metadata(),
                             interactive=False, wrap=True, max_height=300)
        with gr.Tab("Công cụ", id="tools"):
            section_header("06", "Thêm quyền kiểm soát", "Cấu hình kết nối, kiểm tra tác vụ và tạo cấu trúc lệnh TTS cho draft.")
            with gr.Accordion("Kết nối CapCut API", open=True, elem_classes="studio-card"):
                gr.Markdown("Cấu hình áp dụng riêng cho phiên web này. Để trống URL/headers để dùng cấu hình trên máy chủ.")
                with gr.Row():
                    api_origin = gr.Textbox(label="API URL tùy chỉnh", placeholder="Mặc định của SDK", scale=3, min_width=0)
                    api_timeout = gr.Number(value=180, minimum=1, maximum=900, label="Timeout (giây)", min_width=0)
                api_headers = gr.Textbox(label="Headers bổ sung (JSON)", placeholder='{"Authorization": "Bearer …"}',
                                         type="password", max_lines=1)
                save_settings = gr.Button("Áp dụng cho phiên này")
                connection_status = gr.Markdown("Đang dùng cấu hình mặc định.")
                save_settings.click(apply_connection, [api_origin, api_timeout, api_headers, connection],
                                     [connection, connection_status], api_name=False)
            with gr.Accordion("Kiểm tra trạng thái tác vụ", open=False, elem_classes="studio-card"):
                with gr.Row():
                    task_mode = gr.Radio([("Tạo giọng", "tts"), ("Nhận diện phụ đề", "stt")], value="tts", label="Loại tác vụ")
                    task_id = gr.Textbox(label="Task ID", min_width=0)
                with gr.Row():
                    task_token = gr.Textbox(label="Token", type="password", min_width=0)
                    task_bind = gr.Textbox(label="Bind ID (nếu có)", min_width=0)
                query_button = gr.Button("Kiểm tra tác vụ")
                task_status = gr.Markdown()
                task_artifacts = gr.Dataframe(headers=["File", "URL audio", "VID"], interactive=False, max_height=200, wrap=True)
                task_json = gr.JSON(label="Kết quả API")
                query_button.click(query_task_ui, [task_mode, task_id, task_token, task_bind, connection],
                                   [task_json, task_artifacts, task_status], api_name=False)
            with gr.Accordion("Tạo JSON lệnh TTS cho draft", open=False, elem_classes="studio-card"):
                gr.Markdown("Tạo cấu trúc lệnh để tích hợp vào công cụ draft của bạn. Chức năng này chỉ xuất JSON, không chỉnh sửa hay chạy lệnh trong CapCut.")
                action_text = gr.Textbox(label="Văn bản", lines=3)
                with gr.Row():
                    action_segment = gr.Textbox(label="Segment ID", min_width=0)
                    action_speaker = gr.Textbox(label="Speaker ID (tùy chọn)", min_width=0)
                action_options = gr.Code(value="{}", language="json", label="Tùy chọn bổ sung")
                action_button = gr.Button("Tạo JSON")
                action_status = gr.Markdown()
                action_result = gr.JSON(label="Cấu trúc lệnh")
                action_file = gr.File(label="Tải JSON", interactive=False)
                action_button.click(build_action_ui, [action_text, action_segment, action_speaker, action_options],
                                     [action_result, action_file, action_status], api_name=False)
            with gr.Accordion("Danh mục ngôn ngữ của SDK", open=False):
                gr.JSON(value=json.loads((CAPI_DIR / "capcut_tts_api/language.json").read_text(encoding="utf-8")),
                        label="Thông tin ngôn ngữ & chế độ")
    workspace_navigation.change(lambda selected: gr.Tabs(selected=selected),
                                workspace_navigation, workspace, queue=False, api_name=False)
    gr.HTML('<footer class="studio-footer"><span>CapCut Audio Studio</span><span>Giọng nói · Phụ đề · Kết nối câu chuyện</span></footer>')

demo.queue(default_concurrency_limit=2, max_size=24)


def is_port_in_use(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(.5)
        return sock.connect_ex((host, port)) == 0


def find_available_port(start_port=7860, max_attempts=50):
    for port in range(start_port, start_port + max_attempts):
        if not is_port_in_use(port):
            return port
    raise RuntimeError("Không tìm thấy cổng trống để chạy giao diện web.")


def main():
    parser = argparse.ArgumentParser(description="CapCut Audio Studio")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--share", action="store_true")
    args = parser.parse_args()
    port = find_available_port(args.port)
    print(f"CapCut Audio Studio: http://localhost:{port}")
    # Gradio 6 scopes css rules; root and responsive shell rules must stay global.
    demo.launch(server_name=args.host, server_port=port, share=args.share, theme=THEME,
                head=f"<style>{custom_css}</style>")


if __name__ == "__main__":
    main()
