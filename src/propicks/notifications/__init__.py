"""Notifications Telegram (Phase 4).

Due responsabilità:

1. **Outbound**: dispatcher che polla ``alerts`` con ``delivered=0``, invia
   via Telegram, marca come delivered. Decoupled dallo scheduler.

2. **Inbound**: command handlers per ``/status``, ``/alerts``, ``/ack``, ecc.
   Leggono il DB direttamente via le API esistenti di ``io/``.

Sicurezza: bot token e chat_id whitelist via env (``PROPICKS_TELEGRAM_*``).
Richieste da chat non autorizzati vengono ignorate silenziosamente.

Dipendenza opzionale: ``python-telegram-bot>=20`` via extras ``[telegram]``.
Il resto del framework funziona senza — la Phase 3 scheduler continua a
popolare ``alerts``, il CLI ``propicks-scheduler alerts`` resta disponibile.
"""

from propicks.notifications.formatter import alert_to_markdown

__all__ = ["alert_to_markdown"]
