"""sauce-memory plugin — детерминированные slash-команды памяти Соуса.

Регистрирует slash-команды (/memory, /forget, /vault-graph) и хуки:
- pre_llm_call: если вопрос про память/календарь — подтягивает выдержки из
  vault/calendar в контекст хода (детерминированный recall).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

MEM_SCRIPT = "/opt/data/skills/sauce-memory/scripts/memory.py"
CAL_SCRIPTS = "/opt/data/skills/sauce-calendar/scripts"

# вопрос про память/расписание? (ru)
MEM_QUESTION_RE = re.compile(
    r"\b(что (ты )?(помнишь|запоминал)|из памяти|в памяти|покажи память|"
    r"что я (говорил|просил)|что (мы )?обсуждали|когда[^\n]{0,40}?\s(встреч|созвон|эфир|выступл|улета|приезжа)|"
    r"созвон|встреча|календар|расписан|планы на|что завтра|что сегодня)", re.I)

def _run(cmd: list, timeout=15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (("\nERR: " + r.stderr) if r.stderr.strip() and r.returncode else "")
    except Exception as exc:
        return f"ERR: {exc}"

# TG id клиента → canonical заметка (детерминированный роутинг чат→клиент)
CLIENT_BY_TG = {
    "765380033": "marina",
    "541391866": "masha",
    "1481356902": "zlata",
    "312022420": "alexander",
    "8896306609": "alexander",  # Coral = второй аккаунт
}
CLIENT_BY_CHAT = {
    "-5533159439": "masha",     # группа «Персональный ассистент» (Маша+Ольга)
}

def _cmd_memory(raw_args: str, chat_id: str = "", sender_id: str = "", **_):
    client = None
    if sender_id in CLIENT_BY_TG:
        client = CLIENT_BY_TG[sender_id]
    elif chat_id in CLIENT_BY_CHAT:
        client = CLIENT_BY_CHAT[chat_id]
    """/memory [query] — показать индекс/поиск по vault"""
    q = (raw_args or "").strip()
    if not q or q in ("index", "все"):
        out = _run(["python3", MEM_SCRIPT, "graph"])
        try:
            d = json.loads(out)
            notes = sorted(d.get("nodes", []))
            if client:
                # Чат клиента: только ЕГО заметки (user + его факты)
                own = [n for n in notes if n == client or n.startswith(client + "-")]
                other = [n for n in notes if not (n == client or n.startswith(client + "-"))]
                lines = [f"🗂 Память этого чата ({client}):"]
                lines += [f"- [[{n}]]" for n in own]
                if other:
                    lines.append(f"_…и {len(other)} заметок других клиентов — фильтр чата; /memory все — полный список_")
                return "\n".join(lines) + f"\n\nСвоих: {len(own)}. Поиск: /memory <запрос>"
            notes_hdr = "🗂 Память (все заметки):"
            return notes_hdr + "\n" + "\n".join(f"- [[{n}]]" for n in notes) + f"\n\nВсего: {len(notes)}. Поиск: /memory <запрос>"
        except Exception:
            return out
    out = _run(["python3", MEM_SCRIPT, "search", "--query", q])
    try:
        d = json.loads(out)
        hits = d.get("hits", [])
        if client:
            own = [h for h in hits if h["note"] == client or h["note"].startswith(client + "-")]
            rest = [h for h in hits if h not in own]
            hits = (own + rest)[:10]
        lines = [f"🔎 По памяти «{q}» — {d.get('count', 0)} совпадени(й):" + (" (сначала — этот клиент)" if client and hits else "")]
        for h in hits:
            lines.append(f"- [[{h['note']}]] ({h['area']}): {h['snippet'][:120]}…")
        if not hits:
            lines.append(f"Ничего по «{q}». /memory — список заметок.")
        return "\n".join(lines)
    except Exception:
        return out

def _cmd_forget(raw_args: str) -> str:
    """/forget <заметка> — мягкое удаление (→ archive/)"""
    note = (raw_args or "").strip().strip("[]")
    if not note:
        return "🗑 Формат: /forget <имя заметки> — перемещу в archive (можно восстановить /restore <имя>)"
    return _run(["python3", MEM_SCRIPT, "forget", "--note", note]).strip() or "ok"

def _cmd_restore(raw_args: str) -> str:
    note = (raw_args or "").strip().strip("[]")
    if not note:
        return "♻️ Формат: /restore <имя заметки>"
    return _run(["python3", MEM_SCRIPT, "restore", "--note", note]).strip() or "ok"

def _cmd_calendar(raw_args: str) -> str:
    """/calendar [from] [to|all] — детерминированный вывод календаря"""
    args = (raw_args or "").split()
    cmd = ["python3", f"{CAL_SCRIPTS}/calendar_list.py"]
    if args:
        if args[0].lower() == "all":
            cmd += ["--status", "all"]
        else:
            cmd += ["--from", args[0]]
        if len(args) > 1 and args[1].lower() != "all":
            cmd += ["--to", args[1]]
    out = _run(cmd)
    try:
        d = json.loads(out)
        lines = [f"📅 События ({d.get('count', 0)}):"]
        for e in d.get("events", []):
            status = e.get("status", "")
            mark = {"active": "•", "done": "✓", "cancelled": "✗", "missed": "⚠"}.get(status, status)
            lines.append(f"- {mark} №{e['id']} {e['dt']} — {e['title']} ({e.get('who') or '—'})")
        return "\n".join(lines)
    except Exception:
        return out

def _on_pre_llm_call(*, user_message: str = "", **kwargs) -> Any:
    """Контекст-инъекция: вопрос про память/календарь → выдержки из хранилищ."""
    text = user_message or ""
    if not text or not MEM_QUESTION_RE.search(text):
        return None
    parts = []
    if re.search(r"когда|календар|расписан|завтр|сегодня|созвон|встреч|эфир|улета|выступ", text, re.I):
        cal = _run(["python3", f"{CAL_SCRIPTS}/calendar_list.py"])
        if cal and not cal.startswith("ERR"):
            parts.append("[КАЛЕНДАРЬ — единственный источник по событиям]\n" + cal)
    mem = _run(["python3", MEM_SCRIPT, "search", "--query", text[:60]])
    if mem and not mem.startswith("ERR"):
        parts.append("[ПАМЯТЬ (vault) — выдержки по запросу]\n" + mem)
    if not parts:
        return None
    ctx = "\n\n".join(parts)
    logger.info("sauce-memory: injecting %d chars of vault/calendar context", len(ctx))
    return {"context": ctx}

"""Расширение sauce-memory: slash-команда /graph (детерминированная).

