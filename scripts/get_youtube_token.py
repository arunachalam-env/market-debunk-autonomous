import http.server
import socketserver
import urllib.parse
import requests
import os
import sys

PORT = 8091
CLIENT_ID = os.environ.get("YT_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET", "").strip()
REDIRECT_URI = f"http://localhost:{PORT}"

if not CLIENT_ID or not CLIENT_SECRET:
    raise SystemExit(
        "Set YT_CLIENT_ID and YT_CLIENT_SECRET in your environment before running this script."
    )

class OAuthHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        
        if "code" in params:
            code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authorization Successful!</h1><p>You can close this tab now and return to Antigravity.</p>")
            
            print(f"\n[OK] AUTHORIZATION CODE RECEIVED: {code}")
            print("Exchanging authorization code for permanent refresh token...")
            
            token_url = "https://oauth2.googleapis.com/token"
            data = {
                "code": code,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code"
            }
            res = requests.post(token_url, data=data)
            if res.status_code == 200:
                tokens = res.json()
                refresh_token = tokens.get("refresh_token")
                print(f"\n==========================================")
                print(f"PERMANENT YT_REFRESH_TOKEN RECOVERED:")
                print(refresh_token)
                print(f"==========================================\n")
                
                # Write to .env
                with open(".env", "a") as f:
                    f.write(f"\nYT_REFRESH_TOKEN={refresh_token}\n")
            else:
                print(f"TOKEN EXCHANGE FAILED: {res.status_code} {res.text}")
            
            sys.exit(0)
        else:
            self.send_response(400)
            self.end_headers()

auth_url = (
    f"https://accounts.google.com/o/oauth2/v2/auth?"
    f"client_id={CLIENT_ID}&redirect_uri={urllib.parse.quote(REDIRECT_URI)}&"
    f"response_type=code&scope={urllib.parse.quote('https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly')}&"
    f"access_type=offline&prompt=consent"
)

print("\n============================================================")
print("STEP 1: Add http://localhost:8091 to Google Cloud Console Credentials")
print("STEP 2: Click this URL to authorize YouTube uploads + statistics + retention analytics:")
print(auth_url)
print("============================================================\n")

with socketserver.TCPServer(("", PORT), OAuthHandler) as httpd:
    print(f"Server listening on port {PORT}...")
    httpd.serve_forever()
