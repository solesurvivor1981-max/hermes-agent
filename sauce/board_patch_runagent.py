# Master patch script (idempotent) - board tool integration into run_agent.py
s = open('/opt/hermes/run_agent.py').read()
changed = False
# (a) board store init
if '_board_store = _BoardStore' not in s:
    anchor = '''        # In-memory todo list for task planning (one per agent/session)
        from tools.todo_tool import TodoStore
        self._todo_store = TodoStore()
'''
    addition = anchor + '''
        # Persistent per-chat task board (deterministic scope: chat for groups,
        # user for DMs - same rule as memory). Never model-chosen.
        try:
            from tools.board_tool import BoardStore as _BoardStore
            _board_scope = None
            if getattr(self, "_chat_type", None) in ("group", "supergroup") and self._chat_id:
                _board_scope = f"chat{self._chat_id}"
            elif self._user_id:
                _board_scope = f"user{self._user_id}"
            self._board_store = _BoardStore(scope=_board_scope)
        except Exception:
            self._board_store = None
'''
    assert anchor in s
    s = s.replace(anchor, addition, 1); changed = True
# (b) dispatch site 1
if 'store=self._board_store,' not in s:
    d1 = '''        if function_name == "todo":
            from tools.todo_tool import todo_tool as _todo_tool
            return _todo_tool(
                todos=function_args.get("todos"),
                merge=function_args.get("merge", False),
                store=self._todo_store,
            )
'''
    b1 = '''        if function_name == "board":
            from tools.board_tool import board_tool as _board_tool
            return _board_tool(
                action=function_args.get("action", ""),
                text=function_args.get("text"),
                due=function_args.get("due"),
                repeat=function_args.get("repeat"),
                who=function_args.get("who"),
                item_id=function_args.get("item_id"),
                filter_=function_args.get("filter_"),
                new_due=function_args.get("new_due", "__unset__"),
                store=self._board_store,
            )
''' + d1
    assert d1 in s
    s = s.replace(d1, b1, 1); changed = True
# (c) dispatch site 2 with tool_duration
if 'function_result = _board_tool(' not in s:
    d2 = '''            elif function_name == "todo":
                from tools.todo_tool import todo_tool as _todo_tool
                function_result = _todo_tool(
                    todos=function_args.get("todos"),
                    merge=function_args.get("merge", False),
                    store=self._todo_store,
                )
'''
    b2 = '''            elif function_name == "board":
                from tools.board_tool import board_tool as _board_tool
                function_result = _board_tool(
                    action=function_args.get("action", ""),
                    text=function_args.get("text"),
                    due=function_args.get("due"),
                    repeat=function_args.get("repeat"),
                    who=function_args.get("who"),
                    item_id=function_args.get("item_id"),
                    filter_=function_args.get("filter_"),
                    new_due=function_args.get("new_due", "__unset__"),
                    store=self._board_store,
                )
                tool_duration = time.time() - tool_start_time
''' + d2
    assert d2 in s
    s = s.replace(d2, b2, 1); changed = True
import py_compile
py_compile.compile('/opt/hermes/run_agent.py', doraise=True)
open('/opt/hermes/run_agent.py','w').write(s)
print('run_agent board integration OK (changed=%s)' % changed)
