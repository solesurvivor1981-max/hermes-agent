#!/usr/bin/env python3
"""
Board Tool Module - Persistent per-chat task board (deterministic)

One board per chat (groups) / per user (DMs). Scope is computed by run_agent
from chat_id/user_id and injected via the `store` kwarg (BoardStore.scope) -
the tool NEVER accepts a scope from the model. Cross-chat isolation is
architectural, same pattern as memory_tool scope.

Storage: {HERMES_HOME}/boards/{scope}/board.json
Atomic writes (tmp+rename), fcntl lock (same pattern as memory_tool).

Actions (single `board` tool):
  add    (text, due?, repeat?, who?)  - add task; repeat: daily|weekly
  list   (filter?)                    - overdue|today|upcoming|active|all|done
  done   (item_id)                    - complete; repeat tasks get their next due
  cancel (item_id)
  edit   (item_id, text?, new_due?, who?)
  clean  ()                           - drop finished items older than 7 days

Deterministic behaviors (computed in code, never by the model):
- grouping into overdue/today/upcoming/no-due sections
- next-due calculation for repeating tasks
- id generation, sorting, cleanup windows
"""

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Dict, Any, List, Optional

from hermes_constants import get_hermes_home

try:
    import fcntl
except ImportError:
    fcntl = None

logger = logging.getLogger(__name__)

VALID_REPEAT = {"", "daily", "weekly"}
FINISHED_STATUSES = {"done", "cancelled"}
CLEAN_AFTER_DAYS = 7
UNSET = "__unset__"


def get_boards_dir() -> Path:
    return get_hermes_home() / "boards"


def _parse_due(raw: Optional[str]) -> Optional[str]:
    """Normalize due to ISO (YYYY-MM-DD or 'YYYY-MM-DD HH:MM'). None if empty. ValueError if invalid."""
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    raw = raw.replace("T", " ").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            if fmt == "%Y-%m-%d":
                return dt.date().isoformat()
            return dt.isoformat(sep=" ", timespec="minutes")
        except ValueError:
            continue
    raise ValueError(f"bad due format: {raw!r} (use YYYY-MM-DD or 'YYYY-MM-DD HH:MM')")


