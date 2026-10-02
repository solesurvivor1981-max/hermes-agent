import ast
p = '/root/.hermes-sauce/plugins/sauce-memory/__init__.py'
s = open(p).read()

if 'def _cmd_app' not in s and 'register_command("app"' not in s:
    handler = '''
def _cmd_app(raw_args: str, chat_id: str = "", sender_id: str = "", **_):
    """/app — единый вход: панель Соуса (в личке), ссылка с scope (в группе)."""
    is_group = bool(chat_id and chat_id.startswith("-"))
    scope = ("chat" + chat_id.lstrip("-")) if is_group else ("user" + (chat_id or sender_id or "0"))
    import urllib.parse as _q
    panel = "https://miniapp.ai-boost.tech/sauce/?client=" + _q.quote(scope)
    upload = "https://miniapp.ai-boost.tech/upload/?client=" + _q.quote(scope)
    if is_group:
        return (
            "🥫 Панель этого чата (задачи/библиотека/календарь):\\n" + panel + "\\n\\n"
            "📥 Загрузить большой файл:\\n" + upload
        )
    return (
        "🥫 Панель Соуса — календарь · задачи · память · библиотека · граф:\\n" + panel + "\\n\\n"
        "📥 Загрузить большой файл:\\n" + upload
    )'''
    idx = s.find('def _cmd_media_lib(')
    assert idx > 0, 'media anchor'
    s = s[:idx] + handler + '\n\n' + s[idx:]

reg_anchor = '        ctx.register_command("upload", _cmd_upload,'
reg = '''        ctx.register_command("app", _cmd_app,
                             description="Панель Соуса: календарь/задачи/память/библиотека/граф",
                             args_hint="")
'''
assert reg_anchor in s, 'upload register anchor'
s = s.replace(reg_anchor, reg + reg_anchor, 1)
ast.parse(s)
open(p, 'w').write(s)
print('/app added')
else:
    print('already')
