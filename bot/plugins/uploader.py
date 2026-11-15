import os
import math
import asyncio
import subprocess
import aiohttp
import aiofiles
import time
import gc
import psutil
import urllib.parse
import shutil
import random
import urllib.request
import tarfile
import zipfile
import logging
import json
import uuid
import re
from datetime import timedelta
from collections import deque
from pyrogram import Client, filters
from database.mongo_db import (
    get_user_tokens, save_video_metadata, save_active_batch, 
    update_batch_progress, remove_active_batch, get_all_active_batches, remove_dead_token,
    get_uploaded_items, add_uploaded_item, remove_uploaded_items, is_premium_user,
    get_bot_setting, set_bot_setting
)

async def safe_edit(msg, text):
    try: await msg.edit_text(text)
    except Exception: pass

async def safe_delete(msg):
    try: await msg.delete()
    except Exception: pass

from pyrogram.errors import MessageNotModified, FloodWait
from pyrogram import enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from itertools import cycle
from config import Config
from database.mongo_db import get_user_tokens, save_video_metadata

def to_custom_font(text: str) -> str:
    trans = str.maketrans(
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"
    )
    return text.translate(trans)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add current directory to path
os.environ["PATH"] = os.getcwd() + os.pathsep + os.environ.get("PATH", "")

STATUS_CYCLE = cycle([
    "📥 Extracting Media Streams...",
    "⚙️ Processing Architecture...",
    "🛡️ Bypassing Security Layers...",
    "🚀 Preparing Direct Download..."
])

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=180, connect=20, sock_read=90)
REQUEST_TIMEOUT = 60
# Optimized for PW CDN speed limits and YouTube anti-bot
_base_turbo = "--extractor-args \"youtube:player_client=web\" --concurrent-fragments 16 --buffer-size 16M --http-chunk-size 10M --retries 15 --fragment-retries 15 --no-warnings --newline"
TURBO_ARGS = f"--cookies cookies.txt {_base_turbo}"

CANCEL_TASKS = []
ACTIVE_USERS_PROCESSING = set()
STOP_ALL_FLAG = False
MAX_PARALLEL_TASKS = 3

async def load_parallel_setting():
    global MAX_PARALLEL_TASKS
    val = await get_bot_setting("max_parallel_tasks", 3)
    try:
        MAX_PARALLEL_TASKS = max(1, min(8, int(val)))
        logger.info(f"Loaded max parallel concurrency setting: {MAX_PARALLEL_TASKS}")
    except Exception:
        MAX_PARALLEL_TASKS = 3
_session: aiohttp.ClientSession | None = None
API_LOCK = asyncio.Lock()
# 🔥 FIX: Track last API call time to rate-limit PW requests
_last_api_call_time = 0
_api_rate_limit_lock = asyncio.Lock()

async def rate_limited_api_delay(min_interval: float = 4.0):
    """Ensure at least `min_interval` seconds between consecutive PW API calls"""
    global _last_api_call_time
    async with _api_rate_limit_lock:
        now = time.time()
        elapsed = now - _last_api_call_time
        if elapsed < min_interval and _last_api_call_time > 0:
            await asyncio.sleep(min_interval - elapsed)
        _last_api_call_time = time.time()

async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=30,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
            force_close=False,
            keepalive_timeout=45
        )
        _session = aiohttp.ClientSession(
            timeout=HTTP_TIMEOUT,
            connector=connector
        )
    return _session

def optimize_memory():
    gc.enable()
    gc.set_threshold(800, 10, 10)
    try:
        process = psutil.Process(os.getpid())
        memory_mb = process.memory_info().rss / (1024 * 1024)
        # Standard 2X: 1GB RAM, keep under 900MB
        if memory_mb > int(os.environ.get("MEMORY_LIMIT_MB", "900")):
            gc.collect()
    except Exception: pass

def format_failure_report(title, index_str, reason, is_pdf=False, logs=""):
    reason_str = str(reason).strip()
    r_lower = reason_str.lower()
    
    if any(x in r_lower for x in ["pw token expired", "401", "unauthorised", "unauthorized"]):
        diag_title = "🔑 PW Token Expired / Invalid"
        diag_action = "❌ AAPKA PW TOKEN EXPIRE HO GAYA HAI! Pehle `/pwlogin` se naya token save karein aur dobara try karein."
        token_status = "⚠️ EXPIRED (Login Required)"
    elif any(x in r_lower for x in ["payload is not completed", "empty or corrupted", "size: 0", "pdf"]):
        diag_title = "📄 PDF Server / Payload Error"
        diag_action = "ℹ️ PW CDN server is time PDF ka data pura nahi bhej raha (Response payload incomplete). Yeh server-side issue hai."
        token_status = "✅ SAHI HAI (Login dobara NA karein!)"
    elif any(x in r_lower for x in ["429", "anti-bot", "temporarily blocked"]):
        diag_title = "🛑 Rate Limit (429 Anti-Bot)"
        diag_action = "ℹ️ PW ne temporary block kiya hai. Bot ne requests roki hain, kuch ghanto baad retry karein."
        token_status = "✅ SAHI HAI (Login dobara NA karein!)"
    elif any(x in r_lower for x in ["403", "forbidden", "access denied"]):
        diag_title = "🚫 CDN Link Expired / Access Denied (403)"
        diag_action = "ℹ️ Video link ka AWS CloudFront signature expire ho gaya hai ya IP blocked hai. Fresh `.txt` file nikalein."
        token_status = "✅ SAHI HAI (Login dobara NA karein!)"
    elif any(x in r_lower for x in ["timeout", "timed out", "connection reset", "name or service not known"]):
        diag_title = "🌐 Network / Connection Timeout"
        diag_action = "ℹ️ PW CDN server response dene mein bahut time le raha hai. Kuch der baad is index ko retry karein."
        token_status = "✅ SAHI HAI (Login dobara NA karein!)"
    elif any(x in r_lower for x in ["drm", "decryption keys", "404"]):
        diag_title = "🔐 DRM Key / Video Not Found"
        diag_action = "ℹ️ Video DRM keys extract nahi ho sakin ya server par video file available nahi hai."
        token_status = "✅ SAHI HAI (Login dobara NA karein!)"
    else:
        diag_title = "⚠️ Download / Engine Error"
        diag_action = "ℹ️ Stream temporary unreadable thi ya process interrupted hua. Is index ko retry karein."
        token_status = "✅ SAHI HAI (Login dobara NA karein!)"

    report = (
        f"❌ **{diag_title} [Index: {index_str}]**\n"
        f"📌 **Item:** `{title[:100]}`\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💡 **PW Token Status:** `{token_status}`\n"
        f"🎯 **Action Required:** {diag_action}\n"
        f"💬 **Technical Detail:** `{reason_str[:250]}`"
    )
    if logs:
        report += f"\n\n**Engine Logs:**\n```\n{logs[:450]}\n```"
    return report

def local_cleanup(*files):
    for file in files:
        if file and isinstance(file, str) and os.path.exists(file):
            try: os.remove(file)
            except: pass
            


async def setup_re_binary():
    """Robust Auto-Installer for Binaries (Cross-Platform)"""
    is_windows = os.name == 'nt'
    current_dir = os.getcwd()
    
    re_name = "N_m3u8DL-RE.exe" if is_windows else "N_m3u8DL-RE"
    bento_name = "mp4decrypt.exe" if is_windows else "mp4decrypt"
    
    re_path = os.path.join(current_dir, re_name)
    bento_path = os.path.join(current_dir, bento_name)
    
    if os.path.exists(re_path) and os.path.exists(bento_path):
        logger.info("🔥 Global Binaries Detected. Skipping download.")
        return

    opener = urllib.request.build_opener()
    opener.addheaders = [('User-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')]
    urllib.request.install_opener(opener)

    # 1. Download N_m3u8DL-RE
    if not os.path.exists(re_path):
        logger.info(f"🔥 Downloading N_m3u8DL-RE for {'Windows' if is_windows else 'Linux'} locally...")
        try:
            if is_windows:
                re_url = "https://github.com/nilaoda/N_m3u8DL-RE/releases/download/v0.6.0-beta/N_m3u8DL-RE_v0.6.0-beta_win-x64_20260629.zip"
                urllib.request.urlretrieve(re_url, "re.zip")
                with zipfile.ZipFile("re.zip", "r") as zip_ref:
                    for member in zip_ref.namelist():
                        if member.endswith("N_m3u8DL-RE.exe"):
                            source = zip_ref.open(member)
                            with open(re_path, "wb") as target:
                                target.write(source.read())
                os.remove("re.zip")
            else:
                re_url = "https://github.com/nilaoda/N_m3u8DL-RE/releases/download/v0.6.0-beta/N_m3u8DL-RE_v0.6.0-beta_linux-x64_20260629.tar.gz"
                urllib.request.urlretrieve(re_url, "re.tar.gz")
                with tarfile.open("re.tar.gz", "r:gz") as tar:
                    for member in tar.getmembers():
                        if member.name.endswith("N_m3u8DL-RE"):
                            member.name = "N_m3u8DL-RE" 
                            tar.extract(member, path=current_dir)
                os.system(f"chmod +x {re_path}")
                os.remove("re.tar.gz")
            logger.info("✅ N_m3u8DL-RE Installed!")
        except Exception as e: 
            logger.error(f"RE Binary Error: {e}")

    # 2. Download mp4decrypt (Bento4)
    if not os.path.exists(bento_path):
        # Check if mp4decrypt is already in system PATH before downloading
        existing_in_path = shutil.which(bento_name)
        if existing_in_path and existing_in_path != bento_path:
            logger.info(f"✅ mp4decrypt found in system PATH: {existing_in_path}")
            # Copy to project directory
            try:
                shutil.copy2(existing_in_path, bento_path)
                if os.path.exists(bento_path):
                    logger.info("✅ Copied mp4decrypt to project directory")
                    return
            except Exception:
                pass
        
        logger.info(f"🔥 Downloading mp4decrypt for {'Windows' if is_windows else 'Linux'} locally...")
        
        # Multiple URL attempts in case primary server is down
        if is_windows:
            # Working URL first (primary bok.net URL confirmed returning 404)
            bento_urls = [
                "https://www.bok.net/Bento4/binaries/Bento4-SDK-1-6-0-640.x86_64-microsoft-win32.zip",
                "https://www.bok.net/Bento4/binaries/Bento4-SDK-1-6-0-640.win32.zip",
            ]
        else:
            bento_urls = [
                "https://www.bok.net/Bento4/binaries/Bento4-SDK-1-6-0-640.x86_64-unknown-linux.zip",
                "https://www.bok.net/Bento4/binaries/Bento4-SDK-1-6-0-640.x86_64-linux-gnu.zip",
            ]
        
        bento_downloaded = False
        for bento_url in bento_urls:
            try:
                logger.info(f"Trying Bento4 URL: {bento_url[:80]}...")
                # Download with timeout via urlopen since urlretrieve doesn't support timeout
                with urllib.request.urlopen(bento_url, timeout=120) as response:
                    with open("bento.zip", "wb") as f:
                        chunk_size = 8192
                        while True:
                            chunk = response.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                
                # Verify the downloaded file is a valid zip (not an HTML error page)
                if os.path.getsize("bento.zip") < 1000:
                    logger.warning(f"Downloaded file too small ({os.path.getsize('bento.zip')} bytes), likely an error page. Trying next URL...")
                    os.remove("bento.zip")
                    continue
                
                with zipfile.ZipFile("bento.zip", "r") as zip_ref:
                    for member in zip_ref.namelist():
                        if member.endswith(bento_name):
                            source = zip_ref.open(member)
                            with open(bento_path, "wb") as target:
                                target.write(source.read())
                if os.path.exists(bento_path) and os.path.getsize(bento_path) > 1000:
                    bento_downloaded = True
                    logger.info(f"✅ mp4decrypt downloaded successfully from: {bento_url[:80]}...")
                    break
            except Exception as e:
                logger.warning(f"Bento4 download failed for {bento_url[:60]}... : {e}")
                # Clean up failed download
                if os.path.exists("bento.zip"):
                    try: os.remove("bento.zip")
                    except: pass
                continue
        
        # Clean up zip file
        if os.path.exists("bento.zip"):
            try: os.remove("bento.zip")
            except: pass
            
        if not is_windows and os.path.exists(bento_path):
            os.system(f"chmod +x {bento_path}")
        
        if bento_downloaded:
            logger.info("✅ mp4decrypt Installed!")
        else:
            logger.error(
                "❌ Could not download mp4decrypt from any source!\n"
                "The official Bento4 server (bok.net) is currently down.\n"
                "To fix this manually:\n"
                "  1. Download Bento4 SDK from: https://www.bento4.com/downloads/\n"
                "  2. Extract the zip and find 'mp4decrypt.exe' in the 'bin' folder\n"
                f"  3. Place it in: {current_dir}"
            )

