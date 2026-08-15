import os
import asyncio
import yt_dlp

from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from pytgcalls import PyTgCalls, filters as fl
from pytgcalls.types import MediaStream, StreamEnded

load_dotenv("/root/musicbot/.env")

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
TELETHON_SESSION = os.getenv("TELETHON_SESSION")

DOWNLOAD_DIR = "/root/musicbot/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# =========================
# TELEGRAM BOT
# =========================
bot = TelegramClient(
    "music_bot_session",
    API_ID,
    API_HASH
)

# =========================
# USER ACCOUNT / ASSISTANT
# =========================
assistant = TelegramClient(
    StringSession(TELETHON_SESSION),
    API_ID,
    API_HASH
)

calls = PyTgCalls(assistant)


# =========================
# DOWNLOAD MEDIA
# =========================
def download_media(query, video=False):
    import subprocess

    if not query.startswith(("http://", "https://")):
        query = f"ytsearch1:{query}"

    if video:
        fmt = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        extra = [
            "--merge-output-format", "mp4",
        ]
        output = f"{DOWNLOAD_DIR}/%(id)s.%(ext)s"
    else:
        fmt = "bestaudio/best"
        extra = [
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "192K",
        ]
        output = f"{DOWNLOAD_DIR}/%(id)s.%(ext)s"

    cmd = [
        "yt-dlp",
        "-f", fmt,
        "--no-playlist",
        "--no-warnings",
        "--quiet",
        "--print", "before_dl:%(title)s",
        "--print", "before_dl:%(duration)s",
        "--print", "before_dl:%(thumbnail)s",
        "--print", "after_move:filepath",
        "-o", output,
    ]

    cmd.extend(extra)
    cmd.append(query)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )

    lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    ]

    if len(lines) < 4:
        raise RuntimeError(
            "yt-dlp metadata/output incomplete."
        )

    title = lines[0]
    duration_raw = lines[1]
    thumbnail = lines[2]
    path = lines[-1]

    if not os.path.exists(path):
        raise RuntimeError(
            f"Downloaded file nahi mili: {path}"
        )

    try:
        duration = int(float(duration_raw))
    except Exception:
        duration = 0

    return title, path, thumbnail, duration


def download_audio(query):
    return download_media(query, video=False)


def download_video(query):
    return download_media(query, video=True)


# =========================
# NOW PLAYING / QUEUE
# =========================
playing = {}
queues = {}
ui_messages = {}


def format_time(seconds):
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


def requester_text(item):
    name = item.get("requester_name", "Unknown")

    # Markdown-safe clickable Telegram profile link.
    safe_name = (
        str(name)
        .replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )

    user_id = item.get("requester_id")

    if user_id:
        return f"[{safe_name}](tg://user?id={user_id})"

    return safe_name


def now_playing_text(chat_id):
    item = playing.get(chat_id)

    if not item:
        return (
            "🎧 **AJ Music Bot**\n\n"
            "📭 Nothing is playing."
        )

    mode = "🎬 VIDEO" if item.get("video") else "🎵 AUDIO"

    return (
        f"{mode} **NOW PLAYING**\n\n"
        f"🎶 **{item['title']}**\n"
        f"⏱️ `{format_time(item.get('duration', 0))}`\n\n"
        f"👤 **Requested by:** {requester_text(item)}\n\n"
        "🎧 **AJ Music Bot**"
    )


def now_playing_buttons():
    return [
        [
            Button.inline("⏸️ Pause", b"pause"),
            Button.inline("▶️ Resume", b"resume"),
        ],
        [
            Button.inline("⏭️ Skip", b"skip"),
            Button.inline("⏹️ Stop", b"stop"),
        ],
    ]


async def update_now_playing(chat_id):
    # Live playback timer intentionally disabled.
    # Telegram message will NOT be edited every few seconds.
    return


async def send_now_playing(chat_id, item, old_message=None):
    text = now_playing_text(chat_id)
    buttons = now_playing_buttons()

    thumbnail = item.get("thumbnail")

    if thumbnail and thumbnail.startswith(("http://", "https://")):
        try:
            if old_message:
                await old_message.delete()

            sent = await bot.send_file(
                chat_id,
                thumbnail,
                caption=text,
                buttons=buttons
            )

            ui_messages[chat_id] = sent.id
            return

        except Exception as e:
            print("⚠️ THUMBNAIL ERROR:", repr(e), flush=True)

    if old_message:
        await old_message.edit(
            text,
            buttons=buttons
        )
        ui_messages[chat_id] = old_message.id
        return

    sent = await bot.send_message(
        chat_id,
        text,
        buttons=buttons
    )

    ui_messages[chat_id] = sent.id


