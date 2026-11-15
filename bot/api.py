import asyncio
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import HTMLResponse
from urllib.parse import unquote, urlparse
import os
import re
import time
import uuid
import logging
logger = logging.getLogger(__name__)
import aiohttp
import yarl
import json
import base64
from typing import Optional
from collections import defaultdict
from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.pssh import PSSH
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

app = FastAPI(title="Rishi C_B Secure DRM API")
API_KEY = os.getenv("API_SECRET_KEY", "")
api_key_header = APIKeyHeader(name="X-API-KEY", auto_error=True)

rate_limit_store: dict[str, list[float]] = defaultdict(list)

def check_rate_limit(api_key: str, limit: int = 500, window: int = 60) -> bool:
    now = time.time()
    keys_to_delete = [k for k, v in rate_limit_store.items() if v and (now - v[-1] > window)]
    for k in keys_to_delete:
        del rate_limit_store[k]
    rate_limit_store[api_key] = [t for t in rate_limit_store[api_key] if now - t < window]
    if len(rate_limit_store[api_key]) >= limit: return False
    rate_limit_store[api_key].append(now)
    return True

async def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != API_KEY: raise HTTPException(status_code=403, detail="Access Denied! Invalid Key.")
    if not check_rate_limit(api_key): raise HTTPException(status_code=429, detail="Rate limit exceeded!")
    return api_key

cdm = None
cdm_status = {"loaded": False, "system_id": None, "security_level": None, "device_file": None, "pywidevine_version": None}

try:
    import pywidevine
    cdm_version = getattr(pywidevine, "__version__", "unknown")
    logger.info(f"pywidevine version: {cdm_version}")
    cdm_status["pywidevine_version"] = cdm_version
    
    wvd_path = "samsung_sm-a145r_16.1.1_006_3da8a20e_28917_l3.wvd"
    abs_wvd_path = os.path.abspath(wvd_path)
    cdm_status["device_file"] = abs_wvd_path
    
    if os.path.exists(wvd_path):
        file_size = os.path.getsize(wvd_path)
        file_mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(wvd_path)))
        logger.info(f"WVD file found: {abs_wvd_path} (size: {file_size} bytes, modified: {file_mtime})")
        
        device = Device.load(wvd_path)
        cdm_status["system_id"] = device.system_id
        cdm_status["security_level"] = device.security_level
        logger.info(f"Device loaded: System ID={device.system_id}, Security Level=L{device.security_level}")
        
        cdm = Cdm.from_device(device)
        cdm_status["loaded"] = True
        logger.info("CDM initialized successfully - ready for DRM key extraction.")
    else:
        logger.error(f"CDM file NOT FOUND at: {abs_wvd_path}")
        logger.error(f"Expected WVD file 'samsung_sm-a145r_16.1.1_006_3da8a20e_28917_l3.wvd' is missing from the project directory!")
        logger.error("Widevine DRM decryption will be UNAVAILABLE. To fix: place a valid .wvd file in the project root.")
except ImportError as ie:
    logger.error(f"pywidevine package NOT INSTALLED: {ie}")
    logger.error("Run: pip install pywidevine")
except Exception as e:
    logger.error(f"CDM initialization FAILED:", exc_info=True)
    logger.error(f"Error details: {e}")

_mongo_client = None

async def get_mongo_db():
    global _mongo_client
    try:
        if _mongo_client is None:
            mongo_url = os.getenv("MONGO_URL", "")
            if mongo_url:
                _mongo_client = AsyncIOMotorClient(
                    mongo_url,
                    maxPoolSize=100,
                    minPoolSize=10,
                    maxIdleTimeMS=45000,
                    serverSelectionTimeoutMS=10000
                )
                await _mongo_client.admin.command('ping')
        if _mongo_client is not None:
            return _mongo_client["akkiup_db"]
    except Exception as e: 
        logger.error(f"MongoDB connection error: {e}")
    return None

from session_manager import get_global_session as get_session