async def smart_async_download(cmd, log_file=None, user_id=None) -> tuple[bool, str]:
    """LIVE STREAM DEBUGGER ENGINE WITH RAM PROTECTION & INACTIVITY WATCHDOG"""
    try:
        if isinstance(cmd, list):
            proc = await asyncio.create_subprocess_exec(
                *cmd, 
                stdout=asyncio.subprocess.PIPE, 
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                cmd, 
                stdout=asyncio.subprocess.PIPE, 
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL
            )
            
        full_log = deque(maxlen=50) 
        last_activity = [time.time()]
        
        async def read_stream(stream, log_list):
            while True:
                chunk = await stream.read(8192)
                if chunk:
                    last_activity[0] = time.time()
                    decoded = chunk.decode('utf-8', errors='ignore')
                    # Split lines cleanly to maintain log rolling
                    for line in decoded.splitlines():
                        line = line.strip()
                        if line:
                            log_list.append(line)
                            if log_file:
                                try:
                                    with open(log_file, "a", encoding="utf-8") as lf:
                                        lf.write(line + "\n")
                                except: pass
                else:
                    break

        async def watchdog():
            from config import Config
            while proc.returncode is None:
                await asyncio.sleep(2)
                if user_id and (user_id in CANCEL_TASKS or (STOP_ALL_FLAG and user_id != Config.OWNER_ID)):
                    logger.warning(f"Download process INSTANTLY killed by cancel command! user_id: {user_id}")
                    try: proc.kill()
                    except: pass
                    break
                # If process has been inactive (no logs) for > 150 seconds, kill it
                if (time.time() - last_activity[0]) > 150:
                    logger.warning("Download process inactive for >150s. Terminating stuck process...")
                    try: proc.kill()
                    except: pass
                    break

        watchdog_task = asyncio.create_task(watchdog())

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    read_stream(proc.stdout, full_log),
                    read_stream(proc.stderr, full_log)
                ), 
                timeout=1800
            )
            await proc.wait()
        except asyncio.TimeoutError:
            try: proc.kill()
            except: pass
            return False, "Process freeze or timed out after 30 minutes."
        finally:
            watchdog_task.cancel()

        complete_output = "\n".join(full_log).strip()
        
        if proc.returncode == 0:
            return True, ""
        return False, f"Process failed with exit code {proc.returncode}. Last Logs:\n{complete_output}"
    except Exception as e: 
        return False, f"Engine Exception: {str(e)}"

class Timer:
    def __init__(self, time_between=15):
        self.start_time = time.time()
        self.time_between = time_between
    def can_send(self):
        if time.time() > (self.start_time + self.time_between):
            self.start_time = time.time()
            return True
        return False

def hrb(value, digits=2, delim="", postfix=""):
    if value is None: return None
    chosen_unit = "B"
    for unit in ("KB", "MB", "GB", "TB"):
        if value > 1000:
            value /= 1024
            chosen_unit = unit
        else: break
    return f"{value:.{digits}f}" + delim + chosen_unit + postfix

def hrt(seconds, precision=0):
    pieces = []
    value = timedelta(seconds=seconds)
    if value.days: pieces.append(f"{value.days}day")
    seconds = value.seconds
    if seconds >= 3600:
        hours = int(seconds / 3600)
        pieces.append(f"{hours}hr")
        seconds -= hours * 3600
    if seconds >= 60:
        minutes = int(seconds / 60)
        pieces.append(f"{minutes}min")
        seconds -= minutes * 60
    if seconds > 0 or not pieces: pieces.append(f"{seconds}sec")
    if not precision: return "".join(pieces)
    return "".join(pieces[:precision])

# Remove global timer so we can pass instance timers if we want
# timer = Timer()
BATCH_PROGRESS = {}

async def saini_progress_bar(current, total, batch_id, task_id, title, start, timer_instance):
    if timer_instance.can_send():
        now = time.time()
        diff = now - start
        if diff < 5: return
        perc = f"{current * 100 / total:.1f}%"
        elapsed_time = round(diff)
        speed = current / elapsed_time
        remaining_bytes = total - current
        if speed > 0:
            eta_seconds = remaining_bytes / speed
            eta = hrt(eta_seconds, precision=1)
        else: eta = "-"
        sp = str(hrb(speed)) + "/s"
        tot = hrb(total)
        cur = hrb(current)
        bar_length = 10
        completed_length = int(current * bar_length / total)
        remaining_length = bar_length - completed_length
        
        progress_bar = "🟩" * completed_length + "⬜" * remaining_length
        status_line = f"📤 **[UP] {title[:20]}**: {progress_bar} {perc} | {sp}"
        
        if batch_id in BATCH_PROGRESS:
            BATCH_PROGRESS[batch_id][task_id] = status_line

async def smart_progress_loop(client: Client, batch_id, task_id, title, stop_event: asyncio.Event, log_file=None):
    """Enhanced progress loop with download progress parsing"""
    last_progress = None
    while not stop_event.is_set():
        status_text = last_progress if last_progress else f"**⚡ Operation in Progress:**\n\n{next(STATUS_CYCLE)}"
        
        # Parse download progress from N_m3u8DL-RE logs
        if log_file and os.path.exists(log_file):
            try:
                # Read last 15 lines to catch recent progress
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[-15:]
                    
                    if lines:
                        log_tail = "".join(lines).strip()
                        # Clean ANSI escape sequences
                        log_tail = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', log_tail)
                        # Split by \r and \n to get clean lines
                        clean_lines = [line.strip() for line in log_tail.replace('\r', '\n').split('\n') if line.strip()]
                        
                        # Parse N_m3u8DL-RE progress format
                        for line in reversed(clean_lines):
                            pct_match = re.search(r'(\d+\.?\d*)\s*%', line)
                            if pct_match:
                                progress_pct = float(pct_match.group(1))
                                
                                # Draw progress bar
                                bar_length = 10
                                completed_length = int(progress_pct * bar_length / 100)
                                remaining_length = bar_length - completed_length
                                progress_bar = "🟩" * completed_length + "⬜" * remaining_length
                                
                                progress_info = f"📥 **[DL] {title[:20]}**: {progress_bar} {progress_pct:.1f}%"
                                
                                # Extract speed
                                speed_match = re.search(r'([\d.]+\s*[KMG]?i?B/s)', line, re.IGNORECASE)
                                if speed_match:
                                    progress_info += f" | 🚀 {speed_match.group(1).replace('i', '')}"
                                    
                                # Extract time/ETA
                                time_match = re.search(r'(\d{2}:\d{2}:\d{2})\s*/\s*(\d{2}:\d{2}:\d{2})', line)
                                if time_match:
                                    progress_info += f" | ⏱️ {time_match.group(1)} / {time_match.group(2)}"
                                else:
                                    single_time_match = re.search(r'\b(\d{2}:\d{2}:\d{2})\b', line)
                                    if single_time_match:
                                        progress_info += f" | ⏱️ {single_time_match.group(1)}"
                                        
                                # Extract fragment count
                                frag_match = re.search(r'\b(\d+)/(\d+)\b', line)
                                if frag_match:
                                    progress_info += f" | 📦 {frag_match.group(0)}"
                                    
                                if progress_info != last_progress:
                                    status_text = progress_info
                                    last_progress = progress_info
                                break
                            # Fallback: show any line with progress indicators
                            elif any(keyword in line.lower() for keyword in ['downloading', 'downloaded', 'mb/', 'gb/', 'speed']):
                                clean_line = line.strip()[:180]
                                if clean_line and clean_line != last_progress:
                                    status_text = f"**📥 [DL] {title[:20]}...** {clean_line}"
                                    last_progress = clean_line
                                break
            except Exception as e:
                logger.error(f"Error parsing download progress: {e}")
            
        try: 
            if batch_id in BATCH_PROGRESS:
                BATCH_PROGRESS[batch_id][task_id] = status_text
        except Exception: 
            pass
        await asyncio.sleep(2)

async def get_video_duration(filename):
    cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{filename}"'
    proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    try: return int(float(stdout.decode().strip()))
    except: return 0

async def get_video_thumbnail(filename):
    if os.path.exists("thumb.jpg"):
        return "thumb.jpg"
    thumb_name = f"{filename}.jpg"
    cmd = f'ffmpeg -hide_banner -loglevel error -i "{filename}" -ss 00:00:10 -vframes 1 "{thumb_name}"'
    proc = await asyncio.create_subprocess_shell(cmd)
    await proc.communicate()
    if os.path.exists(thumb_name): return thumb_name
    return None

@Client.on_message(filters.command("cancel") & filters.private)
async def cancel_upload(client, message):
    user_id = message.chat.id
    if user_id not in CANCEL_TASKS:
        CANCEL_TASKS.append(user_id)
        await message.reply_text("🛑 **Cancel Request Received!**")
    else: await message.reply_text("⚠️ **Pehle se hi cancel request pending hai!**")

@Client.on_message(filters.command(["stopall", "stopuploads", "killall"]) & filters.private)
async def stop_all_uploads_command(client, message):
    if message.chat.id != Config.OWNER_ID:
        return
    global STOP_ALL_FLAG
    STOP_ALL_FLAG = True
    
    count = len(ACTIVE_USERS_PROCESSING)
    for u_id in list(ACTIVE_USERS_PROCESSING):
        if u_id not in CANCEL_TASKS:
            CANCEL_TASKS.append(u_id)
            
    await message.reply_text(
        f"🛑 **GLOBAL EMERGENCY STOP ACTIVATED!**\n\n"
        f"📊 **Stopped Active Tasks:** `{count}` user sessions.\n"
        f"🚫 All current & future video extractions/uploads are HALTED.\n\n"
        f"👉 Use `/resumeuploads` to allow uploads again."
    )

