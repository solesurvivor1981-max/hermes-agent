"""Regression tests for gateway/video_router.py (restored 2026-10-05, issue #23
in sauce-app: the module was imported by gateway/run.py since commit 90e34cafd
but never actually committed, so every video input raised ImportError and killed
the whole message).

Mocks requests.post/.get directly against the real API contract from
ai-boost-video-analyzer (app/api.py): POST /analyze -> {job_id, status}; GET
/result/{job_id} -> {status, ["result"], ["error"]}; GET /result/{job_id}/file/{name}
-> artifact bytes.
"""
import os
import re
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from gateway import video_router as vr


def _resp(status_code=200, json_body=None, content=b""):
    r = MagicMock()
    r.status_code = status_code
    r.content = content
    r.json.return_value = json_body or {}
    r.raise_for_status = MagicMock()
    if status_code >= 400:
        r.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return r


@pytest.fixture(autouse=True)
def _fast_poll_and_tmp_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(vr, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(vr, "REPORT_CACHE_DIR", str(tmp_path))


def test_analyze_video_file_happy_path_with_pdf():
    post_resp = _resp(202, {"job_id": "abc123", "status": "queued"})
    poll_running = _resp(200, {"job_id": "abc123", "status": "running"})
    poll_done = _resp(200, {
        "job_id": "abc123", "status": "done", "cached": False,
        "result": {"analysis": {"scores": {"hook": 7, "retention": 6},
                                 "overall": "Сильный хук, слабый CTA.",
                                 "actionable": "Добавить призыв в конце."}},
    })
    pdf_resp = _resp(200, content=b"%PDF-fake-bytes")

    with patch.object(vr.requests, "post", return_value=post_resp) as mpost, \
         patch.object(vr.requests, "get", side_effect=[poll_running, poll_done, pdf_resp]):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            f.write(b"fake video bytes")
            f.flush()
            out = vr.analyze_video_file(f.name, "312022420")

    assert mpost.call_count == 1
    assert "[ОТЧЁТ ВИДЕО-АНАЛИЗА]" in out
    assert "hook=7" in out
    assert "Сильный хук" in out
    m = re.search(r"\[VIDEO_REPORT_READY:([^|\]]+)\|", out)
    assert m, "report marker missing from output"
    assert os.path.exists(m.group(1)), "downloaded report.pdf not on disk"


def test_analyze_video_url_error_status_raises():
    post_resp = _resp(202, {"job_id": "abc123", "status": "queued"})
    error_resp = _resp(200, {"job_id": "abc123", "status": "error", "error": "corrupt video"})

    with patch.object(vr.requests, "post", return_value=post_resp), \
         patch.object(vr.requests, "get", return_value=error_resp):
        with pytest.raises(RuntimeError, match="corrupt video"):
            vr.analyze_video_url("https://vk.com/video123", "312022420")


def test_missing_pdf_artifact_degrades_to_text_only():
    post_resp = _resp(202, {"job_id": "abc123", "status": "queued"})
    done_resp = _resp(200, {"job_id": "abc123", "status": "done",
                             "result": {"analysis": {"overall": "ok"}}})
    missing_pdf = _resp(404)

    with patch.object(vr.requests, "post", return_value=post_resp), \
         patch.object(vr.requests, "get", side_effect=[done_resp, missing_pdf]):
        out = vr.analyze_video_url("https://tiktok.com/x", "1")

    assert "[ОТЧЁТ ВИДЕО-АНАЛИЗА]" in out
    assert "VIDEO_REPORT_READY" not in out


def test_timeout_raises_when_never_done():
    post_resp = _resp(202, {"job_id": "abc123", "status": "queued"})
    running_resp = _resp(200, {"job_id": "abc123", "status": "running"})

    with patch.object(vr.requests, "post", return_value=post_resp), \
         patch.object(vr.requests, "get", return_value=running_resp), \
         patch.object(vr, "MAX_WAIT_S", 0):
        with pytest.raises(TimeoutError):
            vr.analyze_video_url("https://tiktok.com/x", "1")
