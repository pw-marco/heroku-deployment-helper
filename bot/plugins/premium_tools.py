import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from config import Config

# ==========================================
# 👑 AKKI'S PREMIUM CONTROL ROOM
# ==========================================
CREDIT = "\n\n〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n👑 **ᴅᴇᴠᴇʟᴏᴘᴇᴅ & ᴏᴡɴᴇᴅ ʙʏ: @rishisin070**\n⚡ *Powered by Cinemaa Boxoffice [C_B]*"
TOTAL_USERS = set()

from database.mongo_db import add_premium_user, remove_premium_user, is_premium_user, get_all_users, get_premium_status

# ==========================================
# 1️⃣ THE AUTH SYSTEM (Premium Access)
# ==========================================
@Client.on_message(filters.command("addauth") & filters.private)
async def add_auth_user(client: Client, message: Message):
    if message.chat.id != Config.OWNER_ID: return
    try:
        args = message.command
        if len(args) < 2:
            return await message.reply_text("❌ **Format:** `/addauth USER_ID [DURATION]`\nExample: `/addauth 123456789 30d` (or `7d`, `1d`, `12h`)")
            
        new_user = int(args[1])
        duration = args[2] if len(args) >= 3 else "30d"
        
        ok, exp_info = await add_premium_user(new_user, duration)
        if ok:
            await message.reply_text(f"✅ **User `{new_user}` is now Premium!**\n⏳ **Duration:** `{duration}`\n📅 **Expires At:** `{exp_info}`")
            try:
                await client.send_message(
                    new_user, 
                    f"🎉 **Badhai Ho! Aapko Cinemaa Boxoffice [C_B] ka Premium Access mil gaya hai!**\n\n"
                    f"⏳ **Duration:** `{duration}`\n"
                    f"📅 **Expiry:** `{exp_info}`\n\n"
                    f"Ab aap batches extract and download kar sakte hain! 🚀"
                )
            except Exception: pass
        else:
            await message.reply_text(f"❌ Error adding premium: `{exp_info}`")
    except Exception as e:
        await message.reply_text(f"❌ **Format:** `/addauth USER_ID [DURATION]`\n`{e}`")

@Client.on_message(filters.command("rmauth") & filters.private)
async def rm_auth_user(client: Client, message: Message):
    if message.chat.id != Config.OWNER_ID: return
    try:
        rm_user = int(message.command[1])
        await remove_premium_user(rm_user)
        await message.reply_text(f"🚫 **User `{rm_user}` removed from Premium.**")
        try:
            await client.send_message(rm_user, "❌ **Aapka Premium Access expire / revoke ho gaya hai.**")
        except Exception: pass
    except Exception:
        await message.reply_text("❌ **Format:** `/rmauth USER_ID`")

@Client.on_message(filters.command(["id", "getemoji", "emoji"]) & filters.private)
async def get_custom_emoji_id(client: Client, message: Message):
    target = message.reply_to_message if message.reply_to_message else message
    
    if target.sticker:
        return await message.reply_text("❌ **Ye ek Sticker hai, Custom Emoji nahi!**\n\nStickers ko HTML code (<emoji id=...>) ke roop me text ke beech nahi lagaya ja sakta. Kripya keyboard se **Custom Premium Emoji** send karein, Sticker nahi!")
        
    if target.entities:
        found = False
        for ent in target.entities:
            if ent.custom_emoji_id:
                found = True
                html_code = f'<emoji id="{ent.custom_emoji_id}">👑</emoji>'
                await message.reply_text(
                    f"✨ **Custom Emoji Found!**\n\n"
                    f"🆔 **ID:** `{ent.custom_emoji_id}`\n"
                    f"💻 **HTML Code:**\n`{html_code}`\n\n"
                    f"💡 *Copy the HTML code and paste it inside your bot's Python files to use this premium emoji!*"
                )
        if found: return
        
    await message.reply_text("❌ **Koi Custom Premium Emoji nahi mila.**\n\nDhyan rahe:\n1. Msg ke shuru me `/id` likhein.\n2. Normal system emoji (jaise 👍) ka koi premium ID nahi hota.\n3. Ya phir kisi custom emoji wale message par `/id` reply karein!")

