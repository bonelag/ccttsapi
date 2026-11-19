"""Offline regressions for the upstream 1.5 SDK and local API extensions."""

import hashlib
import io
import json
import shutil
import subprocess
import wave
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

import pytest

from capcut_tts_api import CapCutAPIError, CapCutClient, SubtitleResult, UploadResult
from capcut_tts_api.cli import main as cli_main
from capcut_tts_api.signer import rsa_encrypt_pkcs1v15


class Response:
    def __init__(self, data=None, content=b"audio", status=200):
        self.data = data
        self.content = content
        self.status_code = status
        self.text = json.dumps(data)

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Session:
    def __init__(self, responses=(), content=b"audio"):
        self.responses = deque(responses)
        self.content = content
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return Response(self.responses.popleft())

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return Response(content=self.content)


def created(bind_in_task=True):
    task = {"id": "task", "token": "token"}
    data = {"tasks": [task]}
    (task if bind_in_task else data)["bind_id"] = "binding"
    return {"ret": 0, "data": data}


def completed(status="success", payload=None):
    if payload is None:
        payload = {"audio_subtitles": [{"speech_url": "https://cdn.example/one"}]}
    return {"ret": 0, "data": {"tasks": [{"status": status, "payload": json.dumps(payload)}]}}


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    for name in ("CAPCUT_BASE_URL", "CAPCUT_COOKIE", "CAPCUT_BEARER", "CAPCUT_HEADERS_JSON",
                 "LYRA_TTS_COOKIE", "LYRA_TTS_BEARER", "LYRA_TTS_HEADERS_JSON"):
        monkeypatch.delenv(name, raising=False)


def test_legacy_positional_request_and_payload_override():
    client = CapCutClient(session=Session(), base_url="https://api.example/", timeout=7)
    url, headers, body = client.build_tts_new_request(
        ["A & <B>", "Second "], "BV074_streaming", None, "1.25", "wav",
        payload_sign="explicit-sign",
    )
    payload = json.loads(json.loads(body)["tasks"][0]["payload"])
    prosody = ET.fromstring(payload["ssml"]).findall(".//{*}prosody")
    assert url.startswith("https://api.example/lv/v1/common_task/new?")
    assert payload["audio_format"] == "wav"
    assert payload["sign"] == "explicit-sign"
    assert [p.text for p in prosody] == ["A & <B> ", "Second "]
    assert all(p.attrib["rate"] == "1.25" for p in prosody)
    assert headers["x-ss-stub"] == hashlib.md5(body.encode()).hexdigest()


@pytest.mark.parametrize("texts", [[], "", "  ", ["valid", ""], [123]])
def test_upstream_rejects_empty_or_non_string_text(texts):
    with pytest.raises(ValueError, match="non-empty strings"):
        CapCutClient(session=Session()).build_tts_new_request(texts)


@pytest.mark.parametrize("status", ["success", "succeed", "succeeded", "COMPLETED", "done"])
@pytest.mark.parametrize("bind_in_task", [True, False])
def test_polling_preserves_legacy_call_and_upstream_statuses(status, bind_in_task):
    session = Session([created(bind_in_task), completed(status)])
    client = CapCutClient(session=session, timeout=9)
    result = client.generate_speech("Hello", "BV074_streaming", None, "0.9", "wav", True, 0, 10)
    assert client.extract_speech_urls(result) == ["https://cdn.example/one"]
    sent = json.loads(session.posts[0][1]["data"])
    assert json.loads(sent["tasks"][0]["payload"])["audio_format"] == "wav"
    query = json.loads(session.posts[1][1]["data"])
    assert query["tasks"][0]["bind_id"] == "binding"
    assert all(kwargs["timeout"] == 9 and kwargs["allow_redirects"] is False
               for _, kwargs in session.posts)


def test_credentials_custom_origin_and_application_error(monkeypatch):
    monkeypatch.setenv("CAPCUT_BASE_URL", "https://custom.example")
    monkeypatch.setenv("CAPCUT_COOKIE", "caller-cookie")
    monkeypatch.setenv("CAPCUT_BEARER", "caller-token")
    monkeypatch.setenv("CAPCUT_HEADERS_JSON", '{"X-Client": "environment"}')
    client = CapCutClient(session=Session([{"ret": 1014, "errmsg": "busy"}]),
                          headers={"X-Client": "explicit"})
    with pytest.raises(CapCutAPIError) as exc:
        client.create_tts_task("Hello", payload_sign="override")
    assert exc.value.response_data["ret"] == 1014
    url, request = client.session.posts[0]
    assert url.startswith("https://custom.example/")
    assert request["headers"]["Cookie"] == "caller-cookie"
    assert request["headers"]["Authorization"] == "Bearer caller-token"
    assert request["headers"]["X-Client"] == "explicit"
    payload = json.loads(json.loads(request["data"])["tasks"][0]["payload"])
    assert payload["sign"] == "override"


