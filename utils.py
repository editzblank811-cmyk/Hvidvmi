import re
import json
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString
from ebooklib import epub, ITEM_DOCUMENT


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


def split_long_text(text, max_chars=1800):
    if len(text) <= max_chars:
        return [text]

    parts = []
    current = ""

    paragraphs = re.split(r"(\n\s*\n)", text)

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                parts.append(current)
                current = ""

            sentences = re.split(
                r"(?<=[.!?。！？])\s+",
                paragraph
            )

            for sentence in sentences:
                if len(sentence) > max_chars:
                    for start in range(0, len(sentence), max_chars):
                        parts.append(
                            sentence[start:start + max_chars]
                        )

                elif len(current) + len(sentence) + 1 <= max_chars:
                    current = (current + " " + sentence).strip()

                else:
                    if current:
                        parts.append(current)
                    current = sentence

        elif len(current) + len(paragraph) <= max_chars:
            current += paragraph

        else:
            if current:
                parts.append(current)
            current = paragraph

    if current:
        parts.append(current)

    return parts or [text]


def translate_text(text, translator, source_setting, max_chars):
    chunks = split_long_text(text, max_chars)
    output = []

    for chunk in chunks:
        source = (
            source_setting
            if source_setting in ("zh", "en")
            else "auto"
        )

        output.append(
            translator.translate(chunk, source)
        )

    return "".join(output)


def load_checkpoint(path):
    if path.exists():
        try:
            return json.loads(
                path.read_text(encoding="utf-8")
            )
        except Exception:
            pass

    return {}


def save_checkpoint(path, data):
    temporary = path.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8"
    )

    temporary.replace(path)


def extract_txt_chapters(path):
    text = path.read_text(
        encoding="utf-8-sig",
        errors="replace"
    )

    pattern = re.compile(
        r"(?im)^\s*(?:"
        r"chapter\b.*|chap\.?\s*\d+.*|"
        r"第.{1,20}[章节回卷集].*|"
        r"卷\s*\d+.*|序章.*|终章.*|番外.*|"
        r"prologue.*|epilogue.*"
        r")$"
    )

    lines = text.splitlines(keepends=True)
    blocks = []
    current = []

    for line in lines:
        if pattern.match(line.strip()) and current:
            blocks.append("".join(current))
            current = []

        current.append(line)

    if current:
        blocks.append("".join(current))

    if len(blocks) <= 1:
        blocks = re.split(r"(?<=\n)\s*(?=\n)", text)
        blocks = [block for block in blocks if block.strip()]

    if not blocks:
        blocks = [text]

    return blocks


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
    translated = {}

    words = sum(
        int(value.get("words", 0))
        for value in completed.values()
        if isinstance(value, dict)
    )

    errors = 0

    for index, chapter in enumerate(chapters):
        if cancelled():
            break

        key = str(index)

        if key in completed and "text" in completed[key]:
            translated[index] = completed[key]["text"]

            progress(
                index + 1,
                len(chapters),
                words,
                f"Chapter {index + 1}",
                index + 1,
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

            translated[index] = result
            word_count = len(chapter.split())
            words += word_count

            completed[key] = {
                "text": result,
                "words": word_count
            }

            save_checkpoint(
                checkpoint_path,
                {"items": completed}
            )

        except Exception:
            errors += 1

            # Keep original content instead of losing the chapter.
            translated[index] = chapter

            completed[key] = {
                "text": chapter,
                "words": len(chapter.split()),
                "error": True
            }

            save_checkpoint(
                checkpoint_path,
                {"items": completed}
            )

        progress(
            index + 1,
            len(chapters),
            words,
            f"Chapter {index + 1}/{len(chapters)}",
            len(completed),
            errors
        )

    output_path.write_text(
        "".join(
            translated.get(index, chapters[index])
            for index in range(len(chapters))
        ),
        encoding="utf-8"
    )


def _translate_html(content, translator, source_setting, max_chars):
    soup = BeautifulSoup(content, "html.parser")
    nodes = []

    for node in soup.find_all(string=True):
        if node.parent and node.parent.name in {
            "script", "style", "code", "pre"
        }:
            continue

        if str(node).strip():
            nodes.append(node)

    for node in nodes:
        original = str(node)

        try:
            translated = translate_text(
                original,
                translator,
                source_setting,
                max_chars
            )

            node.replace_with(NavigableString(translated))

        except Exception:
            # Leave original text if this particular node fails.
            continue

    return str(soup).encode("utf-8")


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

    documents = [
        item for item in book.get_items()
        if item.get_type() == ITEM_DOCUMENT
    ]

    total = len(documents)

    words = sum(
        int(value.get("words", 0))
        for value in completed.values()
        if isinstance(value, dict)
    )

    errors = 0

    for index, item in enumerate(documents):
        if cancelled():
            break

        key = item.get_id()

        if key in completed and "content" in completed[key]:
            item.set_content(
                completed[key]["content"].encode("utf-8")
            )

            progress(
                index + 1,
                total,
                words,
                item.get_name(),
                index + 1,
                errors
            )
            continue

        try:
            original = item.get_content().decode(
                "utf-8",
                errors="replace"
            )

            translated_bytes = _translate_html(
                original,
                translator,
                source_setting,
                max_chars
            )

            item.set_content(translated_bytes)

            word_count = len(
                BeautifulSoup(
                    original, "html.parser"
                ).get_text(" ").split()
            )

            words += word_count

            completed[key] = {
                "content": translated_bytes.decode("utf-8"),
                "words": word_count
            }

            save_checkpoint(
                checkpoint_path,
                {"items": completed}
            )

        except Exception:
            errors += 1

            original = item.get_content().decode(
                "utf-8",
                errors="replace"
            )

            completed[key] = {
                "content": original,
                "words": 0,
                "error": True
            }

            save_checkpoint(
                checkpoint_path,
                {"items": completed}
            )

        progress(
            index + 1,
            total,
            words,
            item.get_name(),
            len(completed),
            errors
        )

    epub.write_epub(str(output_path), book)


def split_text_file(source_path, zip_path, max_bytes=5 * 1024 * 1024):
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