# ==========================================
# 2️⃣ THE BROADCAST SYSTEM (Announcements)
# ==========================================
@Client.on_message(filters.command("broadcast") & filters.private)
async def broadcast_msg(client: Client, message: Message):
    if message.chat.id != Config.OWNER_ID: return
    if not message.reply_to_message:
        return await message.reply_text("⚠️ **Kkisi message (Text/Photo/Video) ko reply karke `/broadcast` likho.**")
    
    success, fail = 0, 0
    status = await message.reply_text("📢 **Broadcasting start kar raha hu...**")
    all_users = await get_all_users()
    
    for user_id in all_users:
        try:
            await message.reply_to_message.copy(user_id)
            success += 1
            await asyncio.sleep(0.1) # Safe delay to avoid flood wait
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await message.reply_to_message.copy(user_id)
            success += 1
        except Exception:
            fail += 1
            
    await status.edit_text(f"📢 **Broadcast Complete!**\n✅ Sent: `{success}`\n❌ Failed: `{fail}`")

# ==========================================
# 3️⃣ HTML WEB PAGE GENERATOR (T2H)
# ==========================================
@Client.on_message(filters.command("t2h") & filters.private)
async def text_to_html(client: Client, message: Message):
    if not await is_premium_user(message.chat.id):
        return await message.reply_text("❌ **Sirf Premium Users is feature ko use kar sakte hain.**")

    ask = await message.reply_text("🌐 **Web Generator Ready!**\nApni `.txt` file bhejo jisko Website me badalna hai:")
    
    try:
        input_msg = await client.listen(message.chat.id, timeout=60)
        if not input_msg.document or not input_msg.document.file_name.endswith('.txt'):
            return await ask.edit("❌ **Invalid Format! Sirf .txt file bhejo.**")
            
        file_path = await input_msg.download()
        batch_name = input_msg.document.file_name.replace(".txt", "")
        
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        videos, pdfs = [], []
        for line in lines:
            if ":" in line and "[VIDEO]" in line or "[NOTES]" in line:
                name, url = line.split(":", 1)
                name = name.replace("[VIDEO]", "").replace("[NOTES]", "").strip()
                url = url.strip()
                if ".pdf" in url.lower(): pdfs.append((name, url))
                else: videos.append((name, url))
                
        # HTML Template Generation
        html_code = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{batch_name} | Cinemaa Boxoffice</title>
            <style>
                body {{ background-color: #121212; color: #ffffff; font-family: Arial, sans-serif; padding: 20px; }}
                h1 {{ text-align: center; color: #00e676; border-bottom: 2px solid #333; padding-bottom: 10px; }}
                .container {{ max-width: 800px; margin: auto; background: #1e1e1e; padding: 20px; border-radius: 10px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); }}
                .section-title {{ color: #ffeb3b; margin-top: 20px; font-size: 20px; }}
                a {{ display: block; background: #333; color: #64b5f6; padding: 12px; margin: 8px 0; text-decoration: none; border-radius: 5px; transition: 0.3s; font-weight: bold; }}
                a:hover {{ background: #00e676; color: #000; transform: translateX(10px); }}
                .footer {{ text-align: center; margin-top: 30px; font-size: 14px; color: #888; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📚 {batch_name}</h1>
                
                <div class="section-title">🎥 Video Lectures ({len(videos)})</div>
                {"".join([f'<a href="{url}" target="_blank">▶ {name}</a>' for name, url in videos])}
                
                <div class="section-title">📕 PDF Notes ({len(pdfs)})</div>
                {"".join([f'<a href="{url}" target="_blank">📄 {name}</a>' for name, url in pdfs])}
                
                <div class="footer">⚡ Extracted by: Rishi C_B Extractor | Powered by Cinemaa Boxoffice</div>
            </div>
        </body>
        </html>
        """
        
        html_file = f"{batch_name}.html"
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_code)
            
        await client.send_document(
            message.chat.id, 
            document=html_file, 
            caption=f"✅ **Website Tayyar Hai!** 🌐\n\nIs HTML file ko Chrome/Safari me open karo aur direct PC par padhai karo!{CREDIT}"
        )
        
        os.remove(file_path)
        os.remove(html_file)
        await ask.delete()
        
    except Exception as e:
        await ask.edit(f"🚨 **Error:** `{e}`")

# Track all users to make Broadcast work
@Client.on_message(filters.private, group=-1)
async def track_users(client, message):
    from database.mongo_db import users_col
    await users_col.update_one({"telegram_id": message.chat.id}, {"$set": {"is_active": True}}, upsert=True)
