# ⚡ MARCOxUPLODER — Direct Link Uploader (no PW login needed)
# Send a download link (video / PDF / any file) or a .txt file full of links,
# the bot downloads each one and uploads it straight to Telegram.
import asyncio
import logging
import mimetypes
import os
import re
import shutil
import time
import urllib.parse
import uuid

import aiofiles
import aiohttp
import yt_dlp
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import Config
from database.mongo_db import is_premium_user

logger = logging.getLogger(__name__)

BRAND_CAPTION = "\n\n⚡ **Powered by Marco** | @officialmarco22"
MAX_LINKS = 200
MAX_SIZE = 2000 * 1024 * 1024  # Telegram bot upload limit (~2 GB)
DOWNLOAD_ROOT = "direct_downloads"
URL_RE = re.compile(r"https?://[^\s<>\"'`]+")
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".flv", ".3gp", ".ts")
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
STREAM_HINTS = (
    ".m3u8", ".mpd", "youtu", "vimeo.com", "dailymotion", "instagram.com",
    "facebook.com", "fb.watch", "twitter.com", "x.com/", "tiktok.com",
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
DEFAULT_QUALITY = "720"

ACTIVE_DIRECT = set()


class DirectCancelled(Exception):
    pass


class DirectError(Exception):
    pass


# ---------------- helpers ----------------
def humanbytes(size):
    if not size:
        return "0 B"
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def progress_bar(pct):
    filled = int(12 * min(pct, 100) // 100)
    return "▓" * filled + "░" * (12 - filled)


def safe_name(name, limit=120):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name or "").strip(" .")
    name = re.sub(r"\s+", " ", name)
    return name[:limit].strip() or "file"


def clean_url(url):
    return url.rstrip(").,];>'\"")


def title_from_url(url):
    path = urllib.parse.urlparse(url).path
    base = urllib.parse.unquote(os.path.basename(path))
    if base:
        return os.path.splitext(base)[0] or base
    return urllib.parse.urlparse(url).hostname or "file"


def parse_links(text):
    """Accepts `Title: URL` lines or bare links. Returns [{title, url}]."""
    items, seen = [], set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        urls = [clean_url(u) for u in URL_RE.findall(line)]
        if not urls:
            continue
        title = line.split(urls[0])[0].strip().strip(":-|–—>").strip()
        # With a title, the first link is the file (later ones are usually thumbnails).
        picked = [urls[0]] if title else urls
        for u in picked:
            if u in seen:
                continue
            seen.add(u)
            items.append({"title": title or title_from_url(u), "url": u})
    return items


def is_cancelled(user_id):
    import plugins.uploader as up
    return user_id in up.CANCEL_TASKS or (up.STOP_ALL_FLAG and user_id != Config.OWNER_ID)


def clear_cancel(user_id):
    import plugins.uploader as up
    while user_id in up.CANCEL_TASKS:
        up.CANCEL_TASKS.remove(user_id)


async def can_use_direct(user_id):
    if user_id == Config.OWNER_ID:
        return True
    if Config.DIRECT_UPLOAD_PREMIUM_ONLY:
        return await is_premium_user(user_id)
    return True


class Throttle:
    def __init__(self, every=6):
        self.every = every
        self.last = 0

    def ready(self, force=False):
        now = time.time()
        if force or now - self.last >= self.every:
            self.last = now
            return True
        return False


async def safe_edit(msg, text):
    try:
        await msg.edit_text(text)
    except Exception:
        pass


# ---------------- download ----------------
def filename_from_response(resp, url, title, ctype):
    name = None
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    if m:
        name = urllib.parse.unquote(m.group(1).strip())
    if not name:
        name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(str(resp.url)).path))
    ext = os.path.splitext(name or "")[1].lower()
    if not ext or len(ext) > 6:
        ext = mimetypes.guess_extension(ctype or "") or ""
        if ext == ".jpe":
            ext = ".jpg"
    base = safe_name(title) if title else safe_name(os.path.splitext(name or "file")[0])
    return f"{base}{ext}"


