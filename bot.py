import json
import asyncio
import threading
import time
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Bot
from telegram.ext import Application, CommandHandler, PollAnswerHandler

print("🔥 BOT STARTED")

TOKEN = os.getenv("TOKEN")

GROUP_ID = -1003976644783
ADMIN_IDS = [1214956315]

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
        f.flush()


# ================== LOAD SCHEDULE ==================

def load_schedule():

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
            "match_no": match["match_no"],
            "team1": match["team1"],
            "team2": match["team2"],
            "type": match["type"],
            "create_time": create_time,
            "close_time": close_time
        })

    print("✅ SCHEDULE LOADED")

    return schedule

# 🔥 IMPORTANT
MATCH_SCHEDULE = load_schedule()


# ================== CREATE POLL ==================

async def create_poll_auto(bot, match):
    data = load_data()

    match_no = match["match_no"]

    if match_no in data["polls"]:
        return

    if datetime.now(ZoneInfo("Asia/Kolkata")) < match["create_time"]:
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

    try:
        print(f"🟢 Creating Match {match_no}")

        message = await bot.send_poll(
            chat_id=GROUP_ID,
            question=f"Match {match_no} ({match['type'].upper()}): {match['team1']} vs {match['team2']}",
            options=options,
            is_anonymous=False
        )

        # 🔥 SAVE BEFORE PIN
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

        print(f"✅ POLL SAVED {match_no}")

        await bot.pin_chat_message(
            GROUP_ID,
            message.message_id
        )

        print(f"📌 PINNED {match_no}")

    except Exception as e:
        print("❌ CREATE ERROR:", e)


# ================== CLOSE POLL ==================

async def close_poll_auto(bot, match):
    data = load_data()

    match_no = match["match_no"]

    if match_no not in data["polls"]:
        return

    poll = data["polls"][match_no]

    if poll["closed"]:
        return

    if datetime.now(ZoneInfo("Asia/Kolkata")) < match["close_time"]:
        return

    try:
        await bot.stop_poll(
            GROUP_ID,
            poll["message_id"]
        )

        poll["closed"] = True

        save_data(data)

        print(f"⛔ CLOSED MATCH {match_no}")

    except Exception as e:
        print("❌ CLOSE ERROR:", e)


# ================== HANDLE VOTES ==================

async def handle_vote(update, context):
    answer = update.poll_answer

    poll_id = answer.poll_id
    user = answer.user

    print(f"🗳 Vote from {user.first_name}")

    # 🔥 retry logic
    for _ in range(5):

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

                print(f"✅ Vote saved for {user.first_name}")

                return

        await asyncio.sleep(1)

    print("❌ Vote failed")


# ================== UPDATE RESULT ==================

async def update_result(update, context):

    if update.effective_user.id not in ADMIN_IDS:
        return

    try:
        match_no = str(context.args[0])
        winner = context.args[1].upper()

    except:
        await update.message.reply_text(
            "Usage: /update 49 SRH"
        )
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

    print("\n====== UPDATE DEBUG ======")

    for uid in data["users"]:

        user = data["users"][uid]

        vote = votes.get(uid)

        name = user["name"]

        if vote is None:

            user["points"] -= 25

            print(f"{name} → NO VOTE → -25")

            continue

        option_text = options[vote]

        team, pts = option_text.split()

        pts = int(pts)

        print(f"{name} voted {option_text}")

        if team == winner:

            user["points"] += pts

            print(f"→ CORRECT +{pts}")

        else:

            penalty = pts // 2

            user["points"] -= penalty

            print(f"→ WRONG -{penalty}")

    poll["updated"] = True

    save_data(data)

    print("====== UPDATE END ======\n")

    # 🔥 unpin
    try:
        await context.bot.unpin_chat_message(
            GROUP_ID,
            poll["message_id"]
        )

    except:
        pass

    # 🔥 leaderboard
    await send_leaderboard(context)

    await update.message.reply_text(
        f"✅ Match {match_no} updated"
    )


# ================== LEADERBOARD ==================

async def send_leaderboard(context):

    data = load_data()

    users = sorted(
        data["users"].items(),
        key=lambda x: x[1]["points"],
        reverse=True
    )

    text = "🏆 <b>Leaderboard</b>\n\n"

    for i, (uid, user) in enumerate(users, 1):

        tag = f'<a href="tg://user?id={uid}">{user["name"]}</a>'

        if i == 1:
            prefix = "🥇"

        elif i == 2:
            prefix = "🥈"

        elif i == 3:
            prefix = "🥉"

        else:
            prefix = f"{i}."

        pts = user["points"]

        if pts > 0:
            pts_text = f"+{pts}"

        else:
            pts_text = str(pts)

        text += f"{prefix} {tag} — <b>{pts_text}</b> pts\n"

    await context.bot.send_message(
        GROUP_ID,
        text,
        parse_mode="HTML"
    )


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

        print("\n🔁 Scheduler running")

        current_time = datetime.now(
            ZoneInfo("Asia/Kolkata")
        )

        print("⏰ CURRENT IST:", current_time)

        for match in MATCH_SCHEDULE:

            print(
               f"Match {match['match_no']} create at {match['create_time']}"
            )

        loop.run_until_complete(run_all())
        print("🔁 Scheduler alive")

        time.sleep(10)


# ================== WEB SERVER ==================

def run_web():

    class Handler(BaseHTTPRequestHandler):

        def do_GET(self):

            self.send_response(200)

            self.end_headers()

            self.wfile.write(b"Bot running")

    port = int(os.environ.get("PORT", 10000))

    print(f"🌐 WEB RUNNING ON {port}")

    HTTPServer(
        ("0.0.0.0", port),
        Handler
    ).serve_forever()


# ================== MAIN ==================

def main():

    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("update", update_result)
    )

    app.add_handler(
        CommandHandler("leaderboard", leaderboard)
    )

    app.add_handler(
        PollAnswerHandler(handle_vote)
    )

    bot = Bot(TOKEN)

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()
    print("🌐 WEB THREAD STARTED")

    time.sleep(2)

    threading.Thread(
        target=scheduler_thread,
        args=(bot,),
        daemon=True
    ).start()

    print("✅ BOT RUNNING")

    app.run_polling()


if __name__ == "__main__":
    main()