"""Offline workflow tests; no media or text is sent to CapCut."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace
import wave
import zipfile

import pytest

from capcut_tts_api import SubtitleResult
import web_workflows as studio


class FakeClient:
    def __init__(self, missing_translation=False, partial=False, fail_subtitles=False):
        self.requests = []
        self.missing_translation = missing_translation
        self.partial = partial
        self.fail_subtitles = fail_subtitles

    def resolve_voice(self, voice):
        return "BV421_vivn_streaming", "7252594014782755330"

    def transcribe_file(self, path, **kwargs):
        self.requests.append((str(path), kwargs))
        if self.fail_subtitles:
            raise RuntimeError("STT unavailable")
        return {}

    def extract_subtitles(self, response):
        return SubtitleResult.from_payload({"utterances": [
            {"text": "Xin chào", "translation_text": None if self.missing_translation else "Hello", "start_time": 125, "end_time": 1200},
            {"text": "Tạm biệt", "translation_text": None if self.missing_translation or self.partial else "Goodbye", "start_time": 1400, "end_time": 2600},
        ]})

    def generate_speech(self, texts, **kwargs):
        self.requests.append((texts, kwargs))
        return {}

    def extract_speech_urls(self, response):
        return ["https://example.test/one"]

    def download_file(self, url, output):
        if output.suffix == ".wav":
            with wave.open(str(output), "wb") as stream:
                stream.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                stream.writeframes(b"\0\0" * 2400)
        else:
            output.write_bytes(b"fake audio")
        return output

    def generate_speech_from_srt(self, path, **kwargs):
        self.requests.append((path, kwargs))
        subtitles = SubtitleResult.from_srt(path)
        cues = []
        for index, cue in enumerate(subtitles.utterances if kwargs["part"] else subtitles.utterances[:1], 1):
            output = Path(kwargs["output_dir"]) / f"{index:03d}.mp3" if kwargs["part"] else Path(kwargs["audio_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"fake audio")
            cues.append({"index": index, "start_time": cue.start_time, "end_time": cue.end_time, "audio_path": str(output), "text": cue.text})
        return {"cues": cues, "audio_path": cues[0]["audio_path"]}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    client = FakeClient()

    @contextmanager
    def factory(settings=None):
        yield client

    monkeypatch.setattr(studio, "api_client", factory)
    count = iter(range(100))

    def directory():
        path = tmp_path / f"job-{next(count)}"
        path.mkdir()
        return path

    monkeypatch.setattr(studio, "job_directory", directory)
    return client


@pytest.fixture
def srt(tmp_path):
    path = tmp_path / "input.srt"
    path.write_text("1\n00:00:00,125 --> 00:00:01,200\nXin chào\n\n"
                    "2\n00:00:01,400 --> 00:00:02,600\nTạm biệt\n", encoding="utf-8")
    return path


def test_translation_exports_both_languages_and_original_timing(fake):
    original, translated, source, target, rows, status = studio.transcribe_media_ui(
        "media.mp4", "vi-VN", "en-US", translate=True)
    assert "Xin chào" in original and "Hello" in translated
    assert "00:00:00,125 --> 00:00:01,200" in translated
    assert Path(source).read_text(encoding="utf-8") == original
    assert Path(target).read_text(encoding="utf-8") == translated
    assert rows[0][2:] == ["Xin chào", "Hello"]
    assert "2/2" in status
    assert fake.requests[0][1]["use_translation"] is True
    assert fake.requests[0][1]["translation_language"] == "en-US"


@pytest.mark.parametrize("missing,partial", [(True, False), (False, True)])
def test_incomplete_translation_is_reported_without_invented_text(fake, missing, partial):
    fake.missing_translation, fake.partial = missing, partial
    _, translated, source, target, _, status = studio.transcribe_media_ui("a.mp3", "vi-VN", "en-US", True)
    assert source
    if missing:
        assert target is None and translated == ""
        assert "chưa trả về bản dịch" in status
    else:
        assert target and "1/2" in status
        assert "Hello" in translated and "Goodbye" not in translated


def test_empty_media_and_same_language_do_not_submit_requests(fake):
    assert studio.transcribe_media_ui(None, "vi-VN")[-1].startswith("Vui lòng")
    assert "khác nhau" in studio.transcribe_media_ui("a.mp4", "vi-VN", "vi-VN", True)[-1]
    assert not fake.requests


def test_transcription_and_srt_preview(fake, srt):
    _, translation, source, target, rows, status = studio.transcribe_media_ui("a.mp3", "vi-VN")
    assert source and not target and translation == ""
    assert len(rows) == 2
    assert fake.requests[0][1]["use_translation"] is False
    info, preview = studio.inspect_srt_ui(srt)
    assert "2 câu" in info and preview[1][1] == "00:00:02,600"


@pytest.mark.parametrize("mode", ["parts", "combined"])
def test_srt_audio_exports_zip_or_combined_file(fake, srt, mode):
    audio, download, rows, status = studio.srt_to_audio_ui(srt, studio.VOICE_CHOICES[0], "", mode)
    assert Path(audio).exists()
    assert len(rows) == (2 if mode == "parts" else 1)
    if mode == "parts":
        with zipfile.ZipFile(download) as archive:
            assert set(archive.namelist()) == {"001.mp3", "002.mp3", "original.srt", "timings.json"}
            manifest = json.loads(archive.read("timings.json"))
            assert manifest[1]["start_time"] == 1400
            assert manifest[1]["audio_path"] == "002.mp3"
    else:
        assert audio == download and download.endswith(".mp3")


def test_srt_validation_before_api(fake, tmp_path):
    empty = tmp_path / "empty.srt"
    empty.write_text("not subtitles", encoding="utf-8")
    assert "không có câu" in studio.srt_to_audio_ui(empty, studio.VOICE_CHOICES[0], "", "parts")[-1]
    assert not fake.requests


@pytest.mark.parametrize("fail", [False, True])
def test_speech_and_subtitles_keeps_audio_on_stt_failure(fake, fail):
    fake.fail_subtitles = fail
    audio, subtitles, status, stats, preview = studio.generate_tts_ui(
        "Xin chào", studio.VOICE_CHOICES[0], "", "wav", 1, 1, True, True, "vi-VN")
    assert Path(audio).is_file()
    assert fake.requests[1][0] == audio
    assert "WAV" in stats
    if fail:
        assert subtitles is None and preview == "" and "chưa tạo được phụ đề" in status
    else:
        assert subtitles and "Xin chào" in preview


def test_long_text_split_preserves_content_and_limit():
    for text in ("word " * 300, "x" * 700, "Một câu. " * 100):
        chunks = studio.split_text_into_chunks(text)
        assert all(0 < len(chunk) <= 250 for chunk in chunks)
        assert "".join("".join(chunks).split()) == "".join(text.split())


def test_action_builder_exports_local_json_and_rejects_server_file_references(fake):
    action, path, status = studio.build_action_ui("Hello", "segment-1", "speaker", '{"emotion": "happy"}')
    assert json.loads(Path(path).read_text(encoding="utf-8")) == action
    assert action["params"]["tts"]["params"]["emotion"] == "happy"
    assert "chưa được thực thi" in status
    assert studio.build_action_ui("Hello", "segment-1", "", "@secrets.json")[1] is None


def test_request_scoped_clients_close_sessions(monkeypatch):
    clients = []

    class Client:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.closed = False
            self.session = SimpleNamespace(close=lambda: setattr(self, "closed", True))
            clients.append(self)

    monkeypatch.setattr(studio, "CapCutClient", Client)
    with studio.api_client({"base_url": "https://first.test", "headers": {"Cookie": "first"}}):
        pass
    with studio.api_client({"headers": {"Cookie": "second"}}):
        pass
    assert clients[0].closed and clients[1].closed
    assert clients[0].options["headers"]["Cookie"] == "first"
    assert clients[1].options["headers"]["Cookie"] == "second"


def test_invalid_connection_settings_preserve_previous_state():
    import app
    previous = {"timeout": 50}
    settings, status = app.apply_connection("", 100, "[]", previous)
    assert settings == previous and "Chưa áp dụng" in status


@pytest.mark.parametrize("field", ["provider_params_json", "extension_json"])
def test_nested_action_options_cannot_read_server_files(fake, tmp_path, field):
    secret = tmp_path / "private.json"
    secret.write_text('{"private_value": "must-not-be-exported"}', encoding="utf-8")
    action, download, status = studio.build_action_ui(
        "Hello", "segment", "", json.dumps({field: f"@{secret}"}))
    assert action == {} and download is None
    assert "must-not-be-exported" not in status
    assert "JSON object" in status


def test_inline_provider_options_are_preserved(fake):
    action, _, _ = studio.build_action_ui("Hello", "segment", "", json.dumps({
        "provider_params_json": {"voice": "custom"}, "extension_json": {"tag": "inline"},
    }))
    params = action["params"]["tts"]["params"]
    assert params["provider_params"] == {"voice": "custom"}
    assert params["extra"] == {"tag": "inline"}


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1, 0, 901])
def test_invalid_timeout_rejected_before_client_creation(fake, timeout):
    with pytest.raises(ValueError, match="1–900"):
        studio.save_connection_settings("", timeout, "{}")


@pytest.mark.parametrize("speed,volume", [(float("inf"), 1), (float("nan"), 1), (1, float("inf")), (1, float("nan"))])
def test_invalid_audio_numbers_do_not_submit_or_start_ffmpeg(fake, tmp_path, speed, volume):
    with pytest.raises(ValueError):
        studio.post_process_audio(tmp_path / "input.wav", tmp_path / "out.wav", speed, volume)
    result = studio.generate_tts_ui("Hello", studio.VOICE_CHOICES[0], "", "wav", speed, volume, True)
    assert result[0] is None and "Không thể tạo giọng" in result[2]
    assert not fake.requests


def test_expiration_only_removes_old_owned_job_directories(monkeypatch, tmp_path):
    root = tmp_path / "studio"
    root.mkdir()
    monkeypatch.setattr(studio, "ARTIFACT_ROOT", root)
    old = root / "job-old"
    recent = root / "job-recent"
    unrelated = root / "other-folder"
    for directory in (old, recent, unrelated):
        directory.mkdir()
        (directory / "result.txt").write_text("result", encoding="utf-8")
    expired = studio.time.time() - studio.ARTIFACT_TTL_SECONDS - 60
    os.utime(old, (expired, expired))
    os.utime(unrelated, (expired, expired))
    new = studio.job_directory()
    assert not old.exists()
    assert recent.exists() and unrelated.exists()
    assert new.parent == root and new.is_dir()


def test_expiration_skips_external_directory_links(monkeypatch, tmp_path):
    root = tmp_path / "studio"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    expired = studio.time.time() - studio.ARTIFACT_TTL_SECONDS - 60
    os.utime(outside, (expired, expired))
    link = root / "job-external"
    if os.name == "nt":
        import subprocess
        # Creating a directory junction needs no symlink privilege on Windows.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(studio, "ARTIFACT_ROOT", root)
    studio.cleanup_expired_jobs()
    assert (outside / "keep.txt").is_file()
    assert link.exists()
