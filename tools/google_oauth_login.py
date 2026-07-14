import json
import wsgiref.simple_server
from pathlib import Path
from urllib.parse import parse_qs

from google_auth_oauthlib.flow import InstalledAppFlow


ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS_PATH = ROOT / "config" / "credentials" / "google_oauth_credentials.json"
URL_PATH = ROOT / "config" / "credentials" / "google_oauth_login_url.txt"
PORT = 8090
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


def main() -> None:
    data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8-sig"))
    client_id = data.get("client_id", "").strip()
    client_secret = data.get("client_secret", "").strip()
    if not client_id or not client_secret:
        raise SystemExit(
            "Fill client_id and client_secret in config/credentials/google_oauth_credentials.json first."
        )

    redirect_uri = f"http://localhost:{PORT}/"
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=SCOPES,
    )
    flow.redirect_uri = redirect_uri
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    URL_PATH.write_text(auth_url, encoding="utf-8")
    print("Full login URL saved to:", URL_PATH)
    print(auth_url)
    print("\nOpen that URL in your browser address bar. Waiting for Google redirect...")

    callback = {}

    def app(environ, start_response):
        query = parse_qs(environ.get("QUERY_STRING", ""))
        callback["query"] = query
        if "error" in query:
            body = f"Authorization failed: {query['error'][0]}".encode("utf-8")
        else:
            body = b"Authorization complete. You can close this tab and return to Codex."
        start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
        return [body]

    server = wsgiref.simple_server.make_server("localhost", PORT, app)
    server.handle_request()

    query = callback.get("query", {})
    if "error" in query:
        raise SystemExit(f"Google authorization failed: {query['error'][0]}")
    code = query.get("code", [""])[0]
    if not code:
        raise SystemExit("Google did not return an authorization code.")

    flow.fetch_token(code=code)
    credentials = flow.credentials
    data["refresh_token"] = credentials.refresh_token or data.get("refresh_token", "")
    data["token_uri"] = credentials.token_uri
    data["scopes"] = SCOPES
    CREDENTIALS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print("OAuth credentials saved:", CREDENTIALS_PATH)
    print("Refresh token present:", bool(data["refresh_token"]))


if __name__ == "__main__":
    main()