async def auto_extract_signed_url(token: str, child_id: str, parent_id: str, quality: str = "720", force_type: str = None, force: bool = False) -> dict:
    if not token or not token.strip():
        return {"error": "Token is required and cannot be empty"}
    if not child_id or not parent_id:
        return {"error": "Both childId and parentId are required"}
    clean_token = token
    custom_random_id = None
    if "|" in clean_token:
        parts = clean_token.split("|", 1)
        clean_token = parts[0]
        custom_random_id = parts[1].strip()
        
    clean_token = clean_token.replace("Bearer ", "").replace("bearer ", "").strip()
    
    try:
        db = await get_mongo_db()
        cache_key = f"pw_data_v20_{child_id}" if child_id else f"pw_data_v20_{clean_token[:20]}"

        # 🔍 CACHE DEBUG: Track cache hits, misses, and bypasses
        if db is not None and not force:
            cached_doc = await db.cookies_cache.find_one({"key": cache_key})
            if cached_doc:
                age = time.time() - cached_doc.get("timestamp", 0)
                payload = cached_doc.get("payload")
                MAX_CACHE_AGE = 600  # 10 min
                if isinstance(payload, dict) and "error" not in payload and age < MAX_CACHE_AGE:
                    # 🔥 FIX: Invalidate cache if Cloudfront URL is missing Signature
                    stream_url = payload.get("stream_url", "")
                    if "cloudfront.net" in stream_url and "Signature=" not in stream_url:
                        logger.info(f"📦 CACHE INVALIDATED [childId={child_id[:12]}...]: Missing CloudFront Signature, forcing fresh fetch")
                    else:
                        logger.info(f"📦 CACHE HIT [childId={child_id[:12]}...]: returning cached URL (age={age:.0f}s/{MAX_CACHE_AGE}s)")
                        return payload
                else:
                    reason = "expired" if age >= MAX_CACHE_AGE else "not valid"
                    logger.info(f"📦 CACHE MISS [childId={child_id[:12]}...]: cache found but {reason} (age={age:.0f}s/{MAX_CACHE_AGE}s)")
            else:
                logger.info(f"📦 CACHE MISS [childId={child_id[:12]}...]: no cached entry found")
        elif force:
            logger.info(f"📦 CACHE BYPASS [childId={child_id[:12]}...]: force=1 requested, fetching fresh URL")

        # 🔥 Dynamically extract organizationId and roles from JWT token payload
        org_id = "5eb393ee95fab7468a79d189"  # Default PW org
        user_role = ""
        try:
            # JWT is 3 parts: header.payload.signature
            token_parts = clean_token.split(".")
            if len(token_parts) == 3:
                # Pad payload for base64 decoding
                payload_b64 = token_parts[1]
                padding = 4 - len(payload_b64) % 4
                if padding != 4:
                    payload_b64 += "=" * padding
                payload_json = base64.urlsafe_b64decode(payload_b64).decode('utf-8')
                payload_data = json.loads(payload_json)
                user_data = payload_data.get("data", {})
                org = user_data.get("organization", {})
                if isinstance(org, dict) and org.get("_id"):
                    org_id = org["_id"]
                roles_list = user_data.get("roles", [])
                if isinstance(roles_list, list) and len(roles_list) > 0:
                    user_role = str(roles_list[0])
                elif isinstance(roles_list, str):
                    user_role = roles_list
        except Exception as jwt_err:
            logger.warning(f"Could not extract org/roles from JWT: {jwt_err}")

        session = await get_session()
        api_url = "https://api.penpencil.co/v1/videos/video-url-details"

        q_map = {"1080": "1080p (FHD)", "720": "720p (HD)", "480": "480p", "360": "360p", "240": "240p"}
        vq = q_map.get(str(quality), "Auto")

        import hashlib
        if custom_random_id:
            final_random_id = custom_random_id
        else:
            token_hash = hashlib.md5(clean_token.encode()).hexdigest()
            final_random_id = f"{token_hash[:8]}-{token_hash[8:12]}-{token_hash[12:16]}-{token_hash[16:20]}-{token_hash[20:32]}"

        headers = {
            "Authorization": f"Bearer {clean_token}",
            "client-id": "5eb393ee95fab7468a79d189",
            "organization-id": org_id,  # Legacy format
            "organizationId": org_id,   # PW's new Media Api Node expects camelCase
            "client-type": "WEB",
            "client-version": "200",
            "randomid": final_random_id,
            "devicememory": "8192",
            "devicetype": "desktop",
            "networktype": "4g",
            "screenresolution": "1536 x 864",
            "frameratecapability": f'{{"videoQuality":"{vq}"}}',
            "audiocodeccapability": '{"AAC-LC":{"isSupported":true,"Profile":[{"container":"audio/mp4","supported":true},{"container":"audio/webm","supported":false},{"container":"audio/ogg","supported":false}]},"HE-AAC v1":{"isSupported":true,"Profile":[{"container":"audio/mp4","supported":true},{"container":"audio/webm","supported":false},{"container":"audio/ogg","supported":false}]},"HE-AAC v2":{"isSupported":true,"Profile":[{"container":"audio/mp4","supported":true},{"container":"audio/webm","supported":false},{"container":"audio/ogg","supported":false}]}}',
            "videocodeccapability": '{"Hevc":{"isSupported":"true","Profile":[{"name":"Main"},{"name":"Main 10"},{"name":"Main 12"},{"name":"Main 4:2:2 10"},{"name":"Main 4:4:4"},{"name":"Main 4:4:4 10"},{"name":"Main 4:4:4 12"},{"name":"Main 4:4:4 16 Intra"}]},"AV1":{"isSupported":"true","Profile":[{"name":"Main"},{"name":"High"},{"name":"Professional"}]}}',
            "drmcapability": '{"aesSupport":"yes","fairPlayDrmSupport":"no","playreadyDrmSupport":"no","widevineDRMSupport":"yes"}',
            "devicestreamingtechnology": '{"dash":{"isSupported":true,"formats":["mp4","m4a"],"codecs":["avc1","aac"]},"hls":{"isSupported":false,"formats":[],"codecs":[]}}',
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
            "Origin": "https://www.pw.live",
            "Referer": "https://www.pw.live/",
            "version": "0.0.1",
            "x-sdk-version": "0.0.20",
            "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "priority": "u=1, i",
            "Content-Type": "application/json"
        }

        # 🔥 prioritize force_type if supplied to reduce requests and avoid 429 rate limit
        if force_type:
            query_combos = [(parent_id, force_type)]
        else:
            query_combos = [
                (parent_id, "BATCHES"),
                (parent_id, "COURSES"),
                (parent_id, "SCHEDULES"),
                (parent_id, "LECTURES"),
                (parent_id, "LIVE"),
                (parent_id, "FREE_VIDEOS"),
                (parent_id, None),
                ("", "BATCHES"),
                ("", "SCHEDULES"),
                ("", None)
            ]
        
        for combo_idx, (p_id, req_type) in enumerate(query_combos):
            # Add an increasing delay between combos to avoid rate limiting
            if combo_idx > 0:
                await asyncio.sleep(0.5 * min(combo_idx, 4))
                
            params = {
                "videoContainerType": "DASH", 
                "reqType": "query", 
                "childId": child_id, 
                "clientVersion": "201",
                "organizationId": org_id,  # 🔥 Extracted from JWT token
            }
            if user_role:
                params["roles"] = user_role  # Only add if non-empty to avoid confusing PW API
            if p_id: params["parentId"] = p_id
            if req_type: params["type"] = req_type

            swapped_headers = False
            for pw_retry in range(3):
                try:
                    async with session.get(api_url, params=params, headers=headers, proxy=Config.get_random_proxy()) as res:
                        if res.status == 200:
                            data = await res.json()
                            api_payload = {"stream_url": "", "lic_url": "", "lic_token": "", "is_drm": False, "raw_error": ""}
                            
                            node = data.get('data')
                            if isinstance(node, dict):
                                url_val = node.get('url') or ""
                                signed_val = node.get('signedUrl') or ""
                                api_payload["stream_url"] = str(url_val) + str(signed_val)
                                
                                # 🔥 FIX: If CloudFront signature is missing, fallback to V2 API to get it
                                if "cloudfront.net" in api_payload["stream_url"] and "Signature=" not in api_payload["stream_url"]:
                                    try:
                                        v2_url = f"https://api.penpencil.co/v2/videos/{child_id}/get-video-link"
                                        async with session.get(v2_url, headers=headers) as v2_res:
                                            if v2_res.status == 200:
                                                v2_data = await v2_res.json()
                                                if "data" in v2_data and "url" in v2_data["data"]:
                                                    v2_stream = v2_data["data"]["url"]
                                                    if "Signature=" in v2_stream:
                                                        logger.info("✅ V2 API provided CloudFront Signature successfully!")
                                                        api_payload["stream_url"] = v2_stream
                                                    else:
                                                        logger.info("ℹ️ V2 API responded but URL still missing Signature in body.")
                                            else:
                                                logger.warning(f"V2 fallback status {v2_res.status}")
                                    except Exception as v2_err:
                                        logger.warning(f"V2 fallback failed: {v2_err}")

                                # 🔥 FIX 2: Check Session Cookies!
                                if "cloudfront.net" in api_payload["stream_url"] and "Signature=" not in api_payload["stream_url"]:
                                    logger.info("Checking session cookies for CloudFront Signature...")
                                    cf_cookies = {}
                                    for cookie in session.cookie_jar:
                                        if "CloudFront" in cookie.key:
                                            cf_cookies[cookie.key.replace("CloudFront-", "")] = cookie.value
                                    
                                    if "Signature" in cf_cookies and "Key-Pair-Id" in cf_cookies and "Policy" in cf_cookies:
                                        logger.info("✅ Found CloudFront cookies in session! Appending to URL.")
                                        joiner = "&" if "?" in api_payload["stream_url"] else "?"
                                        api_payload["stream_url"] += f"{joiner}Signature={cf_cookies['Signature']}&Key-Pair-Id={cf_cookies['Key-Pair-Id']}&Policy={cf_cookies['Policy']}"

                                drm_details = node.get('drmDetails') or {}
                                if drm_details:
                                    api_payload["is_drm"] = True
                                    api_payload["lic_url"] = drm_details.get('licenseUrl') or ""
                                    api_payload["lic_token"] = drm_details.get('licenseToken') or ""

                            if api_payload["stream_url"]:
                                if db is not None:
                                    try:
                                        await db.videos.update_one(
                                            {"video_id": child_id},
                                            {"$set": {"last_extracted_at": time.time(), "parent_id": p_id, "req_type": req_type}},
                                            upsert=True
                                        )
                                        await db.cookies_cache.update_one({"key": cache_key}, {"$set": {"payload": api_payload, "timestamp": time.time()}}, upsert=True)
                                    except Exception: pass
                                return api_payload
                            else:
                                break
                        elif res.status == 429:
                            err_text = await res.text()
                            logger.error(f"PW HTTP API Failed! Code 429: {err_text}")
                            # Immediately return the error so we stop trying and don't make the ban worse
                            return {"error": f"PW HTTP API Failed! Code 429: Account temporarily blocked for video access. PW Anti-Bot triggered. Wait a few hours. {err_text}"}

                        elif res.status == 400:
                            err_data = await res.json()
                            err_str = str(err_data)
                            if "Schedule does not exists" in err_str or "Invalid type" in err_str or "Schedule does not exist" in err_str:
                                break # move to next combo immediately
                            else:
                                await cache_failure(db, cache_key, f"PW API Error 400: {json.dumps(err_data)}")
                                return {"error": f"PW API Error 400: {json.dumps(err_data)}"}
                        elif res.status == 403:
                            try:
                                err_data = await res.json()
                                err_msg = err_data.get("message") or err_data.get("error", {}).get("message") or ""
                            except Exception:
                                err_msg = ""
                            
                            logger.error(f"PW API Error 403 Forbidden: {err_msg}")
                            break
                        else:
                            break
                except Exception:
                    if pw_retry < 2:
                        await asyncio.sleep(2 * (pw_retry + 1))
                        continue
                    break
                finally:
                    # Small jitter between retries even on success paths
                    await asyncio.sleep(0.3)

        # All combos exhausted — cache the negative result to avoid hammering PW
        await cache_failure(db, cache_key, "Schedule does not exist in BATCHES/COURSES or direct query.")
        return {"error": "Schedule does not exist in BATCHES/COURSES or direct query. Video might be completely removed from PW server."}

    except Exception as e: 
        logger.error(f"Auto Extract Crash: {e}")
        return {"error": f"Internal Python Crash in Auto Extract: {str(e)}"}


