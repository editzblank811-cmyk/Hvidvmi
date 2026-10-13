import os
import re
import json
import time
import asyncio
import logging
import hashlib
import zipfile
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

from translator import ArgosTranslator
from utils import (
    format_duration, progress_bar, safe_filename, extract_txt_chapters,
    translate_txt_chapters, translate_epub, split_text_file
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("argos-novel-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WORKDIR = Path(os.getenv("WORKDIR", "work"))
WORKDIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "45"))
MAX_OUTPUT_MB = int(os.getenv("MAX_OUTPUT_MB", "45"))
WORKERS = max(1, min(4, int(os.getenv("WORKERS", "2"))))
CHUNK_CHARS = max(500, min(5000, int(os.getenv("CHUNK_CHARS", "1800"))))
UPDATE_SECONDS = max(2, int(os.getenv("UPDATE_SECONDS", "4")))

USER_SETTINGS = {}
ACTIVE_JOBS = {}
CANCELLED = set()

LANGS = {
    "auto": "Auto detect (Chinese/English)",
    "zh": "Chinese",
    "en": "English",
}

HELP_TEXT = (
    "📚 Argos Novel Translator\n\n"
    "Send an EPUB or TXT file to translate it into Hindi.\n\n"
    "Commands:\n"
    "/start - Start bot\n"
    "/help - Help\n"
    "/lang - Choose source language\n"
    "/status - Current progress\n"
    "/cancel - Cancel current job\n"
    "/split - Split translated TXT into parts\n"
)


def user_dir(user_id):
    path = WORKDIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Auto detect"), KeyboardButton("Chinese")],
            [KeyboardButton("English")]
        ],
        resize_keyboard=True
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    USER_SETTINGS.setdefault(
        update.effective_user.id, {"source": "auto"}
    )

    await update.message.reply_text(
        "📚 Send a novel as EPUB or TXT.\n"
        "Translation engine: Argos Translate\n"
        "Target language: Hindi\n\n" + HELP_TEXT,
        reply_markup=settings_keyboard()
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Choose source language:",
        reply_markup=settings_keyboard()
    )


async def language_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()

    mapping = {
        "auto detect": "auto",
        "chinese": "zh",
        "english": "en"
    }

    if text not in mapping:
        return

    source = mapping[text]
    USER_SETTINGS.setdefault(update.effective_user.id, {})["source"] = source

    await update.message.reply_text(
        f"Source: {LANGS[source]}\nTarget: Hindi",
        reply_markup=settings_keyboard()
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    job = ACTIVE_JOBS.get(update.effective_user.id)

    if not job:
        await update.message.reply_text("No active translation job.")
        return

    await update.message.reply_text(job.get("status_text", "Preparing..."))


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if uid in ACTIVE_JOBS:
        CANCELLED.add(uid)
        await update.message.reply_text(
            "⏹ Cancellation requested. Saving completed items."
        )
    else:
        await update.message.reply_text("No active job.")


async def split_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    directory = user_dir(update.effective_user.id)

    candidates = sorted(
        directory.rglob("*_hi.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    if not candidates:
        await update.message.reply_text("No translated TXT output found.")
        return

    source = candidates[0]
    zip_path = source.with_name(source.stem + "_parts.zip")

    try:
        await asyncio.to_thread(
            split_text_file, source, zip_path, 5 * 1024 * 1024
        )

        with zip_path.open("rb") as file:
            await update.message.reply_document(
                document=file,
                filename=zip_path.name,
                caption="✅ TXT split into numbered parts."
            )

    except Exception as exc:
        log.exception("Split failed")
        await update.message.reply_text(f"Split failed: {exc}")


async def update_progress(app, chat_id, state, force=False):
    now = time.monotonic()

    if (
        not force
        and now - state.get("last_update", 0) < UPDATE_SECONDS
    ):
        return

    state["last_update"] = now

    done = int(state.get("done", 0))
    total = max(1, int(state.get("total", 1)))
    percent = min(100, done * 100 / total)

    elapsed = now - state["started"]
    speed = done / elapsed if elapsed else 0
    eta = (total - done) / speed if speed > 0 else 0

    status = (
        f"⚙️ Translation progress\n"
        f"{progress_bar(percent, 18)} {percent:.1f}%\n\n"
        f"📖 Items: {done:,}/{total:,}\n"
        f"📝 Words: {state.get('words', 0):,}\n"
        f"🚀 Speed: {speed:.2f} items/sec\n"
        f"⏱ Elapsed: {format_duration(elapsed)}\n"
        f"🕒 ETA: {format_duration(eta)}\n"
        f"🔤 Source: {state.get('source_label', 'Auto')} → Hindi\n"
        f"📍 Current: {state.get('current', 'Preparing')}\n"
        f"♻️ Resumed: {state.get('resumed', 0):,}\n"
        f"⚠️ Errors: {state.get('errors', 0):,}"
    )

    state["status_text"] = status
    message_id = state.get("message_id")

    if message_id:
        try:
            await app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=status
            )
        except Exception as exc:
            if "Message is not modified" not in str(exc):
                log.debug("Progress update skipped: %s", exc)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    document = message.document
    uid = update.effective_user.id

    if uid in ACTIVE_JOBS:
        await message.reply_text(
            "A translation is already running. Use /status or /cancel."
        )
        return

    filename = safe_filename(document.file_name or "novel.txt")
    suffix = Path(filename).suffix.lower()

    if suffix not in {".epub", ".txt"}:
        await message.reply_text("Supported formats: EPUB and TXT.")
        return

    if document.file_size and document.file_size > MAX_FILE_MB * 1024 * 1024:
        await message.reply_text(
            f"File exceeds the configured {MAX_FILE_MB} MB limit."
        )
        return

    directory = user_dir(uid)
    incoming = directory / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)

    temp_path = incoming / f"{int(time.time())}_{filename}"
    progress_message = await message.reply_text("⬇️ Downloading file...")

    try:
        tg_file = await context.bot.get_file(document.file_id)
        await tg_file.download_to_drive(custom_path=temp_path)

        if temp_path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
            raise ValueError("Downloaded file exceeds configured size limit.")

        digest = hashlib.sha256()

        with temp_path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)

        job_dir = directory / ("job_" + digest.hexdigest()[:20])
        job_dir.mkdir(parents=True, exist_ok=True)

        input_path = job_dir / filename

        if input_path.exists():
            temp_path.unlink(missing_ok=True)
        else:
            temp_path.replace(input_path)

    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        await progress_message.edit_text(f"Download failed: {exc}")
        return

    source = USER_SETTINGS.get(uid, {}).get("source", "auto")

    state = {
        "started": time.monotonic(),
        "done": 0,
        "total": 1,
        "words": 0,
        "errors": 0,
        "resumed": 0,
        "current": "Loading models",
        "source_label": LANGS.get(source, "Auto detect"),
        "message_id": progress_message.message_id,
        "status_text": "Preparing..."
    }

    ACTIVE_JOBS[uid] = state
    CANCELLED.discard(uid)

    task = asyncio.create_task(
        process_file(
            context.application, uid, message.chat_id,
            input_path, job_dir, filename, suffix, source, state
        )
    )

    state["task"] = task


