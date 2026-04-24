"""CLI ``propicks-bot`` — Telegram bot daemon + gestione queue.

Subcommands:

    propicks-bot run              # daemon polling + dispatcher (bloccante)
    propicks-bot test             # test di connettività (send 1 msg, exit)
    propicks-bot stats            # counters queue (pending/delivered/failed)
    propicks-bot reset-retries    # reset delivery_error per recovery
    propicks-bot mute-backlog     # marca pending come delivered senza inviare
                                  # (utile al primo setup, evita spam storico)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from tabulate import tabulate


def cmd_run(_args: argparse.Namespace) -> int:
    from propicks.notifications.bot import run_bot
    return run_bot()


def cmd_test(_args: argparse.Namespace) -> int:
    """Test connettività: manda 1 messaggio e esce."""
    token = os.environ.get("PROPICKS_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id_raw = os.environ.get("PROPICKS_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id_raw:
        print(
            "[errore] PROPICKS_TELEGRAM_BOT_TOKEN o PROPICKS_TELEGRAM_CHAT_ID mancanti.",
            file=sys.stderr,
        )
        return 1

    try:
        from telegram import Bot
    except ImportError:
        print(
            "[errore] python-telegram-bot non installato: pip install -e '.[telegram]'",
            file=sys.stderr,
        )
        return 1

    chat_ids = [x.strip() for x in chat_id_raw.split(",") if x.strip()]

    async def _send():
        bot = Bot(token=token)
        async with bot:
            for cid in chat_ids:
                try:
                    await bot.send_message(
                        chat_id=cid,
                        text=(
                            "✅ *Propicks Bot — test connettività OK*\n\n"
                            "Se vedi questo messaggio, il bot è configurato correttamente.\n"
                            "Lancia `propicks-bot run` per attivare il daemon."
                        ),
                        parse_mode="Markdown",
                    )
                    print(f"[ok] messaggio inviato a chat_id={cid}")
                except Exception as exc:
                    print(f"[errore] chat_id={cid}: {exc}", file=sys.stderr)
                    return 1
        return 0

    return asyncio.run(_send())


def cmd_stats(_args: argparse.Namespace) -> int:
    from propicks.notifications.dispatcher import delivery_stats

    stats = delivery_stats()
    rows = [
        ["Pending (undelivered)", stats["pending"]],
        ["Delivered oggi", stats["delivered_today"]],
        ["Failed pending", stats["failed_pending"]],
    ]
    print(tabulate(rows, tablefmt="simple"))
    if stats["failed_pending"] > 0:
        print(
            f"\n⚠️  {stats['failed_pending']} alert falliti. "
            "Dopo 3 tentativi vengono skippati. Per recuperare:\n"
            "  propicks-bot reset-retries"
        )
    return 0


def cmd_reset_retries(args: argparse.Namespace) -> int:
    from propicks.notifications.dispatcher import reset_delivery_failures

    n = reset_delivery_failures(alert_id=args.alert_id)
    if args.alert_id:
        print(f"Retry counter resettato per alert {args.alert_id}: {n} righe.")
    else:
        print(f"Retry counter resettato per {n} alert falliti.")
    return 0


def cmd_mute_backlog(_args: argparse.Namespace) -> int:
    """Marca tutti pending come delivered senza inviare. First-setup helper."""
    from propicks.notifications.dispatcher import mark_all_delivered

    n = mark_all_delivered()
    print(f"✅ {n} alert backlog marcati come delivered (non inviati).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram bot daemon + queue helpers (Phase 4).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Avvia bot daemon (bloccante)")
    p_run.set_defaults(func=cmd_run)

    p_test = sub.add_parser("test", help="Test connettività: invia 1 msg e exit")
    p_test.set_defaults(func=cmd_test)

    p_stats = sub.add_parser("stats", help="Counters queue delivery")
    p_stats.set_defaults(func=cmd_stats)

    p_reset = sub.add_parser("reset-retries", help="Reset delivery_error per recovery")
    p_reset.add_argument("--alert-id", type=int, help="Reset solo un alert specifico")
    p_reset.set_defaults(func=cmd_reset_retries)

    p_mute = sub.add_parser(
        "mute-backlog",
        help="Marca pending come delivered (first-setup, evita spam storico)",
    )
    p_mute.set_defaults(func=cmd_mute_backlog)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