class BoardStore:
    """File-backed, scope-bound board. One instance per AIAgent."""

    def __init__(self, scope: Optional[str] = None):
        self.scope = scope  # e.g. chat-5300802347 / user-312022420
        self._items: List[Dict[str, Any]] = []
        self._loaded = False

    # ---------- persistence ----------
    def _board_file(self) -> Path:
        return get_boards_dir() / (self.scope or "_noscope") / "board.json"

    @contextmanager
    def _file_lock(self, path: Path):
        if fcntl is None:
            yield
            return
        lock_path = path.with_suffix(".lock")
        with open(lock_path, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)

    def load_from_disk(self):
        if self._loaded:
            return
        path = self._board_file()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._items = data.get("items", [])
            except Exception:
                logger.exception("board: corrupt file %s, starting empty", path)
                self._items = []
        self._loaded = True

    def save_to_disk(self):
        path = self._board_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._file_lock(path):
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"scope": self.scope, "items": self._items},
                              f, ensure_ascii=False, indent=1)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    # ---------- ids ----------
    def _next_id(self) -> str:
        self.load_from_disk()
        nums = [int(i["id"]) for i in self._items if str(i.get("id", "")).isdigit()]
        return str((max(nums) + 1) if nums else 1)

    # ---------- date helpers (deterministic) ----------
    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    def _is_overdue(self, due: Optional[str]) -> bool:
        if not due:
            return False
        try:
            if " " in due:
                return datetime.fromisoformat(due) < self._now()
            return date.fromisoformat(due) < self._now().date()
        except ValueError:
            return False

    def _is_today(self, due: Optional[str]) -> bool:
        if not due:
            return False
        try:
            d = (datetime.fromisoformat(due).date() if " " in due
                 else date.fromisoformat(due))
            return d == self._now().date()
        except ValueError:
            return False

    def _active(self) -> List[Dict[str, Any]]:
        return [i for i in self._items if i.get("status") == "pending"]

    def _render_item(self, it: Dict[str, Any]) -> str:
        parts = [f"#{it['id']}", it["text"]]
        if it.get("due"):
            mark = ""
            if it.get("status") == "pending" and self._is_overdue(it["due"]):
                mark = " ПРОСРОЧЕНО"
            parts.append(f"[до {it['due']}{mark}]")
        if it.get("repeat"):
            parts.append(f"(каждый {it['repeat']})")
        if it.get("who"):
            parts.append(f"— для: {it['who']}")
        if it.get("status") != "pending":
            parts.append(f"[{it['status']}]")
        return " ".join(parts)

    def _next_due_for_repeat(self, repeat: str, old_due: Optional[str]) -> str:
        base = None
        if old_due:
            try:
                base = (datetime.fromisoformat(old_due) if " " in old_due
                        else datetime.combine(date.fromisoformat(old_due), datetime.min.time()))
            except ValueError:
                base = None
        now = self._now()
        if base is None or base < now:
            base = now
        days = 1 if repeat == "daily" else 7
        return (base + timedelta(days=days)).date().isoformat()

    # ---------- actions ----------
    def add(self, text: str, due: Optional[str] = None,
            repeat: str = "", who: Optional[str] = None) -> Dict[str, Any]:
        self.load_from_disk()
        due_norm = _parse_due(due)
        repeat = (repeat or "").strip().lower()
        if repeat not in VALID_REPEAT:
            return {"success": False,
                    "error": f"repeat must be daily|weekly or omitted"}
        if not text or not str(text).strip():
            return {"success": False, "error": "empty text"}
        item = {
            "id": self._next_id(),
            "text": str(text).strip(),
            "status": "pending",
            "due": due_norm,
            "repeat": repeat,
            "who": (str(who).strip() if who else "") or None,
            "created_at": self._now().isoformat(timespec="seconds"),
        }
        self._items.append(item)
        self.save_to_disk()
        return {"success": True, "added": self._render_item(item),
                "active_total": len(self._active())}

    def list(self, filter_: str = "active") -> Dict[str, Any]:
        self.load_from_disk()
        f = (filter_ or "active").strip().lower()
        act = self._active()
        overdue = [i for i in act if self._is_overdue(i.get("due"))]
        today = [i for i in act if self._is_today(i.get("due"))
                 and not self._is_overdue(i.get("due"))]
        upcoming = [i for i in act if i.get("due")
                    and not self._is_overdue(i.get("due")) and not self._is_today(i.get("due"))]
        nodue = [i for i in act if not i.get("due")]

        if f == "overdue":
            body = "\n".join(self._render_item(i) for i in overdue) if overdue else "Просроченного нет."
        elif f == "today":
            body = "\n".join(self._render_item(i) for i in today) if today else "На сегодня ничего."
        elif f == "upcoming":
            body = ("\n".join(self._render_item(i) for i in sorted(upcoming, key=lambda x: x["due"]))
                    if upcoming else "Ближайших задач со сроком нет.")
        elif f == "all":
            body = "\n".join(self._render_item(i) for i in self._items) if self._items else "Пусто."
        elif f == "done":
            fin = [i for i in self._items if i.get("status") in FINISHED_STATUSES]
            body = "\n".join(self._render_item(i) for i in fin) if fin else "Завершённых нет."
        elif f == "active":
            sections = []
            if overdue:
                sections.append("ПРОСРОЧЕНО:\n" + "\n".join(self._render_item(i) for i in overdue))
            if today:
                sections.append("СЕГОДНЯ:\n" + "\n".join(self._render_item(i) for i in today))
            if upcoming:
                sections.append("ДАЛЬШЕ:\n" + "\n".join(
                    self._render_item(i) for i in sorted(upcoming, key=lambda x: x["due"])))
            if nodue:
                sections.append("БЕЗ СРОКА:\n" + "\n".join(self._render_item(i) for i in nodue))
            body = "\n\n".join(sections) if sections else "Доска пуста."
        else:
            return {"success": False,
                    "error": f"unknown filter {f!r}: overdue|today|upcoming|active|all|done"}
        return {"success": True, "scope": self.scope, "filter": f, "board": body}

    def _find(self, item_id: str) -> Optional[Dict[str, Any]]:
        for i in self._items:
            if str(i.get("id")) == str(item_id):
                return i
        return None

    def done(self, item_id: str) -> Dict[str, Any]:
        self.load_from_disk()
        it = self._find(item_id)
        if not it:
            return {"success": False, "error": f"no item #{item_id}"}
        if it.get("status") != "pending":
            return {"success": False, "error": f"#{item_id} already {it.get('status')}"}
        it["status"] = "done"
        it["finished_at"] = self._now().isoformat(timespec="seconds")
        out = {"success": True, "done": self._render_item(it)}
        if it.get("repeat"):
            nd = self._next_due_for_repeat(it["repeat"], it.get("due"))
            clone = dict(it)
            clone["id"] = self._next_id()
            clone["status"] = "pending"
            clone["due"] = nd
            clone.pop("finished_at", None)
            clone["created_at"] = self._now().isoformat(timespec="seconds")
            self._items.append(clone)
            out["repeat"] = f"новый срок {nd} (#{clone['id']})"
        self.save_to_disk()
        return out

    def cancel(self, item_id: str) -> Dict[str, Any]:
        self.load_from_disk()
        it = self._find(item_id)
        if not it:
            return {"success": False, "error": f"no item #{item_id}"}
        it["status"] = "cancelled"
        it["finished_at"] = self._now().isoformat(timespec="seconds")
        self.save_to_disk()
        return {"success": True, "cancelled": self._render_item(it)}

    def edit(self, item_id: str, text: Optional[str] = None,
             new_due: Optional[str] = UNSET, who: Optional[str] = UNSET) -> Dict[str, Any]:
        self.load_from_disk()
        it = self._find(item_id)
        if not it:
            return {"success": False, "error": f"no item #{item_id}"}
        if text is not None:
            if not str(text).strip():
                return {"success": False, "error": "empty text"}
            it["text"] = str(text).strip()
        if new_due != UNSET:
            it["due"] = _parse_due(new_due)
        if who != UNSET:
            it["who"] = (str(who).strip() if who else "") or None
        self.save_to_disk()
        return {"success": True, "edited": self._render_item(it)}

    def clean(self) -> Dict[str, Any]:
        self.load_from_disk()
        cutoff = self._now() - timedelta(days=CLEAN_AFTER_DAYS)
        kept, removed = [], 0
        for i in self._items:
            if i.get("status") in FINISHED_STATUSES:
                try:
                    fin = datetime.fromisoformat(i.get("finished_at") or "")
                except ValueError:
                    fin = None
                if fin and fin < cutoff:
                    removed += 1
                    continue
            kept.append(i)
        self._items = kept
        self.save_to_disk()
        return {"success": True, "removed": removed, "remaining": len(self._items)}