async def start_song(chat_id, item, old_message=None):
    # Delete this song's separate queue message when it starts.
    queue_message_id = item.get("queue_message_id")

    if queue_message_id:
        try:
            await bot.delete_messages(
                chat_id,
                queue_message_id
            )
        except Exception:
            pass

        item.pop("queue_message_id", None)

    # Delete previous NOW PLAYING message when switching songs.
    current_ui_id = ui_messages.get(chat_id)

    if current_ui_id:
        try:
            await bot.delete_messages(
                chat_id,
                current_ui_id
            )
        except Exception:
            pass

        ui_messages.pop(chat_id, None)

    # Play audio/video.
    await calls.play(
        chat_id,
        MediaStream(item["path"])
    )

    item["position"] = 0
    item["paused"] = False

    playing[chat_id] = item

    # Send a fresh NOW PLAYING message.
    await send_now_playing(
        chat_id,
        item,
        old_message
    )


@calls.on_update(fl.stream_end())
async def pytgcalls_update_handler(update):
    chat_id = update.chat_id

    print(
        "🎵 STREAM ENDED:",
        chat_id,
        flush=True
    )

    # Remove the finished song immediately.
    finished = playing.pop(chat_id, None)

    if finished:
        print(
            "✅ FINISHED:",
            finished.get("title"),
            flush=True
        )

        # Delete downloaded media after playback finishes.
        media_path = finished.get("path")

        if media_path:
            try:
                media_file = Path(media_path)

                if media_file.exists():
                    media_file.unlink()
                    print(
                        "🗑️ DELETED:",
                        str(media_file),
                        flush=True
                    )

            except Exception as delete_error:
                print(
                    "⚠️ FILE DELETE ERROR:",
                    repr(delete_error),
                    flush=True
                )

    # Delete old NOW PLAYING message.
    old_ui_id = ui_messages.pop(chat_id, None)

    if old_ui_id:
        try:
            await bot.delete_messages(
                chat_id,
                old_ui_id
            )
        except Exception:
            pass

    queue = queues.get(chat_id, [])

    # No next song.
    if not queue:
        queues.pop(chat_id, None)

        print(
            "📭 QUEUE EMPTY:",
            chat_id,
            flush=True
        )
        return

    # Get next song ONLY after current stream ended.
    next_item = queue.pop(0)

    print(
        "⏭️ AUTO NEXT:",
        next_item.get("title"),
        flush=True
    )

    try:
        await start_song(
            chat_id,
            next_item
        )
    except Exception as e:
        print(
            "❌ AUTO NEXT ERROR:",
            repr(e),
            flush=True
        )


# =========================
# DEBUG - EVERY MESSAGE

# =========================
@bot.on(events.NewMessage)
async def debug_all(event):
    print(
        "📩 RECEIVED:",
        repr(event.raw_text),
        "CHAT:",
        event.chat_id,
        flush=True
    )


# =========================
# START
# =========================
@bot.on(events.NewMessage(pattern=r"^/start(?:\s|$)"))
async def start(event):
    print("🚀 START RECEIVED:", event.chat_id, flush=True)

    await event.respond(
        "🎵 **AJ Music Bot Online!**\n\n"
        "`/play song name`\n"
        "`/play YouTube URL`\n"
        "`/stop`"
    )


# =========================
# PLAY
# =========================
async def _handle_play(event, video=False):
    command = "/vplay" if video else "/play"

    print(
        f"🎵 {command.upper()} RECEIVED:",
        repr(event.raw_text),
        flush=True
    )

    parts = event.raw_text.split(maxsplit=1)

    if len(parts) < 2:
        await event.respond(
            "❌ **Song name ya YouTube URL do.**"
        )
        return

    query = parts[1].strip()

    status = await event.respond(
        "🔎 **Searching...**\n"
        "⬇️ **Downloading...**\n"
        "🎧 **Preparing your media...**"
    )

    try:
        if video:
            title, path, thumbnail, duration = await asyncio.to_thread(
                download_video,
                query
            )
        else:
            title, path, thumbnail, duration = await asyncio.to_thread(
                download_audio,
                query
            )

        sender = await event.get_sender()

        requester_name = "Unknown"
        requester_id = None

        if sender:
            requester_id = getattr(sender, "id", None)

            try:
                requester_name = (
                    sender.first_name
                    or sender.username
                    or "Unknown"
                )

                if getattr(sender, "last_name", None):
                    requester_name += (
                        f" {sender.last_name}"
                    )
            except Exception:
                requester_name = (
                    getattr(sender, "username", None)
                    or "Unknown"
                )

        item = {
            "title": title,
            "path": path,
            "thumbnail": thumbnail,
            "duration": duration,
            "position": 0,
            "paused": False,
            "video": video,
            "requester_name": requester_name,
            "requester_id": requester_id,
        }

        chat_id = event.chat_id

        # ====================================================
        # QUEUE
        # ====================================================
        if chat_id in playing:
            queue = queues.setdefault(chat_id, [])
            queue.append(item)

            position = len(queue)

            await status.edit(
                "📋 **ADDED TO QUEUE**\n\n"
                f"{'🎬' if video else '🎵'} **{title}**\n"
                f"⏱️ `{format_time(duration)}`\n\n"
                f"🔢 **Queue position: #{position}**\n\n"
                f"👤 **Requested by:** {requester_text(item)}\n\n"
                "🎧 **AJ Music Bot**"
            )

            item["queue_message_id"] = status.id

            print(
                f"📋 QUEUED #{position}:",
                title,
                flush=True
            )

            return

        # ====================================================
        # START IMMEDIATELY
        # ====================================================
        await start_song(
            chat_id,
            item,
            status
        )

        print(
            "▶️ PLAYING:",
            title,
            "| VIDEO:",
            video,
            "| DURATION:",
            duration,
            flush=True
        )

    except Exception as e:
        print(
            "❌ PLAY ERROR:",
            repr(e),
            flush=True
        )

        try:
            await status.edit(
                f"❌ **Error:**\n`{str(e)[:1500]}`"
            )
        except Exception:
            pass


