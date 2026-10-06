"""video_router — deterministic bridge to the video-analyzer service (de-agents:8080).

Restored 2026-10-05 (issue #23, sauce-app repo): gateway/run.py has imported this
module since commit 90e34cafd, but the module itself was never actually committed —
every video input (file or link) raised ImportError and killed the whole message.

API contract (ai-boost-video-analyzer, app/api.py):
    POST /analyze   — multipart: file=<video> OR url=<str>, optional scenario/mode
                       -> 202 {"job_id": str, "status": "queued"}
    GET  /result/{job_id}
                       -> {"job_id", "status": queued|running|done|error, "cached",
                           ["progress"], ["result": {...}] if done, ["error"] if error}
    GET  /result/{job_id}/file/{name}
                       -> artifact bytes (result.json / report.md / summary.md /
                          transcript.json / report.pdf — report.pdf is best-effort,
                          may not exist if PDF rendering failed)

Called from gateway/run.py via run_in_executor (blocking call, not async) wrapped in
asyncio.wait_for(..., timeout=1300) — analyze_video_file/analyze_video_url poll to
completion themselves and return the text to append to message_text. If a PDF
artifact was produced, its *local* path is embedded as
"[VIDEO_REPORT_READY:<path>|...]" — run.py's send loop regexes this out, attaches
the file as MEDIA: once the agent's turn finishes, and strips the marker from what
the model actually sees.
"""
from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

ANALYZER_URL = os.getenv("VIDEO_ANALYZER_URL", "http://127.0.0.1:8080")
POLL_INTERVAL_S = 8
# Чуть меньше внешнего asyncio.wait_for(1300) в run.py — так при реальном
# зависании возвращаем внятную ошибку сами, а не обрываемся снаружи без слова.
MAX_WAIT_S = 1200
REPORT_CACHE_DIR = "/opt/data/cache/videos/reports"


def _submit(*, file_path: str | None = None, url: str | None = None) -> str:
    if file_path:
        with open(file_path, "rb") as f:
            resp = requests.post(f"{ANALYZER_URL}/analyze", files={"file": f}, timeout=60)
    elif url:
        resp = requests.post(f"{ANALYZER_URL}/analyze", data={"url": url}, timeout=30)
    else:
        raise ValueError("file_path or url required")
    resp.raise_for_status()
    return resp.json()["job_id"]


def _poll(job_id: str) -> dict:
    """Опрашивает /result/{job_id} до done/error/таймаута. Блокирующая (вызывается
    из run.py через run_in_executor, т.е. уже в отдельном потоке — тут можно
    безопасно спать между опросами)."""
    deadline = time.monotonic() + MAX_WAIT_S
    url = f"{ANALYZER_URL}/result/{job_id}"
    while True:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        status = body.get("status")
        if status == "done":
            return body
        if status == "error":
            raise RuntimeError(f"video-analyzer job {job_id} failed: {body.get('error')}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"video-analyzer job {job_id} not done after {MAX_WAIT_S}s")
        time.sleep(POLL_INTERVAL_S)


def _download_artifact(job_id: str, name: str, dest_dir: str) -> str | None:
    """Скачивает артефакт (напр. report.pdf) локально; None если его нет/не удалось
    (report.pdf — best-effort на стороне analyzer, может отсутствовать)."""
    try:
        resp = requests.get(f"{ANALYZER_URL}/result/{job_id}/file/{name}", timeout=30)
        if resp.status_code != 200:
            return None
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, f"{job_id}-{name}")
        with open(dest, "wb") as f:
            f.write(resp.content)
        return dest
    except Exception as exc:
        logger.warning("video_router: artifact download failed (%s/%s): %s", job_id, name, exc)
        return None


def _format_result(job: dict) -> str:
    """Короткая сводка в контекст агента — полный report.md слишком длинный для
    инлайна; агент комментирует ПРИЛОЖЕННЫЙ файл (report.pdf), не придумывает своё."""
    result = job.get("result") or {}
    a = result.get("analysis", {})
    scores = a.get("scores", {})
    lines = ["[ОТЧЁТ ВИДЕО-АНАЛИЗА]"]
    if scores:
        lines.append("Баллы: " + ", ".join(f"{k}={v}" for k, v in scores.items()))
    if a.get("overall"):
        lines.append("Вывод: " + str(a["overall"])[:400])
    if a.get("actionable"):
        lines.append("Что сделать: " + str(a["actionable"])[:400])
    return "\n".join(lines)


def _run_and_format(*, file_path: str | None, url: str | None) -> str:
    job_id = _submit(file_path=file_path, url=url)
    job = _poll(job_id)
    text = "\n\n" + _format_result(job)
    pdf_path = _download_artifact(job_id, "report.pdf", REPORT_CACHE_DIR)
    if pdf_path:
        text += f"\n\n[VIDEO_REPORT_READY:{pdf_path}|job={job_id}]"
    return text


def analyze_video_file(path: str, origin: str) -> str:
    """path — локальный файл видео (уже на диске), origin — chat_id (пока не
    используется в запросе к analyzer, зарезервирован под будущий per-chat роутинг/
    скоуп; сигнатура фиксирована вызывающим кодом в run.py)."""
    return _run_and_format(file_path=path, url=None)


def analyze_video_url(url: str, chat_id: str) -> str:
    """url — ссылка (TikTok/Instagram/YouTube Shorts/VK Клипы), chat_id — см. origin
    выше, зарезервирован."""
    return _run_and_format(file_path=None, url=url)