async def cache_failure(db, cache_key: str, error_msg: str):
    """Do NOT cache negative failure persistently so fresh token can retry"""
    pass

async def extract_pssh_and_lic_url(payload: dict):
    try:
        if not payload or not isinstance(payload, dict):
            return None, None
        session = await get_session()
        mpd_url = payload.get('stream_url', '').replace('&amp;', '&')
        if not mpd_url: return None, None
        
        cf_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Origin": "https://www.pw.live", "Referer": "https://www.pw.live/"}
        safe_url = yarl.URL(mpd_url, encoded=True)

        async with session.get(safe_url, headers=cf_headers, allow_redirects=True) as mpd_res:
            text = await mpd_res.text()
            if mpd_res.status == 200:
                pssh_matches = re.findall(r'<cenc:pssh[^>]*>(.*?)</cenc:pssh>', text)
                pssh = None
                if pssh_matches:
                    for p in pssh_matches:
                        try:
                            # Try to parse it to ensure it's Widevine
                            PSSH(p)
                            pssh = p
                            break
                        except Exception: pass
                    if not pssh:
                        pssh = max(pssh_matches, key=len)
                
                lic_matches = re.search(r'<LicenseURL>(.*?)</LicenseURL>', text)
                mpd_lic = lic_matches.group(1) if lic_matches else None
                final_lic_url = payload.get('lic_url') or mpd_lic
                return pssh, final_lic_url
            return None, None
    except Exception as e: 
        logger.error(f"PSSH extract error: {e}")
        return None, None

