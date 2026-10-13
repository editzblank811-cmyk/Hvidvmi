import re
import threading
import logging
import argostranslate.translate

logger = logging.getLogger("argos-translator")
LOCK = threading.RLock()


def detect_language(text):
    text = (text or "")[:5000]

    chinese = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))

    return "zh" if chinese >= 4 and chinese > latin * 0.15 else "en"


def is_garbage(text):
    """Detect obvious model failures, not normal repetitive prose."""
    if not text or not text.strip():
        return True

    cleaned = re.sub(r"\s+", "", text)
    if len(cleaned) < 30:
        return False

    # Detect one letter repeated excessively.
    if re.search(r"([A-Za-z])\1{19,}", cleaned):
        return True

    # Detect repetitive character sequences such as ABCABCABC...
    for size in (1, 2, 3, 4):
        for start in range(min(size, len(cleaned))):
            sample = cleaned[start:]
            if len(sample) >= 60:
                unit = sample[:size]
                if unit and sample.count(unit) > len(sample) * 0.65:
                    return True

    return False


class TranslationRoute:
    def __init__(self, first, second=None):
        self.first = first
        self.second = second

    def translate(self, text):
        with LOCK:
            result = self.first.translate(text)

            if not isinstance(result, str) or not result.strip():
                raise RuntimeError("First translation stage returned empty text.")

            if self.second is not None:
                result = self.second.translate(result)

                if not isinstance(result, str) or not result.strip():
                    raise RuntimeError("Hindi translation stage returned empty text.")

        if is_garbage(result):
            raise RuntimeError(
                "Argos produced suspicious repeated characters. "
                "Translation stopped to protect the output file."
            )

        return result


class ArgosTranslator:
    def __init__(self):
        self.languages = argostranslate.translate.get_installed_languages()
        self.by_code = {language.code: language for language in self.languages}
        self.routes = {}

        logger.info("Installed languages: %s", sorted(self.by_code))

    def _direct(self, source, target):
        src = self.by_code.get(source)
        dst = self.by_code.get(target)

        if src is None or dst is None:
            return None

        try:
            return src.get_translation(dst)
        except Exception:
            logger.exception("Failed to load route %s -> %s", source, target)
            return None

    def get_route(self, source, target="hi"):
        key = (source, target)

        if key in self.routes:
            return self.routes[key]

        if source not in self.by_code or target not in self.by_code:
            raise RuntimeError(
                f"Required language models missing: {source} -> {target}"
            )

        direct = self._direct(source, target)

        if direct is not None:
            route = TranslationRoute(direct)
        elif source == "zh" and target == "hi":
            first = self._direct("zh", "en")
            second = self._direct("en", "hi")

            if first is None or second is None:
                raise RuntimeError(
                    "Chinese -> English -> Hindi models are not both installed."
                )

            route = TranslationRoute(first, second)
        else:
            raise RuntimeError(
                f"No usable translation route: {source} -> {target}"
            )

        self.routes[key] = route
        logger.info("Translation route ready: %s -> %s", source, target)
        return route

    def has_route(self, source, target="hi"):
        try:
            self.get_route(source, target)
            return True
        except Exception as exc:
            logger.warning("Route check failed: %s", exc)
            return False

    def translate(self, text, source="auto"):
        if not text or not text.strip():
            return text

        if source not in ("zh", "en"):
            source = detect_language(text)

        return self.get_route(source, "hi").translate(text)
