import aiohttp
import os
import json
import logging
import asyncio
import uuid
import time
import re
import aiofiles
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from database.mongo_db import get_user_tokens, is_premium_user
from config import Config
import aiofiles.os

# 🟢 ULTRA-DEBUGGING ON
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

CREDIT = "\n\n〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n👑 **ᴅᴇᴠᴇʟᴏᴘᴇᴅ & ᴏᴡɴᴇᴅ ʙʏ: @rishisin070**\n⚡ *Powered by Cinemaa Boxoffice [C_B]*"

_extractor_session: aiohttp.ClientSession | None = None
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=40, connect=15)

async def get_extractor_session() -> aiohttp.ClientSession:
    global _extractor_session
    if _extractor_session is None or _extractor_session.closed:
        _extractor_session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
    return _extractor_session

def get_god_headers(token):
    clean_token = token
    custom_random = None
    if "|" in clean_token:
        parts = clean_token.split("|", 1)
        clean_token = parts[0]
        custom_random = parts[1].strip()
        
    clean_token = clean_token.replace("Bearer ", "").replace("bearer ", "").strip()
    
    import hashlib
    if custom_random:
        final_random_id = custom_random
    else:
        token_hash = hashlib.md5(clean_token.encode()).hexdigest()
        final_random_id = f"{token_hash[:8]}-{token_hash[8:12]}-{token_hash[12:16]}-{token_hash[16:20]}-{token_hash[20:32]}"

    return {
        'authorization': f"Bearer {clean_token}",
        'client-id': '5eb393ee95fab7468a79d189',
        'client-type': 'WEB',
        'client-version': '4.6.0',
        'content-type': 'application/json',
        'origin': 'https://www.pw.live',
        'randomid': final_random_id, 
        'referer': 'https://www.pw.live/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
        'x-sdk-version': '0.0.20'
    }

from pyrogram import enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

USER_BATCHES_CACHE = {}