@bot.on(events.NewMessage(pattern=r"^/play(?:\s|$)"))
async def play(event):
    await _handle_play(event, video=False)


@bot.on(events.NewMessage(pattern=r"^/vplay(?:\s|$)"))
async def vplay(event):
    await _handle_play(event, video=True)


# =========================
# STOP
# =========================
@bot.on(events.NewMessage(pattern=r"^/stop(?:\s|$)"))
async def stop(event):
    chat_id = event.chat_id

    print(
        "⏹️ STOP RECEIVED:",
        chat_id,
        flush=True
    )

    try:
        playing.pop(chat_id, None)
        queues.pop(chat_id, None)
        ui_messages.pop(chat_id, None)

        await calls.leave_call(chat_id)

        await event.respond(
            "⏹️ **Music stopped.**\n\n"
            "🗑️ **Queue cleared.**\n"
            "🎧 **AJ Music Bot**"
        )

    except Exception as e:
        print(
            "❌ STOP ERROR:",
            repr(e),
            flush=True
        )

        await event.respond(
            f"❌ `{str(e)[:1000]}`"
        )


# =========================
# NOW PLAYING BUTTONS
# =========================
@bot.on(events.CallbackQuery)
async def now_playing_buttons_handler(event):
    action = event.data.decode()
    chat_id = event.chat_id

    try:
        if action == "pause":
            await calls.pause(chat_id)

            if chat_id in playing:
                playing[chat_id]["paused"] = True

            await event.answer("⏸️ Paused")

        elif action == "resume":
            await calls.resume(chat_id)

            if chat_id in playing:
                playing[chat_id]["paused"] = False

            await event.answer("▶️ Resumed")

        elif action == "stop":
            task = ui_tasks.pop(chat_id, None)

            if task:
                task.cancel()

            playing.pop(chat_id, None)
            queues.pop(chat_id, None)
            ui_messages.pop(chat_id, None)

            await calls.leave_call(chat_id)

            await event.edit(
                "⏹️ **Music stopped.**\n\n"
                "🗑️ **Queue cleared.**\n"
                "🎧 **AJ Music Bot**"
            )

            await event.answer("Stopped")

        elif action == "skip":
            if chat_id not in playing:
                await event.answer(
                    "📭 Nothing is playing.",
                    alert=True
                )
                return

            queue = queues.get(chat_id, [])

            if not queue:
                await event.answer(
                    "📭 Queue empty hai.",
                    alert=True
                )
                return

            # Remove current song.
            playing.pop(chat_id, None)

            # Start next queued song immediately.
            next_item = queue.pop(0)

            await start_song(
                chat_id,
                next_item
            )

            await event.answer(
                "⏭️ Next song playing!"
            )

    except Exception as e:
        print(
            "❌ BUTTON ERROR:",
            repr(e),
            flush=True
        )

        await event.answer(
            "❌ Action failed",
            alert=True
        )


# =========================
# MAIN

# =========================
async def main():
    print("🚀 Starting bot...", flush=True)

    await bot.start(bot_token=BOT_TOKEN)

    me = await bot.get_me()

    print(
        f"🤖 Bot started: @{me.username} | ID: {me.id}",
        flush=True
    )

    await assistant.start()

    print(
        "👤 Assistant started.",
        flush=True
    )

    await calls.start()

    print(
        "🎵 AJ Music Bot + VC Started!",
        flush=True
    )

    print(
        "👂 Waiting for Telegram updates...",
        flush=True
    )

    try:
        await asyncio.Event().wait()

    finally:
        print("🛑 Shutting down...", flush=True)

        await calls.stop()
        await assistant.disconnect()
        await bot.disconnect()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("🛑 Bot stopped.", flush=True)
    finally:
        loop.close()