Возвращает ответ, в котором первый вложенный файл = PNG-снимок графа,
если headless-браузер доступен; иначе готовый HTML-файл и текст-статистика.
Также /graph публикует свежий снапшот в vault (перезапись graph.html).
"""
import json
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

MEM = "/opt/data/skills/sauce-memory/scripts/memory.py"
GH = "/opt/data/skills/sauce-memory/scripts/graph_html.py"
OUT_HTML = "/opt/data/memory-vault/graph.html"





def _cmd_app(raw_args: str, chat_id: str = "", sender_id: str = "", **_):
    """/app — единый вход: панель Соуса (в личке), ссылка с scope (в группе)."""
    is_group = bool(chat_id and chat_id.startswith("-"))
    scope = ("chat" + chat_id.lstrip("-")) if is_group else ("user" + (chat_id or sender_id or "0"))
    import urllib.parse as _q
    panel = "https://miniapp.ai-boost.tech/sauce/?client=" + _q.quote(scope)
    upload = "https://miniapp.ai-boost.tech/upload/?client=" + _q.quote(scope)
    if is_group:
        return (
            "🎭 Панель этого чата (задачи/библиотека/календарь):\n" + panel + "\n\n"
            "📥 Загрузить большой файл:\n" + upload
        )
    return (
        "🎭 Панель Соуса — календарь · задачи · память · библиотека · граф:\n" + panel + "\n\n"
        "📥 Загрузить большой файл:\n" + upload
    )

def _cmd_media_lib(raw_args: str, chat_id: str = "", sender_id: str = "", **_):
    """Медиа-библиотека (S3): /media search <запрос> | /media link <key> | /media last
    Детерминированно читает /opt/data/scripts/media_library.py (реестр из S3)."""
    import subprocess as _sub
    import json as _json
    args = (raw_args or "").strip()
    scope = ("chat" + chat_id.lstrip("-")) if chat_id.startswith("-") else ("user" + (chat_id or sender_id or "0"))
    try:
        if not args or args in ("last", "список"):
            p = _sub.run(["/opt/hermes/.venv/bin/python", "/opt/data/scripts/media_library.py", "list", "--client", scope, "--limit", "10"],
                         capture_output=True, text=True, timeout=120)
        elif args.startswith(("search", "найти")):
            q = args.split(None, 1)[-1]
            p = _sub.run(["/opt/hermes/.venv/bin/python", "/opt/data/scripts/media_library.py", "search", q, "--client", scope, "--limit", "10"],
                         capture_output=True, text=True, timeout=120)
        elif args.startswith(("link", "ссылка")):
            key = args.split(None, 1)[-1]
            p = _sub.run(["/opt/hermes/.venv/bin/python", "/opt/data/scripts/media_library.py", "link", key],
                         capture_output=True, text=True, timeout=120)
        else:
            # как search по-умолчанию
            p = _sub.run(["/opt/hermes/.venv/bin/python", "/opt/data/scripts/media_library.py", "search", args, "--client", scope, "--limit", "10"],
                         capture_output=True, text=True, timeout=120)
        try:
            d = _json.loads(p.stdout)
        except Exception:
            return "❌ Медиа-библиотека недоступна: " + (p.stderr or p.stdout or "?")[:150]
        if not d.get("ok"):
            return "❌ " + str(d.get("error"))[:150]
        if "url" in d and "key" in d and "hits" not in d:
            return f"🔗 {d['key']}\n{d['url']}\n(ссылка действует 7 дней; файл в библиотеке навсегда)"
        hits = d.get("hits", [])
        if not hits:
            return f"🗂 В библиотеке {scope} пусто (или не нашлось). Загрузить: кнопка «Загрузить файл» или /upload"
        lines = [f"🗂 Библиотека {scope} — {d.get('count', len(hits))}:"]
        for h in hits:
            size = h.get("size", 0)
            mb = f"{size/1048576:.1f}МБ" if size > 1048576 else f"{size/1024:.0f}КБ"
            dur = h.get("duration_s")
            dur_s = f" · {dur}s" if dur else ""
            lbl = h.get("label") or h.get("orig_name") or ""
            lines.append(f"• {h['key']} · {mb}{dur_s}" + (f" · {lbl}" if lbl else ""))
        lines.append("Ссылка на файл: /media link <key>")
        return "\n".join(lines)
    except Exception as e:
        return "❌ " + str(e)[:200]


def _cmd_upload(raw_args: str, chat_id: str = "", sender_id: str = "", **_):
    """Ссылка на загрузку: scope чата зашит в URL (детерминированная привязка).
    scope: chat{id} для групп, user{id} для личек (та же схема что у памяти/доски)."""
    if chat_id and chat_id.startswith("-"):
        scope = "chat" + chat_id.lstrip("-")
    else:
        scope = "user" + (chat_id or sender_id or "0")
    name = (raw_args or "").strip() or ""
    import urllib.parse as _q
    url = ("https://miniapp.ai-boost.tech/upload/?client=" + _q.quote(scope)
           + ("&name=" + _q.quote(name) if name else ""))
    return (
        f"📥 Загрузка больших файлов (до 4 ГБ) — напрямую в S3, мимо Telegram:\n"
        f"{url}\n\n"
        f"Привязка чата детерминированная: scope {scope}\n"
        f"На странице: файл → тип (исходник/готовое/семпл) → подпись → Загрузить.\n"
        f"Проверка после загрузки: /media search <имя файла>"
    )

def _graph_cmd(raw_args: str) -> str:
    raw = (raw_args or "").strip()
    subprocess.run(["python3", MEM, "rebuild"], capture_output=True, text=True, timeout=30)
    subprocess.run(["python3", GH], capture_output=True, text=True, timeout=60)
    if not os.path.exists(OUT_HTML):
        return "⚠ Граф не сгенерировался — смотреть /opt/data/memory-vault/"
    html = open(OUT_HTML, encoding="utf-8").read()
    m = re.search(r"Граф памяти Соуса — (\d+) заметок, (\d+) связ(ей/ь|ей|и/ь)?", html)
    stats = f"{m.group(1)} заметок, {m.group(2)} связ(ей/ь)" if m else "готов"
    lines = [f"🗺 Граф памяти: {stats} (свежий срез, memory.py)"]
    # Пытаемся прикрепить HTML как файл через MEDIA:. Telegram поддерживает txt/md/docx и пр.
    # html не в списке расширений base.py — отдаём как zip (html внутри) + txt-резюме.
    try:
        out = subprocess.run(["/bin/bash", "/opt/data/scripts/publish_graph.sh"],
                             capture_output=True, text=True, timeout=120)
        tail = [ln for ln in out.stdout.splitlines() if ln.startswith("http") or "HTTP" in ln]
        if tail:
            url = tail[-1].split(" (HTTP")[0].strip()
            lines.append(f"🔗 Смотреть: {url} (живёт 24ч)")
        else:
            lines.append(f"Файл: {OUT_HTML} (публикация не удалась: {(out.stdout or out.stderr)[-120:]})")
    except Exception as exc:
        logger.warning("graph publish failed: %s", exc)
        lines.append(f"Файл: {OUT_HTML}")
    if raw == "обсудить" or raw == "список":
        # отдаём список узлов текстом
        out = subprocess.run(["python3", MEM, "graph"], capture_output=True, text=True)
        d = json.loads(out.stdout or "{}")
        nodes = d.get("nodes", [])
        return lines[0] + "\nЗаметки:\n" + "\n".join(f"- [[{n}]]" for n in sorted(nodes))
    return "\n".join(lines)



def register(ctx) -> None:
    try:
        ctx.register_command("memory", _cmd_memory,
                             description="Память этого чата: /memory [запрос|все]",
                             args_hint="[запрос|все]")
        ctx.register_command("media", _cmd_media_lib,
                             description="Медиа-библиотека и память: /media search <file> | /memory [запрос]",
                             args_hint="[запрос|все]")
        ctx.register_command("restore", _cmd_restore,
                             description="Восстановить заметку из archive: /restore <имя>",
                             args_hint="<имя>")
        ctx.register_command("calendar", _cmd_calendar,
                             description="Вывести календарь: /calendar [дата|all]",
                             args_hint="[from|all]")
        ctx.register_command("forget", _cmd_forget,
                             description="Забыть заметку (→ archive): /forget <имя>",
                             args_hint="<имя>")
        ctx.register_command("app", _cmd_app,
                             description="Панель Соуса: календарь/задачи/память/библиотека/граф",
                             args_hint="")
        ctx.register_command("upload", _cmd_upload,
                             description="Ссылка на загрузку больших файлов в S3-библиотеку",
                             args_hint="[клиент]")
        ctx.register_command("graph", _graph_cmd,
                             description="Граф знаний (детерминированно): /graph [список]",
                             args_hint="[список]")
        ctx.register_hook("pre_llm_call", _on_pre_llm_call)
        logger.info("sauce-memory plugin registered")
    except Exception as exc:
        logger.warning("sauce-memory registration failed: %s", exc)