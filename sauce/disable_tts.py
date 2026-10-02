import ast

# Отключить TTS tool у Соуса (тотальный запрет голосовых ответов, решение Саши 02.10 07:08)
p = '/opt/hermes/tools/tts_tool.py'
s = open(p).read()

old = '''registry.register(
    name="text_to_speech",'''

new = '''# DISABLED (02.10.2026, решение Александра): Соус не отвечает голосом вообще.
# Голосовые ответы отключены тотально — даже по просьбе (навык пере-обучить невозможно:
# память помнит «отвечать голосом», модель продолжала TTS-ить). Если понадобится вернуть —
# убрать _DISABLED из имени регистрации ниже.
registry.register(
    name="text_to_speech_DISABLED",'''

if 'text_to_speech_DISABLED' in s:
    print('already disabled')
else:
    assert old in s, 'tts register anchor not found'
    s = s.replace(old, new, 1)
    ast.parse(s)
    open(p, 'w').write(s)
    print('text_to_speech tool disabled + syntax OK')