async def get_license_keys(pssh_b64: str, lic_url: str, lic_token: str, pw_token: str) -> list[str]:
    if not lic_url:
        lic_url = "https://api.penpencil.co/v1/videos/drm-license-manager"
    
    if not cdm:
        logger.error("CDM is None - device profile is not loaded!")
        return {"error": "CDM profile not loaded on server"}
    if not pssh_b64:
        return {"error": "PSSH data is empty"}
    
    session_id = None
    try:
        pssh_obj = PSSH(pssh_b64)
        session_id = cdm.open()
        challenge = cdm.get_license_challenge(session_id, pssh_obj)

        headers = {
            "Content-Type": "application/octet-stream",
            "Authorization": f"Bearer {pw_token}",
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-A145R) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36",
            "client-id": "system-admin",
            "client-type": "Android",
            "client-version": "201",
            "Origin": "https://www.pw.live",
            "Referer": "https://www.pw.live/"
        }
        if lic_token:
            headers["pallycon-customdata-v2"] = lic_token
            headers["customdata"] = lic_token

        session = await get_session()
        async with session.post(lic_url, data=challenge, headers=headers) as license_response:
            raw_response = await license_response.read()
            if license_response.status in [200, 201, 202]:
                content = raw_response
                content_type = license_response.headers.get("Content-Type", "").lower()
                
                # 🔥 FIX: Flexible Pallycon JSON response parsing
                if "application/json" in content_type or raw_response.strip().startswith(b'{'):
                    try:
                        text_resp = raw_response.decode('utf-8')
                        json_resp = json.loads(text_resp)
                        logger.info(f"Pallycon response JSON keys: {list(json_resp.keys())}")
                        
                        # Check for error responses first
                        if json_resp.get("success") is False or "error" in json_resp:
                            err_msg = json_resp.get("error") or json_resp.get("message") or str(json_resp)[:200]
                            logger.error(f"Pallycon returned error: {err_msg}")
                            return {"error": f"Pallycon rejected request: {err_msg}"}
                        
                        # Try multiple possible JSON paths for license key
                        license_b64 = None
                        for path in ["data.license", "response.license", "license", "data.key", "key"]:
                            parts = path.split(".")
                            val = json_resp
                            for part in parts:
                                if isinstance(val, dict):
                                    val = val.get(part)
                                else:
                                    val = None
                                    break
                            if val and isinstance(val, str) and len(val) > 10:
                                license_b64 = val
                                logger.info(f"Found license key at JSON path '{path}'")
                                break
                        
                        if license_b64:
                            content = base64.b64decode(license_b64)
                        else:
                            logger.warning(f"Could not find license key in JSON. Trying raw response. JSON snippet: {text_resp[:300]}")
                    except Exception as lic_err: 
                        logger.error(f"License JSON decode error: {lic_err}, raw: {raw_response[:200]}") 
                
                # Log the actual Pallycon response for debugging
                logger.info(f"Pallycon response status: {license_response.status}, content-type: {content_type}")
                logger.info(f"Pallycon response first 500 chars: {raw_response[:500] if isinstance(raw_response, bytes) else str(raw_response)[:500]}")
                
                # Parse license with CDM
                try:
                    cdm.parse_license(session_id, content)
                except Exception as parse_err:
                    logger.error(f"CDM parse_license failed: {parse_err}")
                    logger.error(f"Content type: {content_type}")
                    logger.error(f"Content first 200 bytes hex: {content[:200].hex() if isinstance(content, bytes) and len(content) >= 200 else f'len={len(content)}: {content[:200]}'}")
                    return {"error": f"Failed to parse license: {str(parse_err)[:200]}"}
                
                keys_data = cdm.get_keys(session_id)
                final_keys = [f"{key.kid.hex}:{key.key.hex()}" for key in keys_data if key.type == "CONTENT"]
                return final_keys
            else:
                err_text = await license_response.text()
                return {"error": f"Pallycon HTTP {license_response.status}: {err_text[:300]}"}
    except Exception as e: 
        logger.error(f"Get License keys error: {e}")
        return {"error": str(e)}
    finally:
        if session_id is not None:
            try:
                cdm.close(session_id)
            except Exception:
                pass

