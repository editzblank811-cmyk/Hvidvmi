import re
import json
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub, ITEM_DOCUMENT


# --------------------------------------------------
# FILE UTILITIES
# --------------------------------------------------

def safe_filename(name):
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = name.strip(" .")
    return name[:150] or "novel.txt"


def format_duration(seconds):
    seconds = max(0, int(seconds or 0))

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days:
        return f"{days}d {hours}h {minutes}m"

    if hours:
        return f"{hours}h {minutes}m {seconds}s"

    if minutes:
        return f"{minutes}m {seconds}s"

    return f"{seconds}s"


def progress_bar(percent, width=20):
    percent = max(0, min(100, float(percent)))
    filled = int(width * percent / 100)

    return "█" * filled + "░" * (width - filled)


# --------------------------------------------------
# TEXT CHUNKING
# --------------------------------------------------

def split_long_text(text, max_chars=1800):
    """
    Split text on paragraph and sentence boundaries when possible.
    Avoid dropping text and preserve paragraph separators.
    """

    if len(text) <= max_chars:
        return [text]

    chunks = []
    current = ""

    paragraphs = re.split(r"(\n\s*\n)", text)

    for paragraph in paragraphs:

        if len(paragraph) > max_chars:

            if current:
                chunks.append(current)
                current = ""

            # Sentence-aware splitting for long paragraphs.
            sentences = re.split(
                r"(?<=[.!?。！？])\s+",
                paragraph
            )

            for sentence in sentences:

                if len(sentence) > max_chars:

                    if current:
                        chunks.append(current)
                        current = ""

                    # Last resort: split exceptionally long sentences.
                    for start in range(0, len(sentence), max_chars):
                        chunks.append(
                            sentence[start:start + max_chars]
                        )

                elif len(current) + len(sentence) <= max_chars:
                    current += sentence

                else:
                    if current:
                        chunks.append(current)

                    current = sentence

        elif len(current) + len(paragraph) <= max_chars:
            current += paragraph

        else:
            if current:
                chunks.append(current)

            current = paragraph

    if current:
        chunks.append(current)

    return chunks or [text]


def translate_text(
    text,
    translator,
    source_setting,
    max_chars
):
    """Translate all chunks and fail rather than silently retain source."""

    if not text or not text.strip():
        return text

    chunks = split_long_text(text, max_chars)
    translated_chunks = []

    for chunk in chunks:

        source = (
            source_setting
            if source_setting in ("zh", "en")
            else "auto"
        )

        result = translator.translate(chunk, source)

        if not isinstance(result, str) or not result.strip():
            raise RuntimeError(
                "Translation returned empty text."
            )

        translated_chunks.append(result)

    return "".join(translated_chunks)


# --------------------------------------------------
# CHECKPOINTS
# --------------------------------------------------

def load_checkpoint(path):
    if not path.exists():
        return {}

    try:
        data = json.loads(
            path.read_text(encoding="utf-8")
        )

        if isinstance(data, dict):
            return data

    except (json.JSONDecodeError, OSError):
        pass

    return {}


def save_checkpoint(path, data):
    """
    Write atomically so an interrupted write is less likely
    to destroy the previous checkpoint.
    """

    temporary = path.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8"
    )

    temporary.replace(path)


# --------------------------------------------------
# TXT CHAPTER EXTRACTION
# --------------------------------------------------

def extract_txt_chapters(path):
    """
    Recognize common English and Chinese chapter headings.
    If headings are not found, group text into manageable blocks.
    """

    text = path.read_text(
        encoding="utf-8-sig",
        errors="replace"
    )

    if not text.strip():
        raise RuntimeError("The TXT file is empty.")

    heading_pattern = re.compile(
        r"(?im)^\s*(?:"
        r"chapter\s+(?:\d+|[ivxlcdm]+|[a-z]).*|"
        r"chap\.?\s*\d+.*|"
        r"第.{1,20}[章节回卷集].*|"
        r"卷\s*\d+.*|"
        r"序章.*|终章.*|番外.*|"
        r"prologue.*|epilogue.*"
        r")\s*$"
    )

    lines = text.splitlines(keepends=True)
    blocks = []
    current = []

    for line in lines:

        if heading_pattern.match(line.strip()) and current:
            blocks.append("".join(current))
            current = []

        current.append(line)

    if current:
        blocks.append("".join(current))

    # Without recognized headings, group by approximately 6,000 characters.
    if len(blocks) <= 1:

        paragraphs = re.split(
            r"(\n\s*\n)",
            text
        )

        blocks = []
        current_text = ""

        for paragraph in paragraphs:

            if (
                len(current_text) + len(paragraph) > 6000
                and current_text
            ):
                blocks.append(current_text)
                current_text = ""

            current_text += paragraph

        if current_text:
            blocks.append(current_text)

    blocks = [
        block
        for block in blocks
        if block.strip()
    ]

    return blocks or [text]


