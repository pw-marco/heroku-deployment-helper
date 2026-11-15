import aiohttp
import asyncio
import logging
from pyrogram import Client, filters
from database.mongo_db import get_user_tokens, is_premium_user
from config import Config
import aiofiles.os

logger = logging.getLogger(__name__)

CREDIT = "\n\n〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n👑 **ᴅᴇᴠᴇʟᴏᴘᴇᴅ & ᴏᴡɴᴇᴅ ʙʏ: @rishisin070**\n⚡ *Powered by Cinemaa Boxoffice [C_B]*"

@Client.on_message(filters.command("fuzz") & filters.private)
async def api_fuzzer(client: Client, message):
    """Smart API fuzzer to find accessible content"""
    
    if not await is_premium_user(message.chat.id):
        return await message.reply_text("❌ **Sirf Premium Users is feature ko use kar sakte hain.**")
    
    if len(message.command) < 2:
        return await message.reply_text(
            "⚠️ **Format:** `/fuzz BATCH_ID`\n\n"
            "**Example:** `/fuzz 641abc123def456`\n\n"
            "🔍 **Ye command multiple API tricks try karega:**\n"
            "• Different API versions\n"
            "• Demo/Free content endpoints\n"
            "• Parameter manipulation\n"
            "• Legacy endpoints\n\n"
            "⚠️ **Educational purpose only!**"
        )
    
    batch_id = message.command[1]
    tokens = await get_user_tokens(message.chat.id)
    
    status_msg = await message.reply_text("🔍 **API Fuzzing shuru kar raha hu...**")
    
    # Test configurations
    api_versions = ["v1", "v2", "v3", "v4"]
    content_types = ["DEMO", "FREE_VIDEOS", "SAMPLE", "TRIAL", "videos", "ALL"]
    org_ids = [
        "5eb393ee95fab7468a79d189",  # Main PW
        "5eb393ee95fab7468a79d188",  # Variation
        None  # No org ID
    ]
    client_versions = ["1", "100", "200", "4.6.0"]
    
    found_content = []
    attempts = 0
    
    try:
        async with aiohttp.ClientSession() as session:
            await status_msg.edit_text("🔍 **Testing API endpoints...**")
            
            # Test 1: Public endpoints (no auth)
            for version in api_versions:
                for content_type in content_types:
                    attempts += 1
                    
                    url = f"https://api.penpencil.co/{version}/batches/{batch_id}/contents"
                    params = {"contentType": content_type, "page": "1"}
                    headers = {
                        "client-id": "5eb393ee95fab7468a79d189",
                        "client-type": "WEB",
                        "User-Agent": "Mozilla/5.0"
                    }
                    
                    try:
                        async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as res:
                            if res.status == 200:
                                data = await res.json()
                                items = data.get('data', [])
                                if items:
                                    logger.info(f"Found {len(items)} items via {version}/{content_type}")
                                    for item in items:
                                        url_val = item.get('url', '')
                                        if url_val and url_val not in [x['url'] for x in found_content]:
                                            found_content.append({
                                                'title': item.get('topic', item.get('name', 'Unknown')),
                                                'url': url_val,
                                                'method': f"{version}/{content_type}",
                                                'is_video': 'youtu' in url_val or '.mpd' in url_val or '.m3u8' in url_val
                                            })
                    except:
                        pass
                    
                    if attempts % 5 == 0:
                        await status_msg.edit_text(f"🔍 **Testing... {attempts} attempts**\n🎯 **Found: {len(found_content)} items**")
            
            # Test 2: With tokens (if available)
            if tokens:
                await status_msg.edit_text(f"🔑 **Testing with tokens... ({len(tokens)} tokens)**")
                
                for token in tokens[:3]:  # Test max 3 tokens
                    for version in ["v2", "v3"]:
                        attempts += 1
                        
                        url = f"https://api.penpencil.co/{version}/batches/{batch_id}/subject"
                        clean_token = token.split('|')[0] if '|' in token else token
                        headers = {
                            "Authorization": f"Bearer {clean_token}",
                            "client-id": "5eb393ee95fab7468a79d189",
                            "client-type": "WEB",
                            "client-version": "200"
                        }
                        
                        try:
                            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as res:
                                if res.status == 200:
                                    data = await res.json()
                                    # Extract subjects and try to get their content
                                    subjects = data.get('data', {}).get('subjects', [])
                                    for sub in subjects[:2]:  # Test first 2 subjects
                                        sub_id = sub.get('_id')
                                        content_url = f"https://api.penpencil.co/{version}/batches/{batch_id}/subject/{sub_id}/contents"
                                        
                                        async with session.get(content_url, params={'page': '1'}, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as c_res:
                                            if c_res.status == 200:
                                                c_data = await c_res.json()
                                                items = c_data.get('data', [])
                                                for item in items[:5]:  # Max 5 per subject
                                                    url_val = item.get('url', '')
                                                    if url_val and url_val not in [x['url'] for x in found_content]:
                                                        found_content.append({
                                                            'title': item.get('topic', item.get('name', 'Unknown')),
                                                            'url': url_val,
                                                            'method': f"Token/{version}",
                                                            'is_video': 'youtu' in url_val or '.mpd' in url_val or '.m3u8' in url_val
                                                        })
                        except:
                            pass
            
            # Test 3: Legacy endpoints
            await status_msg.edit_text(f"🔍 **Testing legacy endpoints...**")
            legacy_endpoints = [
                f"https://api.penpencil.xyz/v1/batches/{batch_id}/videos",
                f"https://api.penpencil.xyz/v2/batches/{batch_id}/demo",
                f"https://api.physicswallah.live/v1/batches/{batch_id}/free"
            ]
            
            for endpoint in legacy_endpoints:
                attempts += 1
                try:
                    async with session.get(endpoint, timeout=aiohttp.ClientTimeout(total=10)) as res:
                        if res.status == 200:
                            data = await res.json()
                            items = data.get('data', [])
                            for item in items:
                                url_val = item.get('url', '')
                                if url_val and url_val not in [x['url'] for x in found_content]:
                                    found_content.append({
                                        'title': item.get('topic', item.get('name', 'Unknown')),
                                        'url': url_val,
                                        'method': 'Legacy',
                                        'is_video': True
                                    })
                except:
                    pass
            
            await status_msg.edit_text(f"✅ **Fuzzing complete!**\n🔍 **Total attempts:** {attempts}\n🎯 **Found:** {len(found_content)} items")
            
            if not found_content:
                return await status_msg.edit_text(
                    f"❌ **Kuch nahi mila!**\n\n"
                    f"🔍 **Attempts:** {attempts}\n\n"
                    "**Possible reasons:**\n"
                    "1. Batch completely private hai\n"
                    "2. Koi demo/free content nahi hai\n"
                    "3. PW ne API security badha di\n\n"
                    "💡 **Try:** Purchase karo ya valid token use karo"
                )
            
            # Create TXT file
            txt_content = f"=================================\n"
            txt_content += f"🔍 API FUZZING RESULTS\n"
            txt_content += f"🆔 Batch ID: {batch_id}\n"
            txt_content += f"🎯 Found: {len(found_content)} items\n"
            txt_content += f"🔍 Attempts: {attempts}\n"
            txt_content += f"=================================\n\n"
            
            for idx, item in enumerate(found_content, 1):
                ext = '.mp4' if item['is_video'] else '.pdf'
                txt_content += f"{idx}. {item['title']}{ext}: {item['url']}\n"
                txt_content += f"   Method: {item['method']}\n\n"
            
            txt_filename = f"FUZZ_{batch_id[:8]}_{len(found_content)}_items.txt"
            
            with open(txt_filename, 'w', encoding='utf-8') as f:
                f.write(txt_content)
            
            await status_msg.delete()
            await client.send_document(
                message.chat.id,
                txt_filename,
                caption=(
                    f"🎉 **API Fuzzing Complete!**\n\n"
                    f"🆔 **Batch ID:** `{batch_id}`\n"
                    f"🔍 **Attempts:** {attempts}\n"
                    f"🎯 **Found:** {len(found_content)} items\n\n"
                    f"💡 **Ab is TXT file ko send karo download karne ke liye!**"
                    + CREDIT
                )
            )
            
            await aiofiles.os.remove(txt_filename)
    
    except Exception as e:
        logger.error(f"Fuzzing error: {e}")
        await status_msg.edit_text(f"🚨 **Error:** `{str(e)}`")