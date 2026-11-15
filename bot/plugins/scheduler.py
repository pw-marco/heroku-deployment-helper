import asyncio
import logging
import os
import re
import time
from datetime import datetime
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import Config
from database.mongo_db import (
    get_user_tokens, save_schedule, get_user_schedules, remove_schedule,
    get_all_schedules, get_extracted_content_ids, add_extracted_content_ids
)
from plugins.extractor import get_god_headers, get_extractor_session
from plugins.uploader import execute_batch

logger = logging.getLogger(__name__)

# Initialize Scheduler
IST = pytz.timezone("Asia/Kolkata")
scheduler = AsyncIOScheduler(timezone=IST)

# -------------------------------------------------------------
# COMMANDS
# -------------------------------------------------------------

@Client.on_message(filters.command("schedule") & filters.private)
async def schedule_batch(client: Client, message):
    if message.chat.id != Config.OWNER_ID: return
    user_id = message.chat.id
    tokens = await get_user_tokens(user_id)
    if not tokens:
        return await message.reply_text("❌ **Pehle login kar le!** `/pwlogin` daba.")
        
    args = message.command
    if len(args) < 4:
        return await message.reply_text(
            "⚠️ **Format galat hai!**\n"
            "Aise likh: `/schedule BATCH_ID TARGET_CHANNEL TIME1 TIME2`\n"
            "Example: `/schedule 641abcd -1001234567 10:00 18:30`"
        )
        
    batch_id = args[1]
    target_channel = args[2]
    schedule_times = args[3:]
    
    try: target_channel_id = int(target_channel)
    except: return await message.reply_text("❌ Target Channel ID number hona chahiye (e.g. -100123456789)")
    
    # Validate times
    valid_times = []
    for t in schedule_times:
        if re.match(r'^([01]\d|2[0-3]):([0-5]\d)$', t):
            valid_times.append(t)
        else:
            return await message.reply_text(f"❌ **Invalid Time Format:** `{t}`\nPlease use 24-hour HH:MM format (e.g. 14:30)")
            
    status_msg = await message.reply_text(f"⏳ **Validating Batch `{batch_id}` for Auto-Schedule...**")
    
    # Validate Batch Access
    valid_token = None
    batch_name = "Unknown Batch"
    try:
        session = await get_extractor_session()
        for tkn in reversed(tokens):
            headers = get_god_headers(tkn)
            try:
                async with session.get(f"https://api.penpencil.co/v3/batches/{batch_id}/details", headers=headers) as res:
                    if res.status == 200:
                        details_data = await res.json()
                        valid_token = tkn
                        raw_batch_name = details_data.get("data", {}).get("name", "Extracted_Batch")
                        batch_name = re.sub(r'[\\/*?:"<>|]', "", raw_batch_name).replace(" ", "_")
                        break
                    elif res.status == 401:
                        from database.mongo_db import remove_dead_token
                        await remove_dead_token(user_id, tkn)
                        try: await status_msg.reply("⚠️ **Aapka ek Token expire ho gaya tha (Auto-Removed).** Kripya nayi access ke liye `/pwlogin` se dubara login karein!")
                        except: pass
            except Exception:
                continue
    except Exception as e:
        return await status_msg.edit_text(f"🚨 **Error validating batch:** `{e}`")
        
    if not valid_token:
        return await status_msg.edit_text("❌ **Batch Access Denied! (Tokens expired ya batch kharida nahi hai)**")
        
    # Save Schedule to DB
    await save_schedule(user_id, batch_id, valid_times, str(target_channel_id))
    
    # Reload Scheduler Engine
    await reload_scheduler_jobs(client)
    
    text = (
        f"🎉 **Batch Scheduled Successfully!**\n\n"
        f"📚 **Batch:** `{batch_name}`\n"
        f"🎯 **Target Channel:** `{target_channel_id}`\n"
        f"⏰ **Times (IST):** {', '.join(valid_times)}\n\n"
        f"💡 Ab main in times pe daily automatic check karunga aur **sirf naye items** ka txt banakar direct safe mode me video download aur upload kar dunga!"
    )
    await message.reply_text(text)


@Client.on_message(filters.command("myschedules") & filters.private)
async def list_schedules(client: Client, message):
    if message.chat.id != Config.OWNER_ID: return
    schedules = await get_user_schedules(message.chat.id)
    if not schedules:
        return await message.reply_text("❌ **Aapka koi active schedule nahi hai.**")
        
    text = "📅 **YOUR ACTIVE SCHEDULES:**\n\n"
    for s in schedules:
        target_channel = s.get("target_channel", "Default Log Channel")
        text += f"📚 **Batch ID:** `{s['batch_id']}`\n"
        text += f"🎯 **Target Channel:** `{target_channel}`\n"
        text += f"⏰ **Times:** {', '.join(s.get('schedule_times', []))}\n\n"
        
    text += "🗑 To delete a schedule, use: `/delschedule BATCH_ID`"
    await message.reply_text(text)


