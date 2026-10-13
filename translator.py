import re
import threading
import argostranslate.translate

_route_lock = threading.Lock()


def detect_language(text):
    sample = (text or "")[:5000]

    if not sample.strip():
        return "en"

    han = len(re.findall(r"[\u4e00-\u9fff]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))

    if han > max(3, latin * 0.12):
        return "zh"

    return "en"


class ArgosTranslator:
    def __init__(self):
        self.languages = argostranslate.translate.get_installed_languages()
        self.by_code = {
            language.code: language
            for language in self.languages
        }
        self._routes = {}

    def has_route(self, source, target):
        try:
            self.get_route(source, target)
            return True
        except Exception:
            return False

    def get_route(self, source, target):
        key = (source, target)

        if key in self._routes:
            return self._routes[key]

        if source not in self.by_code:
            raise RuntimeError(
                f"Argos source model '{source}' is not installed."
            )

        if target not in self.by_code:
            raise RuntimeError(
                f"Argos target model '{target}' is not installed."
            )

        route = self.by_code[source].get_translation(
            self.by_code[target]
        )

        if route is None:
            raise RuntimeError(
                f"No installed Argos route {source}->{target}. "
                "Install the direct model or required intermediate models."
            )

        self._routes[key] = route
        return route

    def translate(self, text, source):
        if not text or not text.strip():
            return text

        if source not in ("zh", "en"):
            source = detect_language(text)

        route = self.get_route(source, "hi")

        with _route_lock:
            return route.translate(text)