# --------------------------------------------------
# TXT TRANSLATION
# --------------------------------------------------

def translate_txt_chapters(
    chapters,
    output_path,
    checkpoint_path,
    translator,
    source_setting,
    workers,
    max_chars,
    progress,
    cancelled
):
    checkpoint = load_checkpoint(checkpoint_path)
    completed = checkpoint.get("items", {})

    if not isinstance(completed, dict):
        completed = {}

    translated = {}
    words = 0
    errors = 0
    resumed = 0

    total = len(chapters)

    for index, chapter in enumerate(chapters):

        if cancelled():
            break

        key = str(index)
        cached = completed.get(key)

        # Reuse only successfully translated blocks.
        if (
            isinstance(cached, dict)
            and cached.get("ok") is True
            and isinstance(cached.get("text"), str)
        ):
            translated[index] = cached["text"]
            words += int(
                cached.get("words", len(chapter.split()))
            )
            resumed += 1

            progress(
                index + 1,
                total,
                words,
                f"Block {index + 1}",
                resumed,
                errors
            )
            continue

        try:

            result = translate_text(
                chapter,
                translator,
                source_setting,
                max_chars
            )

            if not result.strip() and chapter.strip():
                raise RuntimeError("Empty translated block.")

            translated[index] = result
            word_count = len(chapter.split())
            words += word_count

            completed[key] = {
                "ok": True,
                "text": result,
                "words": word_count
            }

            save_checkpoint(
                checkpoint_path,
                {"items": completed}
            )

        except Exception as exc:

            errors += 1

            # Mark the item as failed, not successfully translated.
            completed[key] = {
                "ok": False,
                "error": str(exc),
                "words": len(chapter.split())
            }

            save_checkpoint(
                checkpoint_path,
                {"items": completed}
            )

            progress(
                index,
                total,
                words,
                f"ERROR at block {index + 1}",
                resumed,
                errors
            )

            raise RuntimeError(
                f"TXT translation failed at block {index + 1}: {exc}"
            ) from exc

        progress(
            index + 1,
            total,
            words,
            f"Block {index + 1}/{total}",
            resumed,
            errors
        )

    if cancelled():
        return

    missing = [
        index
        for index in range(total)
        if index not in translated
    ]

    if missing:
        raise RuntimeError(
            f"Translation incomplete: {len(missing)} blocks remain."
        )

    # Only write a complete output.
    output_path.write_text(
        "".join(
            translated[index]
            for index in range(total)
        ),
        encoding="utf-8"
    )


# --------------------------------------------------
# EPUB HTML TRANSLATION
# --------------------------------------------------

def _translate_html(
    content,
    translator,
    source_setting,
    max_chars
):
    """
    Translate readable block elements while preserving the
    outer HTML structure, EPUB metadata and non-text resources.
    """

    soup = BeautifulSoup(content, "html.parser")

    # Do not translate navigation menus and table of contents.
    html_tag = soup.find("html")
    epub_type = ""

    if html_tag:
        epub_type = str(
            html_tag.get("epub:type", "")
        ).lower()

    if "nav" in epub_type:
        return content.encode("utf-8")

    if soup.find(
        "nav",
        attrs={"epub:type": re.compile(
            r"toc|landmarks|page-list",
            re.I
        )}
    ):
        return content.encode("utf-8")

    block_tags = [
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "figcaption"
    ]

    elements = []

    for element in soup.find_all(block_tags):

        # Do not translate nested block text twice.
        if element.find_parent(block_tags):
            continue

        if element.find_parent([
            "nav", "script", "style", "code", "pre"
        ]):
            continue

        original = element.get_text(" ", strip=True)

        if not original:
            continue

        # Leave very short interface labels alone.
        if len(original) < 12 and len(original.split()) < 3:
            continue

        elements.append((element, original))

    # Fallback for EPUBs that use divs instead of paragraphs.
    if not elements:

        for element in soup.find_all("div"):

            if element.find_parent("div"):
                continue

            if element.find_parent([
                "nav", "script", "style", "pre"
            ]):
                continue

            original = element.get_text(" ", strip=True)

            if original:
                elements.append((element, original))

    for element, original in elements:

        translated = translate_text(
            original,
            translator,
            source_setting,
            max_chars
        )

        if not translated.strip():
            raise RuntimeError(
                "Empty translation returned for EPUB text."
            )

        # Preserve outer element and its attributes.
        element.clear()
        element.append(translated)

    return str(soup).encode("utf-8")


