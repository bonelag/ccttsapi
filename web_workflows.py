"""Request-scoped workflows used by the local Gradio studio."""

from contextlib import contextmanager
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import wave
import zipfile

from capcut_tts_api import CapCutClient, CapCutError, SubtitleResult, build_tts_action
from dataset.voice import SUPPORTED_VOICES, resolve_voice_info

VOICE_CHOICES = [voice["name"] for voice in SUPPORTED_VOICES]
ARTIFACT_ROOT = Path(tempfile.gettempdir()) / "capcut-audio-studio"
ARTIFACT_TTL_SECONDS = 24 * 60 * 60
LANGUAGES = [
    ("Tiếng Việt", "vi-VN"), ("Tiếng Anh", "en-US"), ("Tiếng Trung", "zh-CN"),
    ("Tiếng Nhật", "ja-JP"), ("Tiếng Hàn", "ko-KR"), ("Tiếng Pháp", "fr-FR"),
    ("Tiếng Đức", "de-DE"), ("Tiếng Tây Ban Nha", "es-ES"),
    ("Tiếng Bồ Đào Nha", "pt-BR"), ("Tiếng Indonesia", "id-ID"), ("Tiếng Thái", "th-TH"),
]


@contextmanager
def api_client(settings=None):
    """Keep sessions and caller-provided connection settings isolated per job."""
    settings = settings or {}
    client = CapCutClient(base_url=settings.get("base_url") or None,
                          headers=settings.get("headers") or None,
                          timeout=float(settings.get("timeout", 180)))
    try:
        yield client
    finally:
        if client.session is not None:
            client.session.close()


def save_connection_settings(base_url, timeout, headers_json):
    try:
        timeout = float(timeout)
        if not math.isfinite(timeout) or not 1 <= timeout <= 900:
            raise ValueError("Timeout cần nằm trong khoảng 1–900 giây.")
        headers = json.loads(headers_json) if (headers_json or "").strip() else {}
        if not isinstance(headers, dict) or any(not isinstance(v, str) for v in headers.values()):
            raise ValueError("Headers phải là một JSON object với các giá trị dạng chuỗi.")
        settings = {"base_url": (base_url or "").strip(), "timeout": timeout, "headers": headers}
        with api_client(settings):
            pass
        return settings, "Đã áp dụng cấu hình cho phiên làm việc này."
    except Exception as exc:
        # Preserve the previous session state on validation failure (handled by the UI wrapper).
        raise ValueError(str(exc)) from exc


def language_code(value):
    return (value or "vi-VN").split(" ", 1)[0].strip()


def cleanup_expired_jobs():
    """Expire only generated job directories inside the studio's owned root."""
    root = ARTIFACT_ROOT.resolve()
    if not root.is_dir():
        return
    cutoff = time.time() - ARTIFACT_TTL_SECONDS
    for directory in root.glob("job-*"):
        try:
            # Resolving the parent also excludes junctions pointing outside this root.
            if directory.is_symlink() or not directory.is_dir() or directory.resolve().parent != root:
                continue
            if directory.stat().st_mtime < cutoff:
                shutil.rmtree(directory)
        except OSError:
            # A concurrent cleanup or a locked download must not block the next job.
            continue


def job_directory():
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    cleanup_expired_jobs()
    return Path(tempfile.mkdtemp(prefix="job-", dir=ARTIFACT_ROOT))


def subtitle_rows(subtitles):
    return [[SubtitleResult._srt_timestamp(u.start_time), SubtitleResult._srt_timestamp(u.end_time),
             u.text, u.translation_text or ""] for u in subtitles.utterances]