@Client.on_message(filters.command(["resumeuploads", "startuploads"]) & filters.private)
async def resume_uploads_command(client, message):
    if message.chat.id != Config.OWNER_ID:
        return
    global STOP_ALL_FLAG
    STOP_ALL_FLAG = False
    await message.reply_text("✅ **GLOBAL STOP LIFTED!** Users can now upload and extract videos normally again.")

@Client.on_message(filters.command(["setparallel"]) & filters.private)
async def set_parallel_command(client, message):
    global MAX_PARALLEL_TASKS
    if message.chat.id != Config.OWNER_ID:
        return await message.reply_text("❌ **Access Denied:** Sirf Bot Owner/Admin hi parallel concurrency change kar sakte hain!")
    
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.reply_text(
            f"⚙️ **Current Parallel Concurrency:** `{MAX_PARALLEL_TASKS}`\n\n"
            "👉 **Use command like:** `/setparallel 4`\n"
            "*(Allowed range: `1` to `8`)*\n\n"
            "💡 **Tips:**\n"
            "• `1` - `2`: Low RAM Server / Safe Mode\n"
            "• `3`: Standard Mode (2GB RAM / Default)\n"
            "• `4` - `5`: Pro Server Mode (4GB+ RAM / Ultra Fast)\n"
        )
    
    val = int(parts[1])
    if val < 1 or val > 8:
        return await message.reply_text("❌ **Invalid Value!** Parallel setting `1` se `8` ke beech honi chahiye.")
    
    MAX_PARALLEL_TASKS = val
    await set_bot_setting("max_parallel_tasks", val)
    
    await message.reply_text(
        f"✅ **Parallel Concurrency Updated!**\n\n"
        f"🚀 **New Max Parallel Downloads & Upload Workers:** `{MAX_PARALLEL_TASKS}`\n"
        f"*(Ab saare naye batches is concurrency setting par chalenge!)*"
    )

@Client.on_message(filters.command(["parallel", "getparallel"]) & filters.private)
async def get_parallel_command(client, message):
    await message.reply_text(
        f"⚙️ **Current Parallel Concurrency Setting:** `{MAX_PARALLEL_TASKS}`\n\n"
        "📊 **Server Mode Recommendations:**\n"
        "• `1` - `2`: Safe / Low RAM Mode\n"
        "• `3`: Standard Mode (`2 GB RAM` - Default)\n"
        "• `4` - `5`: Pro Mode (`4 GB+ RAM` - Ultra Fast)\n\n"
        "👑 *To change (Admin only):* `/setparallel <number>`"
    )

