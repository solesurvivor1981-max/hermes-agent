p = '/opt/hermes/gateway/run.py'
import ast
s = open(p).read()

old = '''                                        "🎙️ «" + _vp_text[:3500] + "»",'''
new = '''                                        ("🎙️ От " + (getattr(source, "user_name", None) or getattr(source, "chat_name", None) or "клиента") + ": «" + _vp_text[:3500] + "»"),'''

if '🎙️ От ' not in s:
    assert old in s, 'voice print line not found'
    s = s.replace(old, new)
    ast.parse(s)
    open(p, 'w').write(s)
    print('patched: voice print header with author + syntax OK')
else:
    print('already patched')