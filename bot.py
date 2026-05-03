
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

# ================== MATCH SCHEDULE (MOCK TEST - FIXED) ==================

from datetime import datetime, timedelta

def generate_mock_schedule():
    teams = ["SRH", "PBKS", "RCB", "RR", "DC", "GT", "CSK", "LSG", "KKR", "MI"]

    # 🔥 Force immediate start (important fix)
    now = datetime.now() - timedelta(minutes=1)

    schedule = []
    match_no = 16

    for i in range(5):
        team1 = teams[i * 2]
        team2 = teams[i * 2 + 1]

        create_time = now + timedelta(minutes=i * 4)
        close_time = create_time + timedelta(minutes=2)

        schedule.append({
            "match_no": str(match_no + i),
            "team1": team1,
            "team2": team2,
            "type": "normal",
            "create_time": create_time.strftime("%H:%M"),
            "close_time": close_time.strftime("%H:%M")
        })

    return schedule


MATCH_SCHEDULE = generate_mock_schedule()


# ================== AUTO FUNCTIONS ==================

print(f"🟢 Create check for {match['match_no']}")
async def create_poll_auto(bot, match):
    data = load_data()
    match_no = match["match_no"]

    if match_no in data["polls"]:
        return

    now = datetime.now().strftime("%H:%M")

    print(f"[CREATE CHECK] {match_no} now={now} create={match['create_time']}")

    if now < match["create_time"]:
        return

    team1 = match["team1"]
    team2 = match["team2"]

    high, low = 100, 50

    options = [
        f"{team1} {high}",
        f"{team2} {high}",
        f"{team1} {low}",
        f"{team2} {low}"
    ]

    try:
        message = await bot.send_poll(
            chat_id=GROUP_ID,
            question=f"Match {match_no}: {team1} vs {team2}",
            options=options,
            is_anonymous=False
        )

        await bot.pin_chat_message(GROUP_ID, message.message_id)

        data["polls"][match_no] = {
            "match": f"{team1} vs {team2}",
            "poll_id": message.poll.id,
            "message_id": message.message_id,
            "options": options,
            "votes": {},
            "updated": False,
            "closed": False
        }

        save_data(data)
        print(f"✅ Created match {match_no}")

    except Exception as e:
        print("Create error:", e)



async def close_poll_auto(bot, match):
    data = load_data()
    match_no = match["match_no"]

    if match_no not in data["polls"]:
        return

    poll = data["polls"][match_no]

    if poll.get("closed"):
        return

    now = datetime.now().strftime("%H:%M")

    print(f"[CLOSE CHECK] {match_no} now={now} close={match['close_time']}")

    if now < match["close_time"]:
        return

    try:
        await bot.stop_poll(GROUP_ID, poll["message_id"])

        poll["closed"] = True
        save_data(data)

        print(f"⛔ Closed match {match_no}")

    except Exception as e:
        print("Close error:", e)


# ================== SCHEDULER ==================

def scheduler_thread(bot):
    print("🚀 Scheduler thread STARTED")  # MUST PRINT

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run_all():
        for match in MATCH_SCHEDULE:
            print(f"🔍 Checking match {match['match_no']}")  # MUST PRINT
            await create_poll_auto(bot, match)
            await close_poll_auto(bot, match)

    while True:
        try:
            print("🔁 Scheduler running...")
            loop.run_until_complete(run_all())
        except Exception as e:
            print("❌ Scheduler error:", e)

        time.sleep(10)

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