async def process_file(
    app, uid, chat_id, input_path, job_dir,
    filename, suffix, source, state
):
    output_path = None

    try:
        translator = await asyncio.to_thread(ArgosTranslator)

        if source == "zh":
            translator.get_route("zh", "hi")
        elif source == "en":
            translator.get_route("en", "hi")
        else:
            if not (
                translator.has_route("zh", "hi")
                and translator.has_route("en", "hi")
            ):
                raise RuntimeError(
                    "Required translation route is unavailable. "
                    "Check the installed Argos language models."
                )

        stem = Path(filename).stem
        checkpoint = job_dir / "checkpoint.json"

        def progress(done, total, words, current, resumed, errors):
            state.update(
                done=done,
                total=total,
                words=words,
                current=current,
                resumed=resumed,
                errors=errors
            )

        if suffix == ".txt":
            chapters = extract_txt_chapters(input_path)
            state["total"] = len(chapters)

            output_path = job_dir / f"{stem}_hi.txt"

            await asyncio.to_thread(
                translate_txt_chapters,
                chapters, output_path, checkpoint, translator,
                source, WORKERS, CHUNK_CHARS, progress,
                lambda: uid in CANCELLED
            )

        else:
            output_path = job_dir / f"{stem}_hi.epub"

            await asyncio.to_thread(
                translate_epub,
                input_path, output_path, checkpoint, translator,
                source, WORKERS, CHUNK_CHARS, progress,
                lambda: uid in CANCELLED
            )

        if uid in CANCELLED:
            await app.bot.send_message(
                chat_id,
                "⏹ Job cancelled. Completed items were checkpointed."
            )
            return

        if not output_path.exists():
            raise RuntimeError("No output file was produced.")

        size_mb = output_path.stat().st_size / (1024 * 1024)

        if size_mb <= MAX_OUTPUT_MB:
            await app.bot.send_chat_action(
                chat_id, ChatAction.UPLOAD_DOCUMENT
            )

            with output_path.open("rb") as handle:
                await app.bot.send_document(
                    chat_id=chat_id,
                    document=handle,
                    filename=output_path.name,
                    caption=(
                        f"✅ Translation complete\n"
                        f"📖 Items: {state['done']:,}/{state['total']:,}\n"
                        f"📝 Words: {state['words']:,}\n"
                        f"⏱ Time: {format_duration(time.monotonic()-state['started'])}"
                    )
                )
        else:
            zip_path = job_dir / f"{output_path.stem}.zip"

            with zipfile.ZipFile(
                zip_path, "w", zipfile.ZIP_DEFLATED
            ) as archive:
                archive.write(output_path, arcname=output_path.name)

            with zip_path.open("rb") as handle:
                await app.bot.send_document(
                    chat_id=chat_id,
                    document=handle,
                    filename=zip_path.name,
                    caption="✅ Complete. Output has been compressed."
                )

        await update_progress(app, chat_id, state, force=True)

    except Exception as exc:
        log.exception("Translation failed")

        await app.bot.send_message(
            chat_id,
            f"❌ Translation failed: {exc}\n"
            "Check the installed Argos models and logs."
        )

    finally:
        ACTIVE_JOBS.pop(uid, None)
        CANCELLED.discard(uid)


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Set BOT_TOKEN in GitHub Actions Secrets."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("lang", lang_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("split", split_cmd))

    app.add_handler(
        MessageHandler(
            filters.Regex(r"^(Auto detect|Chinese|English)$"),
            language_button
        )
    )

    app.add_handler(
        MessageHandler(filters.Document.ALL, handle_document)
    )

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
