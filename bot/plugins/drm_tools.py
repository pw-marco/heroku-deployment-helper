import aiohttp
import os
import re
import json
import urllib.parse
from pyrogram import Client, filters
from database.mongo_db import get_user_tokens, check_drm_cache, save_drm_cache, is_premium_user
from platforms.pw_api import get_pw_headers
from config import Config

PREMIUM_LOCK_MSG = (
    "❌ **Access Denied! Sirf Premium Users hi DRM Tools & Key Extraction use kar sakte hain.**\n\n"
    "💳 **Premium Subscription lene ke liye Admin se contact karein:**\n"
    "👉 **Contact Admin:** @rishisin070\n\n"
    "ℹ️ *PW Login (`/pwlogin`) sabhi users ke liye Free hai.*"
)

@Client.on_message(filters.command("drmtest") & filters.private)
async def drm_test_command(client, message):
    if not await is_premium_user(message.chat.id):
        return await message.reply_text(PREMIUM_LOCK_MSG)
    tokens = await get_user_tokens(message.chat.id)
    if not tokens or len(message.command) != 2: 
        return await message.reply_text("❌ **Format:** `/drmtest BATCH_ID`")

    batch_id = message.command[1]
    status_msg = await message.reply_text("⏳ **DRM Hunter chalu hai...**")
    valid_token, drm_video_data = None, None

    try:
        async with aiohttp.ClientSession() as session:
            for tkn in tokens:
                async with session.get(f"https://api.penpencil.xyz/v3/batches/{batch_id}/details", headers=get_pw_headers(tkn)) as res:
                    if res.status == 200:
                        subjects = (await res.json()).get("data", {}).get("subjects", [])
                        valid_token = tkn
                        break
            
            if not valid_token or not subjects: 
                return await status_msg.edit_text("❌ Batch Access Denied/No Subjects.")

            for sub in subjects:
                if drm_video_data: break
                for page in range(1, 4):
                    async with session.get(f"https://api.penpencil.xyz/v2/batches/{batch_id}/subject/{sub['_id']}/contents", params={'page': str(page), 'contentType': 'exercises-notes-videos'}, headers=get_pw_headers(valid_token)) as c_res:
                        if c_res.status == 200:
                            items = (await c_res.json()).get("data", [])
                            if not items: break
                            for item in items:
                                if "drm" in item.get("url", "").lower():
                                    drm_video_data = item
                                    break
                            if drm_video_data: break

            if drm_video_data:
                raw_json = json.dumps(drm_video_data, indent=4)
                final_text = f"🎯 **Target Locked! DRM Data:**\n\n{CODE_BLOCK}json\n{raw_json[:3500]}...\n{CODE_BLOCK}"
                await status_msg.edit_text(final_text)
            else:
                await status_msg.edit_text("⚠️ **Nahi Mila!** Is batch me koi actively playable '/drm/' video nahi mili.")
    except Exception as e:
        await status_msg.edit_text(f"🚨 **System Error:** `{e}`")

@Client.on_message(filters.command("getpssh") & filters.private)
async def extract_pssh_command(client, message):
    if not await is_premium_user(message.chat.id):
        return await message.reply_text(PREMIUM_LOCK_MSG)
    tokens = await get_user_tokens(message.chat.id)
    if not tokens: 
        return await message.reply_text("❌ **Pehle login kar le bhai!**")
    if len(message.command) < 2: 
        return await message.reply_text("⚠️ **Format:** `/getpssh MPD_LINK`")

    mpd_url = message.text.split(None, 1)[1].strip()
    status_msg = await message.reply_text("⏳ **Level-3 Bypass: API se CloudFront Cookies chura raha hu...**")

    try:
        async with aiohttp.ClientSession() as session:
            pw_headers = get_pw_headers(tokens[0])
            await session.get("https://api.penpencil.xyz/v3/users/me", headers=pw_headers)
            await session.get("https://api.penpencil.xyz/v3/batches/my-batches?page=1&mode=1", headers=pw_headers)
            await status_msg.edit_text("✅ **Cookies Mil Gayi! Ab CloudFront par attack kar raha hu...**")

            cf_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36", "Origin": "https://pw.live", "Referer": "https://pw.live/"}
            async with session.get(mpd_url, headers=cf_headers) as res:
                if res.status == 200:
                    pssh_matches = re.findall(r'<cenc:pssh[^>]*>(.*?)</cenc:pssh>', await res.text())
                    if pssh_matches:
                        widevine_pssh = max(pssh_matches, key=len)
                        pssh_text = f"🎯 **BOOM! PSSH Extracted!**\n\n**PSSH Code:**\n`{widevine_pssh}`"
                        await status_msg.edit_text(pssh_text)
                    else:
                        await status_msg.edit_text("⚠️ **PSSH Nahi Mila!** MPD me DRM nahi hai. Ye ek Demo/Free video ho sakti hai.")
                else:
                    await status_msg.edit_text(f"❌ **CloudFront Blocked:** `{res.status}`")
    except Exception as e:
        await status_msg.edit_text(f"🚨 **System Error:** `{e}`")