def test_translated_stt_and_srt(monkeypatch, tmp_path):
    payload = {"utterances": [{"text": "Xin chào", "translation_text": "Hello",
                               "start_time": 125, "end_time": 1500}]}
    session = Session([created(), completed("done", payload)])
    client = CapCutClient(session=session)
    monkeypatch.setattr(client, "upload_audio", lambda _: UploadResult("vid", "md5", "md5", 2500))
    result = client.transcribe_file("unused.mp3", language="vi-VN", translation_language="en-US",
                                    use_translation=True, poll_interval=0)
    cap = json.loads(json.loads(session.posts[0][1]["data"])["tasks"][0]["payload"])["cap_json"]
    assert (cap["language"], cap["translation_language"], cap["use_translation"]) == ("vi-VN", "en-US", True)
    subtitles = client.extract_subtitles(result)
    assert subtitles.full_translation_text == "Hello"
    output = client.write_srt(result, tmp_path / "translated.srt", use_translation=True)
    assert "00:00:00,125 --> 00:00:01,500\nHello" in output.read_text(encoding="utf-8")
    assert SubtitleResult.from_srt(output).utterances[0].text == "Hello"


def test_legacy_urls_and_new_artifacts_across_tasks(tmp_path):
    result = completed(payload={"audio_subtitles": [{"audio_url": "https://cdn.example/a"}]})
    result["data"]["tasks"].append({"payload": {"audio_subtitles": [{"url": "https://cdn.example/b"}]}})
    session = Session(content=b"original artifact")
    client = CapCutClient(session=session)
    assert client.extract_speech_urls(result) == ["https://cdn.example/a", "https://cdn.example/b"]
    output = client.download_tts_audio(result, tmp_path / "artifact.mp3", artifact_index=1)
    assert output.read_bytes() == b"original artifact"
    assert session.gets[0][0] == "https://cdn.example/b"


@pytest.mark.parametrize("part", [True, False])
def test_upstream_srt_tts_workflows(tmp_path, part):
    path = tmp_path / "input.srt"
    path.write_text("1\n00:00:00,000 --> 00:00:01,000\nOne\n\n"
                    "2\n00:00:01,500 --> 00:00:02,500\nTwo\n", encoding="utf-8")
    urls = ["https://cdn.example/one", "https://cdn.example/two"] if part else ["https://cdn.example/all"]
    session = Session([created(), completed(payload={"audio_subtitles": [{"speech_url": u} for u in urls]})])
    result = CapCutClient(session=session).generate_speech_from_srt(
        path, part=part, output_dir=tmp_path / "parts", audio_path=tmp_path / "all.mp3")
    assert result["part"] is part
    assert len(result["cues"]) == len(urls)
    assert all(Path(cue["audio_path"]).read_bytes() == b"audio" for cue in result["cues"])
    assert result["cues"][-1]["end_time"] == 2500


def test_local_wav_download_conversion(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is needed to exercise the local WAV fallback")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 2400)
    mp3 = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "mp3", "pipe:1"],
                         input=buffer.getvalue(), capture_output=True, check=True).stdout
    client = CapCutClient(session=Session(content=mp3), timeout=11)
    path = client.download_file("https://cdn.example/cached", tmp_path / "output.wav")
    with wave.open(str(path), "rb") as output:
        assert output.getframerate() == 24000
        assert output.getnchannels() == 1
        assert output.getnframes() > 0
    assert not (tmp_path / "output.tmp.mp3").exists()
    assert client.session.gets[0][1]["timeout"] == 11


def test_cli_retains_rate_and_new_signature_override(capsys):
    cli_main(["tts-new", "--text", "Hello", "--rate", "1.2", "--audio-format", "wav",
              "--payload-sign", "override", "--dry-run"])
    dump = json.loads(capsys.readouterr().out)
    payload = json.loads(dump["body"]["tasks"][0]["payload"])
    assert payload["sign"] == "override"
    assert payload["audio_format"] == "wav"
    assert 'rate="1.2"' in payload["ssml"]


def test_default_rsa_helper_remains_callable():
    import base64
    assert len(base64.b64decode(rsa_encrypt_pkcs1v15("compatibility"))) == 256


def test_catalog_keeps_tts_separate_from_digital_human_metadata():
    client = CapCutClient(session=Session())
    assert len(client.list_voices()) == 129
    assert len(client.list_voices(lang="vi-VN")) == 24
    assert len(client.list_local_digital_human_voice_metadata()) == 73
    assert all(v.catalog_kind != "digital_human_voice" for v in client.list_voices())


def test_local_rest_and_gradio_call_updated_sdk(monkeypatch):
    import api
    import app as gradio_app
    from fastapi.testclient import TestClient
    from capcut_tts_api import __version__

    session = Session([created(), completed()])
    monkeypatch.setattr(api, "client", CapCutClient(session=session))
    with TestClient(api.app) as web:
        assert web.get("/health").json()["version"] == __version__
        assert web.get("/v1/models").status_code == 200
        response = web.post("/v1/audio/speech", json={"input": "Hello", "response_format": "mp3"})
        assert response.status_code == 200
        assert response.content == b"audio"
        assert response.headers["content-type"] == "audio/mpeg"
    assert len(gradio_app.VOICE_CHOICES) == 10
    assert gradio_app.split_text_into_chunks("Short sentence.") == ["Short sentence."]
