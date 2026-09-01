from html import unescape

import requests

LANG_CODES = {"ko": "ko", "en": "en", "ja": "ja", "zh": "zh-CN"}


class TranslationError(RuntimeError):
    pass


class Translator:
    def __init__(self, api_url, timeout=8):
        self.api_url = api_url
        self.timeout = timeout

    def translate(self, text, source, target):
        if source == target:
            return text
        try:
            r = requests.get(
                self.api_url,
                params={
                    "q": text,
                    "langpair": "{}|{}".format(
                        LANG_CODES[source], LANG_CODES[target]
                    ),
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            payload = r.json()
            response_status = payload.get("responseStatus")
            if response_status and int(response_status) >= 400:
                raise TranslationError(
                    payload.get("responseDetails") or "번역 서비스 오류"
                )
            translated = (payload.get("responseData") or {}).get("translatedText")
            if not translated:
                raise TranslationError("빈 번역 결과")
            return unescape(translated)
        except Exception as e:
            raise TranslationError(str(e)) from e
