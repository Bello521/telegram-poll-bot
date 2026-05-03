
import json
import asyncio
import threading
import time
import schedule
import os

from telegram import Bot
from telegram.ext import Application, CommandHandler, PollAnswerHandler

TOKEN = os.getenv("TOKEN")

ADMIN_IDS = [1214956315]
GROUP_ID = -1003976644783

# ================== DATA ==================

DATA_FILE = "data.json"

def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {"users": {}, "polls": {}}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)


# ================== MATCH SCHEDULE ==================

MATCH_SCHEDULE = [
    {
        "match_no": "15",
        "team1": "CSK",
        "team2": "MI",
        "type": "normal",
        "time": "23:59"   # change for testing
    }
]


# ================== AUTO FUNCTIONS ==================

async def create_poll_auto(bot, match):
    data = load_data()
    match_no = match["match_no"]

    if match_no in data["polls"]:
        return

    team1 = match["team1"]
    team2 = match["team2"]
    match_type = match["type"]

    if match_type == "normal":
        high, low = 100, 50
    elif match_type == "double":
        high, low = 300, 150
    elif match_type == "playoff":
        high, low = 1000, 500

    options = [
        f"{team1} {high}",
        f"{team2} {high}",
        f"{team1} {low}",
        f"{team2} {low}"
    ]

    message = await bot.send_poll(
        chat_id=GROUP_ID,
        question=f"Match {match_no}: {team1} vs {team2}",
        options=options,
        is_anonymous=False
    )

    await bot.pin_chat_message(GROUP_ID, message.message_id)

    data["polls"][match_no] = {
        "match": f"{team1} vs {team2}",
        "type": match_type,
        "poll_id": message.poll.id,
        "message_id": message.message_id,
        "options": options,
        "votes": {},
        "updated": False,
        "closed": False
    }

    save_data(data)
    print(f"Created poll {match_no}")


async def close_poll_auto(bot, match):
    data = load_data()
    match_no = match["match_no"]

    if match_no not in data["polls"]:
        return

    poll = data["polls"][match_no]

    if poll.get("closed"):
        return

    now = time.strftime("%H:%M")

    if now >= match["time"]:
        try:
            await bot.stop_poll(GROUP_ID, poll["message_id"])

            poll["closed"] = True
            save_data(data)

            print(f"Closed match {match_no}")

        except Exception as e:
            print("Close error:", e)


# ================== SCHEDULER ==================

def scheduler_thread(bot):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def create_all():
        for match in MATCH_SCHEDULE:
            await create_poll_auto(bot, match)

    # TEST MODE (change later to 06:00)
    schedule.every(1).minutes.do(lambda: loop.run_until_complete(create_all()))

    while True:
        for match in MATCH_SCHEDULE:
            loop.run_until_complete(close_poll_auto(bot, match))

        schedule.run_pending()
        time.sleep(30)


# ================== COMMANDS ==================

async def handle_vote(update, context):
    answer = update.poll_answer
    poll_id = answer.poll_id
    user = answer.user

    data = load_data()

    for match_no, poll in data["polls"].items():
        if "poll_id" not in poll:
            continue

        if poll["poll_id"] == poll_id:

            data["users"][str(user.id)] = {
                "name": user.first_name,
                "points": data["users"].get(str(user.id), {}).get("points", 0)
            }

            if not answer.option_ids:
                if str(user.id) in poll["votes"]:
                    del poll["votes"][str(user.id)]
            else:
                poll["votes"][str(user.id)] = answer.option_ids[0]

            break

    save_data(data)


async def update_result(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return

    try:
        match_no = context.args[0]
        winner = context.args[1].strip().upper()
    except:
        await update.message.reply_text("Usage: /update 15 CSK")
        return

    data = load_data()

    if match_no not in data["polls"]:
        await update.message.reply_text("Invalid match number")
        return

    poll = data["polls"][match_no]

    if poll.get("updated"):
        await update.message.reply_text("Already updated!")
        return

    options = poll["options"]
    votes = poll["votes"]

    high = int(options[0].split()[1])
    low = int(options[2].split()[1])

    for uid, user in data["users"].items():
        vote = votes.get(uid)

        if vote is None:
            user["points"] -= int(low * 0.5)
            continue

        team = options[vote].split()[0].strip().upper()
        pts = int(options[vote].split()[1])

        if team == winner:
            user["points"] += pts
        else:
            user["points"] -= int(high * 0.5)

    poll["updated"] = True
    save_data(data)

    await update.message.reply_text(
        f"🏏 Match {match_no} Result\n\n"
        f"{poll['match']}\n"
        f"🏆 Winner: {winner}\n\n"
        f"Points updated!"
    )


async def leaderboard(update, context):
    data = load_data()
    users = sorted(data["users"].values(), key=lambda x: x["points"], reverse=True)

    text = "🏆 Leaderboard\n\n"
    for i, u in enumerate(users, 1):
        text += f"{i}. {u['name']} — {u['points']}\n"

    await update.message.reply_text(text)


# ================== WEB SERVER (FOR RENDER) ==================

from http.server import BaseHTTPRequestHandler, HTTPServer
import os

def run_web():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")

    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Web server running on port {port}")
    server.serve_forever()

# ================== RUN ==================

import os
TOKEN = os.getenv("TOKEN")

app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("update", update_result))
app.add_handler(CommandHandler("leaderboard", leaderboard))
app.add_handler(PollAnswerHandler(handle_vote))

bot = Bot(TOKEN)

# 🔥 Start scheduler
threading.Thread(target=scheduler_thread, args=(bot,), daemon=True).start()

# 🔥 Start web server (ADD THIS LINE)
threading.Thread(target=run_web, daemon=True).start()

print("Bot running clean version...")

app.run_polling()