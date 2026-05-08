import os
import io
import json
import traceback
import asyncio

from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from pymongo import MongoClient
from telegram import InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    PollAnswerHandler
)

# Load environment variables (from .env locally, or Render Dashboard)
load_dotenv()

# ================== CONFIG ==================

TOKEN = os.getenv("TOKEN")

GROUP_ID = int(
    os.getenv("GROUP_ID", "0")
)

ADMIN_IDS = list(
    map(
        int,
        os.getenv("ADMIN_IDS", "").split(",")
    )
)

# ================== DATABASE ==================

MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    print("❌ CRITICAL: MONGO_URI is missing!")
    exit(1)

print("⏳ Connecting to MongoDB...")
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000, tls=True)
db = client["ipl_prediction_db"]
collection = db["bot_data"]
print("✅ MongoDB Connected")

def load_data():
    try:
        doc = collection.find_one({"_id": "main"})
        if doc and "payload" in doc:
            return doc["payload"]
        return {"users": {}, "polls": {}}
    except Exception as e:
        print(f"❌ DB LOAD ERROR: {e}")
        return {"users": {}, "polls": {}}

def save_data(data):
    try:
        collection.update_one(
            {"_id": "main"},
            {"$set": {"payload": data}},
            upsert=True
        )
    except Exception as e:
        print(f"❌ DB SAVE ERROR: {e}")

# ================== LOAD SCHEDULE ==================

def load_schedule():
    print("📂 Loading schedule.json")
    try:
        with open("schedule.json", "r") as f:
            raw = json.load(f)

        schedule = []
        for match in raw:
            create_time = datetime.strptime(
                match["create_time"],
                "%Y-%m-%d %H:%M"
            ).replace(tzinfo=ZoneInfo("Asia/Kolkata"))

            close_time = datetime.strptime(
                match["close_time"],
                "%Y-%m-%d %H:%M"
            ).replace(tzinfo=ZoneInfo("Asia/Kolkata"))

            schedule.append({
                "match_no": str(match["match_no"]),
                "team1": match["team1"],
                "team2": match["team2"],
                "type": match["type"],
                "create_time": create_time,
                "close_time": close_time
            })
        print("✅ SCHEDULE LOADED")
        return schedule
    except FileNotFoundError:
        print("❌ CRITICAL: schedule.json not found!")
        return []

MATCH_SCHEDULE = load_schedule()

# ================== CREATE POLL ==================

async def create_poll_auto(context, match):
    try:
        data = load_data()
        match_no = match["match_no"]

        # 1. Did we already post this? (Saved in DB)
        if match_no in data["polls"]:
            return

        current_time = datetime.now(ZoneInfo("Asia/Kolkata"))

        # 2. Is it too early?
        if current_time < match["create_time"]:
            return

        # 3. SPAM SAFEGUARD: Is it too late? (Missed match)
        if current_time > match["close_time"]:
            return

        if match["type"] == "normal":
            high, low = 100, 50
        elif match["type"] == "double":
            high, low = 300, 150
        else:
            high, low = 1000, 500

        options = [
            f"{match['team1']} {high}",
            f"{match['team2']} {high}",
            f"{match['team1']} {low}",
            f"{match['team2']} {low}"
        ]

        print(f"🟢 Creating Match {match_no}")

        message = await context.bot.send_poll(
            chat_id=GROUP_ID,
            question=f"{match_no}. {match['team1']} vs {match['team2']}",
            options=options,
            is_anonymous=False
        )

        data["polls"][match_no] = {
            "poll_id": message.poll.id,
            "message_id": message.message_id,
            "options": options,
            "votes": {},
            "closed": False,
            "updated": False,
            "type": match["type"]
        }

        save_data(data)
        await context.bot.pin_chat_message(GROUP_ID, message.message_id)
        print(f"✅ MATCH {match_no} CREATED")

    except:
        print("❌ CREATE POLL ERROR")
        traceback.print_exc()

# ================== CLOSE POLL ==================

async def close_poll_auto(context, match):
    try:
        data = load_data()
        match_no = match["match_no"]

        if match_no not in data["polls"]:
            return

        poll = data["polls"][match_no]

        if poll["closed"]:
            return

        current_time = datetime.now(ZoneInfo("Asia/Kolkata"))

        if current_time < match["close_time"]:
            return

        await context.bot.stop_poll(GROUP_ID, poll["message_id"])
        
        poll["closed"] = True
        save_data(data)
        print(f"⛔ MATCH {match_no} CLOSED")

    except:
        print("❌ CLOSE POLL ERROR")
        traceback.print_exc()

# ================== SCHEDULER ==================

async def scheduler(context):
    try:
        # print("🔁 Scheduler running") # You can comment this out so it stops spamming your console!
        for match in MATCH_SCHEDULE:
            await create_poll_auto(context, match)
            await close_poll_auto(context, match)
    except:
        print("❌ SCHEDULER ERROR")
        traceback.print_exc()