@Client.on_message(filters.command("delschedule") & filters.private)
async def delete_schedule(client: Client, message):
    if message.chat.id != Config.OWNER_ID: return
    args = message.command
    if len(args) != 2:
        return await message.reply_text("⚠️ **Format:** `/delschedule BATCH_ID`")
        
    batch_id = args[1]
    await remove_schedule(message.chat.id, batch_id)
    await reload_scheduler_jobs(client)
    await message.reply_text(f"✅ **Schedule for `{batch_id}` deleted successfully!**")

# -------------------------------------------------------------
# CORE SCHEDULER ENGINE
# -------------------------------------------------------------

async def scan_batch_for_delta(valid_token: str, batch_id: str, session, extract_full_data=False):
    headers = get_god_headers(valid_token)
    extracted_items = []
    
    # Use Weekly Schedules API for ultra-fast delta scanning
    import datetime
    import pytz
    IST = pytz.timezone("Asia/Kolkata")
    
    # Scan last 3 days to be safe in case of downtime
    end_date = datetime.datetime.now(IST)
    start_date = end_date - datetime.timedelta(days=3)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    
    page = 1
    while True:
        api_url = f"https://api.penpencil.co/v2/batches/{batch_id}/weekly-schedules?batchId={batch_id}&batchSubjectId=&startDate={start_str}&endDate={end_str}&page={page}"
        try:
            async with session.get(api_url, headers=headers) as c_res:
                if c_res.status != 200: break
                c_data = await c_res.json()
                items = c_data.get("data", [])
                if not isinstance(items, list) or not items: break
                
                for item in reversed(items):
                    if not isinstance(item, dict): continue
                    
                    item_id = str(item.get("_id"))
                    if not extract_full_data:
                        extracted_items.append(item_id)
                    else:
                        details = item.get("lessonDetails") or item.get("videoDetails") or item.get("notesDetails") or item.get("homeworkDetails") or item.get("bulkScheduleDetails") or item
                        if not isinstance(details, dict): details = item
                        
                        subj_data = details.get("subjectId")
                        sub_name = subj_data.get("name") if isinstance(subj_data, dict) else (details.get("subject") or "Unknown_Subject")
                        
                        raw_topic = details.get("topic") or details.get("name") or details.get("lessonName") or item.get("topic") or item.get("name") or "Class Session"
                        raw_title = raw_topic.replace(":", "-").replace("/", "-").strip()
                        title = f"({sub_name}) {raw_title}"
                        
                        vid_url = details.get("url", "").strip() or details.get("videoUrl", "").strip() or details.get("externalUrl", "").strip() or details.get("fileUrl", "").strip()
                        if isinstance(details.get("videoDetails"), dict):
                            vid_url = details["videoDetails"].get("videoUrl", "").strip() or details["videoDetails"].get("url", "").strip() or vid_url
                            
                        url = vid_url
                        if vid_url:
                            if ("mpd" in vid_url or "m3u8" in vid_url) and item_id:
                                sep = "&" if "?" in vid_url else "?"
                                url = f"{vid_url}{sep}parentId={batch_id}&childId={item_id}"
                        
                        is_video = bool(item.get("type") == "VIDEO" or details.get("lectureType") in ["RECORDED", "LIVE"] or "youtu" in url or ".mpd" in url or ".m3u8" in url or ".mp4" in url)
                        
                        thumb_url = ""
                        v_details = details.get("videoDetails", {}) if isinstance(details.get("videoDetails"), dict) else {}
                        thumb = v_details.get("image") or details.get("image") or details.get("teacherImage") or details.get("previewImageUrl") or details.get("thumbnailUrl")
                        if isinstance(thumb, str) and thumb.startswith("http"):
                            thumb_url = thumb
                        elif isinstance(details.get("previewImage"), dict) and details["previewImage"].get("baseUrl"):
                            thumb_url = details["previewImage"]["baseUrl"] + details["previewImage"]["key"]
                            
                        # Main item
                        if url:
                            extracted_items.append({
                                "_id": item_id,
                                "title": title,
                                "url": url,
                                "thumb_url": thumb_url,
                                "is_video": is_video,
                                "subject_name": sub_name,
                                "topic_name": ""
                            })
                            
                        # Attachments (PDFs)
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

                                        att_id = att.get("_id", item_id)
                                        if pdf_url:
                                            if "mpd" in pdf_url.lower() or "m3u8" in pdf_url.lower():
                                                sep = "&" if "?" in pdf_url else "?"
                                                pdf_url = f"{pdf_url}{sep}parentId={batch_id}&childId={att_id}"
                                            att_name = att.get("name", "Class Notes").replace(":", "-").replace("/", "-").strip()
                                            
                                            extracted_items.append({
                                                "_id": att_id,
                                                "title": f"({sub_name}) {att_name}",
                                                "url": pdf_url,
                                                "thumb_url": "",
                                                "is_video": False,
                                                "subject_name": sub_name,
                                                "topic_name": ""
                                            })
                                except Exception: pass
                                
                        # DPP & Homework
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

                                                att_id = att.get("_id", hw.get("_id", item_id))
                                                if pdf_url:
                                                    extracted_items.append({
                                                        "_id": att_id,
                                                        "title": f"({sub_name}) {hw_raw_title}",
                                                        "url": pdf_url,
                                                        "thumb_url": "",
                                                        "is_video": False,
                                                        "subject_name": sub_name,
                                                        "topic_name": ""
                                                    })
                                                    
                                    if hw.get("videoDetails") and isinstance(hw["videoDetails"], dict):
                                        hw_vid_url = hw["videoDetails"].get("videoUrl", "").strip() or hw["videoDetails"].get("url", "").strip()
                                        hw_vid_id = hw.get("_id")
                                        if hw_vid_url:
                                            if ("mpd" in hw_vid_url or "m3u8" in hw_vid_url) and hw_vid_id:
                                                sep = "&" if "?" in hw_vid_url else "?"
                                                final_hw_url = f"{hw_vid_url}{sep}parentId={batch_id}&childId={hw_vid_id}"
                                            else:
                                                final_hw_url = hw_vid_url
                                                
                                            extracted_items.append({
                                                "_id": hw_vid_id,
                                                "title": f"({sub_name}) Solution to {hw_raw_title}",
                                                "url": final_hw_url,
                                                "thumb_url": "",
                                                "is_video": True,
                                                "subject_name": sub_name,
                                                "topic_name": ""
                                            })
                                except Exception: pass
        except Exception: break
        page += 1
                        
    return extracted_items



