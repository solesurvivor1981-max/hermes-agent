p = '/opt/hermes/gateway/run.py'
s = open(p).read()

old = '''                    # Plugin command context (chat-scoped commands), back-compatible:
                    import inspect as _inspect
                    _names = set(_inspect.signature(plugin_handler).parameters.keys())
                    _ctx_kwargs = {}
                    if "chat_id" in _names:
                        _ctx_kwargs["chat_id"] = str(getattr(event, "chat_id", "") or "")
                    if "sender_id" in _names:
                        _ctx_kwargs["sender_id"] = str(getattr(event, "sender_id", "") or "")'''

new = '''                    # Plugin command context (chat-scoped commands), back-compatible:
                    import inspect as _inspect
                    _names = set(_inspect.signature(plugin_handler).parameters.keys())
                    _ctx_kwargs = {}
                    _src = getattr(event, "source", None)
                    if "chat_id" in _names:
                        _ctx_kwargs["chat_id"] = str((getattr(_src, "chat_id", "") or ""))
                    if "sender_id" in _names:
                        _ctx_kwargs["sender_id"] = str((getattr(_src, "user_id", "") or ""))
                    if "chat_name" in _names:
                        _ctx_kwargs["chat_name"] = str((getattr(_src, "chat_name", "") or ""))'''

if 'getattr(event, "chat_id"' in s:
    assert old in s, 'old ctx block not found'
    s = s.replace(old, new)
    open(p, 'w').write(s)
    print('ctx block fixed to use source')
else:
    print('already fixed / not applicable')