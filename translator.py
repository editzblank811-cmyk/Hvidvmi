import re
import threading
import argostranslate.translate


_TRANSLATION_LOCK = threading.RLock()


def detect_language(text):
    """Detect Chinese or English from a text sample."""
    sample = (text or "")[:5000]

    if not sample.strip():
        return "en"

    chinese_count = len(
        re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", sample)
    )

    english_count = len(
        re.findall(r"[A-Za-z]", sample)
    )

    if chinese_count >= 4 and chinese_count > english_count * 0.15:
        return "zh"

    return "en"


class ArgosTranslator:
    """Argos Translate wrapper with cached translation routes."""

    def __init__(self):
        self.languages = (
            argostranslate.translate.get_installed_languages()
        )

        self.by_code = {
            language.code: language
            for language in self.languages
        }

        self.routes = {}

    def get_route(self, source, target="hi"):
        key = (source, target)

        if key in self.routes:
            return self.routes[key]

        if source not in self.by_code:
            raise RuntimeError(
                f"Source model '{source}' is not installed."
            )

        if target not in self.by_code:
            raise RuntimeError(
                f"Target model '{target}' is not installed."
            )

        route = self.by_code[source].get_translation(
            self.by_code[target]
        )

        if route is None:
            raise RuntimeError(
                f"No Argos route {source} -> {target}. "
                "Install a compatible direct or pivot model."
            )

        self.routes[key] = route
        return route

    def has_route(self, source, target="hi"):
        try:
            self.get_route(source, target)
            return True
        except Exception:
            return False

    def translate(self, text, source="auto"):
        if not text or not text.strip():
            return text

        if source not in ("zh", "en"):
            source = detect_language(text)

        with _TRANSLATION_LOCK:
            result = self.get_route(source, "hi").translate(text)

        if not isinstance(result, str) or not result.strip():
            raise RuntimeError(
                f"Empty result from Argos: {source} -> Hindi"
            )

        return result
