# Argos Novel Translator Telegram Bot

A Telegram bot for translating large EPUB and TXT novels into Hindi
using Argos Translate.

## Features

- EPUB and TXT input
- Chinese and English source languages
- Hindi translation output
- Automatic source-language detection
- Progress percentage and progress bar
- Completed item and word counters
- Elapsed time and estimated remaining time
- Processing speed and error counters
- Per-chapter or per-document checkpoints
- Resume support when the same file is uploaded again
- TXT splitting into numbered files
- Docker deployment support
- GitHub Actions workflow

## Requirements

- Python 3.12
- Telegram bot token
- Internet access to install Python packages and Argos models
- Sufficient disk space and CPU resources

## GitHub Setup

1. Create a GitHub repository.
2. Upload all project files.
3. Open Settings.
4. Select Secrets and variables.
5. Select Actions.
6. Create a repository secret named BOT_TOKEN.
7. Set its value to your Telegram bot token.
8. Open Actions.
9. Select Argos Novel Translator Bot.
10. Click Run workflow.

## Important Translation Model Note

The Argos model installer attempts to install available direct models
for English, Chinese and Hindi.

Direct Chinese-to-Hindi model availability may vary.

Before translating a full novel, confirm that the installed Argos
models provide the required translation route.

If a route is unavailable, install compatible intermediate language
models where supported.

## Persistent Deployment

GitHub-hosted runners are temporary. Files saved in their workspace
are not automatically retained across separate workflow runs.

For reliable checkpoint retention, use a persistent server or Docker
deployment with the work directory mounted as persistent storage.

## Docker Setup

1. Copy .env.example to .env.
2. Put your Telegram bot token in .env.
3. Build and start the service:

   docker compose up -d --build

4. View logs:

   docker compose logs -f

## Environment Variables

BOT_TOKEN
    Telegram bot token.

WORKDIR
    Working directory. Default: work.

WORKERS
    Worker configuration. Default: 2.

CHUNK_CHARS
    Maximum text chunk size. Default: 1800 characters.

MAX_FILE_MB
    Input file size limit. Default: 45 MB.

MAX_OUTPUT_MB
    Output size threshold before ZIP compression. Default: 45 MB.

UPDATE_SECONDS
    Progress update interval. Default: 4 seconds.

## Telegram Commands

/start
    Start the bot.

/help
    Show help.

/lang
    Choose source language.

/status
    Display current translation progress.

/cancel
    Request cancellation.

/split
    Split the most recent translated TXT output.

## Performance

Translation speed depends on CPU performance, model size, text length
and the translation direction.

Start with two workers and a moderate chunk size.

Benchmark a small novel before processing thousands of chapters.

GitHub Actions jobs have a maximum execution duration, so a single
job cannot guarantee completion of a very large novel.

Keep checkpoints on persistent storage if you need reliable resuming.