async def download_hls_aes_decrypted(mpd_url: str, auth_token: str, quality: str, output_filename: str, log_file=None, user_id=None) -> tuple[bool, str]:
    try:
        # 1. Convert master.mpd to master.m3u8
        m3u8_master_url = mpd_url.replace("master.mpd", "master.m3u8")
        
        # Parse query params (GCP CloudFront signature)
        parsed_url = urllib.parse.urlparse(m3u8_master_url)
        query_params = parsed_url.query
        
        # 2. Fetch master M3U8 to find sub-playlists
        cf_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://pw.live",
            "Referer": "https://pw.live/"
        }
        
        session = await get_session()
        async with session.get(m3u8_master_url, headers=cf_headers) as master_res:
            if master_res.status != 200:
                return False, f"Failed to fetch master HLS playlist. Status: {master_res.status}"
            master_content = await master_res.text()
            
        # 3. Parse sub-playlist URL for selected quality
        lines = master_content.splitlines()
        playlists = [line.strip() for line in lines if line.strip().endswith(".m3u8") and not line.strip().startswith("#")]
        
        target_resolution_path = None
        for pl in playlists:
            if f"/{quality}/" in pl or f"{quality}/" in pl:
                target_resolution_path = pl
                break
        
        if not target_resolution_path and playlists:
            target_resolution_path = playlists[0]
            
        if not target_resolution_path:
            return False, "Could not find any sub-playlists in master.m3u8"
                
        # 4. Fetch the HLS sub-playlist
        base_path = m3u8_master_url.split("master.m3u8")[0]
        sub_playlist_url = f"{base_path}{target_resolution_path}?{query_params}"
        
        async with session.get(sub_playlist_url, headers=cf_headers) as sub_res:
            if sub_res.status != 200:
                return False, f"Failed to fetch sub HLS playlist. Status: {sub_res.status}"
            sub_content = await sub_res.text()
            
        # 5. Extract AES Key URL
        key_match = re.search(r'URI="(.*?)"', sub_content)
        if not key_match:
            return False, "HLS playlist does not contain AES-128 decryption URI."
            
        key_url = key_match.group(1)
        if key_url.startswith("skd://"):
            return False, "This video uses Apple FairPlay DRM (skd://). It cannot be downloaded via AES-128 decryption without proper DRM keys."
        # 6. Fetch the 16-byte key using user authorization token
        clean_token = auth_token.split('|')[0] if '|' in auth_token else auth_token
        key_headers = {
            "Authorization": f"Bearer {clean_token}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        
        async with session.get(key_url, headers=key_headers) as key_res:
            if key_res.status != 200:
                return False, f"Failed to fetch HLS AES decryption key. Status: {key_res.status}"
            aes_key = await key_res.read()
            
        # 7. Execute N_m3u8DL-RE with MASTER playlist URL and custom key
        re_bin = shutil.which("N_m3u8DL-RE") or shutil.which("N_m3u8DL-RE.exe") or os.path.join(os.getcwd(), "N_m3u8DL-RE.exe" if os.name == 'nt' else "N_m3u8DL-RE")
        ffmpeg_bin = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe") or "/app/vendor/ffmpeg/ffmpeg"
        re_cmd = [
            re_bin, 
            m3u8_master_url,
            "--append-url-params",
            "--auto-select",
            "--save-name", output_filename.replace(".mp4", ""),
            "--thread-count", "8",
            "-mt",
            "--download-retry-count", "10",
            "--log-level", "INFO",
            "--tmp-dir", f"temp_chunks_{output_filename}",
            "-sv", f"res=\"{quality}|480|360|240\":for=best",          
            "-sa", "best",
            "-M", "format=mp4",
            "--ffmpeg-binary-path", ffmpeg_bin,
            "--custom-hls-key", aes_key.hex(),
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "-H", "Origin: https://pw.live",
            "-H", "Referer: https://pw.live/"
        ]
        
        success, err_reason = await smart_async_download(re_cmd, log_file=log_file, user_id=user_id)
        
        try: shutil.rmtree(f"temp_chunks_{output_filename}", ignore_errors=True)
        except: pass
        
        if success:
            return True, ""
        return False, f"N_m3u8DL-RE decryption download failed: {err_reason}"
        
    except Exception as e:
        return False, f"HLS Decryption exception: {str(e)}"


async def download_pdf_resilient(url: str, output_filename: str) -> tuple[bool, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/pdf,application/octet-stream,*/*",
        "Connection": "keep-alive"
    }
    # 1. First try with existing pooled session and long timeout
    try:
        session = await get_session()
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=180, connect=30, sock_read=90)) as pdf_res:
            if pdf_res.status == 200:
                async with aiofiles.open(output_filename, 'wb') as f:
                    async for chunk in pdf_res.content.iter_chunked(1024 * 1024):
                        await f.write(chunk)
                if os.path.exists(output_filename) and os.path.getsize(output_filename) > 100:
                    return True, ""
            else:
                return False, f"HTTP Status {pdf_res.status}"
    except Exception as e:
        logger.warning(f"aiohttp PDF download error for {url}: {e}")

    # 2. Resilient Backup: Try urllib in asyncio.to_thread if aiohttp fails with payload/socket error
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        def _sync_dl():
            with urllib.request.urlopen(req, timeout=90) as response, open(output_filename, 'wb') as out_file:
                while True:
                    data = response.read(1024 * 1024)
                    if not data:
                        break
                    out_file.write(data)
        await asyncio.to_thread(_sync_dl)
        if os.path.exists(output_filename) and os.path.getsize(output_filename) > 100:
            return True, ""
    except Exception as e2:
        return False, f"Download failed: {e2}"

    return False, "Downloaded PDF file is empty or corrupted"


async def master_progress_updater(client, batch_id, master_msg, status_msg, batch_name, stop_event: asyncio.Event):
    while not stop_event.is_set():
        if batch_id in BATCH_PROGRESS and BATCH_PROGRESS[batch_id]:
            lines = list(BATCH_PROGRESS[batch_id].values())
            panel_text = f"━━━ 📊 **ʟɪᴠᴇ ʙᴀᴛᴄʜ ᴘʀᴏɢʀᴇꜱꜱ** ━━━\n📚 **Batch:** `{batch_name}`\n〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n" + "\n\n".join(lines) + "\n〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n⚡ **Powered by Marco**"
            try:
                if master_msg:
                    await safe_edit(master_msg, panel_text)
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except Exception: pass

            try:
                if status_msg and status_msg != master_msg:
                    await safe_edit(status_msg, panel_text)
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except Exception: pass

        await asyncio.sleep(3)

def parse_index_selection(text, total_items):
    raw_lower = text.strip().lower()
    is_only = any(w in raw_lower for w in ['only', 'sirf', 'just'])
    text_clean = raw_lower.replace('only', '').replace('sirf', '').replace('just', '').strip()
    if is_only and text_clean.isdigit():
        idx = int(text_clean)
        return [idx - 1] if 1 <= idx <= total_items else None

    if ',' not in text and '-' not in text and len(text.split()) == 1 and text.isdigit():
        return None
    indices = set()
    parts = [p.strip() for p in text.replace(' ', ',').split(',') if p.strip()]
    for p in parts:
        if '-' in p:
            sub = [s.strip() for s in p.split('-') if s.strip().isdigit()]
            if len(sub) == 2:
                s_i = max(1, int(sub[0]))
                e_i = min(total_items, int(sub[1]))
                for i in range(s_i, e_i + 1):
                    indices.add(i - 1)
        elif p.isdigit():
            idx = int(p)
            if 1 <= idx <= total_items:
                indices.add(idx - 1)
    if indices:
        return sorted(list(indices))
    return None

async def execute_batch(client: Client, user_id: int, target_chat_id: int, log_channel_id: int, batch_name: str, quality: str, custom_name: str, start_index: int, items: list, status_msg=None, start_msg_id=None, end_msg_id=None, show_index: bool = True, selected_indices: list = None):
    try:
        if target_chat_id:
            await client.get_chat(target_chat_id)
        if log_channel_id:
            await client.get_chat(log_channel_id)
        await client.get_chat(user_id)
        if Config.LOG_CHANNEL_ID:
            await client.get_chat(Config.LOG_CHANNEL_ID)
    except Exception as e:
        logger.warning(f"Failed to pre-fetch chats: {e}")
    while user_id in CANCEL_TASKS:
        CANCEL_TASKS.remove(user_id)
    await setup_re_binary()
    total_items = len(items)
    if start_index > total_items: 
        if status_msg: await status_msg.edit_text("❌ Start index total items se bada hai!")
        return

    batch_id = f"{user_id}_{int(time.time())}"
    BATCH_PROGRESS[batch_id] = {}
    master_stop_event = asyncio.Event()
    master_msg = await client.send_message(Config.LOG_CHANNEL_ID, f"📊 **Initializing Batch:** `{batch_name}`\n━━━━━━━━━━━━━━━━━\n⏳ Please wait while engine starts...")
    master_task = asyncio.create_task(master_progress_updater(client, batch_id, master_msg, status_msg, batch_name, master_stop_event))

    if status_msg:
        try:
            await safe_edit(status_msg, f"✅ **Detect hue:**\n🔗 Total Links: `{total_items}`\n🚀 Starting from: `{start_index}` | Quality: `{quality}p`\n\n**Uploading engine start kar raha hu...**")
        except Exception: pass

    batch_data = {
        "target_chat_id": target_chat_id,
        "log_channel_id": log_channel_id,
        "batch_name": batch_name,
        "quality": quality,
        "custom_name": custom_name,
        "start_index": start_index,
        "items": items,
        "show_index": show_index
    }
    await save_active_batch(user_id, batch_data)

    topic_links = {}

    target_indices = sorted(set(selected_indices)) if selected_indices else list(range(start_index - 1, total_items))
    target_indices = [idx for idx in target_indices if 0 <= idx < total_items]
    if not target_indices:
        target_indices = list(range(start_index - 1, total_items))

    # Max 1 extra download ahead of uploads to save memory and disk space
    upload_queue = asyncio.Queue(maxsize=1)
    completed_uploads = {}
    state = {
        "remaining_indices": list(target_indices),
        "expected_next_index": target_indices[0] if target_indices else (start_index - 1)
    }

    forward_lock = asyncio.Lock()  # 🔥 FIX: Duplicate forward rokne ke liye

    async def try_forward_videos():
        async with forward_lock:  # Sirf ek worker ek waqt mein forward kare
            while state["expected_next_index"] is not None and state["expected_next_index"] in completed_uploads:
                curr_idx = state["expected_next_index"]
                log_msg, disp_title, topic_name = completed_uploads[curr_idx]
                
                del completed_uploads[curr_idx]
                if curr_idx in state["remaining_indices"]:
                    state["remaining_indices"].remove(curr_idx)
                state["expected_next_index"] = state["remaining_indices"][0] if state["remaining_indices"] else None
                await update_batch_progress(user_id, curr_idx + 1)

                if log_msg:
                    try:
                        # Forward sequentially to the requested log_channel_id, else target_chat_id
                        forward_chat = log_channel_id if log_channel_id else target_chat_id
                        
                        if isinstance(log_msg, int):
                            if forward_chat == Config.LOG_CHANNEL_ID:
                                copied_msg = None
                                link_val = f"https://t.me/c/{str(Config.LOG_CHANNEL_ID).replace('-100', '')}/{log_msg}"
                                logger.info(f"Skipped copy because Target is Dump Channel itself")
                            else:
                                copied_msg = await asyncio.wait_for(client.copy_message(forward_chat, Config.LOG_CHANNEL_ID, log_msg), timeout=60)
                                link_val = copied_msg.link if copied_msg else None
                                logger.info(f"Forwarded {disp_title} to Target Channel")
                                await asyncio.sleep(1.5)
                        else:
                            if forward_chat == Config.LOG_CHANNEL_ID:
                                copied_msg = log_msg
                                link_val = copied_msg.link
                                logger.info(f"Skipped copy because Target is Dump Channel itself")
                            else:
                                copied_msg = await asyncio.wait_for(log_msg.copy(forward_chat), timeout=60)
                                link_val = copied_msg.link if copied_msg else None
                                logger.info(f"Forwarded {disp_title} to Target Channel")
                                await asyncio.sleep(1.5)
                            
                        if topic_name not in topic_links and link_val:
                            topic_links[topic_name] = link_val
                    except Exception as e:
                        logger.error(f"Failed to process {disp_title}: {e}")

    async def upload_worker():
        while True:
            job = await upload_queue.get()
            if job is None:
                break
                
            is_video = job["is_video"]
            output_filename = job["output_filename"]
            display_filename = job.get("display_filename", output_filename)
            caption = job["caption"]
            display_title = job["display_title"]
            topic_name = job["topic_name"]
            temp_name = job["temp_name"]
            count_idx = job["count_idx"]
            thumb_path = job.get("thumb_path")
            
            # 🟢 FIX: Hamesha pehle Global Dump Channel (LOG_CHANNEL_ID) me hi upload karo taaki sequential forwarding ho sake!
            upload_target = Config.LOG_CHANNEL_ID
            
            task_id = f"up_{count_idx}"
            BATCH_PROGRESS[batch_id][task_id] = f"📤 **[UP] {display_title[:20]}**: Preparing..."
            start_time = time.time()
            worker_timer = Timer(15)
            
            uploaded_msg = None
            try:
                if not is_video:
                    uploaded_msg = await asyncio.wait_for(
                        client.send_document(
                            chat_id=upload_target, 
                            document=output_filename, 
                            file_name=display_filename,
                            caption=caption,
                            progress=saini_progress_bar,
                            progress_args=(batch_id, task_id, display_title, start_time, worker_timer) 
                        ),
                        timeout=3600
                    )
                    # topic_links population is deferred to try_forward_videos
                    pass
                else:
                    if not thumb_path or not os.path.exists(thumb_path):
                        thumb_path = await get_video_thumbnail(output_filename)
                    video_dur = await get_video_duration(output_filename)
                    
                    uploaded_msg = await asyncio.wait_for(
                        client.send_video(
                            chat_id=upload_target, 
                            video=output_filename, 
                            file_name=display_filename,
                            caption=caption, 
                            thumb=thumb_path,
                            duration=video_dur,
                            width=1280,
                            height=720,
                            supports_streaming=True,
                            progress=saini_progress_bar,
                            progress_args=(batch_id, task_id, display_title, start_time, worker_timer) 
                        ),
                        timeout=3600
                    )
                
                if uploaded_msg:
                    completed_uploads[count_idx] = (uploaded_msg, display_title, topic_name)
                    await try_forward_videos()
            except Exception as e:
                logger.error(f"Upload failed for {output_filename}: {e}")
                try: 
                    index_str = f"{(count_idx + 1):03d}"
                    await client.send_message(Config.LOG_CHANNEL_ID, f"❌ **Upload Failed [Index: {index_str}]:** `{display_title[:100]}`\n\nError: {str(e)[:300]}")
                except Exception:
                    pass
                finally:
                    if count_idx not in completed_uploads:
                        completed_uploads[count_idx] = (None, display_title, topic_name)
                    await try_forward_videos()
            finally:
                if batch_id in BATCH_PROGRESS and task_id in BATCH_PROGRESS[batch_id]: del BATCH_PROGRESS[batch_id][task_id]
                local_cleanup(output_filename, f"{temp_name}.jpg")
                upload_queue.task_done()

    # Dynamic workers and semaphore based on MAX_PARALLEL_TASKS
    worker_tasks = [asyncio.create_task(upload_worker()) for _ in range(MAX_PARALLEL_TASKS)]
    download_sem = asyncio.Semaphore(MAX_PARALLEL_TASKS)
    cancel_notified = False

    async def process_download(count, item):
        nonlocal cancel_notified
        index_str = f"{(count + 1):03d}"
        clean_title = item.get("title", f"Item {index_str}").replace("||", "|").replace("`", "")

        async with download_sem:
            item = items[count]

            raw_title = item["title"].replace("||", "|").replace("`", "")
            url = item["url"]
            is_video = item["is_video"]
            pdf_success = False
            success = False

            if user_id in CANCEL_TASKS or (STOP_ALL_FLAG and user_id != Config.OWNER_ID):
                if not cancel_notified:
                    cancel_notified = True
                    await client.send_message(Config.LOG_CHANNEL_ID, "🛑 **Process cancelled/stopped by Admin!** Agli files skip kar di gayi hain.")
                if count not in completed_uploads:
                    completed_uploads[count] = (None, "Cancelled", "Cancelled")
                    await try_forward_videos()
                return

            import re
            topic_name = ""
            chapter_no = ""
            clean_title = raw_title

            # Remove prefixes like (Notices), (Physics), [Demo], etc.
            temp_title = re.sub(r'^[\(\[].*?[\)\]]\s*', '', raw_title)

            # Split correctly by " : " first since extractor adds it
            if " : " in temp_title:
                topic_part = temp_title.split(" : ", 1)[0].strip()
            else:
                parts = re.split(r'[-:|]', temp_title, 1)
                topic_part = parts[0].strip() if parts else ""

            if topic_part:
                # Extract Chapter Number (e.g. Ch-03, CH-17, ch-01)
                ch_match = re.search(r'Ch\s*-\s*(\d+)', topic_part, flags=re.IGNORECASE)
                if ch_match:
                    chapter_no = ch_match.group(1).strip()
                    # Remove "Ch-XX -" from topic_part to get the clean topic name
                    topic_name = re.sub(r'Ch\s*-\s*\d+\s*-?\s*', '', topic_part, flags=re.IGNORECASE).strip()
                else:
                    topic_name = topic_part

            if len(topic_name) > 60 or "notice" in topic_name.lower():
                topic_name = ""

            file_ext = ".mp4" if is_video else ".pdf"
            # 🔥 FIX: Agar clean_title mein already .pdf ya .mp4 hai toh dobara mat lagao
            clean_title_stripped = re.sub(r'\.(pdf|mp4|mkv|m4v|webm|ts)(\..+)?$', '', clean_title, flags=re.IGNORECASE).strip()
            display_title = f"{clean_title_stripped}{file_ext}"
            
            # Truncate title for filename to avoid OS limits (max 255 bytes)
            safe_title_for_file = clean_title_stripped[:120].strip()
            display_filename = re.sub(r'[\\/*?:"<>|]', "", f"{safe_title_for_file}{file_ext}").strip()

            caption = f"🎬 **{clean_title_stripped}{file_ext}**\n━━━━━━━━━━━━━━━━━━\n"
            if chapter_no:
                caption += f"📌 **Chapter:** `{chapter_no}`\n"
            if topic_name and topic_name.lower() != "class content":
                caption += f"📌 **Topic:** `{topic_name.upper()}`\n"
            caption += f"📦 **Batch:** `{batch_name}`\n"
            if show_index:
                caption += f"🔢 **Index:** `{index_str}`\n"
            caption += f"✨ **Uploaded By: {custom_name}**"

            if len(caption) > 1024:
                caption = caption[:1020] + "..."

            import time
            temp_name = f"temp_vid_{user_id}_{int(time.time())}_{count}"
            output_filename = f"{temp_name}_{display_filename}"
            thumb_path = None
            if item.get("thumb_url"):
                try:
                    async with aiohttp.ClientSession() as thumb_sess:
                        async with thumb_sess.get(item["thumb_url"]) as thumb_res:
                            if thumb_res.status == 200:
                                thumb_path = f"thumb_{temp_name}.jpg"
                                with open(thumb_path, "wb") as tf:
                                    tf.write(await thumb_res.read())
                except Exception as e:
                    logger.error(f"Thumbnail download failed: {e}")
            
            expected_out = f"{temp_name}.mp4"
            cmd = ""

            optimize_memory()
            task_id = f"dl_{count}"
            BATCH_PROGRESS[batch_id][task_id] = f"📥 **[DL] {display_title[:20]}**: Preparing..."
            stop_event = asyncio.Event()
            loop_task = asyncio.create_task(smart_progress_loop(client, batch_id, task_id, display_title, stop_event, f"{temp_name}_dl.log"))

            try:
                if not is_video or "pdf" in url.lower() or "static.pw" in url:
                    stop_event.set(); await loop_task
                    BATCH_PROGRESS[batch_id][task_id] = f"📥 **Downloading PDF...**\n`{clean_title}`"

                    max_retries = 3
                    pdf_success = False
                    pdf_err_msg = ""
                    for attempt in range(max_retries):
                        pdf_success, pdf_err_msg = await download_pdf_resilient(url, output_filename)
                        if pdf_success:
                            break
                        if attempt < max_retries - 1:
                            BATCH_PROGRESS[batch_id][task_id] = f"📥 **Retrying PDF... ({attempt + 2}/{max_retries})**\n`{clean_title}`\n*(Error: {pdf_err_msg})*"
                            await asyncio.sleep(4)
                        else:
                            BATCH_PROGRESS[batch_id][task_id] = f"❌ **PDF Download Failed! Error: {pdf_err_msg}**"
                            await asyncio.sleep(2)

                    if pdf_success:
                        job_data = {
                            "is_video": False,
                            "output_filename": output_filename,
                            "display_filename": display_filename,
                            "caption": caption,
                            "display_title": display_title,
                            "topic_name": topic_name,
                            "temp_name": temp_name,
                            "count_idx": count,
                            "thumb_path": thumb_path
                        }
                        BATCH_PROGRESS[batch_id][task_id] = f"✅ **Downloaded PDF.** Added to Upload Queue."
                        await upload_queue.put(job_data)
                        await asyncio.sleep(2)
                        if batch_id in BATCH_PROGRESS and task_id in BATCH_PROGRESS[batch_id]: del BATCH_PROGRESS[batch_id][task_id]
                    else:
                        if not pdf_err_msg:
                            pdf_err_msg = "PDF file is empty or corrupted"
                        
                        error_msg = format_failure_report(clean_title, index_str, pdf_err_msg, is_pdf=True)
                        try:
                            await client.send_message(Config.LOG_CHANNEL_ID, error_msg)
                        except Exception:
                            pass
                        
                        BATCH_PROGRESS[batch_id][task_id] = f"❌ **PDF Failed [Index: {index_str}]**"
                        await asyncio.sleep(2)
                        if count not in completed_uploads:
                            completed_uploads[count] = (None, display_title, topic_name)
                            await try_forward_videos()
                        return

                else:
                    using_temp = False
                    success = False
                    error_reason = ""
                    cmd = ""

                    if "youtu" in url:
                        cmd = f'yt-dlp {TURBO_ARGS} -f "bestvideo[height<={quality}]+bestaudio/bestvideo+bestaudio/best" -o "{expected_out}" "{url}"'
                        using_temp = True
                    elif ".mpd" in url or ".m3u8" in url:
                        parsed_url = urllib.parse.urlparse(url)
                        params = urllib.parse.parse_qs(parsed_url.query)
                        pw_batch_id = params.get('parentId', [None])[0]
                        video_id = params.get('childId', [None])[0]
                        video_type = params.get('type', [None])[0]
                        if pw_batch_id and not video_type:
                            video_type = "BATCHES"

                        if video_id:
                            # 🔥 FIX: Rate-limit delay between consecutive PW API calls
                            await rate_limited_api_delay(5.0)
                            
                            await save_video_metadata(video_id, {
                                "title": clean_title,
                                "batch": batch_name,
                                "topic": topic_name,
                                "url": url,
                                "is_video": is_video,
                                "index": index_str,
                                "timestamp": time.time()
                            })

                        tokens = await get_user_tokens(user_id)
                        if not tokens: 
                            raise Exception("PW Token Database me nahi hai. Pehle /pwlogin karo!")

                        session = await get_session()
                        
                        # 🔥 FIX: Initialize before if video_id block to prevent NameError when video_id is None
                        final_url = url
                        keys = []
                        is_drm = False
                        # FIX: Use the latest added token (-1) instead of the oldest (0) to avoid 403 on AES streams
                        first_auth_token = tokens[-1] if tokens else None

                        if video_id:
                            api_success_flag = False
                            last_api_err = "No tokens tried"

                            # FIX: Try the newest tokens first to save time and reduce failures
                            for auth_token in reversed(tokens):
                                if first_auth_token is None:
                                    first_auth_token = auth_token
                                api_params = {"url": final_url, "parentId": pw_batch_id, "childId": video_id, "token": auth_token, "quality": quality}
                                if video_type:
                                    api_params["type"] = video_type
                                api_retries = 3

                                for attempt in range(api_retries):
                                    try:
                                        # Add small delay to respect PW rate limits
                                        if attempt > 0:
                                            await asyncio.sleep(1.5 * attempt)
                                        async with API_LOCK:
                                            for api_base_url in Config.get_api_base_urls():
                                                try:
                                                    async with session.get(f"{api_base_url}/api/get-keys", params=api_params, headers={"X-API-KEY": Config.API_SECRET_KEY}) as res:
                                                        if res.status == 200:
                                                            data = await res.json()
                                                            logger.info(f"🐞 DEBUG: Raw API Response for {clean_title} -> {json.dumps(data)[:300]}...")
                                                            
                                                            if data.get("success"):
                                                                is_drm = data.get("is_drm", True)
                                                                keys = data.get("keys", [])
                                                                if data.get("direct_url"):
                                                                    final_url = data.get("direct_url")
                                                                    logger.info(f"🐞 DEBUG: success=True! Updated final_url to -> {final_url}")
                                                                else:
                                                                    logger.warning(f"🐞 DEBUG: success=True BUT NO direct_url returned!")
                                                                api_success_flag = True
                                                                break # Success, exit retry loop
                                                            else:
                                                                api_err = data.get("error", "Unknown API Error")
                                                                if "429" in str(api_err) or (isinstance(api_err, dict) and api_err.get("status") == 429):
                                                                    if attempt < api_retries - 1:
                                                                        await asyncio.sleep(6 * (attempt + 1))
                                                                        continue
                                                                elif isinstance(api_err, dict) and api_err.get("status") == 401:
                                                                    await remove_dead_token(user_id, auth_token)
                                                                    last_api_err = "Token Expired (401)"
                                                                    await asyncio.sleep(2)
                                                                    break
                                                                elif isinstance(api_err, dict) and api_err.get("status") == 403:
                                                                    last_api_err = "This token does not have purchase access to this batch."
                                                                    await asyncio.sleep(1.5)
                                                                    break
                                                                last_api_err = f"API Rejected: {api_err}"
                                                                await asyncio.sleep(1.5)
                                                                break
                                                        else:
                                                            last_api_err = f"API Server ({api_base_url}) Status: {res.status}"
                                                except Exception as conn_err:
                                                    last_api_err = f"Connection error to {api_base_url}: {conn_err}"
                                            if api_success_flag or "Token Expired" in last_api_err or "does not have purchase" in last_api_err:
                                                break
                                    except Exception as e:
                                        if attempt < api_retries - 1 and "429" in str(e):
                                            await asyncio.sleep(3 * (attempt + 1))
                                            continue
                                        last_api_err = f"Custom API Exception: {e}"
                                        break

                                if api_success_flag:
                                    break # Stop token loop, we got the keys!

                            # --- SELF HEALING: Find correct childId by matching title if API failed ---
                            if not api_success_flag and pw_batch_id:
                                logger.info(f"Self-healing: Searching batch {pw_batch_id} for correct ID matching '{clean_title}'")
                                try:
                                    from platforms.pw_api import get_pw_headers
                                    headers = get_pw_headers(first_auth_token)
                                    async with session.get(f"https://api.penpencil.xyz/v3/batches/{pw_batch_id}/details", headers=headers) as b_res:
                                        if b_res.status == 200:
                                            b_data = await b_res.json()
                                            subjects = b_data.get("data", {}).get("subjects", [])
                                            found_new_id = None
                                            for sub in subjects:
                                                s_id = sub.get("_id")
                                                # Scan contents for this subject
                                                async with session.get(f"https://api.penpencil.xyz/v2/batches/{pw_batch_id}/subject/{s_id}/contents?page=1&contentType=exercises-notes-videos", headers=headers) as c_res:
                                                    if c_res.status == 200:
                                                        c_data = await c_res.json()
                                                        for item in c_data.get("data", []):
                                                            item_name = (item.get("name") or item.get("topic") or "").lower()
                                                            # Compare simplified titles
                                                            simple_clean_title = re.sub(r'[^a-zA-Z0-9]', '', clean_title.lower())
                                                            simple_item_name = re.sub(r'[^a-zA-Z0-9]', '', item_name)
                                                            
                                                            # Try fuzzy match or direct inclusion
                                                            if simple_item_name in simple_clean_title or simple_clean_title in simple_item_name or (item.get("_id") == video_id):
                                                                found_new_id = item.get("_id")
                                                                if isinstance(item.get("videoDetails"), dict) and item["videoDetails"].get("_id"):
                                                                    found_new_id = item["videoDetails"]["_id"]
                                                                logger.info(f"Self-healing: Found matching item '{item.get('name')}' with new ID: {found_new_id}")
                                                                break
                                                if found_new_id:
                                                    break
                                            
                                            if found_new_id and found_new_id != video_id:
                                                logger.info(f"Self-healing: Retrying key extraction with new childId: {found_new_id}")
                                                video_id = found_new_id
                                                # Retry API loop with the healed ID
                                                for auth_token in tokens:
                                                    api_params = {"url": final_url, "parentId": pw_batch_id, "childId": video_id, "token": auth_token, "quality": quality}
                                                    if video_type:
                                                        api_params["type"] = video_type
                                                    for api_base_url in Config.get_api_base_urls():
                                                        try:
                                                            async with session.get(f"{api_base_url}/api/get-keys", params=api_params, headers={"X-API-KEY": Config.API_SECRET_KEY}) as retry_res:
                                                                if retry_res.status == 200:
                                                                    retry_data = await retry_res.json()
                                                                    if retry_data.get("success"):
                                                                        is_drm = retry_data.get("is_drm", True)
                                                                        keys = retry_data.get("keys", [])
                                                                        if retry_data.get("direct_url"):
                                                                            final_url = retry_data.get("direct_url")
                                                                        api_success_flag = True
                                                                        break
                                                        except Exception: pass
                                                    if api_success_flag:
                                                        break
                                except Exception as healing_err:
                                    logger.error(f"Self-healing title search failed: {healing_err}")

                            if not api_success_flag:
                                if "429" in last_api_err:
                                    # Anti-Bot Rate Limit Hit!
                                    raise Exception(f"PW HTTP API Failed! Code 429: Account temporarily blocked for video access. PW Anti-Bot triggered. Wait a few hours. Detail: {last_api_err}")
                                elif "404" in last_api_err or "Not Found" in last_api_err:
                                    # Video Deleted / ID Changed
                                    raise Exception(f"Video Not Found (404). The video might be completely removed from PW server or its ID changed. Please generate fresh txt! Detail: {last_api_err}")
                                elif final_url and "cloudfront.net" in final_url and "Signature=" not in final_url:
                                    raise Exception(f"PW Token Expired/Invalid (`{last_api_err}`). Pehle /pwlogin karke naya token save karein!")
                                elif final_url and (final_url.startswith("http://") or final_url.startswith("https://")):
                                    logger.warning(f"API key extraction failed ({last_api_err}). Falling back to direct stream download for: {final_url}")
                                    is_drm = False
                                    keys = []
                                else:
                                    raise Exception(f"Kisi bhi saved token se DRM keys nahi nikal payi. Last Error: {last_api_err}")

                        stop_event.set(); await loop_task

                        if is_drm and not keys:
                            stop_event.set(); await loop_task
                            error_reason = "DRM Decryption Keys nahi mil payin! Video encrypted hai aur backend se keys extract nahi ho saki."
                            logger.error(error_reason)
                            BATCH_PROGRESS[batch_id][task_id] = f"❌ **Download Failed:** `{clean_title}`\n\n**Reason:**\n`{error_reason}`"
                            success = False
                        elif keys and is_drm:
                            # DRM Encrypted Video -> Use N_m3u8DL-RE with Decryption Keys
                            BATCH_PROGRESS[batch_id][task_id] = f"🔐 **DRM Keys Found!** Decrypting natively..."
                            stop_event.clear()
                            loop_task = asyncio.create_task(smart_progress_loop(client, batch_id, task_id, display_title, stop_event, f"{temp_name}_dl.log"))

                            re_bin = shutil.which("N_m3u8DL-RE") or shutil.which("N_m3u8DL-RE.exe") or os.path.join(os.getcwd(), "N_m3u8DL-RE.exe" if os.name == 'nt' else "N_m3u8DL-RE")
                            bento_bin = shutil.which("mp4decrypt") or shutil.which("mp4decrypt.exe") or os.path.join(os.getcwd(), "mp4decrypt.exe" if os.name == 'nt' else "mp4decrypt")

                            clean_final_url = final_url.replace("&amp;", "&")
                            REAL_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

                            # 🔥 OPTIMIZED: Using 4 threads for maximum safe speed download
                            safe_temp_name = os.path.basename(temp_name)  # Remove any path separators
                            save_dir = os.getcwd()  # Explicitly save to current working directory
                            os.makedirs(f"temp_chunks_{safe_temp_name}", exist_ok=True)
                            ffmpeg_bin = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe") or "/app/vendor/ffmpeg/ffmpeg"
                            cmd = [
                                re_bin, 
                                clean_final_url,
                                "-H", f"User-Agent: {REAL_UA}",
                                "-H", "Origin: https://www.pw.live",
                                "-H", "Referer: https://www.pw.live/",
                                "--append-url-params",
                                "--auto-select",
                                "--save-name", safe_temp_name,
                                "--save-dir", save_dir,
                                "--thread-count", "16",
                                "-mt",
                                "--download-retry-count", "10",
                                "--log-level", "INFO",
                                "--tmp-dir", f"temp_chunks_{safe_temp_name}",            
                                "-sv", f"res=\"{quality}|480|360|240\":for=best",          
                                "-sa", "best",
                                "-M", "format=mp4",
                                "--ffmpeg-binary-path", ffmpeg_bin,
                                "--decryption-binary-path", bento_bin
                            ]
                            for k in keys:
                                cmd.extend(["--key", k])
                            using_temp = True
                            temp_name = safe_temp_name  # Use safe name going forward
                        else:
                            # Try AES-encrypted HLS path first if we have a valid token
                            hls_aes_success = False
                            if first_auth_token and "master.mpd" in final_url:
                                stop_event.clear()
                                loop_task = asyncio.create_task(smart_progress_loop(client, batch_id, task_id, display_title, stop_event, f"{temp_name}_dl.log"))
                                hls_aes_success, hls_aes_err = await download_hls_aes_decrypted(
                                    final_url.replace("&amp;", "&"), 
                                    first_auth_token, 
                                    quality, 
                                    output_filename, 
                                    log_file=f"{temp_name}_dl.log",
                                    user_id=user_id
                                )
                                stop_event.set(); await loop_task
                                if hls_aes_success:
                                    logger.info(f"✅ AES HLS decryption succeeded for: {clean_title}")
                                    success = True
                                    using_temp = False  # download_hls_aes_decrypted already saved to output_filename
                                    cmd = ""  # Skip the plain N_m3u8DL-RE command
                            
                            if not hls_aes_success:
                                BATCH_PROGRESS[batch_id][task_id] = f"🔓 **Downloading natively via N_m3u8DL-RE...**"
                                stop_event.clear()
                                loop_task = asyncio.create_task(smart_progress_loop(client, batch_id, task_id, display_title, stop_event, f"{temp_name}_dl.log"))

                                re_bin = shutil.which("N_m3u8DL-RE") or shutil.which("N_m3u8DL-RE.exe") or os.path.join(os.getcwd(), "N_m3u8DL-RE.exe" if os.name == 'nt' else "N_m3u8DL-RE")
                                clean_final_url = final_url.replace("&amp;", "&")
                                REAL_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                                safe_temp_name2 = os.path.basename(temp_name)  # Remove any path separators
                                save_dir2 = os.getcwd()
                                os.makedirs(f"temp_chunks_{safe_temp_name2}", exist_ok=True)
                                ffmpeg_bin = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe") or "/app/vendor/ffmpeg/ffmpeg"

                                cmd = [
                                    re_bin, 
                                    clean_final_url,
                                    "-H", f"User-Agent: {REAL_UA}",
                                    "-H", "Origin: https://www.pw.live",
                                    "-H", "Referer: https://www.pw.live/",
                                    "--append-url-params",
                                    "--auto-select",
                                    "--save-name", safe_temp_name2,
                                    "--save-dir", save_dir2,
                                    "--thread-count", "16",
                                    "-mt",
                                    "--download-retry-count", "10",
                                    "--log-level", "INFO",
                                    "--tmp-dir", f"temp_chunks_{safe_temp_name2}",
                                    "-M", "format=mp4",
                                    "--ffmpeg-binary-path", ffmpeg_bin
                                ]
                                using_temp = True
                                temp_name = safe_temp_name2  # Use safe name going forward
                    else:
                        # Direct Video Format (e.g. .mp4 Notice/Demo attachment) -> Use yt-dlp directly to temp_name
                        BATCH_PROGRESS[batch_id][task_id] = f"📥 **Downloading Video Link...**"
                        stop_event.clear()
                        loop_task = asyncio.create_task(smart_progress_loop(client, batch_id, task_id, display_title, stop_event, f"{temp_name}_dl.log"))

                        clean_url = url.replace("&amp;", "&")
                        REAL_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                        cmd = f'yt-dlp {TURBO_ARGS} -f "bestvideo[height<={quality}]+bestaudio/bestvideo+bestaudio/best" --add-header "User-Agent:{REAL_UA}" -o "{temp_name}.mp4" "{clean_url}"'
                        using_temp = True

                    logger.info(f"Downloading: {clean_title}")
                    if cmd:
                        success, error_reason = await smart_async_download(cmd, log_file=f"{temp_name}_dl.log", user_id=user_id)
                    else:
                        # Already processed by download_hls_aes_decrypted helper
                        pass

                    if success:
                        gc.collect()
                        await asyncio.sleep(2) 

                        if using_temp:
                            import glob
                            # Extra wait - N_m3u8DL-RE sometimes flushes file after exit
                            await asyncio.sleep(3)
                            mp4_exists = os.path.exists(f"{temp_name}.mp4") and os.path.getsize(f"{temp_name}.mp4") > 1000
                            mkv_exists = os.path.exists(f"{temp_name}.mkv") and os.path.getsize(f"{temp_name}.mkv") > 1000

                            if mp4_exists:
                                os.rename(f"{temp_name}.mp4", output_filename)
                            elif mkv_exists:
                                os.rename(f"{temp_name}.mkv", output_filename)
                            else:
                                # 🔥 FIX: N_m3u8DL-RE kabhi kabhi alag naam se file save karta hai
                                # Glob se dhundho - temp_name se shuru hone wali koi bhi video file
                                found_files = []
                                for ext in ["mp4", "mkv", "m4v", "ts", "webm"]:
                                    found_files.extend(glob.glob(f"{temp_name}*.{ext}"))
                                # Sirf valid size wali files rakho
                                found_files = [f for f in found_files if os.path.getsize(f) > 1000]
                                
                                if found_files:
                                    # Sabse badi file use karo (actual video)
                                    best_file = max(found_files, key=os.path.getsize)
                                    logger.info(f"✅ N_m3u8DL-RE output found via glob: {best_file}")
                                    os.rename(best_file, output_filename)
                                else:
                                    if not is_drm:
                                        logger.info(f"N_m3u8DL-RE did not produce file. Retrying via yt-dlp fallback: {clean_title}")
                                        yt_cmd = f'yt-dlp {TURBO_ARGS} -f "bestvideo[height<={quality}]+bestaudio/bestvideo+bestaudio/best" --add-header "User-Agent:Mozilla/5.0" --add-header "Origin:https://www.pw.live" -o "{output_filename}" "{clean_final_url}"'
                                        yt_ok, _ = await smart_async_download(yt_cmd, log_file=f"{temp_name}_dl.log", user_id=user_id)
                                        if yt_ok and os.path.exists(output_filename) and os.path.getsize(output_filename) > 1000:
                                            success = True
                                        else:
                                            success = False
                                            error_reason = "Final video file could not be generated by N_m3u8DL-RE or yt-dlp fallback."
                                    else:
                                        success = False
                                        # Determine exact failure reason for logging
                                        if os.path.exists(f"{temp_name}.mp4") or os.path.exists(f"{temp_name}.mkv"):
                                            size_info = os.path.getsize(f"{temp_name}.mp4") if os.path.exists(f"{temp_name}.mp4") else os.path.getsize(f"{temp_name}.mkv")
                                            error_reason = f"Downloaded video file is empty or corrupted (size: {size_info} B). The stream URL may have expired or returned 403 Forbidden."
                                        else:
                                            error_reason = "Final video file was not generated by N_m3u8DL-RE. URL may have expired or been blocked."
                        else:
                            # Video that downloaded directly to output_filename (using yt-dlp)
                            if os.path.exists(output_filename) and os.path.getsize(output_filename) > 1000:
                                pass
                            else:
                                success = False
                                size_info = os.path.getsize(output_filename) if os.path.exists(output_filename) else 0
                                error_reason = f"Downloaded video file is empty or corrupted (size: {size_info} B). The download might have failed or been blocked."

                    # 🔥 FIX: If N_m3u8DL-RE got 403 (expired URL from CDN cache), retry with fresh URL via yt-dlp
                    #    * Match "40", "403", "Forbidden", etc. (sometimes truncated to just "40" in log)
                    #    * Try both DRM and non-DRM (CDN expiry affects both)
                    err_val = error_reason if 'error_reason' in locals() and error_reason else ""
                    err_lower = str(err_val).lower()
                    # Match HTTP errors from N_m3u8DL-RE logs (full "403 Forbidden" or truncated "40" both contain "not indicate success")
                    is_403 = any(x in err_lower for x in ["403", "forbidden", "not indicate success"])
                    if not success and is_403 and final_url and final_url.startswith("http"):
                        logger.warning(f"CDN 403 detected! Retrying with yt-dlp fallback: {clean_title}")
                        BATCH_PROGRESS[batch_id][task_id] = f"🔄 **CDN 403 detected! Retrying with fresh URL...**"
                        stop_event.set(); await loop_task
                        stop_event.clear()
                        loop_task = asyncio.create_task(smart_progress_loop(client, batch_id, task_id, display_title, stop_event, f"{temp_name}_dl_yt.log"))

                        # Strategy 1: Try to refresh URL from API first (bypass API cache with fresh call)
                        fresh_url = None
                        if video_id and tokens:
                            try:
                                await rate_limited_api_delay(5.0)
                                async with API_LOCK:
                                    for api_base_url in Config.get_api_base_urls():
                                        # 🔥 FIX: Pass force=1 to bypass API cache and get a truly fresh signed URL
                                        refresh_params = {"url": url, "parentId": pw_batch_id, "childId": video_id, "token": tokens[0], "quality": quality, "force": 1}
                                        if video_type:
                                            refresh_params["type"] = video_type
                                        try:
                                            async with session.get(f"{api_base_url}/api/get-keys", params=refresh_params, headers={"X-API-KEY": Config.API_SECRET_KEY}) as refresh_res:
                                                if refresh_res.status == 200:
                                                    refresh_data = await refresh_res.json()
                                                    if refresh_data.get("success") and refresh_data.get("direct_url"):
                                                        fresh_url = refresh_data["direct_url"]
                                                        logger.info(f"✅ Got fresh URL from API for: {clean_title}")
                                                        break
                                        except Exception: pass
                            except Exception as api_refresh_err:
                                logger.warning(f"API refresh failed for 403 fallback: {api_refresh_err}")

                        # Strategy 2: yt-dlp with fresh URL (or original URL if refresh failed)
                        yt_url = (fresh_url or final_url).replace("&amp;", "&")
                        yt_cmd = f'yt-dlp {TURBO_ARGS} -f "bestvideo[height<={quality}]+bestaudio/bestvideo+bestaudio/best" --add-header "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" --add-header "Origin:https://www.pw.live" --add-header "Referer:https://www.pw.live/" -o "{output_filename}" "{yt_url}"'
                        yt_ok, yt_err = await smart_async_download(yt_cmd, log_file=f"{temp_name}_dl_yt.log", user_id=user_id)

                        # Strategy 3: If first yt-dlp failed and we had a fresh URL, try original URL too
                        if not yt_ok and fresh_url:
                            logger.info(f"Fresh URL also failed. Trying original item URL with yt-dlp: {clean_title}")
                            original_url = url.replace("&amp;", "&")
                            yt_cmd2 = f'yt-dlp {TURBO_ARGS} -f "bestvideo[height<={quality}]+bestaudio/bestvideo+bestaudio/best" --add-header "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" --add-header "Origin:https://www.pw.live" --add-header "Referer:https://www.pw.live/" -o "{output_filename}" "{original_url}"'
                            yt_ok, yt_err = await smart_async_download(yt_cmd2, log_file=f"{temp_name}_dl_yt2.log", user_id=user_id)

                        stop_event.set(); await loop_task
                        if yt_ok and os.path.exists(output_filename) and os.path.getsize(output_filename) > 1000:
                            logger.info(f"✅ yt-dlp 403 fallback succeeded for: {clean_title}")
                            success = True
                            error_reason = ""
                        else:
                            logger.warning(f"yt-dlp 403 fallback also failed for: {clean_title} - {yt_err[:100]}")

                    if not success:
                        logger.error(f"DOWNLOAD FAILED: {error_reason}")
                        stop_event.set(); await loop_task
                        
                        debug_logs = ""
                        log_path = f"{temp_name}_dl.log"
                        if os.path.exists(log_path):
                            try:
                                with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                                    lines = lf.readlines()
                                    debug_logs = "\n".join([line.strip() for line in lines[-15:] if line.strip()]).strip()
                            except Exception:
                                pass
                                
                        try:
                            msg_text = format_failure_report(clean_title, index_str, error_reason, is_pdf=False, logs=debug_logs)
                            await client.send_message(Config.LOG_CHANNEL_ID, msg_text)
                        except Exception:
                            pass
                            
                        BATCH_PROGRESS[batch_id][task_id] = f"❌ **Video Failed [Index: {index_str}]**\n`{clean_title[:40]}`\n*(Reason: {str(error_reason)[:60]})*"
                        local_cleanup(expected_out, f"{temp_name}.m4a", f"{temp_name}.webm", f"{temp_name}.mp4", f"{temp_name}.mkv")
                        try: shutil.rmtree(f"temp_chunks_{temp_name}", ignore_errors=True)
                        except: pass

                        # Ensure queue isn't blocked by marking this index as failed
                        completed_uploads[count] = (None, display_title, topic_name)
                        await try_forward_videos()
                        return

                    stop_event.set(); await loop_task

                    job_data = {
                        "is_video": True,
                        "output_filename": output_filename,
                        "display_filename": display_filename,
                        "caption": caption,
                        "display_title": display_title,
                        "topic_name": topic_name,
                        "temp_name": temp_name,
                        "count_idx": count,
                        "thumb_path": thumb_path,
                        "url": url
                    }
                    BATCH_PROGRESS[batch_id][task_id] = f"✅ **Downloaded Video.** Added to Upload Queue."
                    await upload_queue.put(job_data)
                    local_cleanup(f"{temp_name}.m4a", f"{temp_name}.webm", f"{temp_name}.mp4", f"{temp_name}.mkv")
                    try: shutil.rmtree(f"temp_chunks_{temp_name}", ignore_errors=True)
                    except: pass
                    await asyncio.sleep(2)
                    if batch_id in BATCH_PROGRESS and task_id in BATCH_PROGRESS[batch_id]: del BATCH_PROGRESS[batch_id][task_id]

            except Exception as e:
                logger.error(f"Task error: {e}")
                if not stop_event.is_set(): stop_event.set(); await loop_task
                
                # Send permanent notification to LOG_CHANNEL_ID
                try:
                    await client.send_message(Config.LOG_CHANNEL_ID, f"🚨 **Task Error [Index: {index_str}]:** `{clean_title[:100]}`\n\n**Error:** {str(e)[:300]}")
                except Exception:
                    pass
                    
                BATCH_PROGRESS[batch_id][task_id] = f"🚨 **Task Error [Index: {index_str}]:** `{e}`"
                if count not in completed_uploads:
                    completed_uploads[count] = (None, display_title, topic_name)
                    await try_forward_videos()

            finally:
                local_cleanup(f"{temp_name}.m4a", f"{temp_name}.webm", f"{temp_name}.mp4", f"{temp_name}.mkv")
                try: shutil.rmtree(f"temp_chunks_{temp_name}", ignore_errors=True)
                except: pass
                if count not in completed_uploads and not pdf_success and not success:
                    completed_uploads[count] = (None, display_title, topic_name)
                    await try_forward_videos()

    if status_msg:
        try: await safe_edit(status_msg, "🚀 **Starting batch upload!**")
        except: pass

    if batch_id in BATCH_PROGRESS and "scan" in BATCH_PROGRESS[batch_id]:
        del BATCH_PROGRESS[batch_id]["scan"]

    download_tasks = [asyncio.create_task(process_download(c, items[c])) for c in target_indices if 0 <= c < total_items]
    if download_tasks:
        await asyncio.gather(*download_tasks)


    # Put None tokens to terminate all workers based on MAX_PARALLEL_TASKS
    for _ in range(MAX_PARALLEL_TASKS):
        await upload_queue.put(None)
    await asyncio.gather(*worker_tasks)
    
    was_cancelled = cancel_notified or (user_id in CANCEL_TASKS)
    while user_id in CANCEL_TASKS:
        CANCEL_TASKS.remove(user_id)
    ACTIVE_USERS_PROCESSING.discard(user_id)

    if not was_cancelled:
        # Generate the final index
        formatted_batch = to_custom_font(batch_name)
        index_text = f"ʙᴀᴛᴄʜ: {formatted_batch} ʙᴀᴛᴄʜ\n\n"
        
        idx = 1
        for topic, link in topic_links.items():
            formatted_topic = to_custom_font(topic)
            if show_index:
                formatted_num = to_custom_font(str(idx))
                index_text += f"{formatted_num}. [{formatted_topic}]({link})\n\n"
            else:
                index_text += f"• [{formatted_topic}]({link})\n\n"
            idx += 1
            
        forward_chat = log_channel_id if log_channel_id else target_chat_id
        await client.send_message(forward_chat, index_text, disable_web_page_preview=True)
        if forward_chat != user_id:
            await client.send_message(user_id, "🎉 **Bhai, TXT File Ki Saari Videos Aur PDFs Process Ho Gayi!** 🚀\nAur tere specific Target Channel me bhej di gayi hai.")
        else:
            await client.send_message(user_id, "🎉 **Bhai, TXT File Ki Saari Videos Aur PDFs Process Ho Gayi!** 🚀")
            
        # Removed clear tracking database call
        logger.info(f"Batch completed: {batch_name}")
    else:
        logger.info(f"Batch cancelled/stopped: {batch_name}")

    await remove_active_batch(user_id)
    master_stop_event.set()
    if batch_id in BATCH_PROGRESS:
        del BATCH_PROGRESS[batch_id]
    try:
        if was_cancelled:
            await safe_edit(master_msg, f"🛑 **Batch Cancelled/Stopped:** `{batch_name}`\n━━━━━━━━━━━━━━━━━\n⚠️ Process stopped by Admin/User.")
        else:
            await safe_edit(master_msg, f"✅ **Batch Completed:** `{batch_name}`\n━━━━━━━━━━━━━━━━━\n🎉 All tasks finished successfully.")
    except Exception: pass

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

UPLOAD_STATE = {}

@Client.on_message(filters.document & filters.private)
async def handle_txt_file(client: Client, message):
    if message.document.file_name == "cookies.txt":
        from config import Config
        if message.chat.id != Config.OWNER_ID: return
        await message.download(file_name="cookies.txt")
        return await message.reply_text("✅ **Cookies successfully updated!** yt-dlp will now use these new cookies.\\n\\n*(Note: YouTube might invalidate them again if used heavily from a datacenter. Keep a fresh export handy!)*")
        
    if not message.document.file_name.endswith('.txt'): return
    user_id = message.chat.id
    if STOP_ALL_FLAG and user_id != Config.OWNER_ID:
        return await message.reply_text("⛔ **Admin Notice:** All uploads & extractions are currently STOPPED by Admin. Please try again later!")
    if not await is_premium_user(user_id):
        return await message.reply_text(
            "❌ **Access Denied! Sirf Premium Users hi TXT File se Videos Extract/Download kar sakte hain.**\n\n"
            "💳 **Premium Subscription lene ke liye Admin se contact karein:**\n"
            "👉 **Contact Admin:** @officialmarco22\n\n"
            "ℹ️ *PW Login (`/pwlogin`) sabhi users ke liye Free hai.*"
        )
    if user_id in ACTIVE_USERS_PROCESSING:
        return await message.reply_text("⏳ **Aapka ek Batch already Upload ho raha hai! Pehle use complete hone dein ya /stop dabayein.**")

    while user_id in CANCEL_TASKS:
        CANCEL_TASKS.remove(user_id)
    ACTIVE_USERS_PROCESSING.add(user_id)
    file_path = await client.download_media(message)
    with open(file_path, 'r', encoding='utf-8') as f: 
        content = f.read()
        lines = content.splitlines()
    base_name = message.document.file_name.replace(".txt", "").replace("_", " ")
    batch_name = base_name

    status_msg = await message.reply_text("⏳ **TXT File process kar raha hu...**")

    local_cleanup(file_path)

    items = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("=") or line.startswith("🏴‍☠️") or line.startswith("📚") or line.startswith("📂") or line.startswith("🖼️") or line.startswith("Thumb URL"):
            continue

        if "http" in line:
            import re
            urls = re.findall(r'https?://[^\s]+', line)
            if urls:
                url = urls[0]
                thumb_url = urls[1] if len(urls) > 1 else None
                
                raw_title = line.split("http")[0].strip().rstrip(":")
                url_lower = url.lower()
                is_video = (".mpd" in url_lower) or (".m3u8" in url_lower) or ("youtu" in url_lower) or (".mp4" in url_lower) or (".mkv" in url_lower) or (".webm" in url_lower) or (".avi" in url_lower) or (".mov" in url_lower)
                
                items.append({"title": raw_title, "url": url, "is_video": is_video, "thumb_url": thumb_url})

    if not items: return await status_msg.edit_text("❌ **Is file me koi valid link nahi mila!**")

    show_index = not ("NEW" in message.document.file_name)
    UPLOAD_STATE[user_id] = {"batch_name": batch_name, "items": items, "show_index": show_index}
    await status_msg.delete()

    buttons = [
        [InlineKeyboardButton("📱 360p (Save Data)", callback_data="quality:360", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton("🖥️ 480p (Standard)", callback_data="quality:480", style=enums.ButtonStyle.PRIMARY)],
        [InlineKeyboardButton("⚡ 720p (HD Quality)", callback_data="quality:720", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton("💎 1080p (Full HD)", callback_data="quality:1080", style=enums.ButtonStyle.DANGER)],
        [InlineKeyboardButton("━━━ 👑 ᴍᴀʀᴄᴏxᴜᴘʟᴏᴅᴇʀ ━━━", callback_data="quality:720", style=enums.ButtonStyle.SUCCESS)]
    ]
    await message.reply_text(
        f"━━━ ⚙️ **ᴠɪᴅᴇᴏ Qᴜᴀʟɪᴛʏ ꜱᴇʟᴇᴄᴛɪᴏɴ** ⚙️ ━━━\n\n"
        f"🎞️ **Batch:** `{batch_name}`\n"
        f"〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
        f" ▸ **ᴄʟɪᴄᴋ ᴀ ʀᴇꜱᴏʟᴜᴛɪᴏɴ ʙᴇʟᴏᴡ ᴛᴏ sᴛᴀʀᴛ** ◂ ",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@Client.on_callback_query(filters.regex(r"^quality:"))
async def quality_callback(client: Client, callback_query):
    quality = callback_query.data.split(":")[1]
    user_id = callback_query.message.chat.id
    await callback_query.message.delete()

    state = UPLOAD_STATE.get(user_id)
    if not state:
        return await client.send_message(user_id, "⚠️ **State lost. Please upload the TXT file again.**")

    batch_name = state["batch_name"]
    items = state["items"]
    show_index = state.get("show_index", True)

    await client.send_message(
        user_id,
        "🔢 **Koun-koun se Index upload karne hain?**\n\n"
        "👉 **Option 1 (Normal Start):** Shuruat ka number likho (e.g., `1` ya `51` -> wahan se end tak chalega)\n"
        "👉 **Option 2 (Selected / Failed Retry):** Specific index numbers ya range likho (comma se), e.g., `510, 511, 513, 515` ya `10-15, 25`\n\n"
        "Likh kar bhejo:"
    )
    try:
        index_msg = await client.listen(user_id, timeout=60)
        raw_text = index_msg.text.strip()
        selected_indices = parse_index_selection(raw_text, len(items))
        if selected_indices:
            start_index = selected_indices[0] + 1
        else:
            start_index = max(1, min(len(items), int(raw_text)))
    except Exception:
        selected_indices = None
        start_index = 1
        await client.send_message(user_id, "⚠️ Invalid ya Timeout. Index 1 se shuru kar raha hu.")

    await client.send_message(user_id, "🏷️ **Caption me 'Extracted By' kiska naam likhna hai?**\n(Jaise: `⚡ ᴍᴀʀᴄᴏxᴜᴘʟᴏᴅᴇʀ ⚡` ya `@officialmarco22`):")
    try:
        name_msg = await client.listen(user_id, timeout=60)
        custom_name = name_msg.text.strip()
    except Exception:
        custom_name = "⚡ ᴍᴀʀᴄᴏxᴜᴘʟᴏᴅᴇʀ ⚡"
        await client.send_message(user_id, f"⚠️ Timeout. Default name '{custom_name}' set kar diya.")

    await client.send_message(user_id, "📢 **Log Channel ID kya hai?**\n(Agar koi specific channel me upload karna hai to ID likho, varna 'no' likho):")
    try:
        log_msg = await client.listen(user_id, timeout=60)
        log_input = log_msg.text.strip()
        if log_input.lower() == 'no' or not log_input:
            log_channel_id = None
        else:
            try:
                log_channel_id = int(log_input)
                if -100 < log_channel_id < 1000000: # Garbage small numbers like '1'
                    log_channel_id = None
            except ValueError:
                log_channel_id = None
    except Exception:
        log_channel_id = None
        await client.send_message(user_id, f"⚠️ Timeout. Default channel (None) set kar diya.")

    target_chat_id = log_channel_id if log_channel_id else user_id

    try:
        if start_index == 1:
            pinned_msg = await client.send_message(Config.LOG_CHANNEL_ID, f"📌 **Starting Batch Extraction:** `{batch_name}`")
            await client.pin_chat_message(Config.LOG_CHANNEL_ID, pinned_msg.id)
    except Exception as e:
        logger.warning(f"Could not pin message in target chat: {e}")

    status_msg = await client.send_message(user_id, "⏳ **Analyzer Start kar raha hu...**")
    await setup_re_binary()

    asyncio.create_task(execute_batch(client, user_id, target_chat_id, log_channel_id, batch_name, quality, custom_name, start_index, items, status_msg, show_index=show_index, selected_indices=selected_indices))


@Client.on_message(filters.command("clearbatch") & filters.private & filters.user(Config.OWNER_ID))
async def clear_batch_command(client: Client, message):
    if len(message.command) < 2:
        return await message.reply("Usage: `/clearbatch <Batch Name or User ID>`")
    target = message.text.split(" ", 1)[1].strip()
    from database.mongo_db import batches_col
    query = {"$or": [{"batch_name": {"$regex": f"^{re.escape(target)}$", "$options": "i"}}]}
    if target.isdigit():
        uid = int(target)
        if uid not in CANCEL_TASKS:
            CANCEL_TASKS.append(uid)
        query["$or"].append({"user_id": uid})
    res = await batches_col.delete_many(query)
    await message.reply(f"🛑 **Batch Stopped & Cleared:** `{target}` (Removed {res.deleted_count} tasks from DB)")

@Client.on_message(filters.command(["clearallbatches", "clearactive", "purgeactive"]) & filters.private & filters.user(Config.OWNER_ID))
async def clear_all_batches_command(client: Client, message):
    from database.mongo_db import batches_col
    res = await batches_col.delete_many({})
    await message.reply_text(f"🧹 **SUCCESS!** Wiped all active/stuck batch tasks from database.\n📊 **Deleted Tasks Count:** `{res.deleted_count}`")

async def resume_all_batches(client: Client):
    from database.mongo_db import get_all_active_batches, remove_active_batch, is_premium_user
    try:
        batches = await get_all_active_batches()
        for b in batches:
            user_id = b["user_id"]
            # 🔒 Security: Check if user is still a Premium user. Non-premium users' active batches are purged!
            if not await is_premium_user(user_id):
                await remove_active_batch(user_id)
                logger.info(f"Purged stuck active batch '{b.get('batch_name')}' for non-premium user {user_id}")
                continue

            target_chat_id = b["target_chat_id"]
            log_channel_id = b.get("log_channel_id")
            batch_name = b["batch_name"]
            quality = b["quality"]
            custom_name = b["custom_name"]
            start_index = b["start_index"]
            items = b["items"]
            show_index = b.get("show_index", True)

            status_msg = await client.send_message(Config.LOG_CHANNEL_ID, f"🔄 **Auto-Resuming Batch:** {batch_name}\n🚀 **From Index:** {start_index}\n\n*(Server restart hua tha, wahin se continue kar raha hu...)*")
            import asyncio
            asyncio.create_task(execute_batch(client, user_id, target_chat_id, log_channel_id, batch_name, quality, custom_name, start_index, items, status_msg, show_index=show_index))
    except Exception as e:
        import logging
        logging.error(f"Error resuming batches: {e}")

@Client.on_callback_query(filters.regex(r"^uptxt:"))
async def txt_upload_button_callback(client: Client, callback_query):
    action = callback_query.data.split(":")[1]
    msg = callback_query.message
    if not msg.document or not msg.document.file_name.endswith('.txt'):
        return await callback_query.answer("❌ Is message me .txt file nahi hai!", show_alert=True)
        
    if action == "all":
        await callback_query.answer("🚀 Starting Upload All...", show_alert=False)
        await handle_txt_file(client, msg)
    elif action == "sel":
        from pyrogram.types import ForceReply
        await client.send_message(
            msg.chat.id,
            "🔢 **Kaunsa Index ya Range upload karna hai?**\n\n"
            "👉 Example:\n"
            "• `51` (Sirf 51st)\n"
            "• `10-20` (10 se 20 tak)\n"
            "• `1, 5, 12` (Specific index)\n\n"
            "*(Is message ka reply karke index likhein!)*",
            reply_to_message_id=msg.id,
            reply_markup=ForceReply(selective=True)
        )
        await callback_query.answer()



@Client.on_message(filters.document & filters.private)
async def handle_cookies_file(client, message):
    from config import Config
    import os
    if message.chat.id != Config.OWNER_ID:
        return
    if message.document.file_name == "cookies.txt":
        await message.download(file_name="cookies.txt")
        await message.reply_text("✅ **Cookies successfully updated!** yt-dlp will now use these new cookies.\n\n*(Note: YouTube might invalidate them again if used heavily from a datacenter. Keep a fresh export handy!)*")
