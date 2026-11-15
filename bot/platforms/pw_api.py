def get_pw_headers(token=None):
    """PW platform ke liye custom WEB headers"""
    import uuid
    import hashlib
    headers = {
        'authorization': '',
        'client-id': '5eb393ee95fab7468a79d189',
        'client-type': 'WEB',
        'client-version': '4.6.0',
        'content-type': 'application/json',
        'origin': 'https://www.pw.live',
        'randomid': str(uuid.uuid4()), 
        'referer': 'https://www.pw.live/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
        'x-sdk-version': '0.0.20'
    }
    if token:
        clean_token = token
        custom_random = None
        if "|" in clean_token:
            parts = clean_token.split("|", 1)
            clean_token = parts[0]
            custom_random = parts[1].strip()
            
        clean_token = clean_token.replace("Bearer ", "").replace("bearer ", "").strip()
        headers["authorization"] = f"Bearer {clean_token}"
        
        if custom_random:
            headers["randomid"] = custom_random
        else:
            token_hash = hashlib.md5(clean_token.encode()).hexdigest()
            headers["randomid"] = f"{token_hash[:8]}-{token_hash[8:12]}-{token_hash[12:16]}-{token_hash[16:20]}-{token_hash[20:32]}"
    else:
        del headers['authorization']
    return headers

# Yahan aage chal kar hum PW ke login requests ya OTP APIs ko bhi alag functions me daal sakte hain.
