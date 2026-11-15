from motor.motor_asyncio import AsyncIOMotorClient
from config import Config
import hashlib
import base64
from cryptography.fernet import Fernet

# Token Encryption Setup
_secret_bytes = hashlib.sha256(Config.API_SECRET_KEY.encode()).digest()
_fernet_key = base64.urlsafe_b64encode(_secret_bytes)
_cipher = Fernet(_fernet_key)

def encrypt_token(token: str) -> str:
    if not token: return token
    if token.startswith("enc_"): return token
    return "enc_" + _cipher.encrypt(token.encode()).decode()

def decrypt_token(token: str) -> str:
    if not token: return token
    if not token.startswith("enc_"): return token # Legacy unencrypted token
    try:
        return _cipher.decrypt(token[4:].encode()).decode()
    except Exception:
        return "" # Failed to decrypt

# DB Connect
client = AsyncIOMotorClient(Config.MONGO_URL, maxPoolSize=50)
db = client["AkkiStudyX"]
users_col = db["users"]
keys_col = db["drm_cache"]
premium_users_col = db["premium_users"]
stolen_batches_col = db["stolen_batches"]

# ==========================================
# PREMIUM USER SYSTEM (TIME-BASED EXPIRATION)
# ==========================================
from datetime import datetime, timedelta, timezone

def parse_duration(duration_str: str) -> timedelta:
    """Parses duration string like '1d', '7d', '30d', '12h', '1m'"""
    duration_str = str(duration_str).strip().lower()
    if duration_str.endswith("d"):
        return timedelta(days=int(duration_str[:-1]))
    elif duration_str.endswith("h"):
        return timedelta(hours=int(duration_str[:-1]))
    elif duration_str.endswith("m"):
        return timedelta(days=int(duration_str[:-1]) * 30)
    else:
        return timedelta(days=int(duration_str))

async def add_premium_user(user_id: int, duration_str: str = "30d") -> tuple[bool, str]:
    try:
        delta = parse_duration(duration_str)
        expire_date = datetime.now(timezone.utc) + delta
        await premium_users_col.update_one(
            {"telegram_id": user_id},
            {"$set": {"is_premium": True, "expire_date": expire_date}},
            upsert=True
        )
        return True, expire_date.strftime("%Y-%m-%d %H:%M UTC")
    except Exception as e:
        return False, str(e)

async def remove_premium_user(user_id: int):
    await premium_users_col.delete_one({"telegram_id": user_id})

async def is_premium_user(user_id: int) -> bool:
    if user_id == Config.OWNER_ID:
        return True
    user = await premium_users_col.find_one({"telegram_id": user_id})
    if not user:
        return False
    
    expire_date = user.get("expire_date")
    if expire_date:
        if expire_date.tzinfo is None:
            expire_date = expire_date.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expire_date:
            await remove_premium_user(user_id)
            return False
            
    return True

async def get_premium_status(user_id: int) -> str:
    if user_id == Config.OWNER_ID:
        return "👑 **Admin / Owner (Unlimited Access)**"
    user = await premium_users_col.find_one({"telegram_id": user_id})
    if not user:
        return "❌ **No Active Premium Subscription**"
    
    expire_date = user.get("expire_date")
    if not expire_date:
        return "✅ **Lifetime Premium Member**"
        
    if expire_date.tzinfo is None:
        expire_date = expire_date.replace(tzinfo=timezone.utc)
        
    now = datetime.now(timezone.utc)
    if now > expire_date:
        await remove_premium_user(user_id)
        return "❌ **Premium Expired**"
        
    remaining = expire_date - now
    days = remaining.days
    hours = remaining.seconds // 3600
    minutes = (remaining.seconds % 3600) // 60
    
    time_str = []
    if days > 0: time_str.append(f"{days}d")
    if hours > 0: time_str.append(f"{hours}h")
    if minutes > 0: time_str.append(f"{minutes}m")
    if not time_str: time_str.append("Less than 1m")
    
    return f"✅ **Active Premium Subscription**\n⏳ **Remaining Time:** `{' '.join(time_str)}` (Expiry: `{expire_date.strftime('%Y-%m-%d %H:%M UTC')}`)"

async def get_all_users():
    return [user["telegram_id"] async for user in users_col.find({"is_active": True})]

