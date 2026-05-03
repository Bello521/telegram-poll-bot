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
        json.dump(data, f)


# ================== MOCK MATCH SCHEDULE ==================

def generate_mock_schedule():
    teams = ["SRH", "PBKS", "RCB", "RR"]

    now = datetime.now() - timedelta(minutes=1)

    schedule = []
    match_no = 16

    for i in range(2):
        team1 = teams[i * 2]
        team2 = teams[i * 2 + 1]

        create_time = now + timedelta(minutes=i * 4)
        close_time = create_time + timedelta(minutes=2)

        schedule.append({
            "match_no": str(match_no + i),
            "team1": team1,
            "team2": team2,
            "create_time": create_time.strftime("%H:%M"),
            "close_time": close_time.strftime("%H:%M")
        })

    return schedule


MATCH_SCHEDULE = generate_mock_schedule()


# ================== POLL FUNCTIONS ==================

async def create_poll_auto(bot, match):
    data = load_data()
    match_no = match["match_no"]

    if match_no in data["polls"]:
        return

    now = datetime.now().strftime("%H:%M")

    print(f"🟢 CREATE CHECK {match_no} | now={now} target={match['create_time']}")

    if now < match["create_time"]:
        return

    options = [
        f"{match['team1']} 100",
        f"{match['team2']} 100",
        f"{match['team1']} 50",
        f"{match['team2']} 50"
    ]

    try:
        message = await bot.send_poll(
            chat_id=GROUP_ID,
            question=f"Match {match_no}: {match['team1']} vs {match['team2']}",
            options=options,
            is_anonymous=False
        )

        await bot.pin_chat_message(GROUP_ID, message.message_id)

        data["polls"][match_no] = {
            "poll_id": message.poll.id,
            "message_id": message.message_id,
            "options": options,
            "votes": {},
            "closed": False,
            "updated": False
        }

        save_data(data)
        print(f"✅ CREATED MATCH {match_no}")

    except Exception as e:
        print("❌ CREATE ERROR:", e)


async def close_poll_auto(bot, match):
    data = load_data()
    match_no = match["match_no"]

    if match_no not in data["polls"]:
        return

    poll = data["polls"][match_no]

    if poll.get("closed"):
        return

    now = datetime.now().strftime("%H:%M")

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

            # ensure user exists
            if str(user.id) not in data["users"]:
                data["users"][str(user.id)] = {
                    "name": user.first_name,
                    "points": 0
                }

            # vote removed
            if not answer.option_ids:
                if str(user.id) in poll["votes"]:
                    del poll["votes"][str(user.id)]
            else:
                poll["votes"][str(user.id)] = answer.option_ids[0]

            break

    save_data(data)


# ================== UPDATE RESULT ==================

async def update_result(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return

    try:
        match_no = context.args[0]
        winner = context.args[1].upper()
    except:
        await update.message.reply_text("Usage: /update 16 SRH")
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

    high = int(options[0].split()[1])
    low = int(options[2].split()[1])

    for uid, user in data["users"].items():
        vote = votes.get(uid)

        if vote is None:
            user["points"] -= int(low * 0.5)
            continue

        team = options[vote].split()[0].upper()
        pts = int(options[vote].split()[1])

        if team == winner:
            user["points"] += pts
        else:
            user["points"] -= int(high * 0.5)

    poll["updated"] = True
    save_data(data)

    await update.message.reply_text(f"✅ Match {match_no} updated: {winner}")


# ================== LEADERBOARD ==================

async def leaderboard(update, context):
    data = load_data()

    if not data["users"]:
        await update.message.reply_text("No players yet.")
        return

    users = sorted(data["users"].values(), key=lambda x: x["points"], reverse=True)

    text = "🏆 Leaderboard\n\n"

    for i, user in enumerate(users, 1):
        text += f"{i}. {user['name']} — {user['points']} pts\n"

    await update.message.reply_text(text)


# ================== SCHEDULER ==================

def scheduler_thread(bot):
    print("🚀 Scheduler started")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run_all():
        for match in MATCH_SCHEDULE:
            await create_poll_auto(bot, match)
            await close_poll_auto(bot, match)

    while True:
        try:
            print("🔁 Scheduler loop")
            loop.run_until_complete(run_all())
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
    print("🚀 BOT STARTING")

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