# WhatsApp unread count

**Status:** the `WhatsAppCollector` exists and always returns `null`. The Messages page renders the WhatsApp cell with `--`.

## Why no real data

WhatsApp has no personal or desktop API. Every approach we considered fails one of: legality, reliability, or maintenance burden.

| Approach | Why we don't ship it |
|---|---|
| WhatsApp Business API | Designed for businesses; rate-limited; not free-tier; not available for personal numbers. |
| WhatsApp Web automation (Puppeteer / Selenium) | Violates WhatsApp ToS. Breaks on every UI change. Bot-detection has caused account bans. |
| Reading WhatsApp Desktop's local SQLite (`~/Library/Application Support/WhatsApp/`) | The schema is undocumented, encrypted in newer versions, and changes between releases. Highly likely to silently report stale data. |
| macOS NotificationCenter SQLite (`~/Library/Application Support/NotificationCenter/db2/db`) | Requires Full Disk Access. Schema moved between macOS versions. Returns *delivered* notifications, not *unread*, so the count drifts upward forever. |
| Reading another app's dock-tile badge | macOS deliberately blocks this. There is no public or private API that lets one process read another process's badge label. |

## If you really want this number

The lowest-risk option (still unofficial) is to write a small WhatsApp Web userscript that posts the unread count to a localhost endpoint dashd polls. We chose not to ship that because:

- It requires a logged-in WhatsApp Web tab to stay open.
- It still violates ToS.
- Any reliable solution would belong in its own opt-in module that's a userscript install, not a default collector.

If WhatsApp ever ships an official desktop unread API or a webhook-style hook, the slot is already there in [collectors/whatsapp.py](../agent/dashd/collectors/whatsapp.py).