@app.get("/api/get-keys")
async def secure_extract(url: str, parentId: str, childId: str, token: str, type: str = None, api_key: str = Depends(verify_api_key), quality: str = "720", force: int = 0):
    try:
        payload = await auto_extract_signed_url(token, childId, parentId, quality, type, force=bool(force))
        
        if payload and "error" in payload:
            return {"success": False, "error": payload["error"]}
        
        if not payload or not payload.get('stream_url'):
            return {"success": False, "error": "Internal API payload extraction failed silently."}

        pssh_data, lic_url = await extract_pssh_and_lic_url(payload)

        is_drm = payload.get('is_drm', False) and bool(payload.get('lic_token'))

        if not is_drm:
            return {
                "success": True, 
                "video_id": childId, 
                "batch_id": parentId, 
                "is_drm": False, 
                "direct_url": payload.get('stream_url')
            }

        if not pssh_data:
            # Maybe it's a non-DRM video that was mislabelled, preserve the signed URL!
            return {
                "success": True, 
                "video_id": childId, 
                "batch_id": parentId, 
                "is_drm": False,
                "keys": [],
                "direct_url": payload.get('stream_url'),
                "error": "MPD blocked or PSSH missing (Assuming Non-DRM)"
            }

        lic_token = payload.get('lic_token', '')
        extracted_keys = await get_license_keys(pssh_data, lic_url, lic_token, token)
        
        if isinstance(extracted_keys, dict) and "error" in extracted_keys:
            # DRM Failed, but preserve the signed URL so download works (even if encrypted)
            return {
                "success": True, 
                "video_id": childId, 
                "batch_id": parentId, 
                "is_drm": True,
                "keys": [],
                "direct_url": payload.get('stream_url'),
                "error": f"Pallycon Rejected: {extracted_keys['error']}"
            }
        
        if not extracted_keys:
            return {
                "success": True, 
                "video_id": childId, 
                "batch_id": parentId, 
                "is_drm": True,
                "keys": [],
                "direct_url": payload.get('stream_url'),
                "error": "Pallycon License Server returned no decryption keys."
            }

        kid_hex, key_hex = extracted_keys[0].split(":") 
        kid_with_dash = f"{kid_hex[:8]}-{kid_hex[8:12]}-{kid_hex[12:16]}-{kid_hex[16:20]}-{kid_hex[20:]}"

        return {
            "success": True, 
            "video_id": childId, 
            "batch_id": parentId, 
            "is_drm": True,
            "keys": extracted_keys, 
            "direct_url": payload.get('stream_url'),
            "data": { 
                "mpd": payload.get('stream_url'),
                "kid": kid_with_dash,
                "kid_nodash": kid_hex,
                "key": key_hex
            }
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

# 🔥 YEH HAI NAYA DIRECT PLAYER ENDPOINT 🔥
@app.get("/play", response_class=HTMLResponse)
async def auto_play_video(url: str, parentId: str, childId: str, token: str):
    try:
        payload = await auto_extract_signed_url(token, childId, parentId)
        
        if payload and "error" in payload:
            return f"<h1 style='color:red; text-align:center;'>Error: {payload['error']}</h1>"
            
        direct_url = payload.get('stream_url')
        if not direct_url:
            return "<h1 style='color:red; text-align:center;'>Error: Stream URL not found!</h1>"

        clear_keys_js = "{}"
        pssh_data, lic_url = await extract_pssh_and_lic_url(payload)
        is_drm = payload.get('is_drm', False) or (pssh_data is not None)

        if is_drm:
            if pssh_data:
                extracted_keys = await get_license_keys(pssh_data, lic_url, payload.get('lic_token', ''), token)
                if extracted_keys:
                    keys_dict = {k.split(':')[0]: k.split(':')[1] for k in extracted_keys}
                    clear_keys_js = json.dumps(keys_dict)

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Rishi C_B Stream Engine ⚡</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <script src="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.7.1/shaka-player.compiled.js"></script>
            <style>
                body {{ background-color: #000; margin: 0; display: flex; justify-content: center; align-items: center; height: 100vh; overflow: hidden; }}
                video {{ width: 100%; max-width: 100%; height: 100%; outline: none; }}
            </style>
        </head>
        <body>
            <video id="video" controls autoplay></video>
            
            <script>
                async function initPlayer() {{
                    const video = document.getElementById('video');
                    const player = new shaka.Player(video);
                    
                    const manifestUri = '{direct_url}';
                    const isDrm = {'true' if is_drm else 'false'};
                    const clearKeys = {clear_keys_js};

                    if (isDrm && Object.keys(clearKeys).length > 0) {{
                        player.configure({{
                            drm: {{ clearKeys: clearKeys }}
                        }});
                    }}

                    try {{
                        await player.load(manifestUri);
                        console.log('Stream loaded automagically! 🚀');
                    }} catch (e) {{
                        console.error('Player Error:', e);
                        alert("Stream load nahi hui bhai, console check karo.");
                    }}
                }}
                document.addEventListener('DOMContentLoaded', initPlayer);
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html)

    except Exception as e:
        return f"<h1 style='color:red; text-align:center;'>Internal API Error: {str(e)}</h1>"

@app.get("/")
async def root(): return {"status": "ok", "message": "Rishi C_B API Running"}

@app.get("/health")
async def health():
    mongo_ok = _mongo_client is not None
    return {
        "status": "ok" if cdm_status["loaded"] else "degraded",
        "cdm": cdm_status,
        "components": {
            "mongodb": "connected" if mongo_ok else "not connected",
        }
    }

@app.on_event("shutdown")
async def cleanup():
    global _mongo_client
    if _mongo_client:
        _mongo_client.close()
    # Session cleanup is handled by session_manager
