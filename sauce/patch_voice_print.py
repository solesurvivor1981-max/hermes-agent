p = '/opt/hermes/gateway/run.py'
s = open(p).read()

# Патч: детерминированный отпечаток транскрипта в чат. Вставляем отправку
# транскрипта в чат сразу после успешного _enrich_message_with_transcription.
old = '''            if audio_paths:
                message_text = await self._enrich_message_with_transcription(
                    message_text,
                    audio_paths,
                )
                _stt_fail_markers = ('''
new = '''            if audio_paths:
                message_text = await self._enrich_message_with_transcription(
                    message_text,
                    audio_paths,
                )
                # Голосовой отпечаток (детерминированный, 02.10.2026): сразу после
                # транскрипции отправляем текстовую расшифровку в чат как отдельное
                # сообщение — чтобы в истории чата остался читаемый отпечаток
                # голосового. Патч во внутреннем коде hermes: переживает restart,
                # теряется при recreate (переприменить patch_voice_print.py).
                if "[The user sent a voice message~" in message_text:
                    try:
                        _vp_start = 0
                        import re as _re_vp
                        for _vp_m in _re_vp.finditer(r'\[The user sent a voice message~\s*Here' + "'" + r's what they said: "(.+?)"\]', message_text, _re_vp.S):
                            _vp_text = _vp_m.group(1).strip()
                            if not _vp_text:
                                continue
                            _vp_adapter = self.adapters.get(source.platform)
                            _vp_meta = self._thread_metadata_for_source(source, self._reply_anchor_for_event(event))
                            if _vp_adapter and _vp_text:
                                try:
                                    await _vp_adapter.send(
                                        source.chat_id,
                                        "🎙️ «" + _vp_text[:3500] + "»",
                                        metadata=_vp_meta,
                                    )
                                except Exception as _vp_err:
                                    logger.debug("Voice print send failed: %s", _vp_err)
                    except Exception as _vp_err:
                        logger.debug("Voice print extraction failed: %s", _vp_err)
                _stt_fail_markers = ('''

if 'Voice print send failed' in s:
    print('already patched (run.py)')
else:
    assert old in s, 'anchor block not found'
    s = s.replace(old, new)
    open(p, 'w').write(s)
    import ast
    ast.parse(s)
    print('patched + syntax OK')