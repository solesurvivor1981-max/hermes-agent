# Отключаем video_analyze tool (Соусу не нужно — анализ видео = job @AIBoostVideoBot). Блокируем саму регистрацию ToolSpec + добавляем заглушку.
s = open('/opt/hermes/tools/vision_tools.py').read()
changed = False

old_reg = '''    name="video_analyze",'''
new_reg = '''    # DISABLED (02.10.2026, решение Александра): анализ видео в чате Соуса запрещён —
    # это работа отдельного бота видео-анализатора (@AIBoostVideoBot). Соус в чате:
    # сценарии / нарезка / склейка / плашки / субтитры. Регистрация пропущена.
    name="video_analyze_DISABLED",'''

if 'video_analyze_DISABLED' not in s:
    assert old_reg in s, 'tool spec anchor not found'
    s = s.replace(old_reg, new_reg, 1)
    changed = True
    print('video_analyze TOOL unregistered (renamed to _DISABLED)')

open('/opt/hermes/tools/vision_tools.py', 'w').write(s)
import ast
ast.parse(s)
print('saved + syntax OK, changed=', changed)