def split_text_into_chunks(text, max_chars=250):
    """Split at punctuation/word boundaries, including long unpunctuated text."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    cleaned = (text or "").strip()
    chunks = []
    while len(cleaned) > max_chars:
        window = cleaned[:max_chars + 1]
        breaks = list(re.finditer(r"[.!?,;\n]\s+", window))
        cut = breaks[-1].end() if breaks else window.rfind(" ")
        if cut <= 0 or cut > max_chars:
            cut = max_chars
        chunks.append(cleaned[:cut].strip())
        cleaned = cleaned[cut:].strip()
    if cleaned:
        chunks.append(cleaned)
    return chunks


def post_process_audio(input_path, output_path, speed=1.0, volume=1.0, output_format="wav"):
    if not math.isfinite(speed) or not math.isfinite(volume) or speed <= 0 or volume < 0:
        raise ValueError("Tốc độ phải lớn hơn 0 và âm lượng không được âm.")
    needs_processing = abs(speed - 1) >= .01 or abs(volume - 1) >= .01
    needs_processing |= input_path.suffix.lower() != f".{output_format.lower()}"
    if not needs_processing:
        shutil.copy2(input_path, output_path)
        return output_path
    if not shutil.which("ffmpeg"):
        raise CapCutError("Cần cài ffmpeg để đổi tốc độ, âm lượng hoặc định dạng âm thanh.")
    filters = []
    current = speed
    while current > 2:
        filters.append("atempo=2.0")
        current /= 2
    while current < .5:
        filters.append("atempo=0.5")
        current /= .5
    if abs(speed - 1) >= .01:
        filters.append(f"atempo={current:.4f}")
    if abs(volume - 1) >= .01:
        filters.append(f"volume={volume:.3f}")
    cmd = ["ffmpeg", "-y", "-i", str(input_path)]
    if filters:
        cmd += ["-filter:a", ",".join(filters)]
    subprocess.run(cmd + [str(output_path)], capture_output=True, check=True, timeout=180)
    return output_path


def generate_tts_ui(text, voice_select, custom_voice, audio_format, speed, volume, auto_split,
                    with_subtitles=False, language="vi-VN", settings=None):
    if not (text or "").strip():
        return None, None, "Vui lòng nhập văn bản cần đọc.", "", ""
    started = time.monotonic()
    try:
        if not math.isfinite(speed) or not math.isfinite(volume) or not .5 <= speed <= 2 or not .2 <= volume <= 2:
            raise ValueError("Tốc độ cần trong khoảng 0,5–2 và âm lượng trong khoảng 0,2–2.")
        chunks = split_text_into_chunks(text) if auto_split else [text.strip()]
        fmt = "wav" if "wav" in audio_format.lower() else "mp3"
        if (len(chunks) > 1 or abs(speed - 1) >= .01 or abs(volume - 1) >= .01) and not shutil.which("ffmpeg"):
            raise CapCutError("Cần cài ffmpeg để ghép đoạn hoặc điều chỉnh âm thanh.")
        with api_client(settings) as client:
            voice, resource, name = resolve_voice_info((custom_voice or "").strip() or voice_select, client)
            result = client.generate_speech(chunks, voice=voice, resource_id=resource, audio_format=fmt,
                                            timeout=float((settings or {}).get("timeout", 180)))
            urls = client.extract_speech_urls(result)
            if len(urls) != len(chunks):
                raise CapCutError(f"Nhận {len(urls)} đoạn audio cho {len(chunks)} đoạn văn bản. Vui lòng thử lại.")
            directory = job_directory()
            files = []
            for index, url in enumerate(urls):
                path = directory / f"part-{index:03d}.{fmt}"
                client.download_file(url, path)
                files.append(path)
            merged = directory / f"merged.{fmt}"
            if len(files) == 1:
                shutil.copy2(files[0], merged)
            else:
                # Relative, generated filenames keep concat syntax independent of user paths.
                manifest = directory / "concat.txt"
                manifest.write_text("\n".join(f"file '{p.name}'" for p in files), encoding="utf-8")
                subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(manifest),
                                "-c", "copy", str(merged)], capture_output=True, check=True, timeout=180)
            output = post_process_audio(merged, directory / f"speech.{fmt}", speed, volume, fmt)
            srt_file, srt_preview = None, ""
            subtitle_warning = ""
            if with_subtitles:
                # Transcribe the final processed file so timings match speed-adjusted audio.
                try:
                    stt = client.transcribe_file(output, language=language_code(language),
                                                 timeout=float((settings or {}).get("timeout", 180)))
                    subtitles = client.extract_subtitles(stt)
                    if not subtitles.utterances:
                        raise CapCutError("CapCut không trả về nội dung phụ đề.")
                    srt_file = str(subtitles.write_srt(directory / "speech.srt"))
                    srt_preview = subtitles.to_srt()
                except Exception as exc:
                    subtitle_warning = f"Đã tạo audio; chưa tạo được phụ đề: {exc}"
            duration = ""
            if fmt == "wav":
                with wave.open(str(output), "rb") as audio:
                    duration = f" · {audio.getnframes() / audio.getframerate():.1f} giây"
            stats = (f"**{name}** · {fmt.upper()}{duration} · {output.stat().st_size / 1024:.1f} KB\n\n"
                     f"{len(chunks)} đoạn · Tốc độ {speed:g}× · Âm lượng {volume:g}× · {time.monotonic() - started:.1f}s xử lý")
            status = subtitle_warning or ("Đã tạo audio và phụ đề." if with_subtitles else "Đã tạo âm thanh.")
            return str(output), srt_file, status, stats, srt_preview
    except Exception as exc:
        return None, None, f"Không thể tạo giọng: {exc}", "", ""


def transcribe_media_ui(media, source_language, target_language="en-US", translate=False, settings=None):
    empty = ("", "", None, None, [], "Vui lòng tải lên file âm thanh hoặc video.")
    if not media:
        return empty
    if translate and language_code(source_language) == language_code(target_language):
        return "", "", None, None, [], "Ngôn ngữ gốc và ngôn ngữ dịch cần khác nhau."
    try:
        with api_client(settings) as client:
            response = client.transcribe_file(
                media, language=language_code(source_language),
                translation_language=language_code(target_language), use_translation=translate,
                timeout=float((settings or {}).get("timeout", 180)),
            )
            subtitles = client.extract_subtitles(response)
        if not subtitles.utterances:
            raise CapCutError("Không tìm thấy lời nói trong kết quả CapCut trả về.")
        directory = job_directory()
        source = str(subtitles.write_srt(directory / "original.srt"))
        translated, translated_preview = None, ""
        status = f"Đã nhận diện {len(subtitles.utterances)} câu phụ đề."
        if translate:
            count = sum(bool((u.translation_text or "").strip()) for u in subtitles.utterances)
            if count:
                translated = str(subtitles.write_srt(directory / "translated.srt", use_translation=True))
                translated_preview = subtitles.to_srt(use_translation=True)
                status = f"Đã dịch {count}/{len(subtitles.utterances)} câu phụ đề."
                if count < len(subtitles.utterances):
                    status += " Bản SRT dịch chỉ chứa những câu CapCut đã trả bản dịch."
            else:
                status = "Đã nhận diện lời nói, nhưng CapCut chưa trả về bản dịch. Bạn có thể tải SRT gốc và thử lại."
        return subtitles.to_srt(), translated_preview, source, translated, subtitle_rows(subtitles), status
    except Exception as exc:
        return "", "", None, None, [], f"Không thể xử lý phụ đề: {exc}"


def transcribe_stt_ui(audio_file, language):
    """Compatibility wrapper for callers of the original two-output helper."""
    preview, _, source, _, _, status = transcribe_media_ui(audio_file, language)
    return f"{status}\n\n{preview}", source


def inspect_srt_ui(path):
    if not path:
        return "Chọn file SRT để xem nội dung và mốc thời gian.", []
    try:
        subtitles = SubtitleResult.from_srt(path)
        if not subtitles.utterances:
            raise ValueError("File không có câu phụ đề hợp lệ.")
        end = max(u.end_time for u in subtitles.utterances)
        return f"{len(subtitles.utterances)} câu · Mốc cuối {SubtitleResult._srt_timestamp(end)}", subtitle_rows(subtitles)
    except Exception as exc:
        return f"Không đọc được SRT: {exc}", []


def srt_to_audio_ui(path, voice_select, custom_voice, mode, settings=None):
    if not path:
        return None, None, [], "Vui lòng chọn file SRT."
    try:
        subtitles = SubtitleResult.from_srt(path)
        if not subtitles.utterances:
            raise ValueError("File không có câu phụ đề hợp lệ.")
        directory = job_directory()
        part = mode == "parts"
        with api_client(settings) as client:
            voice, resource, _ = resolve_voice_info((custom_voice or "").strip() or voice_select, client)
            result = client.generate_speech_from_srt(
                path, voice=voice, resource_id=resource, part=part, output_dir=directory / "parts",
                audio_path=directory / "speech.mp3", timeout=float((settings or {}).get("timeout", 180)),
            )
        cues = result["cues"]
        rows = [[cue["index"], SubtitleResult._srt_timestamp(cue["start_time"]),
                 SubtitleResult._srt_timestamp(cue["end_time"]), Path(cue["audio_path"]).name] for cue in cues]
        if part:
            archive = directory / "audio-parts.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for cue in cues:
                    bundle.write(cue["audio_path"], Path(cue["audio_path"]).name)
                bundle.write(path, "original.srt")
                manifest = [{**cue, "audio_path": Path(cue["audio_path"]).name} for cue in cues]
                bundle.writestr("timings.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            return cues[0]["audio_path"], str(archive), rows, f"Đã tạo {len(cues)} file audio. Nghe thử câu đầu và tải toàn bộ dưới dạng ZIP."
        return result["audio_path"], result["audio_path"], rows, "Đã tạo một file audio liên tục từ nội dung SRT."
    except Exception as exc:
        return None, None, [], f"Không thể tạo audio từ SRT: {exc}"


def search_voice_catalog(query, lang_filter):
    with api_client() as client:
        voices = client.list_voices(lang=None if lang_filter == "Tất cả" else lang_filter)
    query = (query or "").strip().lower()
    return [[v.display_name, v.voice_type, v.resource_id, v.lang] for v in voices
            if not query or query in " ".join((v.display_name, v.voice_type, v.resource_id, v.lang)).lower()]


def digital_human_metadata():
    with api_client() as client:
        return [[v.get("display_name", ""), v.get("voice_type", ""), v.get("lang", ""),
                 v.get("service_compatibility", "")] for v in client.list_local_digital_human_voice_metadata()]


def build_action_ui(text, segment_id, speaker, options_json):
    try:
        options = json.loads(options_json or "{}")
        if not isinstance(options, dict):
            raise ValueError("Tùy chọn phải là một JSON object.")
        # The SDK accepts @file for these fields. The web interface accepts inline objects only.
        for key in ("provider_params_json", "extension_json"):
            if key in options and options[key] is not None and not isinstance(options[key], dict):
                raise ValueError(f"{key} cần là JSON object, không phải đường dẫn hoặc chuỗi.")
        action = build_tts_action(text=text, seg_id=segment_id, speaker=speaker or None, **options)
        directory = job_directory()
        output = directory / "tts-action.json"
        output.write_text(json.dumps(action, ensure_ascii=False, indent=2), encoding="utf-8")
        return action, str(output), "Đã tạo JSON. Lệnh chưa được thực thi trong CapCut."
    except Exception as exc:
        return {}, None, f"Không thể tạo lệnh: {exc}"


def query_task_ui(mode, task_id, token, bind_id, settings=None):
    if not (task_id or "").strip() or not (token or "").strip():
        return {}, [], "Nhập Task ID và token của tác vụ cần kiểm tra."
    try:
        with api_client(settings) as client:
            query = client.query_tts_task if mode == "tts" else client.query_stt_task
            response = query(task_id.strip(), token.strip(), bind_id=(bind_id or "").strip())
            artifacts = client.extract_tts_artifacts(response) if mode == "tts" else []
        tasks = (response.get("data") or {}).get("tasks") or []
        status = str(tasks[0].get("status", "Chưa có trạng thái")) if tasks else "Chưa có kết quả"
        rows = [[index, item.get("speech_url") or item.get("audio_url") or item.get("url") or "",
                 item.get("vid") or ""] for index, item in enumerate(artifacts, 1)]
        return response, rows, f"Trạng thái: {status}"
    except Exception as exc:
        return {}, [], f"Không thể truy vấn tác vụ: {exc}"
