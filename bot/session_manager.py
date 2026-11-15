import aiohttp
import atexit
import asyncio

_app_session: aiohttp.ClientSession | None = None

async def get_global_session() -> aiohttp.ClientSession:
    global _app_session
    if _app_session is None or _app_session.closed:
        timeout = aiohttp.ClientTimeout(total=120, connect=20, sock_read=60)
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=30,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
            force_close=False,
            keepalive_timeout=45
        )
        _app_session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            raise_for_status=False,
            cookie_jar=aiohttp.CookieJar(unsafe=True)
        )
    return _app_session

async def close_global_session():
    """Properly close the global session"""
    global _app_session
    if _app_session and not _app_session.closed:
        await _app_session.close()
        _app_session = None

def cleanup_session():
    """Synchronous cleanup for atexit"""
    global _app_session
    if _app_session and not _app_session.closed:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(close_global_session())
            else:
                loop.run_until_complete(close_global_session())
        except Exception:
            pass

atexit.register(cleanup_session)