# --------------------------------------------------
# EPUB TRANSLATION
# --------------------------------------------------

def translate_epub(
    input_path,
    output_path,
    checkpoint_path,
    translator,
    source_setting,
    workers,
    max_chars,
    progress,
    cancelled
):
    book = epub.read_epub(str(input_path))

    checkpoint = load_checkpoint(checkpoint_path)
    completed = checkpoint.get("items", {})

    if not isinstance(completed, dict):
        completed = {}

    documents = [
        item
        for item in book.get_items()
        if item.get_type() == ITEM_DOCUMENT
    ]

    total = len(documents)

    if total == 0:
        raise RuntimeError(
            "No readable EPUB chapter documents found."
        )

    words = 0
    errors = 0
    resumed = 0

    for index, item in enumerate(documents):

        if cancelled():
            return

        key = item.get_id()
        cached = completed.get(key)

        if (
            isinstance(cached, dict)
            and cached.get("ok") is True
            and "content" in cached
        ):
            item.set_content(
                cached["content"].encode("utf-8")
            )

            words += int(cached.get("words", 0))
            resumed += 1

            progress(
                index + 1,
                total,
                words,
                item.get_name(),
                resumed,
                errors
            )
            continue

        original = item.get_content().decode(
            "utf-8",
            errors="replace"
        )

        try:

            translated_bytes = _translate_html(
                original,
                translator,
                source_setting,
                max_chars
            )

            if not translated_bytes:
                raise RuntimeError(
                    "Empty translated EPUB document."
                )

            item.set_content(translated_bytes)

            word_count = len(
                BeautifulSoup(
                    original,
                    "html.parser"
                ).get_text(" ").split()
            )

            words += word_count

            completed[key] = {
                "ok": True,
                "content": translated_bytes.decode("utf-8"),
                "words": word_count
            }

            save_checkpoint(
                checkpoint_path,
                {"items": completed}
            )

        except Exception as exc:

            errors += 1

            completed[key] = {
                "ok": False,
                "error": str(exc)
            }

            save_checkpoint(
                checkpoint_path,
                {"items": completed}
            )

            progress(
                index,
                total,
                words,
                f"ERROR: {item.get_name()}",
                resumed,
                errors
            )

            raise RuntimeError(
                f"EPUB translation failed in "
                f"{item.get_name()}: {exc}"
            ) from exc

        progress(
            index + 1,
            total,
            words,
            item.get_name(),
            resumed,
            errors
        )

    if cancelled():
        return

    # Save the EPUB only after all documents have processed.
    epub.write_epub(str(output_path), book)


# --------------------------------------------------
# TXT SPLITTING
# --------------------------------------------------

def split_text_file(
    source_path,
    zip_path,
    max_bytes=5 * 1024 * 1024
):
    text = source_path.read_text(
        encoding="utf-8",
        errors="replace"
    )

    max_chars = max(1000, max_bytes // 3)

    chunks = []
    current = ""

    for line in text.splitlines(keepends=True):

        if len(current) + len(line) > max_chars and current:
            chunks.append(current)
            current = ""

        current += line

    if current:
        chunks.append(current)

    with zipfile.ZipFile(
        zip_path,
        "w",
        zipfile.ZIP_DEFLATED
    ) as archive:

        for index, chunk in enumerate(chunks, 1):
            archive.writestr(
                f"{source_path.stem}_part_{index:04d}.txt",
                chunk
                                      )
