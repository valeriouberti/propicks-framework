"""Telegram bot daemon — async application wiring.

Combina:
1. **Command handlers** (``/status``, ``/alerts``, ecc.) via
   ``CommandHandler`` di python-telegram-bot.
2. **Alert dispatcher** — task asyncio concorrente che polla ``alerts``
   DB ogni N secondi e invia i pending.
3. **Auth middleware** — pre-filtra i comandi da chat non whitelisted.

Event loop: ``asyncio.run(_run())`` — graceful su Ctrl+C e SIGTERM.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

from propicks.notifications.bot_commands import COMMANDS, is_authorized
from propicks.notifications.dispatcher import dispatch_pending
from propicks.obs.log import get_logger

_log = get_logger("notifications.bot")


def _get_env_config() -> dict:
    """Legge config dall'env. Solleva ValueError se mancante."""
    token = os.environ.get("PROPICKS_TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids_raw = os.environ.get("PROPICKS_TELEGRAM_CHAT_ID", "").strip()
    if not token:
        raise ValueError(
            "PROPICKS_TELEGRAM_BOT_TOKEN non settato. Crea un bot con "
            "@BotFather e imposta la variabile (vedi CLAUDE.md Phase 4)."
        )
    if not chat_ids_raw:
        raise ValueError(
            "PROPICKS_TELEGRAM_CHAT_ID non settato. Invia /start al bot "
            "e usa @userinfobot per ottenere il tuo chat_id, poi imposta "
            "la variabile (CSV per chat multipli)."
        )
    chat_ids = [x.strip() for x in chat_ids_raw.split(",") if x.strip()]
    poll_interval = int(os.environ.get("PROPICKS_TELEGRAM_POLL_INTERVAL", "60"))
    return {"token": token, "chat_ids": chat_ids, "poll_interval": poll_interval}


async def _dispatcher_loop(app, chat_ids: list[str], poll_interval: int, stop_event: asyncio.Event) -> None:
    """Task concorrente: ogni ``poll_interval`` chiama ``dispatch_pending``."""
    _log.info(
        "dispatcher_loop_started",
        extra={"ctx": {"poll_interval_s": poll_interval, "n_chats": len(chat_ids)}},
    )
    while not stop_event.is_set():
        try:
            result = await dispatch_pending(app.bot, chat_ids)
            if result["sent"] > 0 or result["failed"] > 0:
                _log.info("dispatcher_cycle", extra={"ctx": result})
        except Exception as exc:
            _log.error(
                "dispatcher_loop_error",
                extra={"ctx": {"error": f"{type(exc).__name__}: {exc}"}},
            )
        # Attesa interrompibile da stop_event
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
        except TimeoutError:
            pass  # normale, continua ciclo


def _build_command_callback(cmd_name: str, handler_fn):
    """Wrappa un handler puro in una callback async compatibile PTB."""
    async def callback(update, _context) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is None or not is_authorized(chat_id):
            _log.warning(
                "unauthorized_command",
                extra={"ctx": {"chat_id": str(chat_id), "cmd": cmd_name}},
            )
            return  # silenzioso — no ack a chat non-whitelist

        args = _context.args if hasattr(_context, "args") else []
        try:
            resp = handler_fn(args or [])
        except Exception as exc:
            _log.error(
                "command_handler_error",
                extra={"ctx": {"cmd": cmd_name, "error": str(exc)}},
            )
            resp = {"text": f"⚠️ Errore handler `{cmd_name}`: `{exc}`", "parse_mode": "Markdown"}

        await update.effective_chat.send_message(
            text=resp["text"],
            parse_mode=resp.get("parse_mode"),
        )
    return callback


async def _run() -> None:
    try:
        from telegram.ext import Application, CommandHandler
    except ImportError as exc:
        raise SystemExit(
            "[errore] python-telegram-bot non installato. "
            "Installa con: pip install -e '.[telegram]'"
        ) from exc

    cfg = _get_env_config()
    app = Application.builder().token(cfg["token"]).build()

    # Registra tutti i comandi
    for cmd_name, fn in COMMANDS.items():
        app.add_handler(CommandHandler(cmd_name, _build_command_callback(cmd_name, fn)))

    # Signal handling per shutdown grazioso
    stop_event = asyncio.Event()

    def _signal_handler(signum, _frame):
        print(f"\n[bot] shutdown ({signum})...", file=sys.stderr)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Avvia app + dispatcher loop + polling in parallelo
    print(
        f"[bot] avviato. Polling attivo, dispatcher ogni {cfg['poll_interval']}s.\n"
        f"Chat autorizzati: {len(cfg['chat_ids'])}\n"
        "Ctrl+C per fermare.",
        file=sys.stderr,
    )
    _log.info(
        "bot_started",
        extra={"ctx": {
            "n_chats": len(cfg["chat_ids"]),
            "n_commands": len(COMMANDS),
            "poll_interval_s": cfg["poll_interval"],
        }},
    )

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        # Task concorrente del dispatcher
        dispatcher_task = asyncio.create_task(
            _dispatcher_loop(app, cfg["chat_ids"], cfg["poll_interval"], stop_event)
        )

        # Aspetta fino a shutdown
        await stop_event.wait()

        # Cleanup
        dispatcher_task.cancel()
        try:
            await dispatcher_task
        except asyncio.CancelledError:
            pass
        await app.updater.stop()
        await app.stop()

    _log.info("bot_stopped")


def run_bot() -> int:
    """Entry point bloccante. Apre asyncio event loop."""
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 1
    return 0