def board_tool(action: str, text: Optional[str] = None, due: Optional[str] = None,
               repeat: Optional[str] = None, who: Optional[str] = None,
               item_id: Optional[str] = None, filter_: Optional[str] = None,
               new_due: Optional[str] = UNSET,
               store: Optional[BoardStore] = None, **kw) -> str:
    """Entry point; the dispatcher passes store=BoardStore (scope-bound)."""
    if store is None or not getattr(store, "scope", None):
        return json.dumps({"success": False,
                           "error": "board недоступен: скоуп чата не определён"},
                          ensure_ascii=False)
    try:
        if action == "add":
            result = store.add(text=text or "", due=due, repeat=repeat or "", who=who)
        elif action == "list":
            result = store.list(filter_ or "active")
        elif action == "done":
            result = store.done(item_id or "")
        elif action == "cancel":
            result = store.cancel(item_id or "")
        elif action == "edit":
            kwargs = {"text": text}
            if new_due != UNSET:
                kwargs["new_due"] = new_due
            if who != UNSET:
                kwargs["who"] = who
            result = store.edit(item_id or "", **kwargs)
        elif action == "clean":
            result = store.clean()
        else:
            result = {"success": False, "error": "action: add|list|done|cancel|edit|clean"}
    except ValueError as ve:
        result = {"success": False, "error": str(ve)}
    except Exception as e:
        logger.exception("board tool failed")
        result = {"success": False, "error": f"internal: {e}"}
    return json.dumps(result, ensure_ascii=False)


BOARD_SCHEMA = {
    "name": "board",
    "description": (
        "Персистентная доска задач этого чата (одна доска на чат, изоляция гарантируется кодом). "
        "Используй, когда собеседник просит что-то не забыть, обещает сделать, ставит дедлайн "
        "или просит напомнить — и сверяйся с ней при обсуждении планов (board list). "
        "Доска живёт между сессиями. Действия: add (text, due YYYY-MM-DD или 'YYYY-MM-DD HH:MM', "
        "repeat daily|weekly, who), list (filter overdue|today|upcoming|active|all|done), "
        "done/cancel/edit (item_id), clean. Секции ПРОСРОЧЕНО/СЕГОДНЯ считает код — не выдумывай их сам."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "done", "cancel", "edit", "clean"],
                "description": "Действие с доской.",
            },
            "text": {"type": "string", "description": "Текст задачи (add/edit)."},
            "due": {"type": "string", "description": "Срок: YYYY-MM-DD или 'YYYY-MM-DD HH:MM' (add)."},
            "repeat": {"type": "string", "enum": ["daily", "weekly"],
                       "description": "Повтор задачи (add)."},
            "who": {"type": "string", "description": "Для кого задача — свободный тег (add/edit)."},
            "item_id": {"type": "string", "description": "ID задачи (done/cancel/edit)."},
            "filter_": {"type": "string",
                        "enum": ["active", "overdue", "today", "upcoming", "all", "done"],
                        "description": "Фильтр для list (по умолчанию active с секциями)."},
            "new_due": {"type": "string",
                        "description": "Новый срок для edit; пустая строка = убрать срок."},
        },
        "required": ["action"],
    },
}


def check_board_requirements() -> bool:
    return True


# --- Registry (top-level: discover_builtin_tools imports modules with top-level register) ---
from tools.registry import registry

registry.register(
    name="board",
    toolset="board",
    schema=BOARD_SCHEMA,
    handler=lambda args, **kw: board_tool(
        action=args.get("action", ""),
        text=args.get("text"),
        due=args.get("due"),
        repeat=args.get("repeat"),
        who=args.get("who"),
        item_id=args.get("item_id"),
        filter_=args.get("filter_"),
        new_due=args.get("new_due", UNSET),
        store=kw.get("store"),
    ),
    check_fn=check_board_requirements,
    emoji="📌",
)