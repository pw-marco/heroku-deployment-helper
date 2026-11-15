import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Required environment variables - no defaults for security
    API_ID = int(os.environ.get("API_ID") or 0)
    API_HASH = os.environ.get("API_HASH", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    MONGO_URL = os.environ.get("MONGO_URL", "")

    # 🟢 FIX: Yahan apna Asli Telegram ID daal de (bina quotes ke)
    OWNER_ID = int(os.environ.get("OWNER_ID") or 0)  # Heroku Config Vars me OWNER_ID daalo

    # API Configuration
    API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "")
    HOST_URL = os.environ.get("HOST_URL", "")
    LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID") or 0)
    AUTH_LOG_CHANNEL_ID = int(os.environ.get("AUTH_LOG_CHANNEL_ID") or 0)

    # Direct link upload (no PW login). "true" = sirf Premium users, "false" = sab users
    DIRECT_UPLOAD_PREMIUM_ONLY = os.environ.get("DIRECT_UPLOAD_PREMIUM_ONLY", "false").strip().lower() in ("1", "true", "yes", "on")

    @classmethod
    def get_api_base_urls(cls):
        port = os.environ.get("PORT", "8000")
        urls = [f"http://127.0.0.1:{port}"]
        if cls.HOST_URL and cls.HOST_URL.rstrip('/') not in urls:
            urls.append(cls.HOST_URL.rstrip('/'))
        return urls

    # Allowed domains for SSRF protection
    ALLOWED_VIDEO_DOMAINS = [
        "d1d34p8vz63oiq.cloudfront.net",
        "cdn.pw.live",
        "media-cdn.classplusapp.com",
        "videotest.adda247.com"
    ]

import random
import requests
import logging

class ProxyManager:
    _proxies = []
    
    @classmethod
    def get_proxy(cls):
        if not cls._proxies:
            env_proxies = os.environ.get("PROXIES", "")
            if env_proxies:
                cls._proxies = [p.strip() for p in env_proxies.split(",") if p.strip()]
        
        if cls._proxies:
            import random
            return random.choice(cls._proxies)
        return None

Config.get_random_proxy = ProxyManager.get_proxy