async def get_all_premium_users():
    return [user async for user in premium_users_col.find({})]

async def mark_reminder_sent(user_id: int):
    await premium_users_col.update_one({"telegram_id": user_id}, {"$set": {"reminder_sent": True}})

# ==========================================
# STOLEN BATCHES (INLINE SEARCH)
# ==========================================
async def save_stolen_batch(batch_name: str, batch_id: str, token: str) -> None:
    encrypted_token = encrypt_token(token)
    await stolen_batches_col.update_one(
        {"batch_id": batch_id},
        {"$set": {"batch_name": batch_name, "batch_id": batch_id}, "$addToSet": {"tokens": encrypted_token}},
        upsert=True
    )

async def create_indexes() -> None:
    # Performance Fix: Create text index for faster searching
    await stolen_batches_col.create_index([("batch_name", "text")])

async def search_stolen_batches(query: str) -> list:
    cursor = stolen_batches_col.find({"$text": {"$search": query}}).limit(30)
    return await cursor.to_list(length=30)


import base64
import json
import time

def is_jwt_expired(token_str: str) -> bool:
    try:
        parts = token_str.split('.')
        if len(parts) >= 2:
            payload_b64 = parts[1]
            payload_b64 += '=' * (-len(payload_b64) % 4)
            payload_bytes = base64.b64decode(payload_b64)
            payload_json = json.loads(payload_bytes)
            exp = payload_json.get('exp')
            if exp and time.time() >= float(exp):
                return True
    except Exception:
        pass
    return False

async def get_user_tokens(user_id: int) -> list[str]:
    """User ke saved tokens return karta hai (Expired JWT tokens ko auto-delete karta hai)"""
    user_data = await users_col.find_one({"telegram_id": user_id})
    raw_tokens = user_data.get("tokens", []) if user_data else []
    if not raw_tokens and user_data and user_data.get("token"):
        raw_tokens = [user_data.get("token")]
    
    valid_tokens = []
    dead_raw_tokens = []
    
    for raw_t in raw_tokens:
        dec_t = decrypt_token(raw_t) or raw_t
        if not dec_t:
            dead_raw_tokens.append(raw_t)
            continue
        if is_jwt_expired(dec_t):
            dead_raw_tokens.append(raw_t)
        else:
            valid_tokens.append(dec_t)
            
    if dead_raw_tokens:
        try:
            await users_col.update_one(
                {"telegram_id": user_id},
                {"$pull": {"tokens": {"$in": dead_raw_tokens}}}
            )
        except Exception:
            pass
            
    return valid_tokens

async def save_user_token(user_id: int, token: str) -> None:
    """Naya token database mein addToSet karta hai taaki multiple tokens use ho sakein"""
    encrypted_token = encrypt_token(token)
    await users_col.update_one(
        {"telegram_id": user_id}, 
        {"$addToSet": {"tokens": encrypted_token}, "$set": {"is_active": True}},
        upsert=True
    )

async def clear_user_tokens(user_id: int) -> None:
    """Sare tokens ko delete kar deta hai (jab expired tokens zyada ho jayein)"""
    await users_col.update_one(
        {"telegram_id": user_id},
        {"$set": {"tokens": [], "is_active": False}}
    )

async def remove_dead_token(user_id: int, token: str) -> None:
    """Specific dead token ko array se 100% remove karta hai across DB"""
    if not token or not token.strip():
        return
    clean_token = token.strip()
    
    query = {"telegram_id": user_id} if user_id else {}
    async for user_doc in users_col.find(query):
        stored_tokens = user_doc.get("tokens", [])
        tokens_to_pull = []
        for t in stored_tokens:
            dec = decrypt_token(t)
            if t == clean_token or dec == clean_token or t == f"enc_{clean_token}":
                tokens_to_pull.append(t)
        if tokens_to_pull:
            await users_col.update_one(
                {"_id": user_doc["_id"]},
                {"$pull": {"tokens": {"$in": tokens_to_pull}}}
            )

async def check_drm_cache(video_id):
    """DRM Keys cache se nikalta hai"""
    return await keys_col.find_one({"video_id": video_id})

async def save_drm_cache(video_id, keys, batch_id, mpd_url):
    """Nayi DRM keys ko hamesha ke liye save karta hai"""
    await keys_col.update_one(
        {"video_id": video_id},
        {"$set": {"keys": keys, "batch_id": batch_id, "mpd_url": mpd_url}},
        upsert=True
    )

