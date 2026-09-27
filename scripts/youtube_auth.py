import os
import sys

# Ensure dependencies are installed or instruct the user
try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Error: 'google-auth-oauthlib' is not installed.")
    print("Please run: pip install google-auth-oauthlib")
    sys.exit(1)

# YouTube Data API upload scope
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

def main():
    print("====================================================")
    # Highlight title block
    print("      YOUTUBE OAUTH2 REFRESH TOKEN GENERATOR        ")
    print("====================================================\n")
    print("This script will guide you through the process of generating your")
    print("YouTube Refresh Token. You will need to copy this token into your")
    print("Google Cloud Secret Manager under the name 'YT_REFRESH_TOKEN'.\n")
    
    client_id = input("1. Enter your YouTube Client ID: ").strip()
    if not client_id:
        print("Error: Client ID cannot be empty.")
        return
        
    client_secret = input("2. Enter your YouTube Client Secret: ").strip()
    if not client_secret:
        print("Error: Client Secret cannot be empty.")
        return
        
    # Build OAuth2 Client Config
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            # Standard localhost redirect URI
            "redirect_uris": ["http://localhost:8080/"]
        }
    }
    
    print("\nStarting local server on port 8080...")
    print("A browser window should open shortly requesting permissions.")
    print("If it does not, copy the link displayed in the terminal.\n")
    
    try:
        # Prompt consent forces Google to return a refresh token (offline access)
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        creds = flow.run_local_server(
            port=8080,
            prompt="consent",
            access_type="offline"
        )
        
        print("\n====================================================")
        print("          CREDENTIALS CONFIGURATION VALUES          ")
        print("====================================================")
        print("Copy the following values exactly into your GCP Secret Manager:\n")
        print(f"YT_CLIENT_ID:      {client_id}")
        print(f"YT_CLIENT_SECRET:  {client_secret}")
        print(f"YT_REFRESH_TOKEN:  {creds.refresh_token}")
        print("====================================================")
        print("Authentication flow completed successfully!")
        
    except Exception as error:
        print(f"\nAuthentication failed: {error}")
        print("Ensure that you added 'http://localhost:8080/' as an Authorized Redirect URI")
        print("in your Google Cloud Credentials dashboard under OAuth 2.0 Client ID settings.")

if __name__ == "__main__":
    main()
