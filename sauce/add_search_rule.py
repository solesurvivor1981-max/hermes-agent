import yaml

p = '/root/.hermes-sauce/config.yaml'
c = yaml.safe_load(open(p)) or {}
ag = c.get('agent') or {}
sp = ag.get('system_prompt') or ''
add = (
    '\n\nПРАВИЛО ПОИСКА (детерминированное): когда пользователь просит «поищи информацию», '
    '«найди», «что по X в интернете», актуальные данные/цены/новости/факты о внешнем мире — '
    'СНАЧАЛА выполняй web_search и открой 2-3 источника, ответ строй на них со ссылками. '
    'Память (vault) и собственные знания используй для контекста клиента и прошлых решений, '
    'но НЕ как источник актуальных фактов. Если web_search недоступен или пуст — честно скажи '
    'и предложи то, что знаешь, пометив это как «по памяти, требует проверки».'
)
if 'ПРАВИЛО ПОИСКА' not in sp:
    ag['system_prompt'] = (sp + add).strip()
    c['agent'] = ag
    yaml.safe_dump(c, open(p, 'w'), allow_unicode=True, sort_keys=False, default_flow_style=False)
    print('search rule added to system prompt')
else:
    print('search rule already present')