# ================== HANDLE VOTES ==================

async def handle_vote(update, context):
    try:
        answer = update.poll_answer
        poll_id = answer.poll_id
        user = answer.user
        data = load_data()

        for match_no, poll in data["polls"].items():
            if poll["poll_id"] == poll_id:
                if str(user.id) not in data["users"]:
                    data["users"][str(user.id)] = {
                        "name": user.first_name,
                        "points": 0
                    }

                if answer.option_ids:
                    poll["votes"][str(user.id)] = answer.option_ids[0]
                else:
                    poll["votes"].pop(str(user.id), None)

                save_data(data)
                print(f"🗳 Vote recorded for {user.first_name}")
                return
    except:
        print("❌ VOTE ERROR")
        traceback.print_exc()

# ================== UPDATE RESULT ==================

async def update_result(update, context):
    try:
        if update.effective_user.id not in ADMIN_IDS:
            return

        try:
            match_no = str(context.args[0])
            winner = context.args[1].upper()
        except:
            await update.message.reply_text("Usage: /update 49 SRH")
            return

        data = load_data()

        if match_no not in data["polls"]:
            await update.message.reply_text("Invalid match")
            return

        poll = data["polls"][match_no]

        if poll["updated"]:
            await update.message.reply_text("Already updated")
            return

        options = poll["options"]
        votes = poll["votes"]

        for uid in data["users"]:
            user = data["users"][uid]
            vote = votes.get(uid)

            if vote is None:
                user["points"] -= 25
                continue

            option_text = options[vote]
            team, pts = option_text.split()
            pts = int(pts)

            if team == winner:
                user["points"] += pts
            else:
                user["points"] -= pts // 2

        poll["updated"] = True
        save_data(data)

        try:
            await context.bot.unpin_chat_message(GROUP_ID, poll["message_id"])
        except:
            pass

        await send_leaderboard(context)
        await update.message.reply_text(f"✅ Match {match_no} updated")

    except:
        print("❌ UPDATE ERROR")
        traceback.print_exc()

# ================== LEADERBOARD ==================

async def send_leaderboard(context):
    try:
        data = load_data()
        users = sorted(
            data["users"].items(),
            key=lambda x: x[1]["points"],
            reverse=True
        )

        updated_matches = [int(m) for m, p in data["polls"].items() if p["updated"]]
        latest_match = str(max(updated_matches)) if updated_matches else "0"

        text = "🏆 <b>IPL Prediction Leaderboard</b>\n\n"

        for i, (uid, user) in enumerate(users, 1):
            tag = f'<a href="tg://user?id={uid}">{user["name"]}</a>'
            prefix = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            text += f"{prefix} {tag} — <b>{user['points']}</b> pts\n"

        text += f"\n📌 Updated after Match {latest_match}"

        await context.bot.send_message(
            GROUP_ID,
            text,
            parse_mode="HTML"
        )
    except:
        print("❌ LEADERBOARD ERROR")
        traceback.print_exc()

async def leaderboard(update, context):
    await send_leaderboard(context)

# ================== PING ==================

async def ping(update, context):
    await update.message.reply_text("🏓 Bot is alive and securely connected to DB!")

# ================== KEEP ALIVE ==================

async def keep_alive(context):
    print("✅ BOT STILL RUNNING")

# ================== BACKUP ==================

async def backup(update, context):
    """Pulls current DB state and sends as a JSON file to the admin"""
    try:
        if update.effective_user.id not in ADMIN_IDS:
            return

        data = load_data()
        
        # Convert dictionary to a JSON byte stream in memory
        json_bytes = json.dumps(data, indent=4).encode('utf-8')
        file_stream = io.BytesIO(json_bytes)
        
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=InputFile(file_stream, filename=f"mongo_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
        )
        await update.message.reply_text("✅ Backup retrieved directly from MongoDB.")
    except:
        print("❌ BACKUP ERROR")
        traceback.print_exc()

# ================== ERROR HANDLER ==================

async def error_handler(update, context):
    print("\n❌ TELEGRAM ERROR ❌\n")
    traceback.print_exception(
        type(context.error),
        context.error,
        context.error.__traceback__
    )
    print("\n❌ END ERROR ❌\n")

# ================== MAIN ==================

def main():
    print("🔥 BOT STARTING")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("update", update_result))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("backup", backup))
    app.add_handler(PollAnswerHandler(handle_vote))
    app.add_error_handler(error_handler)

    app.job_queue.run_repeating(scheduler, interval=10, first=5)
    app.job_queue.run_repeating(keep_alive, interval=300, first=10)

    PORT = int(os.environ.get("PORT", 10000))
    RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")

    if RENDER_EXTERNAL_HOSTNAME:
        WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}/webhook"
        print(f"🚀 STARTING WEBHOOK MODE on {WEBHOOK_URL}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=WEBHOOK_URL,
            drop_pending_updates=True
        )
    else:
        print("🚀 STARTING POLLING MODE (Local Fallback)")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()