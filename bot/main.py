import asyncio

# Setup a single global event loop to prevent mismatched loop errors in Pyrogram
try:
    global_loop = asyncio.get_event_loop_policy().get_event_loop()
except RuntimeError:
    global_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(global_loop)

def _get_event_loop():
    return global_loop

asyncio.get_event_loop = _get_event_loop

import sys
# Fix terminal encoding for Windows (cp1252) to support Unicode/emoji output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import logging
from pyrogram import Client
from config import Config
import pyrogram.utils as pyroutils

def get_peer_type_new(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    elif peer_id_str.startswith("-100"):
        return "channel"
    else:
        return "chat"

pyroutils.get_peer_type = get_peer_type_new

# Configure logging with UTF-8 encoding for Windows compatibility
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('akkiup.log', encoding='utf-8')
    ]
)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

RISHI_BANNER = """
   ██████╗ ██╗███████╗██╗  ██╗██╗
   ██╔══██╗██║██╔════╝██║  ██║██║
   ██████╔╝██║███████╗███████║██║    C_B
   ██╔══██╗██║╚════██║██╔══██║██║    EXTRACTOR
   ██║  ██║██║███████║██║  ██║██║
   ╚═╝  ╚═╝╚═╝╚══════╝╚═╝  ╚═╝╚═╝
 ⚡ THE ULTIMATE DRM BYPASS ENGINE ⚡
 👑 Developed by: @rishisin070 👑
"""

logger.info("Starting Rishi C_B Extractor...")

app = Client(
    "AkkiUploaderBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    in_memory=True,
    sleep_threshold=120,
    max_concurrent_transmissions=5,
    plugins=dict(root="plugins")
)

from pyrogram.handlers import MessageHandler
from pyrogram import StopPropagation

# ----------------- CUSTOM LISTENER IMPLEMENTATION -----------------
_active_listeners = {}

async def listen_handler(client, message):
    chat_id = message.chat.id
    if chat_id in _active_listeners:
        future, filter_fn = _active_listeners[chat_id]
        if not future.done():
            if filter_fn:
                if callable(filter_fn):
                    if asyncio.iscoroutinefunction(filter_fn.__call__):
                        matched = await filter_fn(client, message)
                    else:
                        matched = filter_fn(client, message)
                    if not matched:
                        return
            future.set_result(message)
            raise StopPropagation

async def listen(self, chat_id, filters=None, timeout=None):
    if chat_id in _active_listeners:
        old_future, _ = _active_listeners[chat_id]
        if not old_future.done():
            old_future.cancel()
    
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    _active_listeners[chat_id] = (future, filters)
    
    try:
        if timeout:
            return await asyncio.wait_for(future, timeout=timeout)
        else:
            return await future
    finally:
        if chat_id in _active_listeners:
            curr_future, _ = _active_listeners[chat_id]
            if curr_future is future:
                del _active_listeners[chat_id]

# Monkeypatch Client class and add high-priority listener handler
Client.listen = listen
app.add_handler(MessageHandler(listen_handler), group=-1)
# ------------------------------------------------------------------

if __name__ == "__main__":
    from pyrogram import idle
    import asyncio
    from plugins.uploader import resume_all_batches, load_parallel_setting
    from plugins.scheduler import start_scheduler

    async def web_server():
        import uvicorn
        from api import app as fastapi_app
        import os
        port = int(os.environ.get("PORT", 10000))
        config = uvicorn.Config(app=fastapi_app, host="0.0.0.0", port=port, log_level="info")
        server = uvicorn.Server(config)
        print(f"FastAPI API Server started on port {port} for Render!")
        await server.serve()

    async def main():
        print(RISHI_BANNER)
        print("\U0001f680 Rishi C_B Modular Engine Starting...")
        
        # Start command in Render already runs uvicorn, so we just start the bot
        # asyncio.create_task(web_server())
        
        await app.start()
        print("\U0001f30d API and BOT Dual-Engine is ONLINE!")

        from pyrogram.types import BotCommand
        await app.set_bot_commands([
            BotCommand("start", "Start the bot & Open Dashboard"),
            BotCommand("pwlogin", "Login to PW Account via Token"),
            BotCommand("getbatch", "Extract all contents of a Batch"),
            BotCommand("today", "Extract today's videos & notes"),
            BotCommand("date", "Extract videos & notes of a specific date"),
            BotCommand("daterange", "Extract contents between two dates"),
            BotCommand("fromdate", "Extract contents from date till today"),
            BotCommand("subject", "Extract contents of specific subjects"),
            BotCommand("mybatches", "View your purchased batches"),
            BotCommand("mytokens", "View all your Active Tokens"),
            BotCommand("cleartokens", "Clear all your login Tokens"),
            BotCommand("schedule", "Setup auto extraction schedule (Admin)"),
            BotCommand("myschedules", "View active schedules (Admin)"),
            BotCommand("cancel", "Cancel current active extraction"),
            BotCommand("stopall", "Stop all running uploads"),
            BotCommand("addauth", "Give premium access to a user (Admin)"),
            BotCommand("broadcast", "Send message to all users (Admin)")
        ])
        print("Bot commands set successfully!")
        
        # Load configurable parallel setting from DB
        await load_parallel_setting()
        
        # Trigger auto-resume
        await resume_all_batches(app)
        
        # Start Delta Scheduler
        start_scheduler(app)
        print("\u23f0 APScheduler Engine is ACTIVE!")
        
        await idle()
        await app.stop()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