async def send_batches_page(client, chat_id, page, msg=None):
    batches = USER_BATCHES_CACHE.get(chat_id, [])
    if not batches:
        return
    
    total_batches = len(batches)
    per_page = 5
    total_pages = (total_batches + per_page - 1) // per_page
    
    start = page * per_page
    end = start + per_page
    current_batches = batches[start:end]
    
    text = (
        f"━━━ 📚 **ʏᴏᴜʀ ᴀᴄᴛɪᴠᴇ ʙᴀᴛᴄʜᴇꜱ** 📚 ━━━\n\n"
        f"🔹 **Total Batches:** `{total_batches}`  |  **Page:** `{page+1}/{total_pages}`\n"
        f"〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n\n"
    )
    for b in current_batches:
        text += f"👤 Acc {b['account']} | ▪️ **{b['name']}**\n   └ 🆔: `{b['id']}`\n\n"
        
    text += " ▸ **ᴄʟɪᴄᴋ ᴀ ʙᴀᴛᴄʜ ʙᴇʟᴏᴡ ᴛᴏ ᴏᴘᴇɴ ᴍᴇɴᴜ** ◂ "
    
    buttons = []
    for b in current_batches:
        buttons.append([InlineKeyboardButton(f"📂 ➭ {b['name'][:30]}", callback_data=f"selbatch:{b['id']}", style=enums.ButtonStyle.SUCCESS)])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◄ Prev", callback_data=f"page:batch:{page-1}", style=enums.ButtonStyle.PRIMARY))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ►", callback_data=f"page:batch:{page+1}", style=enums.ButtonStyle.PRIMARY))
    if nav:
        buttons.append(nav)
        
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
    
    try:
        if msg:
            await msg.edit_text(text + CREDIT, reply_markup=reply_markup)
        else:
            await client.send_message(chat_id, text + CREDIT, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Pagination error: {e}")

@Client.on_callback_query(filters.regex(r"^page:batch:"))
async def batch_page_callback(client: Client, callback_query):
    page = int(callback_query.data.split(":")[2])
    await send_batches_page(client, callback_query.message.chat.id, page, callback_query.message)

@Client.on_callback_query(filters.regex(r"^selbatch:"))
async def batch_action_menu_callback(client: Client, callback_query):
    batch_id = callback_query.data.split(":")[1]
    buttons = [
        [
            InlineKeyboardButton("⚡ Aaj Ke Lectures (/today)", callback_data=f"extact:today:{batch_id}", style=enums.ButtonStyle.SUCCESS),
            InlineKeyboardButton("📥 Poora Batch (Full)", callback_data=f"extact:full:{batch_id}", style=enums.ButtonStyle.PRIMARY)
        ],
        [
            InlineKeyboardButton("📖 Subject-Wise Extract", callback_data=f"selsubj:{batch_id}", style=enums.ButtonStyle.SUCCESS)
        ],
        [InlineKeyboardButton("🔙 Back To Batches List", callback_data="page:batch:0", style=enums.ButtonStyle.DANGER)]
    ]
    await callback_query.message.edit_text(
        f"━━━ 🎯 **ACTIVE BATCH SELECTED** 🎯 ━━━\n\n"
        f"➤ **Batch ID:** `{batch_id}` 🏷️\n"
        f"➤ **Status:** `Unlocked (Level-3 DRM)` 🔓\n\n"
        f"🎁 **EXTRACTION OPTIONS** 🎁\n\n"
        f"➜ **Daily Lectures** ➭ Latest Classes ⚡\n"
        f"➜ **Full Batch** ➭ Complete Backup 📥\n"
        f"➜ **Subject-Wise** ➭ Specific Folders 📂\n\n"
        f"〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
        f" ▸ **CLICK BELOW TO EXTRACT DATA** ◂ ",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def show_subject_selection_menu(client: Client, chat_id: int, batch_id: str, message=None):
    from database.mongo_db import get_user_tokens
    tokens = await get_user_tokens(chat_id)
    if not tokens:
        text = "❌ Token not found. pehle login karein."
        return await message.reply_text(text) if message else await client.send_message(chat_id, text)
    headers = get_god_headers(tokens[0])
        
    url = f"https://api.penpencil.co/v3/batches/{batch_id}/details"
    session = await get_extractor_session()
    try:
        async with session.get(url, headers=headers) as res:
            if res.status != 200:
                text = "❌ Batch Details load nahi hue."
                return await message.edit_text(text) if message and hasattr(message, 'edit_text') else await client.send_message(chat_id, text)
            data = await res.json()
            subjects = data.get("data", {}).get("subjects", [])
            batch_name = data.get("data", {}).get("name", "Batch")
            
            if not subjects:
                text = "❌ Is batch me koi subject nahi mila."
                return await message.edit_text(text) if message and hasattr(message, 'edit_text') else await client.send_message(chat_id, text)
                
            buttons = []
            for s in subjects:
                s_name = (s.get("subject") or s.get("name") or "Unknown")[:30]
                s_id = s.get("_id")
                buttons.append([InlineKeyboardButton(f"📖 ➭ {s_name}", callback_data=f"extsubj:{batch_id}:{s_id}", style=enums.ButtonStyle.PRIMARY)])
            buttons.append([InlineKeyboardButton("🔙 Back To Batch Menu", callback_data=f"selbatch:{batch_id}", style=enums.ButtonStyle.DANGER)])
            
            text = (
                f"━━━ 📖 **SUBJECT SELECTION** 📖 ━━━\n\n"
                f"➤ **Batch:** `{batch_name}` 📚\n\n"
                f"🎁 **AVAILABLE SUBJECTS** 🎁\n\n"
                f"➜ Click on any subject to extract its full contents 📂\n\n"
                f"〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
                f" ▸ **CLICK A SUBJECT BELOW TO START** ◂ "
            )
            reply_markup = InlineKeyboardMarkup(buttons)
            if message and hasattr(message, 'edit_text'):
                try:
                    await message.edit_text(text, reply_markup=reply_markup)
                except Exception as edit_err:
                    if "MESSAGE_NOT_MODIFIED" not in str(edit_err):
                        raise edit_err
            else:
                await client.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Subject menu error: {e}")
        if "MESSAGE_NOT_MODIFIED" not in str(e):
            error_msg = f"❌ **Error loading subjects:**\n`{str(e)}`\n\n__Please send a screenshot of this error.__"
            if message and hasattr(message, 'edit_text'):
                await message.edit_text(error_msg)
            else:
                await client.send_message(chat_id, error_msg)

@Client.on_callback_query(filters.regex(r"^selsubj:"))
async def select_subject_menu_callback(client: Client, callback_query):
    batch_id = callback_query.data.split(":")[1]
    await show_subject_selection_menu(client, callback_query.message.chat.id, batch_id, callback_query.message)

@Client.on_callback_query(filters.regex(r"^extsubj:"))
async def execute_subject_extraction_callback(client: Client, callback_query):
    parts = callback_query.data.split(":")
    batch_id = parts[1]
    sub_id = parts[2]
    
    class DummyMessage:
        def __init__(self, chat_id, cmd_list, orig_msg):
            self.chat = orig_msg.chat
            self.chat.id = chat_id
            self.command = cmd_list
            self._orig = orig_msg
        async def reply_text(self, *args, **kwargs):
            return await client.send_message(self.chat.id, *args, **kwargs)
            
    dummy = DummyMessage(callback_query.message.chat.id, ["subject", batch_id], callback_query.message)
    await callback_query.message.edit_text(f"🚀 **1-Click Subject Extraction: `{batch_id}`...**")
    await extract_batch(client, dummy, target_sub_id=sub_id)

@Client.on_callback_query(filters.regex(r"^extact:"))
async def execute_batch_action_callback(client: Client, callback_query):
    parts = callback_query.data.split(":")
    mode = parts[1]
    batch_id = parts[2]
    
    class DummyMessage:
        def __init__(self, chat_id, cmd_list, orig_msg):
            self.chat = orig_msg.chat
            self.chat.id = chat_id
            self.command = cmd_list
            self._orig = orig_msg
        async def reply_text(self, *args, **kwargs):
            return await client.send_message(self.chat.id, *args, **kwargs)
            
    cmd = "today" if mode == "today" else "getbatch"
    dummy = DummyMessage(callback_query.message.chat.id, [cmd, batch_id], callback_query.message)
    await callback_query.message.edit_text(f"🚀 **1-Click Extraction: `{cmd} {batch_id}`...**")
    await extract_batch(client, dummy)



@Client.on_message(filters.command("mybatches") & filters.private)
async def my_batches_list(client: Client, message):
    if not await is_premium_user(message.chat.id):
        return await message.reply_text(
            "❌ **Access Denied! Sirf Premium Users hi Batches View/Extract kar sakte hain.**\n\n"
            "💳 **Premium Subscription lene ke liye Admin se contact karein:**\n"
            "👉 **Contact Admin:** @rishisin070\n\n"
            "ℹ️ *PW Login (`/pwlogin`) sabhi users ke liye Free hai.*"
        )
        
    tokens = await get_user_tokens(message.chat.id)
    if not tokens:
        return await message.reply_text("❌ **Pehle login kar le!** `/pwlogin` daba.")

    status_msg = await message.reply_text("⏳ **Tere saare accounts scan kar raha hu...**")
    api_url = "https://api.penpencil.co/v3/batches/my-batches?page=1&mode=1"
    
    USER_BATCHES_CACHE[message.chat.id] = []

    try:
        session = await get_extractor_session()
        for index, token in enumerate(reversed(tokens), 1):
            headers = get_god_headers(token)
            try:
                async with session.get(api_url, headers=headers) as res:
                    if res.status == 200:
                        batches_data = await res.json()
                        batches = batches_data.get("data", [])
                        if batches:
                            for b in batches:
                                batch_info = b.get("batch", b)
                                b_name = batch_info.get("name", "Unknown")
                                b_id = batch_info.get("_id") or batch_info.get("id", "Unknown")
                                USER_BATCHES_CACHE[message.chat.id].append({
                                    "account": index,
                                    "name": b_name,
                                    "id": b_id
                                })
            except Exception as e:
                logger.warning(f"Account {index} error: {e}")
                continue

        if not USER_BATCHES_CACHE[message.chat.id]:
            return await status_msg.edit_text("⚠️ **Kisi bhi account me koi active batch nahi mila!**")

        await send_batches_page(client, message.chat.id, 0, status_msg)
    except Exception as e:
        logger.error(f"mybatches error: {e}")
        await status_msg.edit_text(f"🚨 **System Crash:** `{e}`")


@Client.on_message(filters.command(["getbatch", "today", "date", "daterange", "fromdate", "datefrom", "subject", "subjects"]) & filters.private)
async def extract_batch(client: Client, message, target_sub_id=None):
    if not await is_premium_user(message.chat.id):
        return await message.reply_text(
            "❌ **Access Denied! Sirf Premium Users hi Batches Extract kar sakte hain.**\n\n"
            "💳 **Premium Subscription lene ke liye Admin se contact karein:**\n"
            "👉 **Contact Admin:** @rishisin070\n\n"
            "ℹ️ *PW Login (`/pwlogin`) sabhi users ke liye Free hai.*"
        )

    tokens = await get_user_tokens(message.chat.id)
    if not tokens:
        return await message.reply_text("❌ **Pehle login kar le!** `/pwlogin` daba.")

    cmd_name = message.command[0].lower()
    
    if len(message.command) < 2:
        if cmd_name == "getbatch":
            return await message.reply_text("⚠️ **Format galat hai!**\nAise likh: `/getbatch BATCH_ID`")
        elif cmd_name == "today":
            return await message.reply_text("⚠️ **Format galat hai!**\nAise likh: `/today BATCH_ID`")
        elif cmd_name in ["subject", "subjects"]:
            return await message.reply_text("⚠️ **Format galat hai!**\nAise likh: `/subject BATCH_ID`")
        elif cmd_name in ["daterange", "fromdate", "datefrom"]:
            return await message.reply_text("⚠️ **Format galat hai!**\nAise likh:\n1️⃣ **Date se Aaj Tak:** `/fromdate BATCH_ID YYYY-MM-DD`\n2️⃣ **Date se Date Tak:** `/daterange BATCH_ID START_DATE END_DATE`\n\nExample: `/fromdate 641abcd 2026-07-20`")
        else:
            return await message.reply_text("⚠️ **Format galat hai!**\nAise likh: `/date BATCH_ID YYYY-MM-DD`")

    batch_id = message.command[1]
    if cmd_name in ["subject", "subjects"] and not target_sub_id and len(message.command) == 2:
        return await show_subject_selection_menu(client, message.chat.id, batch_id, message)

    
    date_filter = None
    if cmd_name == "today":
        import datetime
        import pytz
        IST = pytz.timezone("Asia/Kolkata")
        today_str = datetime.datetime.now(IST).strftime("%Y-%m-%d")
        date_filter = (today_str, today_str)
    elif cmd_name == "date":
        if len(message.command) != 3:
            return await message.reply_text("⚠️ **Format galat hai!**\nAise likh: `/date BATCH_ID YYYY-MM-DD`\nExample: `/date 641abcd 2026-06-08`")
        date_filter = (message.command[2], message.command[2])
    elif cmd_name in ["daterange", "fromdate", "datefrom"]:
        if len(message.command) < 3:
            return await message.reply_text("⚠️ **Format galat hai!**\nAise likh:\n1️⃣ **Date se Aaj Tak:** `/fromdate BATCH_ID YYYY-MM-DD`\n2️⃣ **Date se Date Tak:** `/daterange BATCH_ID START_DATE END_DATE`\n\nExample: `/fromdate 641abcd 2026-07-20`")
        s_date = message.command[2]
        if len(message.command) >= 4:
            e_date = message.command[3]
        else:
            import datetime
            import pytz
            IST = pytz.timezone("Asia/Kolkata")
            e_date = datetime.datetime.now(IST).strftime("%Y-%m-%d")
        date_filter = (s_date, e_date)
    status_msg = await message.reply_text(f"⏳ **Batch `{batch_id}` ko Validate kar raha hu...**")
    valid_token = None
    details_data = None

    try:
        session = await get_extractor_session()

        for tkn in reversed(tokens):
            headers = get_god_headers(tkn)
            try:
                async with session.get(f"https://api.penpencil.co/v3/batches/{batch_id}/details", headers=headers) as res:
                    if res.status == 200:
                        details_data = await res.json()
                        valid_token = tkn
                        break
                    elif res.status == 401:
                        from database.mongo_db import remove_dead_token
                        await remove_dead_token(message.chat.id, tkn)
                        try: await status_msg.reply("⚠️ **Aapka ek Token expire ho gaya tha (Auto-Removed).** Kripya nayi access ke liye `/pwlogin` se dubara login karein!")
                        except: pass
            except Exception:
                continue

        if not valid_token:
            return await status_msg.edit_text("❌ **Batch Access Denied! (Tokens expired ya batch kharida nahi hai)**")

        # THUMBNAIL EXTRACTION
        batch_thumbnail = ""
        try:
            preview_img = details_data.get("data", {}).get("previewImage", {})
            if isinstance(preview_img, dict) and preview_img.get("baseUrl") and preview_img.get("key"):
                batch_thumbnail = preview_img.get("baseUrl") + preview_img.get("key")
        except Exception as e:
            logger.debug(f"Thumbnail extraction error: {e}")

        subjects = details_data.get("data", {}).get("subjects", [])
        if not subjects:
            return await status_msg.edit_text("❌ **Batch me koi subject nahi mila!**")

        if target_sub_id:
            subjects = [s for s in subjects if str(s.get("_id")) == str(target_sub_id)]
            if not subjects:
                return await status_msg.edit_text("❌ **Selected Subject is batch me nahi mila!**")
        elif cmd_name in ["subject", "subjects"] and len(message.command) >= 3:
            sub_query = " ".join(message.command[2:]).lower()
            subjects = [s for s in subjects if sub_query in s.get("name", "").lower()]
            if not subjects:
                return await status_msg.edit_text(f"❌ **Subject '{sub_query}' is batch me nahi mila!**")

        # Filename Sanitization
        raw_batch_name = details_data["data"].get("name", "Extracted_Batch")
        batch_name = re.sub(r'[\\/*?:"<>|]', "", raw_batch_name).replace(" ", "_")
        
        await status_msg.edit_text(f"✅ **Batch Validated!**\n🔥 **TRIPLE-API ENGINE ACTIVE:** Extracting with Perfect Sequence & Speed...")

        headers = get_god_headers(valid_token)
        total_items = 0
        sub_suffix = f"_{re.sub(r'[\\/*?:\"<>|]', '', subjects[0].get('name', 'Sub')).replace(' ', '_')}" if (target_sub_id or cmd_name in ['subject', 'subjects']) and subjects else ""
        file_name = f"NEW_{batch_name}{sub_suffix}.txt" if date_filter else f"{batch_name}{sub_suffix}.txt"
        
        extracted_data = [
            "=================================\n",
            "⚡ EXTRACTED BY: RISHI C_B EXTRACTOR ⚡\n",
            f"📚 BATCH: {batch_name}\n=================================\n"
        ]

        seen_urls = set()
        last_edit_time = time.time()


        if date_filter:
            s_dt, e_dt = date_filter
            await status_msg.edit_text(f"🚀 **Fast Scan Active!** Fetching schedule (`{s_dt}` to `{e_dt}`) directly from PW Server...")
            page = 1
            while True:
                api_url = f"https://api.penpencil.co/v2/batches/{batch_id}/weekly-schedules?batchId={batch_id}&batchSubjectId=&startDate={s_dt}&endDate={e_dt}&page={page}"
                async with session.get(api_url, headers=headers) as c_res:
                    if c_res.status != 200:
                        err_text = await c_res.text()
                        await status_msg.edit_text(f"⚠️ **API ERROR {c_res.status}:**\n`{err_text[:800]}`\n\nURL: `{api_url}`")
                        break
                    c_data = await c_res.json()
                    items = c_data.get("data", [])
                    if not isinstance(items, list) or not items:
                        # Wait, do not edit status_msg here because it gets overwritten
                        break
                        
                    for item in reversed(items):
                        if not isinstance(item, dict): continue
                        
                        details = item.get("lessonDetails") or item.get("videoDetails") or item.get("notesDetails") or item.get("homeworkDetails") or item.get("bulkScheduleDetails") or item
                        if not isinstance(details, dict): details = item
                        
                        subj_data = details.get("subjectId")
                        sub_name = subj_data.get("name") if isinstance(subj_data, dict) else (details.get("subject") or "Unknown_Subject")
                        
                        raw_topic = details.get("topic") or details.get("name") or details.get("lessonName") or item.get("topic") or item.get("name") or "Class Session"
                        raw_title = raw_topic.replace(":", "-").replace("/", "-").strip()
                        title = f"({sub_name}) {raw_title}"
                        
                        vid_url = details.get("url", "").strip() or details.get("videoUrl", "").strip() or details.get("externalUrl", "").strip() or details.get("fileUrl", "").strip()
                        real_vid_id = item.get("_id") or details.get("_id")
                        
                        if isinstance(details.get("videoDetails"), dict):
                            vid_url = details["videoDetails"].get("videoUrl", "").strip() or details["videoDetails"].get("url", "").strip() or vid_url
                            
                        if vid_url:
                            if "mpd" in vid_url or "m3u8" in vid_url or "youtu" in vid_url:
                                if ("mpd" in vid_url or "m3u8" in vid_url) and real_vid_id:
                                    sep = "&" if "?" in vid_url else "?"
                                    final_url = f"{vid_url}{sep}parentId={batch_id}&childId={real_vid_id}"
                                else:
                                    final_url = vid_url
                                    
                                if final_url not in seen_urls:
                                    seen_urls.add(final_url)
                                    thumb_url = ""
                                    v_details = details.get("videoDetails", {}) if isinstance(details.get("videoDetails"), dict) else {}
                                    thumb = v_details.get("image") or details.get("image") or details.get("teacherImage") or details.get("previewImageUrl") or details.get("thumbnailUrl")
                                    if isinstance(thumb, str) and thumb.startswith("http"):
                                        thumb_url = thumb
                                    elif isinstance(details.get("previewImage"), dict) and details["previewImage"].get("baseUrl"):
                                        thumb_url = details["previewImage"]["baseUrl"] + details["previewImage"]["key"]
                                        
                                    if thumb_url:
                                        extracted_data.append(f"{title}:{final_url} {thumb_url}\n")
                                    else:
                                        extracted_data.append(f"{title}:{final_url}\n")
                                    total_items += 1
                            else:
                                if vid_url not in seen_urls:
                                    seen_urls.add(vid_url)
                                    extracted_data.append(f"{title}:{vid_url}\n")
                                    total_items += 1

                        if isinstance(details.get("attachmentIds"), list):
                            for att in details["attachmentIds"]:
                                try:
                                    if isinstance(att, dict):
                                        pdf_key = att.get("key")
                                        pdf_url = att.get("url") or att.get("fileUrl") or ""
                                        if not pdf_url and pdf_key:
                                            pdf_url = att.get("baseUrl", "") + pdf_key
                                        elif not pdf_url and att.get("baseUrl") and len(att.get("baseUrl")) > 30:
                                            pdf_url = att.get("baseUrl")

                                        if pdf_url and pdf_url not in seen_urls: 
                                            if "mpd" in pdf_url.lower() or "m3u8" in pdf_url.lower():
                                                sep = "&" if "?" in pdf_url else "?"
                                                pdf_url = f"{pdf_url}{sep}parentId={batch_id}&childId={item.get('_id')}"
                                            seen_urls.add(pdf_url)
                                            att_name = att.get("name", "Class Notes").replace(":", "-").replace("/", "-").strip()
                                            extracted_data.append(f"({sub_name}) {att_name}:{pdf_url}\n")
                                            total_items += 1
                                except Exception: pass
                                
                        if isinstance(details.get("homeworkIds"), list):
                            for hw in details["homeworkIds"]:
                                try:
                                    if not isinstance(hw, dict): continue
                                    hw_raw_title = (hw.get("topic") or hw.get("note") or hw.get("name") or "DPP").replace(":", "-").replace("/", "-").strip()

                                    if isinstance(hw.get("attachmentIds"), list):
                                        for att in hw["attachmentIds"]:
                                            if isinstance(att, dict):
                                                pdf_key = att.get("key")
                                                pdf_url = att.get("url") or att.get("fileUrl") or ""
                                                if not pdf_url and pdf_key:
                                                    pdf_url = att.get("baseUrl", "") + pdf_key
                                                elif not pdf_url and att.get("baseUrl") and len(att.get("baseUrl")) > 30:
                                                    pdf_url = att.get("baseUrl")

                                                if pdf_url and pdf_url not in seen_urls:
                                                    seen_urls.add(pdf_url)
                                                    extracted_data.append(f"({sub_name}) {hw_raw_title}:{pdf_url}\n")
                                                    total_items += 1

                                    if hw.get("videoDetails") and isinstance(hw["videoDetails"], dict):
                                        hw_vid_url = hw["videoDetails"].get("videoUrl", "").strip() or hw["videoDetails"].get("url", "").strip()
                                        hw_vid_id = hw.get("_id")
                                        if hw_vid_url:
                                            if ("mpd" in hw_vid_url or "m3u8" in hw_vid_url) and hw_vid_id:
                                                sep = "&" if "?" in hw_vid_url else "?"
                                                final_hw_url = f"{hw_vid_url}{sep}parentId={batch_id}&childId={hw_vid_id}"
                                            else:
                                                final_hw_url = hw_vid_url
                                                
                                            if final_hw_url not in seen_urls:
                                                seen_urls.add(final_hw_url)
                                                extracted_data.append(f"({sub_name}) Solution to {hw_raw_title}:{final_hw_url}\n")
                                                total_items += 1
                                except Exception: pass
                page += 1
                
        else:
            for sub in subjects:
                sub_id = sub["_id"]
                sub_name = sub.get("subject", "Unknown_Subject")
                extracted_data.append(f"\n\n=================================\n📂 SUBJECT: {sub_name}\n=================================\n\n")
            
                topics = [{"_id": "", "name": "Bina Chapter (Root)"}] 
                batch_tag_types = ["UNITS", "STUDY_MATERIAL", "TESTS", "ASSIGNMENT", "NOTES", "NOTICE"] 
            
                for tag_type in batch_tag_types:
                    t_page = 1
                    while t_page <= 10:
                        try:
                            new_topic_api = f"https://api.penpencil.co/batch-service/v1/batch-tags/{batch_id}/subject/{sub_id}/topics?page={t_page}&batchTagType={tag_type}&limit=20&enabled=true"
                            async with session.get(new_topic_api, headers=headers) as t_res:
                                if t_res.status == 200:
                                    t_data = await t_res.json()
                                    new_topics = t_data.get("data", [])
                                    if not isinstance(new_topics, list) or not new_topics: break
                                    for nt in new_topics:
                                        if isinstance(nt, dict) and not any(t["_id"] == nt["_id"] for t in topics):
                                            topics.append(nt)
                                else: break
                        except Exception as e: 
                            logger.debug(f"Topic fetch error: {e}")
                            break
                        t_page += 1

                for topic in topics:
                    topic_id = topic["_id"]
                    topic_name = topic.get("name", "").replace(":", "-").replace("/", "-")
                
                    # FIX 1: "ALL" aage lagaya perfect sequence ke liye, aur specific names aage taaki hidden files pakdi jayein
                    v3_content_types = ["ALL", "LECTURES", "NOTES", "DPP_VIDEOS", "DPP_PDF", "ASSIGNMENT"] 
                
                    for c_type in v3_content_types:
                        skip_count = 0
                        empty_streak = 0  # Reverted to 1 for speed (PW pages are contiguous)
                        retry_count = 0 
                    
                        while empty_streak < 1 and skip_count <= 2000:
                            api_url = f"https://api.penpencil.co/batch-service/v3/batch-subject-schedules/{batch_id}/subject/{sub_id}/contents?skip={skip_count}&limit=50&contentType={c_type}&contentFilter=ALL"
                            if topic_id: 
                                api_url += f"&tagId={topic_id}"
                            
                            # FIX 3: c_type pura print kiya log me confusion hatane ke liye
                            logger.info(f"🔎 Scanning: Sub={sub_name[:10]}, Topic={topic_name[:15]}, Type={c_type}, Skip={skip_count}")
                        
                            try:
                                async with session.get(api_url, headers=headers) as c_res:
                                    if c_res.status == 429:
                                        retry_count += 1
                                        logger.warning(f"⚠️ Rate Limit 429! Retry: {retry_count}")
                                        if retry_count > 3: break
                                        await asyncio.sleep(2)
                                        continue
                                
                                    retry_count = 0 
                                    if c_res.status != 200:
                                        empty_streak += 1
                                        skip_count += 50
                                        continue
                                
                                    c_data = await c_res.json()
                                    items = c_data.get("data", [])
                                
                                    if not isinstance(items, list) or not items:
                                        empty_streak += 1
                                        skip_count += 50
                                        continue
                                
                                    empty_streak = 0 # Data found, reset streak
                                
                                    # Fast Early Exit for /today and /date commands
                                    if date_filter:
                                        all_older = True
                                        for item in items:
                                            chk_inner = item.get("data", item) if isinstance(item, dict) else {}
                                            c_time = str(chk_inner.get("createdAt", "")) or str(item.get("createdAt", "") if isinstance(item, dict) else "")
                                            d_time = str(chk_inner.get("date", "")) or str(item.get("date", "") if isinstance(item, dict) else "")
                                            item_date = c_time[:10] if c_time else (d_time[:10] if d_time else "")
                                            if not item_date or item_date >= date_filter:
                                                all_older = False
                                                break
                                        if all_older:
                                            break # Stop scanning older pages!

                                    for item in reversed(items):
                                        if not isinstance(item, dict): continue
                                    
                                        inner_data = item.get("data", item)
                                        if not isinstance(inner_data, dict): continue
                                    
                                        if date_filter:
                                            c_time = str(inner_data.get("createdAt", "")) or str(item.get("createdAt", ""))
                                            d_time = str(inner_data.get("date", "")) or str(item.get("date", ""))
                                            if date_filter not in c_time and date_filter not in d_time:
                                                continue
                                    
                                        raw_title = (inner_data.get("name") or inner_data.get("topic") or "Unknown").replace(":", "-").replace("/", "-").strip()
                                        if topic_name and topic_name in raw_title:
                                            title = f"({sub_name}) {raw_title}"
                                        else:
                                            title = f"({sub_name}) {topic_name} : {raw_title}" if topic_id else f"({sub_name}) {raw_title}"
                                    
                                        # 1. MAIN LECTURE / E-BOOK / DPP VIDEO
                                        vid_url = inner_data.get("url", "").strip() or inner_data.get("externalUrl", "").strip() or inner_data.get("fileUrl", "").strip()
                                        real_vid_id = inner_data.get("_id")
                                    
                                        if isinstance(inner_data.get("videoDetails"), dict):
                                            vid_url = inner_data["videoDetails"].get("videoUrl", "").strip() or inner_data["videoDetails"].get("url", "").strip() or vid_url
                                            # if inner_data["videoDetails"].get("_id"):
                                                # real_vid_id = inner_data["videoDetails"]["_id"]
                                        
                                        if vid_url:
                                            if "mpd" in vid_url or "m3u8" in vid_url or "youtu" in vid_url:
                                                if ("mpd" in vid_url or "m3u8" in vid_url) and real_vid_id:
                                                    sep = "&" if "?" in vid_url else "?"
                                                    final_url = f"{vid_url}{sep}parentId={batch_id}&childId={real_vid_id}"
                                                else:
                                                    final_url = vid_url
                                                
                                                if final_url not in seen_urls:
                                                    seen_urls.add(final_url)
                                                
                                                    thumb_url = ""
                                                    v_details = inner_data.get("videoDetails", {}) if isinstance(inner_data.get("videoDetails"), dict) else {}
                                                    thumb = v_details.get("image") or inner_data.get("image") or inner_data.get("teacherImage") or inner_data.get("previewImageUrl") or inner_data.get("thumbnailUrl") or item.get("thumbnailUrl")
                                                    if isinstance(thumb, str) and thumb.startswith("http"):
                                                        thumb_url = thumb
                                                    elif isinstance(inner_data.get("previewImage"), dict) and inner_data["previewImage"].get("baseUrl"):
                                                        thumb_url = inner_data["previewImage"]["baseUrl"] + inner_data["previewImage"]["key"]
                                                    elif isinstance(item.get("previewImage"), dict) and item["previewImage"].get("baseUrl"):
                                                        thumb_url = item["previewImage"]["baseUrl"] + item["previewImage"]["key"]
                                                    
                                                    if thumb_url:
                                                        extracted_data.append(f"{title}:{final_url} {thumb_url}\n")
                                                    else:
                                                        extracted_data.append(f"{title}:{final_url}\n")
                                                    total_items += 1
                                            else:
                                                if vid_url not in seen_urls:
                                                    seen_urls.add(vid_url)
                                                    extracted_data.append(f"{title}:{vid_url}\n")
                                                    total_items += 1

                                        # 2. CLASS NOTES & MAIN ATTACHMENTS
                                        if isinstance(inner_data.get("attachmentIds"), list):
                                            for att in inner_data["attachmentIds"]:
                                                try:
                                                    if isinstance(att, dict):
                                                        pdf_key = att.get("key")
                                                        pdf_url = att.get("url") or att.get("fileUrl") or ""
                                                        if not pdf_url and pdf_key:
                                                            pdf_url = att.get("baseUrl", "") + pdf_key
                                                        elif not pdf_url and att.get("baseUrl") and len(att.get("baseUrl")) > 30:
                                                            pdf_url = att.get("baseUrl")

                                                        if pdf_url and pdf_url not in seen_urls: 
                                                            if "mpd" in pdf_url.lower() or "m3u8" in pdf_url.lower():
                                                                sep = "&" if "?" in pdf_url else "?"
                                                                pdf_url = f"{pdf_url}{sep}parentId={batch_id}&childId={inner_data.get('_id')}"
                                                        
                                                            seen_urls.add(pdf_url)
                                                        
                                                            if c_type == "DPP_PDF" or "dpp" in raw_title.lower():
                                                                att_name = raw_title
                                                            else:
                                                                att_name = att.get("name", "Class Notes").replace(":", "-").replace("/", "-").strip()
                                                        
                                                            extracted_data.append(f"({sub_name}) {topic_name} : {att_name}:{pdf_url}\n")
                                                            total_items += 1
                                                except Exception: 
                                                    pass
                                            
                                        # 3. DPP & DPP VIDEO SOLUTIONS
                                        if isinstance(inner_data.get("homeworkIds"), list):
                                            for hw in inner_data["homeworkIds"]:
                                                try:
                                                    if not isinstance(hw, dict): continue
                                                    hw_raw_title = (hw.get("topic") or hw.get("note") or hw.get("name") or "DPP").replace(":", "-").replace("/", "-").strip()

                                                    if isinstance(hw.get("attachmentIds"), list):
                                                        for att in hw["attachmentIds"]:
                                                            if isinstance(att, dict):
                                                                pdf_key = att.get("key")
                                                                pdf_url = att.get("url") or att.get("fileUrl") or ""
                                                                if not pdf_url and pdf_key:
                                                                    pdf_url = att.get("baseUrl", "") + pdf_key
                                                                elif not pdf_url and att.get("baseUrl") and len(att.get("baseUrl")) > 30:
                                                                    pdf_url = att.get("baseUrl")

                                                                if pdf_url and pdf_url not in seen_urls:
                                                                    if "mpd" in pdf_url.lower() or "m3u8" in pdf_url.lower():
                                                                        sep = "&" if "?" in pdf_url else "?"
                                                                        pdf_url = f"{pdf_url}{sep}parentId={batch_id}&childId={hw.get('_id')}"
                                                                
                                                                    seen_urls.add(pdf_url)
                                                                    extracted_data.append(f"({sub_name}) {topic_name} : {hw_raw_title}:{pdf_url}\n")
                                                                    total_items += 1

                                                    if hw.get("videoDetails") and isinstance(hw["videoDetails"], dict):
                                                        hw_vid_url = hw["videoDetails"].get("videoUrl", "").strip() or hw["videoDetails"].get("url", "").strip()
                                                        hw_vid_id = hw.get("_id")
                                                        # if hw["videoDetails"].get("_id"):
                                                            # hw_vid_id = hw["videoDetails"]["_id"]
                                                        
                                                        if hw_vid_url:
                                                            if ("mpd" in hw_vid_url or "m3u8" in hw_vid_url) and hw_vid_id:
                                                                sep = "&" if "?" in hw_vid_url else "?"
                                                                final_hw_url = f"{hw_vid_url}{sep}parentId={batch_id}&childId={hw_vid_id}"
                                                            else:
                                                                final_hw_url = hw_vid_url
                                                            
                                                            if final_hw_url not in seen_urls:
                                                                seen_urls.add(final_hw_url)
                                                                extracted_data.append(f"({sub_name}) {topic_name} : Solution to {hw_raw_title}:{final_hw_url}\n")
                                                                total_items += 1
                                                except Exception: 
                                                    pass
                                                    

                            except Exception as e:
                                logger.error(f"Error at {sub_name} -> {topic_name} Skip {skip_count}: {e}")
                                await asyncio.sleep(2)
                        
                            skip_count += 50

                    # ==========================================
                    # ENGINE 2: STANDALONE DPP & TEST API
                    # ==========================================
                    dpp_page = 1
                    dpp_empty_streak = 0
                
                    while dpp_empty_streak < 1 and dpp_page <= 15: # Reverted to 1 for speed
                        dpp_api = f"https://api.penpencil.co/v3/test-service/tests/new-dpp-list?page={dpp_page}&batchId={batch_id}&batchSubjectId={sub_id}&dppType=ALL&limit=20"
                        if topic_id:
                            dpp_api += f"&chapterId={topic_id}"
                    
                        try:
                            async with session.get(dpp_api, headers=headers) as dpp_res:
                                if dpp_res.status == 429:
                                    await asyncio.sleep(2)
                                    continue
                                if dpp_res.status != 200:
                                    dpp_empty_streak += 1
                                    dpp_page += 1
                                    continue
                            
                                dpp_data = await dpp_res.json()
                                items = dpp_data.get("data", [])
                            
                                if not isinstance(items, list) or not items:
                                    dpp_empty_streak += 1
                                    dpp_page += 1
                                    continue
                            
                                dpp_empty_streak = 0
                            
                                if date_filter:
                                    all_older = True
                                    for item in items:
                                        c_time = str(item.get("createdAt", "") if isinstance(item, dict) else "")
                                        d_time = str(item.get("date", "") if isinstance(item, dict) else "")
                                        item_date = c_time[:10] if c_time else (d_time[:10] if d_time else "")
                                        if not item_date or item_date >= date_filter:
                                            all_older = False
                                            break
                                    if all_older:
                                        break

                                for item in reversed(items):
                                    if not isinstance(item, dict): continue
                                
                                    if date_filter:
                                        c_time = str(item.get("createdAt", ""))
                                        d_time = str(item.get("date", ""))
                                        if date_filter not in c_time and date_filter not in d_time:
                                            continue
                                
                                    raw_title = (item.get("name") or item.get("topic") or "Test").replace(":", "-").replace("/", "-").strip()
                                    title = f"({sub_name}) {topic_name} : {raw_title}" if topic_id else f"({sub_name}) {raw_title}"

                                    if isinstance(item.get("attachmentIds"), list):
                                        for att in item["attachmentIds"]:
                                            try:
                                                if isinstance(att, dict):
                                                    pdf_key = att.get("key")
                                                    pdf_url = att.get("url") or att.get("fileUrl") or ""
                                                    if not pdf_url and pdf_key:
                                                        pdf_url = att.get("baseUrl", "") + pdf_key
                                                    elif not pdf_url and att.get("baseUrl") and len(att.get("baseUrl")) > 30:
                                                        pdf_url = att.get("baseUrl")

                                                    if pdf_url and pdf_url not in seen_urls:
                                                        if "mpd" in pdf_url.lower() or "m3u8" in pdf_url.lower():
                                                            sep = "&" if "?" in pdf_url else "?"
                                                            pdf_url = f"{pdf_url}{sep}parentId={batch_id}&childId={item.get('_id')}"
                                                    
                                                        seen_urls.add(pdf_url)
                                                        extracted_data.append(f"{title}:{pdf_url}\n")
                                                        total_items += 1
                                            except Exception: 
                                                pass
                                
                                    test_url = item.get("url", "").strip() or item.get("externalUrl", "").strip() or item.get("fileUrl", "").strip()
                                    if test_url and test_url not in seen_urls:
                                        seen_urls.add(test_url)
                                        extracted_data.append(f"{title}:{test_url}\n")
                                        total_items += 1


                        except Exception as e:
                            logger.debug(f"Engine 2 error: {e}")
                        dpp_page += 1

                    # ==========================================
                    # ENGINE 3: BATCH QUIZZES API
                    # ==========================================
                    quiz_api = f"https://api.penpencil.co/v3/test-service/tests/check-tests?testSource=BATCH_QUIZ&batchId={batch_id}&batchSubjectId={sub_id}"
                    if topic_id:
                        quiz_api += f"&chapterId={topic_id}"
                
                    try:
                        async with session.get(quiz_api, headers=headers) as q_res:
                            if q_res.status == 200:
                                q_data = await q_res.json()
                                items = q_data.get("data", [])
                            
                                if isinstance(items, list):
                                    for item in reversed(items):
                                        if not isinstance(item, dict): continue
                                    
                                        raw_title = (item.get("name") or item.get("topic") or "Quiz").replace(":", "-").replace("/", "-").strip()
                                        title = f"({sub_name}) {topic_name} : {raw_title}" if topic_id else f"({sub_name}) {raw_title}"
                                    
                                        quiz_url = item.get("url", "").strip() or item.get("externalUrl", "").strip() or item.get("fileUrl", "").strip()
                                        if quiz_url and quiz_url not in seen_urls:
                                            seen_urls.add(quiz_url)
                                            extracted_data.append(f"{title}:{quiz_url}\n")
                                            total_items += 1
                                        
                                        if isinstance(item.get("attachmentIds"), list):
                                            for att in item["attachmentIds"]:
                                                try:
                                                    if isinstance(att, dict):
                                                        pdf_key = att.get("key")
                                                        pdf_url = att.get("url") or att.get("fileUrl") or ""
                                                        if not pdf_url and pdf_key:
                                                            pdf_url = att.get("baseUrl", "") + pdf_key
                                                        elif not pdf_url and att.get("baseUrl") and len(att.get("baseUrl")) > 30:
                                                            pdf_url = att.get("baseUrl")

                                                        if pdf_url and pdf_url not in seen_urls:
                                                            if "mpd" in pdf_url.lower() or "m3u8" in pdf_url.lower():
                                                                sep = "&" if "?" in pdf_url else "?"
                                                                pdf_url = f"{pdf_url}{sep}parentId={batch_id}&childId={item.get('_id')}"
                                                        
                                                            seen_urls.add(pdf_url)
                                                            extracted_data.append(f"{title}:{pdf_url}\n")
                                                            total_items += 1
                                                except Exception: 
                                                    pass
                    except Exception as e:
                        logger.debug(f"Engine 3 error: {e}")

                    # LIVE STATUS UPDATE
                    if time.time() - last_edit_time > 15:
                        try:
                            short_topic = topic_name[:25] + ".." if len(topic_name) > 25 else topic_name
                            await status_msg.edit_text(
                                f"⚡ **TRIPLE-API ENGINE RUNNING...**\n"
                                f"📂 **Subject:** `{sub_name}`\n"
                                f"🔍 **Scanning:** `{short_topic}`\n"
                                f"🔗 **Links Extracted:** `{total_items}`\n\n"
                                f"*(Extracting Sequence & Filtering Duplicates)*"
                            )
                            last_edit_time = time.time()
                        except FloodWait as e:
                            await asyncio.sleep(e.value)
                        except Exception:
                            pass
            
        if batch_thumbnail:
            extracted_data.append(f"\n=================================\n🖼️ BATCH THUMBNAIL\n=================================\n")
            extracted_data.append(f"Thumb URL : {batch_thumbnail}\n")

        # Async File Write
        async with aiofiles.open(file_name, "w", encoding="utf-8") as f:
            await f.writelines(extracted_data)

        if total_items > 0:
            await status_msg.edit_text(f"✅ **Extraction Complete! Sequence Matched & Duplicates Removed.**")
            caption = (
                f"━━━ ⚡ **RISHI C_B VIP EXTRACTOR** ⚡ ━━━\n\n"
                f"➤ **Batch:** `{batch_name}` 📚\n"
                f"➤ **Total Links:** `{total_items}` 🔗\n\n"
                f"🎁 **UPLOAD OPTIONS** 🎁\n\n"
                f"➜ **Start Upload ALL** ➭ Full Text 🚀\n"
                f"➜ **Specific Range** ➭ Custom Index 🔢\n\n"
                f"〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
                f" ▸ **CLICK BELOW TO START UPLOAD** ◂ "
                f"{CREDIT}"
            )
            up_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Start Upload ALL (Full TXT)", callback_data="uptxt:all", style=enums.ButtonStyle.SUCCESS)],
                [InlineKeyboardButton("🔢 Upload Specific Index / Range", callback_data="uptxt:sel", style=enums.ButtonStyle.PRIMARY)],
                [InlineKeyboardButton("━━━ 👑 CINEMAA BOXOFFICE [C_B] ━━━", callback_data="uptxt:all", style=enums.ButtonStyle.SUCCESS)]
            ])
            await client.send_document(chat_id=message.chat.id, document=file_name, caption=caption, reply_markup=up_buttons)
            await aiofiles.os.remove(file_name)
            
            try:
                await status_msg.delete()
            except Exception as e:
                logger.debug(f"Could not delete status message: {e}")
        else:
            await status_msg.edit_text("⚠️ **Batch me koi bhi data nahi mila!**")
            if os.path.exists(file_name): await aiofiles.os.remove(file_name)

    except Exception as e:
        logger.error(f"extract_batch error: {e}")
        await status_msg.edit_text(f"🚨 **Extraction Error:** `{e}`")