async def http_download(url, dest_dir, title, user_id, on_progress):
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout) as session:
        async with session.get(url, allow_redirects=True, ssl=False) as resp:
            if resp.status >= 400:
                raise DirectError(f"Server ne HTTP {resp.status} diya")
            ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype.startswith("text/html"):
                return None  # web page, let yt-dlp try
            total = int(resp.headers.get("Content-Length") or 0)
            if total > MAX_SIZE:
                raise DirectError(f"File {humanbytes(total)} ki hai, 2 GB se badi file Telegram pe nahi jaati")
            path = os.path.join(dest_dir, filename_from_response(resp, url, title, ctype))
            done = 0
            async with aiofiles.open(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    if is_cancelled(user_id):
                        raise DirectCancelled()
                    await f.write(chunk)
                    done += len(chunk)
                    if done > MAX_SIZE:
                        raise DirectError("File 2 GB se badi hai")
                    await on_progress(done, total)
            if done == 0:
                raise DirectError("Khali file mili (0 bytes)")
            return path


def _ytdlp_download(url, dest_dir, title, quality, user_id, hook):
    out_name = safe_name(title).replace("%", "%%") if title else "%(title).80s"
    q = quality or DEFAULT_QUALITY
    opts = {
        "outtmpl": os.path.join(dest_dir, f"{out_name}.%(ext)s"),
        "format": f"bv*[height<={q}]+ba/b[height<={q}]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "http_headers": HEADERS,
        "concurrent_fragment_downloads": 8,
        "max_filesize": MAX_SIZE,
        "progress_hooks": [hook],
    }
    if os.path.exists("cookies.txt"):
        opts["cookiefile"] = "cookies.txt"
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = [os.path.join(dest_dir, f) for f in os.listdir(dest_dir) if not f.endswith((".part", ".ytdl"))]
    if not files:
        raise DirectError("Video download nahi hua")
    return max(files, key=os.path.getsize)


async def ytdlp_download(url, dest_dir, title, quality, user_id, on_progress):
    loop = asyncio.get_running_loop()

    def hook(d):
        if is_cancelled(user_id):
            raise DirectCancelled()
        if d.get("status") == "downloading":
            done = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            asyncio.run_coroutine_threadsafe(on_progress(done, total), loop)

    try:
        return await asyncio.to_thread(_ytdlp_download, url, dest_dir, title, quality, user_id, hook)
    except DirectCancelled:
        raise
    except yt_dlp.utils.DownloadError as e:
        if is_cancelled(user_id):
            raise DirectCancelled()
        msg = str(e).replace("ERROR: ", "")
        raise DirectError(msg[:200])


async def download_item(item, dest_dir, quality, user_id, on_progress):
    url = item["url"]
    try:
        from plugins.bypasser import direct_link_generator
        url = await asyncio.to_thread(direct_link_generator, url) or url
    except Exception:
        pass

    if any(h in url.lower() for h in STREAM_HINTS):
        return await ytdlp_download(url, dest_dir, item["title"], quality, user_id, on_progress)

    http_error = None
    try:
        path = await http_download(url, dest_dir, item["title"], user_id, on_progress)
        if path:
            return path
    except (DirectCancelled, DirectError) as e:
        if isinstance(e, DirectCancelled) or "2 GB" in str(e):
            raise
        http_error = e
    except Exception as e:
        http_error = e
    for f in os.listdir(dest_dir):
        try:
            os.remove(os.path.join(dest_dir, f))
        except Exception:
            pass
    try:
        return await ytdlp_download(url, dest_dir, item["title"], quality, user_id, on_progress)
    except DirectError:
        if http_error:
            raise DirectError(str(http_error)[:200])
        raise


# ---------------- upload ----------------
async def upload_file(client, chat_id, path, caption, on_progress):
    from plugins.uploader import get_video_duration, get_video_thumbnail

    size = os.path.getsize(path)
    if size == 0:
        raise DirectError("Khali file mili (0 bytes)")
    if size > MAX_SIZE:
        raise DirectError(f"File {humanbytes(size)} ki hai, 2 GB se badi file Telegram pe nahi jaati")
    ext = os.path.splitext(path)[1].lower()
    caption = caption[:1020]

    if ext in VIDEO_EXT:
        thumb = await get_video_thumbnail(path)
        duration = await get_video_duration(path)
        try:
            await client.send_video(
                chat_id, video=path, caption=caption, thumb=thumb, duration=duration,
                supports_streaming=True, progress=on_progress,
            )
        finally:
            if thumb and thumb != "thumb.jpg" and os.path.exists(thumb):
                try:
                    os.remove(thumb)
                except Exception:
                    pass
    elif ext in AUDIO_EXT:
        await client.send_audio(chat_id, audio=path, caption=caption, progress=on_progress)
    elif ext in IMAGE_EXT and size < 10 * 1024 * 1024:
        try:
            await client.send_photo(chat_id, photo=path, caption=caption)
        except Exception:
            await client.send_document(chat_id, document=path, caption=caption, progress=on_progress)
    else:
        await client.send_document(chat_id, document=path, caption=caption, progress=on_progress)


# ---------------- main runner ----------------
async def run_direct_upload(client, user_id, chat_id, items, batch_name=None, quality=None):
    if user_id in ACTIVE_DIRECT:
        return await client.send_message(chat_id, "⏳ **Aapka ek direct upload already chal raha hai.** Pehle use khatam hone do ya /cancel bhejo.")
    if not items:
        return await client.send_message(chat_id, "❌ **Koi valid download link nahi mila!**")

    items = items[:MAX_LINKS]
    total = len(items)
    clear_cancel(user_id)
    ACTIVE_DIRECT.add(user_id)
    work_root = os.path.join(DOWNLOAD_ROOT, f"{user_id}_{uuid.uuid4().hex[:8]}")
    os.makedirs(work_root, exist_ok=True)
    header = f"━━━ ⚡ **ᴍᴀʀᴄᴏxᴜᴘʟᴏᴅᴇʀ** ⚡ ━━━\n"
    if batch_name:
        header += f"📚 **Batch:** `{batch_name}`\n"
    status = await client.send_message(chat_id, header + f"🔗 **Total Links:** `{total}`\n⏳ Shuru kar raha hu...")

    ok, failed, cancelled = 0, [], False
    try:
        for idx, item in enumerate(items, 1):
            if is_cancelled(user_id):
                cancelled = True
                break
            title = item["title"]
            label = f"`[{idx}/{total}]` **{safe_name(title, 60)}**"
            item_dir = os.path.join(work_root, str(idx))
            os.makedirs(item_dir, exist_ok=True)
            throttle = Throttle()

            async def dl_progress(done, size, _label=label, _t=throttle):
                if not _t.ready(force=bool(size) and done >= size):
                    return
                pct = (done / size * 100) if size else 0
                size_txt = f"{humanbytes(done)} / {humanbytes(size)}" if size else humanbytes(done)
                await safe_edit(status, f"{header}📥 **Downloading** {_label}\n\n┠ [{progress_bar(pct)}] **{pct:.1f}%**\n┠ 📦 {size_txt}\n\n_/cancel se rok sakte ho_")

            up_throttle = Throttle()

            async def up_progress(done, size, _label=label, _t=up_throttle):
                if not _t.ready():
                    return
                pct = (done / size * 100) if size else 0
                await safe_edit(status, f"{header}📤 **Uploading** {_label}\n\n┠ [{progress_bar(pct)}] **{pct:.1f}%**\n┠ 📦 {humanbytes(done)} / {humanbytes(size)}")

            try:
                await safe_edit(status, f"{header}📥 **Downloading** {label}...")
                path = await download_item(item, item_dir, quality, user_id, dl_progress)
                index_txt = f"`{idx}.` " if total > 1 else ""
                caption = f"{index_txt}**{title}**"
                if batch_name:
                    caption += f"\n📚 **Batch:** `{batch_name}`"
                await upload_file(client, chat_id, path, caption + BRAND_CAPTION, up_progress)
                ok += 1
            except DirectCancelled:
                cancelled = True
                break
            except Exception as e:
                logger.warning(f"Direct upload failed for {item['url']}: {e}")
                failed.append(f"`{idx}.` {safe_name(title, 50)} — {str(e)[:120]}")
            finally:
                shutil.rmtree(item_dir, ignore_errors=True)
    finally:
        ACTIVE_DIRECT.discard(user_id)
        clear_cancel(user_id)
        shutil.rmtree(work_root, ignore_errors=True)

    head = "🛑 **Direct Upload Ruk Gaya!**" if cancelled else "✅ **Direct Upload Complete!**"
    summary = f"{head}\n\n✅ **Uploaded:** `{ok}`\n❌ **Failed:** `{len(failed)}`\n🔗 **Total:** `{total}`"
    if failed:
        summary += "\n\n**Failed links:**\n" + "\n".join(failed)
    summary += BRAND_CAPTION
    await safe_edit(status, summary[:4000])


# ---------------- handlers ----------------
UPLOAD_HELP = (
    "━━━ ⚡ **ᴅɪʀᴇᴄᴛ ᴜᴘʟᴏᴀᴅ** ⚡ ━━━\n\n"
    "Bina kisi login ke file Telegram pe paao:\n\n"
    "➜ **Link bhejo** ➭ Video / PDF / koi bhi file ka download link seedha chat me bhejo 🔗\n"
    "➜ **Kai links ek saath** ➭ Har link nayi line me bhejo 📋\n"
    "➜ **TXT file bhejo** ➭ Jisme `Title: Link` likha ho, phir **⚡ Instant Upload** dabao 📄\n"
    "➜ **/upload link** ➭ Command se bhi chalega\n"
    "➜ **/cancel** ➭ Chalta hua upload roko 🛑\n\n"
    "ℹ️ Video max 720p me aata hai. 2 GB se badi file Telegram nahi leta.\n"
    "⚠️ DRM / locked videos is tareeke se nahi aate."
)


async def _deny(message):
    await message.reply_text(
        "❌ **Direct upload sirf Premium Users ke liye hai.**\n\n"
        "👉 **Contact Admin:** @officialmarco22"
    )


@Client.on_message(filters.command(["upload", "url", "dl"]) & filters.private)
async def upload_command(client: Client, message: Message):
    user_id = message.chat.id
    if not await can_use_direct(user_id):
        return await _deny(message)

    reply = message.reply_to_message
    if reply and reply.document and reply.document.file_name and reply.document.file_name.lower().endswith(".txt"):
        path = await client.download_media(reply)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        finally:
            try:
                os.remove(path)
            except Exception:
                pass
        batch = reply.document.file_name[:-4].replace("_", " ")
        return await run_direct_upload(client, user_id, message.chat.id, parse_links(text), batch_name=batch)

    text = (message.text or "").split(None, 1)
    items = parse_links(text[1]) if len(text) > 1 else []
    if reply and not items:
        items = parse_links(reply.text or reply.caption or "")
    if not items:
        return await message.reply_text(UPLOAD_HELP + BRAND_CAPTION)
    await run_direct_upload(client, user_id, message.chat.id, items)


@Client.on_message(filters.private & filters.text & ~filters.regex(r"^/"), group=1)
async def link_message_handler(client: Client, message: Message):
    if message.reply_to_message:
        return  # replies are answers to other bot prompts
    if not URL_RE.search(message.text or ""):
        return
    user_id = message.chat.id
    if not await can_use_direct(user_id):
        return await _deny(message)
    await run_direct_upload(client, user_id, message.chat.id, parse_links(message.text))


@Client.on_callback_query(filters.regex(r"^directtxt$"))
async def direct_txt_callback(client: Client, callback_query):
    import plugins.uploader as up
    user_id = callback_query.message.chat.id
    if not await can_use_direct(user_id):
        return await callback_query.answer("❌ Sirf Premium users ke liye", show_alert=True)
    state = up.UPLOAD_STATE.get(user_id)
    if not state:
        return await callback_query.answer("⚠️ TXT file dobara bhejo.", show_alert=True)
    await callback_query.answer("⚡ Instant upload shuru!")
    up.ACTIVE_USERS_PROCESSING.discard(user_id)
    try:
        await callback_query.message.delete()
    except Exception:
        pass
    items = [{"title": it.get("title") or title_from_url(it["url"]), "url": it["url"]} for it in state["items"]]
    up.UPLOAD_STATE.pop(user_id, None)
    await run_direct_upload(client, user_id, user_id, items, batch_name=state.get("batch_name"))


@Client.on_callback_query(filters.regex(r"^dash:direct$"))
async def direct_help_callback(client: Client, callback_query):
    await callback_query.answer()
    await callback_query.message.reply_text(UPLOAD_HELP + BRAND_CAPTION)