@Client.on_message(filters.command("getkeys") & filters.private)
async def fetch_drm_keys(client, message):
    if not await is_premium_user(message.chat.id):
        return await message.reply_text(PREMIUM_LOCK_MSG)
    tokens = await get_user_tokens(message.chat.id)
    if not tokens: 
        return await message.reply_text("❌ **Pehle login kar le!**")
    if len(message.command) != 4: 
        return await message.reply_text("⚠️ **Format:** `/getkeys BATCH_ID VIDEO_ID MPD_LINK`")

    batch_id = message.command[1]
    video_id = message.command[2]
    mpd_url = message.command[3]
    status_msg = await message.reply_text("🔍 **Checking MongoDB Cache...**")

    # 🟢 CACHE SCAN
    cached_data = await check_drm_cache(video_id)
    if cached_data:
        resp = "⚡ **FAST CACHE HIT! DRM KEYS FOUND!** 🔓\n\n"
        for key in cached_data["keys"]: 
            resp += f"`{key}`\n"
        resp += "\n👉 *Database se 0.1s me nikala!*"
        return await status_msg.edit_text(resp)

    # 🔴 API HIT
    await status_msg.edit_text("⏳ **Key not in DB. Private API ko hit kar raha hu...**")
    
    params = {"url": mpd_url, "parentId": batch_id, "childId": video_id, "token": tokens[0]}
    headers = {"X-API-KEY": Config.API_SECRET_KEY}

    try:
        async with aiohttp.ClientSession() as session:
            data = None
            res_status = 500
            for base_url in Config.get_api_base_urls():
                try:
                    async with session.get(f"{base_url}/api/get-keys", params=params, headers=headers) as res:
                        res_status = res.status
                        if res.status == 200:
                            data = await res.json()
                            if data.get("success") is True:
                                break
                except Exception:
                    continue

            # Check for successful API hit (handles DRM, Non-DRM and Pallycon errors)
            if data and data.get("success") is True:
                # Video doesn't have DRM
                if not data.get("is_drm", True):
                    return await status_msg.edit_text(f"🔓 **NO DRM DETECTED!** Ye video direct downloadable hai.\n\n**Direct URL:**\n`{data.get('direct_url', mpd_url)}`")
                
                # Video has DRM and keys were found
                if "keys" in data: 
                    await save_drm_cache(video_id, data["keys"], batch_id, mpd_url)
                
                resp = "🎯 **BOOM! SECURE DRM KEYS EXTRACTED & SAVED!** 🔓\n\n"
                if "keys" in data:
                    for key in data["keys"]: 
                        resp += f"`{key}`\n"
                else:
                    dump_json = json.dumps(data, indent=2)
                    resp += f"{CODE_BLOCK}json\n{dump_json}\n{CODE_BLOCK}"
                    
                await status_msg.edit_text(resp)
            
            # Handling failures
            else:
                err_msg = data.get("error", "Unknown API Error") if data else f"HTTP Status: {res_status}"
                # Make Pallycon rejection clear
                if "Pallycon License Server rejected" in str(err_msg):
                    err_text = f"❌ **Pallycon Error:** API couldn't extract keys for this video."
                else:
                    err_text = f"⚠️ **API Key Fetch Failed:** `{err_msg}`"
                
                await status_msg.edit_text(err_text)
    except Exception as e:
        await status_msg.edit_text(f"❌ **API Error:** `{e}`")
