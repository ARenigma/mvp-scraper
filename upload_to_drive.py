from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from loguru import logger
import json
import os
import requests

logger.add("upload.log", rotation="1 week", level="INFO")

# Google Drive Folder ID (where to upload)
FOLDER_ID = "1N2DJlGgDpjTKV4SizuLqo4oxGKOxe5yO"

# Create temp service account JSON from Railway environment variable
SERVICE_ACCOUNT_FILE = "temp_creds.json"
key_data = json.loads(os.environ["GDRIVE_KEY_JSON"])

with open(SERVICE_ACCOUNT_FILE, "w") as f:
    json.dump(key_data, f)

def upload_to_drive(csv_path):
    """
    Upload a CSV file to Google Drive under the given folder.
    Sends a Telegram alert on success or failure.
    """
    try:
        if not os.path.exists(csv_path):
            logger.error(f"❌ File not found: {csv_path}")
            notify_telegram(f"❌ *Upload failed:* File not found:\n`{csv_path}`")
            return

        # Load credentials
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        drive_service = build("drive", "v3", credentials=creds)

        # Prepare metadata
        file_metadata = {
            "name": os.path.splitext(os.path.basename(csv_path))[0],
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "parents": [FOLDER_ID]
        }
        media = MediaFileUpload(csv_path, mimetype="text/csv", resumable=True)

        # Upload
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id,webViewLink"
        ).execute()

        sheet_link = file.get("webViewLink")
        logger.success(f"✅ Google Sheet created: {sheet_link}")

        # ✅ Telegram success notification
        notify_telegram(f"✅ *Upload successful!*\n\n`{os.path.basename(csv_path)}`\n[📄 Open Sheet]({sheet_link})")

    except Exception as e:
        logger.exception(f"❌ Upload failed: {e}")
        notify_telegram(f"❌ *Upload to Google Drive failed!*\n\nError:\n`{str(e)}`")

def notify_telegram(message):
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")

        if not token or not chat_id:
            logger.warning("⚠️ Telegram credentials not set. Skipping alert.")
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }

        r = requests.post(url, json=payload)
        if r.status_code == 200:
            logger.info("📨 Telegram alert sent.")
        else:
            logger.warning(f"⚠️ Failed to send Telegram alert: {r.text}")

    except Exception as e:
        logger.error(f"❌ Telegram notification error: {e}")