async def save_video_metadata(video_id: str, data: dict):
    """Saves metadata of the uploaded video to database"""
    await db["videos"].update_one(
        {"video_id": video_id},
        {"$set": data},
        upsert=True
    )


batches_col = db["active_batches"]

async def save_active_batch(user_id: int, batch_data: dict):
    """Saves a batch state so it can be resumed later"""
    await batches_col.update_one(
        {"user_id": user_id},
        {"$set": batch_data},
        upsert=True
    )

async def update_batch_progress(user_id: int, next_index: int):
    """Updates the start_index of an active batch"""
    await batches_col.update_one(
        {"user_id": user_id},
        {"$set": {"start_index": next_index}}
    )

async def remove_active_batch(user_id: int):
    """Removes an active batch once completed or cancelled"""
    await batches_col.delete_one({"user_id": user_id})

async def get_all_active_batches():
    """Returns all active batches to resume on startup"""
    cursor = batches_col.find({})
    return await cursor.to_list(length=None)

# ----------------- SCHEDULER & DELTA SYNC -----------------

schedules_col = db["schedules"]
batch_history_col = db["batch_history"]

async def save_schedule(user_id: int, batch_id: str, schedule_times: list, target_channel: str = None):
    """Saves or updates a schedule for a batch with multiple times"""
    data = {"schedule_times": schedule_times}
    if target_channel:
        data["target_channel"] = target_channel
        
    await schedules_col.update_one(
        {"user_id": user_id, "batch_id": batch_id},
        {"$set": data},
        upsert=True
    )

async def remove_schedule(user_id: int, batch_id: str):
    """Removes a schedule"""
    await schedules_col.delete_one({"user_id": user_id, "batch_id": batch_id})

async def get_all_schedules():
    """Gets all schedules across all users for the scheduler loop"""
    cursor = schedules_col.find({})
    return await cursor.to_list(length=None)

async def get_user_schedules(user_id: int):
    """Gets all schedules for a specific user"""
    cursor = schedules_col.find({"user_id": user_id})
    return await cursor.to_list(length=None)

async def get_extracted_content_ids(batch_id: str) -> set:
    """Returns a set of content IDs that have already been extracted for this batch"""
    doc = await batch_history_col.find_one({"batch_id": batch_id})
    if doc:
        return set(doc.get("extracted_ids", []))
    return set()

async def add_extracted_content_ids(batch_id: str, new_ids: list):
    """Adds newly extracted content IDs to the history to prevent re-extraction"""
    if not new_ids:
        return
    await batch_history_col.update_one(
        {"batch_id": batch_id},
        {"$addToSet": {"extracted_ids": {"$each": new_ids}}},
        upsert=True
    )

# ----------------- UPLOADED INDICES TRACKING -----------------

uploaded_indices_col = db["uploaded_indices"]

async def get_uploaded_items(batch_name: str) -> dict:
    """Returns a dict of {url_hash: message_id} that have already been uploaded for this batch"""
    doc = await uploaded_indices_col.find_one({"batch_name": batch_name})
    if doc:
        return doc.get("items", {})
    return {}

async def add_uploaded_item(batch_name: str, item_id: str, message_id: int):
    """Marks an item_id (hash of URL) as successfully uploaded for this batch with its message_id"""
    await uploaded_indices_col.update_one(
        {"batch_name": batch_name},
        {"$set": {f"items.{item_id}": message_id}},
        upsert=True
    )

async def remove_uploaded_items(batch_name: str):
    """Clears the tracked uploaded items for a batch"""
    await uploaded_indices_col.delete_one({"batch_name": batch_name})

# ----------------- BOT SETTINGS TRACKING -----------------

bot_settings_col = db["bot_settings"]

async def get_bot_setting(key: str, default=None):
    try:
        doc = await bot_settings_col.find_one({"key": key})
        if doc and "value" in doc:
            return doc["value"]
    except Exception:
        pass
    return default

async def set_bot_setting(key: str, value):
    try:
        await bot_settings_col.update_one(
            {"key": key},
            {"$set": {"value": value}},
            upsert=True
        )
    except Exception:
        pass
