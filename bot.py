print("🔥 BOT FILE STARTED")
import json
import asyncio
import threading
import time
import os
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Bot
from telegram.ext import Application, CommandHandler, PollAnswerHandler

print("🔥 BOT FILE STARTED")

# ================== CONFIG ==================

TOKEN = os.getenv("TOKEN")

GROUP_ID = -1003976644783   # 🔴 PUT YOUR GROUP ID
ADMIN_IDS = [1214956315]     # 🔴 PUT YOUR TELEGRAM ID

DATA_FILE = "data.json"


# ================== DATA ==================

def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {"users": {}, "polls": {}}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


# ================== MATCH SCHEDULE ==================

def generate_mock_schedule():
    now = datetime.now()

    return [
        {
            "match_no": "1",
            "team1": "SRH",
            "team2": "RCB",
            "type": "normal",
            "create_time": now - timedelta(seconds=5),   # immediate create
            "close_time": now + timedelta(minutes=2),
        },
        {
            "match_no": "2",
            "team1": "CSK",
            "team2": "MI",
            "type": "double",
            "create_time": now + timedelta(minutes=3),
            "close_time": now + timedelta(minutes=5),
        },
        {
            "match_no": "3",
            "team1": "KKR",
            "team2": "RR",
            "type": "playoffs",
            "create_time": now + timedelta(minutes=6),
            "close_time": now + timedelta(minutes=8),
        }
    ]


MATCH_SCHEDULE = generate_mock_schedule()


# ================== CREATE POLL ==================

async def create_poll_auto(bot, match):
    data = load_data()
    match_no = match["match_no"]

    # prevent duplicate poll
    if match_no in data["polls"]:
        return

    now = datetime.now()

    # 🔍 DEBUG
    print(f"[CREATE CHECK] Match {match_no} | now={now} | create={match['create_time']}")

    if now < match["create_time"]:
        return

    # 🔥 dynamic scoring by type
    if match["type"] == "normal":
        high, low = 100, 50
    elif match["type"] == "double":
        high, low = 300, 150
    elif match["type"] == "playoffs":
        high, low = 1000, 500

    options = [
        f"{match['team1']} {high}",
        f"{match['team2']} {high}",
        f"{match['team1']} {low}",
        f"{match['team2']} {low}"
    ]

    try:
        message = await bot.send_poll(
            chat_id=GROUP_ID,
            question=f"Match {match_no} ({match['type'].upper()}): {match['team1']} vs {match['team2']}",
            options=options,
            is_anonymous=False
        )

        await bot.pin_chat_message(GROUP_ID, message.message_id)

        # 🔥 store poll
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
        print(f"✅ CREATED MATCH {match_no} ({match['type']})")

    except Exception as e:
        print("❌ CREATE ERROR:", e)


# ================== CLOSE POLL ==================

async def close_poll_auto(bot, match):
    data = load_data()
    match_no = match["match_no"]

    if match_no not in data["polls"]:
        return

    poll = data["polls"][match_no]

    if poll.get("closed"):
        return

    now = datetime.now()

    # 🔍 DEBUG
    print(f"[CLOSE CHECK] Match {match_no} | now={now} | close={match['close_time']}")

    if now < match["close_time"]:
        return

    try:
        await bot.stop_poll(GROUP_ID, poll["message_id"])
        poll["closed"] = True
        save_data(data)

        print(f"⛔ CLOSED MATCH {match_no}")

    except Exception as e:
        print("❌ CLOSE ERROR:", e)

# ================== VOTE HANDLER ==================

async def handle_vote(update, context):
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

            if not answer.option_ids:
                poll["votes"].pop(str(user.id), None)
            else:
                poll["votes"][str(user.id)] = answer.option_ids[0]

            break

    print(f"Vote updated: {user.first_name} -> {answer.option_ids}")

    save_data(data)


# ================== UPDATE RESULT ==================

async def update_result(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return

    try:
        match_no = str(context.args[0])
        winner = context.args[1].strip().upper()
    except:
        await update.message.reply_text("Usage: /update 1 SRH")
        return

    data = load_data()

    if match_no not in data["polls"]:
        await update.message.reply_text("Invalid match")
        return

    poll = data["polls"][match_no]

    if poll.get("updated"):
        await update.message.reply_text("Already updated")
        return

    options = poll["options"]
    votes = poll["votes"]

    print("\n====== DEBUG START ======")
    print(f"Winner Entered: {winner}")
    print(f"Available Options: {options}")
    print(f"Votes Mapping: {votes}")
    print("------------------------")

    for uid, user in data["users"].items():
        vote = votes.get(uid)
        name = user["name"]

        if vote is None:
            print(f"{name} → NO VOTE → -25")
            continue

        option_text = options[vote]
        team, pts = option_text.split()
        team = team.upper()
        pts = int(pts)

        print(f"{name} chose: {option_text}")

        if team == winner:
            print(f"→ CORRECT (+{pts})")
        else:
            print(f"→ WRONG (-{pts//2})")

    print("====== DEBUG END ======\n")

    poll["updated"] = True
    save_data(data)

    # ✅ UNPIN AFTER UPDATE
    try:
        await context.bot.unpin_chat_message(GROUP_ID, poll["message_id"])
    except:
        pass

    # ✅ AUTO LEADERBOARD
    await send_leaderboard(context)

    await update.message.reply_text(f"✅ Match {match_no} updated: {winner}")


# ================== SEND LEADERBOARD ==================

async def send_leaderboard(context):
    data = load_data()

    if not data["users"]:
        await context.bot.send_message(chat_id=GROUP_ID, text="No players yet.")
        return

    users = sorted(data["users"].values(), key=lambda x: x["points"], reverse=True)

    text = "🏆 Leaderboard\n\n"

    for i, user in enumerate(users, 1):
        text += f"{i}. {user['name']} — {user['points']} pts\n"

    await context.bot.send_message(chat_id=GROUP_ID, text=text)


# ================== LEADERBOARD COMMAND ==================

async def leaderboard(update, context):
    await send_leaderboard(context)

# ================== SCHEDULER ==================

def scheduler_thread(bot):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run_all():
        for match in MATCH_SCHEDULE:
            await create_poll_auto(bot, match)
            await close_poll_auto(bot, match)

    while True:
        try:
            loop.run_until_complete(run_all())
            print("🔁 Scheduler running...")
        except Exception as e:
            print("❌ Scheduler error:", e)

        time.sleep(10)


# ================== WEB SERVER ==================

def run_web():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")

    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"🌐 Web running on port {port}")
    server.serve_forever()


# ================== MAIN ==================

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("update", update_result))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(PollAnswerHandler(handle_vote))

    bot = Bot(TOKEN)

    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=scheduler_thread, args=(bot,), daemon=True).start()

    print("✅ BOT RUNNING")

    app.run_polling()


if __name__ == "__main__":
    main()