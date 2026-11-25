"""DashScope ASR for DSH audio chunks, with original media offsets."""

from plugin.astrbot_plugin_face_convert.speech_asr import MODEL, URL, recognize


async def transcribe_audio(audio, key, *, model=MODEL, endpoint=None, workspace="", language="", keep_dialect=False):
    data_uri = f"data:{audio['mime']};base64,{audio['data']}"
    result = await recognize(data_uri, key, model=model, endpoint=endpoint, workspace=workspace,
                             language=language, keep_dialect=keep_dialect)
    if result["status"] == "transcribed":
        return {**result, "start": audio["start"], "end": audio["end"]}
    return result
