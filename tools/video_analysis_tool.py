"""video_analysis — детерминированный анализ видео через сервис video-analyzer.

Агент вызывает tool с job-параметрами; ВЕСЬ анализ выполняет внешний сервис
(video-analyzer на localhost:8080 de-agents). Агент не имеет доступа к
whisper/ffmpeg/кадрам — только submit + готовый отчёт.

Дополнительно: при взятии видео в работу отправляет уведомление владельцу
(«взял на анализ», reply-quote на исходное сообщение) в TELEGRAM_OWNER_CHAT.
"""
from __future__ import annotations

import os
import time
import urllib.request
import urllib.error
import json
import logging

log = logging.getLogger(__name__)

ANALYZER_URL = os.getenv("VIDEO_ANALYZER_URL", "http://127.0.0.1:8080")
OWNER_CHAT = os.getenv("TELEGRAM_OWNER_CHAT", "312022420")
POLL_TIMEOUT_SEC = int(os.getenv("VIDEO_ANALYZER_TIMEOUT", "900"))  # 15 min max


def _http_post_form(url: str, fields: dict, timeout: int = 30) -> dict:
    """multipart/form-data POST (API принимает Form, не JSON)."""
    import uuid
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += f"{v}\r\n".encode()
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _http_get_json(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def notify_owner_take(text: str = "🎬 Взял видео на анализ", reply_to: int | None = None,
                      chat_context: str = "") -> None:
    """Уведомление владельцу в личку: 'взял на анализ' с цитированием."""
    try:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return
        body: dict = {"chat_id": OWNER_CHAT, "text": text, "link_preview_options": {"is_disabled": True}}
        if reply_to:
            body["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        if chat_context:
            body["text"] = f"{text}\n\n📍 {chat_context}"
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode())
            if not resp.get("ok"):
                log.warning("notify_owner failed: %s", resp)
    except Exception as e:  # never break the flow on notification failure
        log.warning("notify_owner error: %s", e)


def _http_post_file(url: str, file_path: str, timeout: int = 120) -> dict:
    """multipart/form-data POST с файлом (API: file=...)."""
    import uuid
    boundary = uuid.uuid4().hex
    fname = os.path.basename(file_path) or "source.mp4"
    with open(file_path, "rb") as f:
        fdata = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + fdata + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def video_analysis(action: str = "analyze_url", url: str = "", file_path: str = "",
                   message_id: int | None = None, source_chat: str = "", **_) -> str:
    """Детерминированный анализ видео.

    action: analyze_url (ссылка) ИЛИ analyze_file (локальный файл из кэша).
    message_id/source_chat: для уведомления владельцу с цитированием.
    """
    if action not in ("analyze_url", "analyze_file"):
        return f"Неизвестное действие: {action}. Доступно: analyze_url, analyze_file"

    if action == "analyze_url" and not url:
        return "Ошибка: нужен url видео (TikTok/Reels/прямой файл)."
    if action == "analyze_file" and not file_path:
        return "Ошибка: нужен file_path (путь к видео в кэше, например /opt/data/cache/videos/...)."

    # 1. Submit job (url или файл) — до уведомления, чтобы не сообщать «взял в работу» при ошибке
    try:
        if action == "analyze_url":
            resp = _http_post_form(f"{ANALYZER_URL}/analyze", {"url": url})
        else:
            if not os.path.exists(file_path):
                return f"Файл не найден: {file_path}"
            resp = _http_post_file(f"{ANALYZER_URL}/analyze", file_path)
    except Exception as e:
        return f"Ошибка запуска анализа: {e}"

    # 2. Уведомление владельцу «взял на анализ» с цитированием исходного сообщения
    notify_owner_take(reply_to=message_id, chat_context=source_chat)

    job_id = resp.get("job_id")
    if not job_id:
        return f"Сервис не вернул job_id: {resp}"

    # 3. Poll result (детерминированное ожидание)
    deadline = time.time() + POLL_TIMEOUT_SEC
    while time.time() < deadline:
        time.sleep(10)
        try:
            res = _http_get_json(f"{ANALYZER_URL}/result/{job_id}")
        except Exception as e:
            return f"Ошибка опроса результата: {e}"
        status = res.get("status")
        if status == "done":
            report = _http_get_json(f"{ANALYZER_URL}/result/{job_id}")  # full payload
            r = report.get("result", {})
            a = r.get("analysis", {})
            lines = [
                "## Анализ видео (детерминированный сервис)",
                f"- Длительность: {r.get('duration', '?')} c",
                f"- Модель: {r.get('models', {}).get('text', '?')}",
                "",
                "**Оценки:**",
            ]
            scores = a.get("scores", {})
            for k, v in scores.items():
                if k != "overall":
                    lines.append(f"- {k}: {v}")
            lines.append(f"- **Итого:** {scores.get('overall', a.get('overall', '?'))}")
            lines += ["", "**Почему это работает:**", a.get("why_it_works", "—"),
                      "", "**Что улучшить:**"]
            for i, tip in enumerate(a.get("actionable", []), 1):
                lines.append(f"{i}. {tip}")
            return "\n".join(lines)
        if status == "error":
            return f"Анализ не удался: {res.get('error', 'неизвестно')}"
        # still running
    return f"Таймаут анализа ({POLL_TIMEOUT_SEC}s). Job: {job_id}"


VIDEO_ANALYSIS_SCHEMA = {
    "name": "video_analysis",
    "description": (
        "Детерминированный анализ видео (Reels/TikTok/файл). ВЫЗЫВАЙ ЭТОТ ИНСТРУМЕНТ "
        "СРАЗУ при получении видео от пользователя — без импровизации. Возвращает "
        "готовый отчёт: оценки по критериям, разбор, рекомендации. ФАЙЛ из события → action=analyze_file c file_path (media_urls), ССЫЛКА → analyze_url. "
        "Единственное действие агента — вызов и пересылка результата."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["analyze_url", "analyze_file"],
                        "description": "Тип анализа: analyze_url для ссылки, analyze_file для файла из кэша"},
            "url": {"type": "string",
                     "description": "Ссылка на видео (TikTok/Instagram/прямой файл). Для action=analyze_url"},
            "file_path": {"type": "string",
                     "description": "Путь к видео в кэше (media_urls из события, напр. /opt/data/cache/videos/video_xxx.mov). Для action=analyze_file"},
            "message_id": {"type": "integer",
                            "description": "message_id исходного сообщения с видео (для цитирования в уведомлении)"},
            "source_chat": {"type": "string",
                             "description": "Имя чата, откуда пришло видео (для уведомления владельцу)"},
        },
        "required": ["action"],
    },
}

# --- Registry ---
try:
    from tools.registry import registry

    registry.register(
        name="video_analysis",
        toolset="video",
        schema=VIDEO_ANALYSIS_SCHEMA,
        handler=lambda args, **kw: video_analysis(
            action=args.get("action", "analyze_url"),
            url=args.get("url", ""),
            file_path=args.get("file_path", ""),
            message_id=args.get("message_id"),
            source_chat=args.get("source_chat", ""),
        ),
        emoji="🎬",
    )
except Exception as e:
    log.warning("video_analysis tool registration failed: %s", e)