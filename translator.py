import re
import threading
import logging

import argostranslate.translate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("argos-translator")

_TRANSLATION_LOCK = threading.RLock()


def detect_language(text):
    """Detect Chinese or English text."""
    sample = (text or "")[:5000]

    if not sample.strip():
        return "en"

    chinese = len(
        re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", sample)
    )

    latin = len(
        re.findall(r"[A-Za-z]", sample)
    )

    if chinese >= 4 and chinese > latin * 0.15:
        return "zh"

    return "en"


class TranslationRoute:
    """Supports direct translation and two-step pivot translation."""

    def __init__(self, first, second=None):
        self.first = first
        self.second = second

    def translate(self, text):
        with _TRANSLATION_LOCK:
            result = self.first.translate(text)

            if not isinstance(result, str) or not result.strip():
                raise RuntimeError(
                    "First translation step returned empty text."
                )

            if self.second is not None:
                result = self.second.translate(result)

                if not isinstance(result, str) or not result.strip():
                    raise RuntimeError(
                        "Second translation step returned empty text."
                    )

            return result


class ArgosTranslator:
    def __init__(self):
        self.languages = (
            argostranslate.translate.get_installed_languages()
        )

        self.by_code = {
            language.code: language
            for language in self.languages
        }

        self.routes = {}

        logger.info(
            "Installed Argos languages: %s",
            ", ".join(sorted(self.by_code.keys()))
        )

    def _direct_route(self, source, target):
        """Return a direct route, or None if unavailable."""
        source_language = self.by_code.get(source)
        target_language = self.by_code.get(target)

        if source_language is None or target_language is None:
            return None

        try:
            return source_language.get_translation(target_language)
        except Exception:
            logger.exception(
                "Could not load direct route %s -> %s",
                source,
                target
            )
            return None

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

        # 1. Prefer a direct route whenever it exists.
        direct = self._direct_route(source, target)

        if direct is not None:
            route = TranslationRoute(direct)
            self.routes[key] = route

            logger.info(
                "Using direct route: %s -> %s",
                source,
                target
            )

            return route

        # 2. Chinese -> English -> Hindi fallback.
        if source == "zh" and target == "hi":
            zh_en = self._direct_route("zh", "en")
            en_hi = self._direct_route("en", "hi")

            if zh_en is None:
                raise RuntimeError(
                    "Chinese -> English model is missing."
                )

            if en_hi is None:
                raise RuntimeError(
                    "English -> Hindi model is missing."
                )

            route = TranslationRoute(zh_en, en_hi)
            self.routes[key] = route

            logger.info(
                "Using pivot route: Chinese -> English -> Hindi"
            )

            return route

        raise RuntimeError(
            f"No translation route {source} -> {target}. "
            "Install the required Argos models."
        )

    def has_route(self, source, target="hi"):
        try:
            self.get_route(source, target)
            return True
        except Exception as exc:
            logger.warning(
                "Route unavailable %s -> %s: %s",
                source,
                target,
                exc
            )
            return False

    def translate(self, text, source="auto"):
        if not text or not text.strip():
            return text

        if source not in ("zh", "en"):
            source = detect_language(text)

        route = self.get_route(source, "hi")
        result = route.translate(text)

        if not isinstance(result, str) or not result.strip():
            raise RuntimeError(
                f"Translation returned empty text: {source} -> hi"
            )

        return result
