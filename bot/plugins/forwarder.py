import asyncio
import re
from pyrogram import Client, filters
from config import Config

def parse_telegram_link(url_str):
    if not url_str:
        return None, None
    url_str = url_str.strip()
    
    # Check for private channel format: t.me/c/123456789/456 or telegram.me/c/123456789/456
    private_match = re.search(r't\.me/c/(\d+)/(\d+)', url_str)
    if not private_match:
        private_match = re.search(r'telegram\.me/c/(\d+)/(\d+)', url_str)
        
    if private_match:
        try:
            chat_id = int(f"-100{private_match.group(1)}")
            msg_id = int(private_match.group(2))
            return chat_id, msg_id
        except:
            pass
            
    # Check for public channel format: t.me/username/456 or telegram.me/username/456
    public_match = re.search(r't\.me/([^/]+)/(\d+)', url_str)
    if not public_match:
        public_match = re.search(r'telegram\.me/([^/]+)/(\d+)', url_str)
        
    if public_match:
        try:
            chat_id = public_match.group(1)
            if chat_id.isdigit():
                chat_id = int(chat_id)
            msg_id = int(public_match.group(2))
            return chat_id, msg_id
        except:
            pass
            
    try:
        msg_id = int(url_str.rstrip("/").split("/")[-1])
        return None, msg_id
    except:
        return None, None

@Client.on_message(filters.command("forward") & filters.private & filters.user(Config.OWNER_ID))
async def manual_forwarder(client, message):
    if len(message.command) < 3:
        return await message.reply("Usage: `/forward <Target Channel ID> <Batch Name> [--from link] [--to link] [--skip 1,2,3]`")
        
    parts = message.text.split(" ", 2)
    target_chat_str = parts[1]
    try:
        target_chat_id = int(target_chat_str)
    except ValueError:
        return await message.reply("❌ Target Channel ID ek number hona chahiye (e.g., -100123456789).")
        
    text = parts[2]
    skip_indices = []
    start_msg_id = None
    end_msg_id = None

    start_chat_id = None
    end_chat_id = None

    # Parse --from
    match_from = re.search(r'--from\s+([^\s]+)', text)
    if match_from:
        from_url = match_from.group(1)
        start_chat_id, start_msg_id = parse_telegram_link(from_url)
        text = text.replace(match_from.group(0), "")

    # Parse --to
    match_to = re.search(r'--to\s+([^\s]+)', text)
    if match_to:
        to_url = match_to.group(1)
        end_chat_id, end_msg_id = parse_telegram_link(to_url)
        text = text.replace(match_to.group(0), "")

    # Parse --skip
    match_skip = re.search(r'--skip\s+([\d,\-]+)', text)
    if match_skip:
        skip_str = match_skip.group(1)
        for part in skip_str.split(","):
            part = part.strip()
            if "-" in part:
                try:
                    start, end = map(int, part.split("-"))
                    skip_indices.extend(range(start, end + 1))
                except: pass
            elif part.isdigit():
                skip_indices.append(int(part))
        text = text.replace(match_skip.group(0), "")
        
    batch_name = text.strip()
    
    m = await message.reply(f"🔍 **Scanning Log Channel for '{batch_name}'...**\n(Scanning up to last 15,000 messages. This may take 20 seconds...)")
    
    msgs = {}
    chat_id = start_chat_id or end_chat_id or Config.LOG_CHANNEL_ID
    limit = 15000
    
    msgs_list = []
    
    try:
        if start_msg_id or end_msg_id:
            raise Exception("CUSTOM_RANGE_TRIGGERED")
            
        async for msg in client.get_chat_history(chat_id, limit=limit):
            if msg.caption and batch_name.lower() in msg.caption.lower():
                msgs_list.append(msg)
    except Exception as e:
        if "BOT_METHOD_INVALID" in str(e) or "HISTORY" in str(e).upper() or "CUSTOM_RANGE_TRIGGERED" in str(e):
            try:
                if start_msg_id and end_msg_id:
                    s_id = min(start_msg_id, end_msg_id)
                    e_id = max(start_msg_id, end_msg_id)
                    message_ids = list(range(s_id, e_id + 1))
                    await m.edit(f"🔍 Fast Scanning exact range: {s_id} to {e_id}...")
                elif start_msg_id:
                    dummy = await client.send_message(chat_id, "🔍 Fast Scanning from given start link...")
                    latest_id = dummy.id
                    await dummy.delete()
                    message_ids = list(range(start_msg_id, latest_id + 1))
                else:
                    dummy = await client.send_message(chat_id, "🔍 Slow Scanning...")
                    latest_id = dummy.id
                    await dummy.delete()
                    start_id = max(1, latest_id - limit)
                    message_ids = list(range(start_id, latest_id))
                    
                message_ids.reverse() 
                
                for i in range(0, len(message_ids), 200):
                    if i % 1000 == 0:
                        try: await m.edit(f"🔍 Slow Scanning ({i}/{len(message_ids)} messages)...")
                        except: pass
                    
                    chunk = message_ids[i:i+200]
                    fetched = await client.get_messages(chat_id, chunk)
                    for msg in fetched:
                        if msg and not msg.empty and msg.caption and batch_name.lower() in msg.caption.lower():
                            msgs_list.append(msg)
                    
                    await asyncio.sleep(0.3)
            except Exception as e2:
                return await m.edit(f"❌ Error in fallback scanning: {e2}")
        else:
            return await m.edit(f"❌ Error scanning log channel: {e}")
        
    if not msgs_list:
        return await m.edit(f"❌ Koi bhi message nahi mila log channel me is batch name se: `{batch_name}`")

    # Sort messages chronologically by message ID (oldest first)
    msgs_list.sort(key=lambda x: x.id)
    
    # Rebuild msgs dict using extracted Index OR chronological order
    msgs = {}
    for i, msg in enumerate(msgs_list):
        match = re.search(r"Index:?\s*\*?\*?\s*`?(\d+)", msg.caption, re.IGNORECASE)
        if match:
            idx = int(match.group(1))
        else:
            idx = i + 1  # Fallback to sequential index
        
        if idx not in msgs:
            msgs[idx] = msg
            
    max_idx = max(msgs.keys())
    await m.edit(f"✅ Found {len(msgs)} unique messages. Max Index mapped to {max_idx}.\n\n🚀 Starting manual sequential forward to Target Channel...")
    
    forwarded = 0
    missing = 0
    skipped = 0
    
    for i in range(1, max_idx + 1):
        if i in skip_indices:
            skipped += 1
            continue
            
        if i in msgs:
            msg = msgs[i]
            try:
                await msg.copy(target_chat_id)
                forwarded += 1
                await asyncio.sleep(1.5)
            except Exception as e:
                await message.reply(f"❌ Error forwarding Index {i}: {e}")
        else:
            missing += 1
            try:
                await client.send_message(target_chat_id, f"❌ **Missing File**\n\n📦 **Batch:** `{batch_name}`\n🔢 **Index:** `{i:03d}`\n\n_(Ye file log channel me nahi mili ya corrupt ho gayi thi)_")
                await asyncio.sleep(1.5)
            except:
                pass
                
    await m.edit(f"🎉 **Forwarding Completed!**\n\n📊 **Stats:**\n- Forwarded: {forwarded}\n- Missing (Text Sent): {missing}\n- Skipped by User: {skipped}\n- Total Processed: {max_idx}")