async def execute_scheduled_job(client: Client, user_id: int, batch_id: str, target_channel_str: str = None):
    logger.info(f"🚀 RUNNING SCHEDULED JOB: User {user_id} | Batch {batch_id}")
    
    tokens = await get_user_tokens(user_id)
    if not tokens: return
    
    valid_token = None
    batch_name = "Scheduled_Batch"
    session = await get_extractor_session()
    
    # 1. Validate Token & Get Details
    for tkn in reversed(tokens):
        headers = get_god_headers(tkn)
        try:
            async with session.get(f"https://api.penpencil.co/v3/batches/{batch_id}/details", headers=headers) as res:
                if res.status == 200:
                    details_data = await res.json()
                    valid_token = tkn
                    batch_name = details_data.get("data", {}).get("name", batch_name)
                    batch_name = re.sub(r'[\\/*?:"<>|]', "", batch_name).replace(" ", "_")
                    break
                elif res.status == 401:
                    from database.mongo_db import remove_dead_token
                    await remove_dead_token(user_id, tkn)
                    try: await client.send_message(user_id, "⚠️ **Aapka ek Token expire ho gaya tha (Auto-Removed).** Kripya nayi access ke liye `/pwlogin` se dubara login karein!")
                    except: pass
        except: continue
        
    if not valid_token:
        try: await client.send_message(user_id, f"⚠️ **Auto-Schedule Failed!**\nBatch `{batch_id}` access denied. Please re-login.")
        except: pass
        return
        
    try:
        status_msg = await client.send_message(user_id, f"🔄 **Auto-Schedule Started:** `{batch_name}`\nScanning for newly added items...")
    except:
        status_msg = None
        
    # 2. Get DB History
    history_ids = await get_extracted_content_ids(batch_id)
    
    # 3. Scan Delta
    all_contents = await scan_batch_for_delta(valid_token, batch_id, session, extract_full_data=True)
    
    new_items = []
    new_ids = []
    txt_content = f"=================================\n⚡ RISHI C_B EXTRACTOR - DELTA SYNC ⚡\n📚 BATCH: {batch_name}\n⏰ TIME: {datetime.now(IST).strftime('%Y-%m-%d %I:%M %p')}\n=================================\n\n"
    
    for c in all_contents:
        if c["_id"] not in history_ids:
            new_ids.append(c["_id"])
            new_items.append({
                "title": c["title"],
                "url": c["url"],
                "thumb_url": c.get("thumb_url"),
                "is_video": c["is_video"]
            })
            ext = ".mp4" if c["is_video"] else ".pdf"
            if c.get("thumb_url"):
                txt_content += f"{c['title']}{ext}:{c['url']}:{c['thumb_url']}\n"
            else:
                txt_content += f"{c['title']}{ext}:{c['url']}\n"
            
    if not new_items:
        if status_msg: await status_msg.edit_text(f"✅ **Auto-Schedule Complete:** `{batch_name}`\nNo new items were added today.")
        return
        
    # 4. Save TXT & Update History
    txt_filename = f"NEW_{batch_name}_{int(time.time())}.txt"
    with open(txt_filename, "w", encoding="utf-8") as f:
        f.write(txt_content)
        
    await add_extracted_content_ids(batch_id, new_ids)
    
    # 5. Send TXT File
    if status_msg: await status_msg.delete()
    await client.send_document(
        user_id, 
        txt_filename, 
        caption=f"🎉 **{len(new_items)} Naye Items Mile!**\n\nAb inki auto-uploading start kar raha hu..."
    )
    os.remove(txt_filename)
    
    # 6. Trigger Upload Process (Safe Mode)
    uploader_status_msg = await client.send_message(user_id, "⏳ Initiating upload engine...")
    upload_target_id = Config.LOG_CHANNEL_ID
    if target_channel_str:
        try: upload_target_id = int(target_channel_str)
        except: pass
        
    await execute_batch(
        client, 
        user_id=user_id, 
        target_chat_id=user_id, 
        log_channel_id=upload_target_id,
        batch_name=batch_name,
        quality="720",
        custom_name="⚡ ʀɪꜱʜɪ ᴄ_ʙ xᴛʀᴀᴄᴛᴏʀ ⚡",
        start_index=1,
        items=new_items,
        status_msg=uploader_status_msg,
        show_index=False
    )



