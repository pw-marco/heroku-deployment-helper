import os
from pyrogram import Client, filters
from config import Config

@Client.on_message(filters.command("t") & filters.user(Config.OWNER_ID))
async def set_thumbnail(client, message):
    if not message.reply_to_message or not message.reply_to_message.photo:
        return await message.reply_text("❌ **Please reply to a photo with `/t` to set it as a custom thumbnail.**")
    
    msg = await message.reply_text("⏳ **Downloading thumbnail...**")
    try:
        # Save it as thumb.jpg in the root directory
        await message.reply_to_message.download(file_name="thumb.jpg")
        await msg.edit("✅ **Custom thumbnail saved successfully!**\nIt will be applied to all future video uploads.")
    except Exception as e:
        await msg.edit(f"❌ **Failed to save thumbnail:** `{str(e)}`")

@Client.on_message(filters.command("delthumb") & filters.user(Config.OWNER_ID))
async def delete_thumbnail(client, message):
    if os.path.exists("thumb.jpg"):
        try:
            os.remove("thumb.jpg")
            await message.reply_text("✅ **Custom thumbnail deleted successfully!**\nBot will now auto-generate thumbnails from videos.")
        except Exception as e:
            await message.reply_text(f"❌ **Error deleting thumbnail:** `{str(e)}`")
    else:
        await message.reply_text("ℹ️ **No custom thumbnail found.**")
