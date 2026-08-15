# Userbot deletion worker

Telegram bots cannot delete group messages older than **48 hours**
(`message can't be deleted for everyone`). User accounts have no such limit,
so this Telethon worker deletes those old messages **on behalf of your user
account**.

> ⚠️ **Terms of Service**: userbots are against Telegram's ToS. This worker
> only deletes messages (it never reads chats or sends anything), which keeps
> the risk low, but your account could still be restricted by Telegram. Use at
> your own risk.

## How it works

1. When the management bot deletes an approved design and hits the 48h wall,
   it records the `(chat_id, message_id)` in the `userbot_deletion_queue`
   table (same MySQL database the management bot uses) and tells you how many
   messages were handed to the userbot.
2. This worker polls that table every few seconds, deletes the messages with
   your user account (with pacing to avoid flood limits) and marks them done.
3. `/userbotqueue` (sudo command in the management bot) shows queue status and
   any failed rows.

Both processes run on the same server, so the worker simply reads the same
`.env` as the management bot (`MAIN_DB_*`) plus the `USERBOT_*` values below.

## Requirements

```bash
pip install telethon
```

## Setup

1. Create Telegram API credentials at <https://my.telegram.org> → *API
   development tools*. Add them to the management bot's `.env`:

   ```env
   USERBOT_API_ID=1234567
   USERBOT_API_HASH=your_api_hash_here
   ```

2. Create a session (choose one):

   - **Session file** (interactive, first time only):

     ```env
     USERBOT_SESSION_FILE=/home/selfnit4/self/public/TisacaseManagement/userbot/userbot.session
     ```

     then run once from the project root:

     ```bash
     python userbot/delete_worker.py --login
     ```

     and enter the phone number + login code. The `.session` file is created.
   - **String session** (if you already have a session string from your
     existing self-bot):

     ```env
     USERBOT_SESSION=1BQANOTEuMTA4...
     ```

3. Optional tuning (defaults shown):

   ```env
   USERBOT_POLL_INTERVAL=5      # seconds between empty-queue polls
   USERBOT_BATCH_SIZE=25        # rows claimed per cycle
   USERBOT_DELETE_PACE=2.0      # seconds between two deletions (flood safety)
   USERBOT_MAX_ATTEMPTS=5       # attempts before a row is marked failed
   USERBOT_STALE_MINUTES=15     # reclaim rows stuck in 'processing'
   ```

4. Run it:

   ```bash
   python userbot/delete_worker.py        # run forever
   python userbot/delete_worker.py --once # single pass (test/cron)
   ```

### systemd unit (recommended)

`/etc/systemd/system/tisa-userbot.service`:

```ini
[Unit]
Description=TisaChap userbot deletion worker
After=network.target mysql.service

[Service]
WorkingDirectory=/home/selfnit4/self/public/TisacaseManagement
EnvironmentFile=/home/selfnit4/self/public/TisacaseManagement/.env
ExecStart=/home/selfnit4/virtualenv/self/3.12/bin/python userbot/delete_worker.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tisa-userbot
sudo journalctl -u tisa-userbot -f   # watch the logs
```

## Important notes

- Your account must be a **member of the products and print groups** — in a
  basic group any member can delete any message; in a supergroup your account
  needs delete rights (admin) to remove the bot's messages.
- The worker deletes messages posted by the bot, not by you — that is exactly
  the case the 48h limit blocked.
- Failed rows stay in the queue table and are shown by `/userbotqueue`; they
  are not retried automatically after the attempt cap — review the error
  (e.g. account not in the chat) and, if fixable, reset the row or ask Sudo to
  enqueue again.
- Never commit the `.session` file or the session string — both live only in
  the server's `.env` (which is already git-ignored).