async def check_premium_expirations(client: Client):
    try:
        users = await get_all_premium_users()
        now = datetime.now(timezone.utc)
        for user in users:
            user_id = user.get("telegram_id")
            expire_date = user.get("expire_date")
            if not expire_date: continue
            
            if expire_date.tzinfo is None:
                expire_date = expire_date.replace(tzinfo=timezone.utc)
                
            remaining = expire_date - now
            
            if remaining.total_seconds() <= 0:
                await remove_premium_user(user_id)
                try:
                    await client.send_message(user_id, "⚠️ **Aapka Premium Subscription expire ho gaya hai!**\nBot ki premium services (jaise `/batch`) use karne ke liye admin se contact karke naya plan kharidein.")
                except: pass
            
            elif timedelta(hours=0) < remaining <= timedelta(hours=24):
                if not user.get("reminder_sent"):
                    try:
                        await client.send_message(user_id, f"⚠️ **Reminder:** Aapka Premium Subscription 1 din me expire hone wala hai! (Expiry: `{expire_date.strftime('%Y-%m-%d %H:%M UTC')}`)")
                        await mark_reminder_sent(user_id)
                    except: pass
    except Exception as e:
        logger.error(f"Error in premium expiry checker: {e}")

async def reload_scheduler_jobs(client: Client):
    """Loads all schedules from MongoDB and feeds them to APScheduler"""
    scheduler.remove_all_jobs()
    schedules = await get_all_schedules()
    
    job_count = 0
    for s in schedules:
        user_id = s.get("user_id")
        batch_id = s.get("batch_id")
        times = s.get("schedule_times", [])
        
        for t in times:
            try:
                hour, minute = map(int, t.split(":"))
                target_channel = s.get("target_channel")
                scheduler.add_job(
                    execute_scheduled_job,
                    CronTrigger(hour=hour, minute=minute, timezone=IST),
                    args=[client, user_id, batch_id, target_channel],
                    id=f"{user_id}_{batch_id}_{hour}_{minute}",
                    replace_existing=True
                )
                job_count += 1
            except Exception as e:
                logger.error(f"Error adding job for {t}: {e}")
                
    logger.info(f"✅ APScheduler Reloaded: {job_count} active jobs scheduled.")

def start_scheduler(client: Client):
    scheduler.add_job(check_premium_expirations, 'interval', hours=1, args=[client], id="premium_expiry_checker", replace_existing=True)
    scheduler.start()
    asyncio.create_task(reload_scheduler_jobs(client))
