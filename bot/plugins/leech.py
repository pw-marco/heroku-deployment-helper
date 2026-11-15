import asyncio
import os
import time
import uuid
import shutil
import math
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import Message
from config import Config
from plugins.uploader import get_video_thumbnail, get_video_duration
from plugins.bypasser import direct_link_generator, DirectDownloadLinkException

# ---- GLOBAL QUEUE SYSTEM ----
LEECH_QUEUE = asyncio.Queue()
MAX_CONCURRENT_LEECHES = 2
LEECH_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_LEECHES)

# Helper to format bytes
def humanbytes(size):
    if not size: return ""
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN[n] + 'B'

def TimeFormatter(milliseconds: int) -> str:
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    tmp = ((str(days) + "d, ") if days else "") + \
        ((str(hours) + "h, ") if hours else "") + \
        ((str(minutes) + "m, ") if minutes else "") + \
        ((str(seconds) + "s, ") if seconds else "")
    return tmp[:-2] if tmp else "0s"

# Professional Progress Bar UI
async def progress_for_pyrogram(current, total, ud_type, message, start):
    now = time.time()
    diff = now - start
    if round(diff % 5.00) == 0 or current == total:
        percentage = current * 100 / (total or 1)
        speed = current / diff if diff > 0 else 0
        elapsed_time = round(diff) * 1000
        time_to_completion = round((total - current) / speed) * 1000 if speed > 0 else 0
        estimated_total_time = elapsed_time + time_to_completion

        elapsed_str = TimeFormatter(milliseconds=elapsed_time)
        eta_str = TimeFormatter(milliseconds=time_to_completion)

        bar_length = 12
        filled_length = int(bar_length * percentage // 100)
        bar = "▓" * filled_length + "░" * (bar_length - filled_length)

        progress_str = (
            f"**{ud_type}**\n\n"
            f"**┠** [{bar}] **{round(percentage, 1)}%**\n"
            f"**┠ 🚀 Speed:** {humanbytes(speed)}/s\n"
            f"**┠ ⏳ ETA:** {eta_str}\n"
            f"**┠ 📦 Size:** {humanbytes(current)} / {humanbytes(total)}\n"
        )
        try:
            await message.edit(text=progress_str)
        except Exception:
            pass

class YTDLProgressHook:
    def __init__(self, message, file_name):
        self.message = message
        self.file_name = file_name
        self.start_time = time.time()
        self.last_update_time = time.time()

    def __call__(self, d):
        if d['status'] == 'downloading':
            now = time.time()
            if now - self.last_update_time >= 5:
                self.last_update_time = now
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes', d.get('total_bytes_estimate', 0))
                speed = d.get('speed', 0)
                eta = d.get('eta', 0)
                
                percentage = (downloaded / total * 100) if total else 0
                
                bar_length = 12
                filled_length = int(bar_length * percentage // 100)
                bar = "▓" * filled_length + "░" * (bar_length - filled_length)

                progress_str = (
                    f"**📥 Downloading:** `{self.file_name}`\n\n"
                    f"**┠** [{bar}] **{round(percentage, 1)}%**\n"
                    f"**┠ 🚀 Speed:** {humanbytes(speed)}/s\n"
                    f"**┠ ⏳ ETA:** {TimeFormatter(eta * 1000)}\n"
                    f"**┠ 📦 Size:** {humanbytes(downloaded)} / {humanbytes(total)}\n"
                )
                try:
                    # Creating a background task to avoid blocking yt-dlp
                    asyncio.create_task(self.message.edit(text=progress_str))
                except Exception:
                    pass

async def process_leech_task(client: Client, message: Message, url: str, extract_flag: bool, quality: str, custom_name: str, msg: Message):
    async with LEECH_SEMAPHORE:
        temp_dir = "leech_downloads"
        os.makedirs(temp_dir, exist_ok=True)
        uid = str(uuid.uuid4())[:8]
        
        output_template = f"{temp_dir}/{uid}_%(title)s.%(ext)s"
        if custom_name:
            output_template = f"{temp_dir}/{uid}_{custom_name}.%(ext)s"
            
        format_str = 'bestvideo+bestaudio/best'
        if quality:
            # If user asks for 720, we use bestvideo[height<=720]+bestaudio/best[height<=720]
            format_str = f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best'
            
        ydl_opts = {
            'outtmpl': output_template,
            'format': format_str,
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'impersonate': 'chrome'
        }
        
        downloaded_file = None
        extract_dir = None
        try:
            await msg.edit("⏳ **Extracting link metadata...**")
            
            # BYPASS LOGIC
            try:
                # Attempt to bypass if it's a known URL format
                bypassed_url = await asyncio.to_thread(direct_link_generator, url)
                if bypassed_url != url:
                    await msg.edit("✅ **Bypass Successful!** Fetching metadata...")
                    url = bypassed_url
            except DirectDownloadLinkException as e:
                return await msg.edit(f"❌ **Bypass Failed:**\n`{str(e)}`")
            except Exception as e:
                pass # Proceed normally if bypass isn't supported or fails silently
            
            def extract_info():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return ydl.prepare_filename(info), info.get('title', 'Unknown')
                    
            file_path_expected, title = await asyncio.to_thread(extract_info)
            hook = YTDLProgressHook(msg, title)
            ydl_opts['progress_hooks'] = [hook]
            
            def download_file():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                    
            await asyncio.to_thread(download_file)
            
            # Since yt-dlp might change extension (e.g. mkv -> mp4 merge), we must find the actual downloaded file
            # Best way is to check the directory for files starting with the uid
            for file in os.listdir(temp_dir):
                if file.startswith(uid):
                    downloaded_file = os.path.join(temp_dir, file)
                    break

            if not downloaded_file or not os.path.exists(downloaded_file):
                return await msg.edit("❌ **Failed to download the file.**")
                
            files_to_upload = []
            
            if extract_flag and downloaded_file.endswith(('.zip', '.tar', '.gz', '.bz2', '.tar.gz', '.rar', '.7z')):
                await msg.edit("📦 **Extracting archive...**")
                extract_dir = f"{temp_dir}/{uid}_extracted"
                os.makedirs(extract_dir, exist_ok=True)
                try:
                    await asyncio.to_thread(shutil.unpack_archive, downloaded_file, extract_dir)
                    for root, _, files in os.walk(extract_dir):
                        for file in files:
                            files_to_upload.append(os.path.join(root, file))
                except Exception as ex:
                    await msg.edit(f"❌ **Extraction failed:** `{str(ex)}`\nWill upload the original archive instead.")
                    files_to_upload = [downloaded_file]
                    extract_dir = None
            else:
                files_to_upload = [downloaded_file]
                if extract_flag:
                    await msg.edit("⚠️ **File is not a supported archive. Uploading directly...**")

            if not files_to_upload:
                return await msg.edit("❌ **No files found to upload after extraction.**")

            total_files = len(files_to_upload)
            
            for idx, file_path in enumerate(files_to_upload, 1):
                file_name = os.path.basename(file_path)
                await msg.edit(f"📤 **Uploading {idx}/{total_files}:** `{file_name}`...")
                start_time = time.time()
                
                if file_path.endswith(('.mp4', '.mkv', '.webm', '.avi')):
                    thumb_path = await get_video_thumbnail(file_path)
                    video_dur = await get_video_duration(file_path)
                    
                    await client.send_video(
                        chat_id=message.chat.id,
                        video=file_path,
                        caption=f"**{file_name}**",
                        thumb=thumb_path,
                        duration=video_dur,
                        supports_streaming=True,
                        progress=progress_for_pyrogram,
                        progress_args=(f"📤 **Uploading {idx}/{total_files}:** `{file_name}`", msg, start_time)
                    )
                    if thumb_path and os.path.exists(thumb_path):
                        try: os.remove(thumb_path)
                        except: pass
                else:
                    await client.send_document(
                        chat_id=message.chat.id,
                        document=file_path,
                        caption=f"**{file_name}**",
                        progress=progress_for_pyrogram,
                        progress_args=(f"📤 **Uploading {idx}/{total_files}:** `{file_name}`", msg, start_time)
                    )
                
            await msg.delete()
            
        except Exception as e:
            await msg.edit(f"❌ **Error:** `{str(e)[:200]}`")
        finally:
            # Cleanup
            if downloaded_file and os.path.exists(downloaded_file):
                os.remove(downloaded_file)
            if extract_dir and os.path.exists(extract_dir):
                shutil.rmtree(extract_dir)

@Client.on_message(filters.command(["leech", "l"]) & filters.user(Config.OWNER_ID))
async def leech_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("**Usage:** `/leech <url> [-e] [-q 720] [custom_name]`\nUse `/l` as alias.\n`-e` to extract zip/tar files.\n`-q <res>` to select video quality (e.g. `-q 1080`, `-q 720`).\nAnything after flags is treated as `custom_name`.")
    
    args = message.command[1:]
    
    extract_flag = False
    if "-e" in args:
        extract_flag = True
        args.remove("-e")
        
    quality = None
    if "-q" in args:
        idx = args.index("-q")
        if len(args) > idx + 1:
            quality = args[idx+1]
            args.pop(idx+1)
        args.pop(idx)
    elif "-vt" in args:
        idx = args.index("-vt")
        if len(args) > idx + 1:
            quality = args[idx+1]
            args.pop(idx+1)
        args.pop(idx)
        
    if not args:
        return await message.reply_text("❌ **URL not provided.**")
        
    url = args[0]
    custom_name = " ".join(args[1:]) if len(args) > 1 else None
    
    queue_pos = LEECH_QUEUE.qsize() + 1
    if queue_pos > 1:
        msg = await message.reply_text(f"⏳ **Added to Queue**\nPosition: `{queue_pos}`")
    else:
        msg = await message.reply_text("⏳ **Processing Request...**")
    
    # Put task in queue
    await LEECH_QUEUE.put((client, message, url, extract_flag, quality, custom_name, msg))

# Queue Worker
async def leech_queue_worker():
    while True:
        task = await LEECH_QUEUE.get()
        if task:
            client, message, url, extract_flag, quality, custom_name, msg = task
            asyncio.create_task(process_leech_task(client, message, url, extract_flag, quality, custom_name, msg))
            await asyncio.sleep(1) # Prevent flooding the event loop
            
# Start worker
asyncio.create_task(leech_queue_worker())
