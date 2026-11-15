# AkkiUp Bot - Automated Extraction & Uploading

This repository contains a Telegram Bot built with Pyrogram and FastAPI. It is designed to extract course content (Videos, PDFs) via APIs, generate text indexes, and automatically upload them to Telegram channels. It supports daily scheduled extractions and persistent state tracking.

## 📂 Project Structure & Component Guide

To help future modifications, here is how the codebase is organized and how it works:

### Core Files
- **`main.py`**: The main entry point for the Telegram Bot. It initializes the Pyrogram client, loads the plugins, and starts the background task for the scheduler.
- **`api.py`**: A FastAPI server. It provides health-check routes to keep the Heroku web dyno alive and exposes endpoints for external integrations.
- **`config.py`**: Loads and manages all environment variables.
- **`Dockerfile` / `heroku.yml`**: Configuration for Docker-based deployment on Heroku. The Docker container runs both the FastAPI server (`uvicorn`) and the Bot (`python3 main.py`) concurrently.

### 🔌 Plugins (`/plugins` directory)
This is where the bot commands and logic reside.
- **`auth.py`**: Handles user authentication commands (`/start`, `/login`, `/token`, `/logout`).
- **`extractor.py`**: Core logic for downloading course structures. Commands like `/extract` (full), `/today` (current day), and `/date` (specific day) generate `.txt` files containing video and PDF links. Daily output files are prefixed with `NEW_`.
- **`uploader.py`**: The most critical module for uploading. 
  - **`execute_batch`**: Downloads media using external tools (`N_m3u8DL-RE` / `yt-dlp`) and uploads it to Telegram.
  - **Caption Logic**: Reads `show_index` to decide whether to include `🔢 Index:` numbers.
  - **Topic Links**: Captures the Target Channel links for the uploaded files and creates a Final Index List (Table of Contents) at the end of the batch.
- **`scheduler.py`**: Handles daily automated extractions. Tracks when to run auto-updates using MongoDB.
- **`forwarder.py` / `premium_tools.py`**: Extra utility tools for forwarding and DRM handling.

### 🌐 API Interaction (`/platforms` directory)
- **`pw_api.py`**: Logic for directly interacting with platform APIs. Contains functions to get batches, subjects, and batch details, and generate headers for requests.

### 🗄️ Database (`/database` directory)
- **`mongo_db.py`**: Handles all MongoDB interactions. Saves authorized users, active batches (for auto-resume), scheduler data, and platform tokens.

---

## 🚀 How Automated Daily Uploads Work (Workflow)

1. The user schedules an update via `/schedule`.
2. The `schedule_loop` inside `main.py` periodically checks for pending jobs.
3. Once triggered, `extractor.py` pulls only the content for the current day. It generates a text file named `NEW_...txt`.
4. The file is passed to `uploader.py` via `execute_batch` with `show_index=False`.
5. The bot downloads the content, uploads it to the `LOG_CHANNEL_ID`, forwards it to the `TARGET_CHANNEL_ID`, and creates a final Table of Contents list without index numbers.

---

## 🛠️ Deployment Instructions

This project is deployed to **Heroku** using the **Docker** stack.

**To Push Changes to Heroku:**
```bash
git add .
git commit -m "Your descriptive message here"
git push heroku main
```

**Required Environment Variables (`.env`):**
- `BOT_TOKEN`: Telegram Bot Token from BotFather
- `API_ID` / `API_HASH`: Telegram API credentials
- `OWNER_ID`: Your Telegram User ID
- `LOG_CHANNEL_ID`: Channel ID where items are initially uploaded (Dump channel)
- `DATABASE_URL`: MongoDB connection string
- `PORT`: Automatically set by Heroku for FastAPI

## 📝 Tips for Future Changes
- **Database changes**: If you add new data to track, add it to `mongo_db.py`.
- **Upload changes**: If a video fails to download or upload, check `upload_worker` inside `uploader.py`.
- **Naming changes**: Filename and caption formatting are handled in `uploader.py`. Ensure that you maintain the `show_index` parameter logic for daily schedules.
