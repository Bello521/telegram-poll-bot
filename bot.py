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
        # print("🔁 Scheduler running") 
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
        uid_str = str(user.id)

        # 1. Find target match
        data = load_data()
        target_match = None
        for match_no, poll in data["polls"].items():
            if poll["poll_id"] == poll_id:
                target_match = match_no
                break

        if not target_match:
            return 

        # 2. Add user if they are new
        if uid_str not in data["users"]:
            data["users"][uid_str] = {
                "name": user.first_name,
                "points": 0
            }
            save_data(data) 

        # 3. ATOMIC MONGODB UPDATE (Race Condition Killer)
        if answer.option_ids:
            vote_val = answer.option_ids[0]
            collection.update_one(
                {"_id": "main"},
                {"$set": {f"payload.polls.{target_match}.votes.{uid_str}": vote_val}}
            )
            print(f"🗳 Vote recorded for {user.first_name} (Atomic)")
        else:
            collection.update_one(
                {"_id": "main"},
                {"$unset": {f"payload.polls.{target_match}.votes.{uid_str}": ""}}
            )
            print(f"🗳 Vote retracted for {user.first_name} (Atomic)")

    except Exception:
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
            await update.message.reply_text("Usage: /update 53 SRH")
            return

        data = load_data()

        if match_no not in data["polls"]:
            await update.message.reply_text("Invalid match")
            return

        poll = data["polls"][match_no]

        if poll["updated"]:
            await update.message.reply_text("Already updated.")
            return

        options = poll["options"]
        votes = poll["votes"]

        print(f"\n🔍 DEBUG MATCH {match_no}:")
        print(f"Total votes in database for this match: {len(votes)}")
        print(f"Raw Votes Dictionary: {votes}\n")

        low_points = int(options[2].split()[1])
        no_vote_penalty = low_points // 2 

        for uid in list(data["users"].keys()):
            user = data["users"][uid]
            
            # THE BULLETPROOF ID CHECK
            vote = votes.get(uid)
            if vote is None:
                vote = votes.get(str(uid))
            if vote is None:
                try:
                    vote = votes.get(int(uid))
                except:
                    pass

            # NO VOTE LOGIC
            if vote is None:
                print(f"❌ NO VOTE FOUND FOR: {user.get('name', 'Unknown')} (ID: '{uid}')")
                user["points"] -= no_vote_penalty
                continue

            # VOTE CALCULATIONS
            option_text = options[vote]
            team, pts_str = option_text.split()
            pts = int(pts_str)

            if team == winner:
                user["points"] += pts
            else:
                user["points"] -= (pts // 2)

        poll["updated"] = True
        save_data(data)

        try:
            await context.bot.unpin_chat_message(GROUP_ID, poll["message_id"])
        except:
            pass

        await send_leaderboard(context)
        await update.message.reply_text(f"✅ Match {match_no} updated!")

    except Exception:
        traceback.print_exc()


# =============== UNDO_UPDATE ===================

async def undo_update(update, context):
    try:
        if update.effective_user.id not in ADMIN_IDS:
            return

        try:
            match_no = str(context.args[0])
            prev_winner = context.args[1].upper()
        except:
            await update.message.reply_text("Usage: /undo 53 SRH")
            return

        data = load_data()

        if match_no not in data["polls"]:
            await update.message.reply_text("Invalid match")
            return

        poll = data["polls"][match_no]

        if not poll["updated"]:
            await update.message.reply_text("This match hasn't been updated yet.")
            return

        options = poll["options"]
        votes = poll["votes"]

        # Dynamic Math
        low_points = int(options[2].split()[1])
        no_vote_penalty = low_points // 2 

        for uid in list(data["users"].keys()):
            user = data["users"][uid]
            
            # --- THE BULLETPROOF ID CHECK ---
            vote = votes.get(uid)
            if vote is None:
                vote = votes.get(str(uid))
            if vote is None:
                try:
                    vote = votes.get(int(uid))
                except:
                    pass

            # 1. REVERSE NO VOTE PENALTY
            if vote is None:
                user["points"] += no_vote_penalty  
                continue

            # 2. REVERSE VOTE CALCULATIONS
            option_text = options[vote]
            team, pts_str = option_text.split()
            pts = int(pts_str)

            if team == prev_winner:
                user["points"] -= pts        
            else:
                user["points"] += (pts // 2) 

        poll["updated"] = False
        save_data(data)

        await send_leaderboard(context)
        await update.message.reply_text(f"♻️ Match {match_no} undone!\nReversed no-vote penalty: +{no_vote_penalty}")

    except Exception:
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

#======================= CHECK VOTE ============

async def check_vote(update, context):
    try:
        try:
            match_no = str(context.args[0])
        except:
            await update.message.reply_text("Usage: /checkvote 53")
            return

        data = load_data()
        uid = str(update.effective_user.id)
        
        if match_no not in data["polls"]:
            await update.message.reply_text("Invalid match number.")
            return
            
        poll = data["polls"][match_no]
        votes = poll.get("votes", {})
        
        # Check all ID formats
        vote = votes.get(uid) or votes.get(int(uid))
        
        if vote is None:
            await update.message.reply_text(f"❌ Match {match_no}: The database says you DID NOT VOTE.")
        else:
            option_text = poll["options"][vote]
            await update.message.reply_text(f"✅ Match {match_no}: Your saved vote is -> {option_text}")
            
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# ================== MISSING VOTES ==================

async def missing_votes(update, context):
    try:
        if update.effective_user.id not in ADMIN_IDS:
            return

        try:
            match_no = str(context.args[0])
        except:
            await update.message.reply_text("Usage: /missingvotes 53")
            return

        data = load_data()

        if match_no not in data["polls"]:
            await update.message.reply_text(f"Match {match_no} not found in database.")
            return

        poll = data["polls"][match_no]
        votes = poll.get("votes", {})
        
        missing_players = []

        for uid, user_info in data["users"].items():
            vote = votes.get(uid)
            if vote is None:
                vote = votes.get(str(uid))
            if vote is None:
                try:
                    vote = votes.get(int(uid))
                except:
                    pass

            if vote is None:
                name = user_info.get("name", "Unknown")
                missing_players.append(f"• {name} (ID: {uid})")

        if not missing_players:
            await update.message.reply_text(f"✅ Match {match_no}: Every player on the leaderboard has a vote recorded!")
        else:
            report = f"❌ Match {match_no} Missing Votes ({len(missing_players)} players):\n\n"
            report += "\n".join(missing_players)
            await update.message.reply_text(report)

    except Exception as e:
        await update.message.reply_text(f"Error checking missing votes: {e}")

# ================== ERROR HANDLER ==================

async def error_handler(update, context):
    print("\n❌ TELEGRAM ERROR ❌\n")
    traceback.print_exception(
        type(context.error),
        context.error,
        context.error.__traceback__
    )
    print("\n❌ END ERROR ❌\n")
    
    # Send error right to Telegram if an Admin ID is set!
    if ADMIN_IDS:
        import html
        error_msg = f"⚠️ <b>BOT ERROR</b> ⚠️\n<pre>{html.escape(str(context.error))}</pre>"
        try:
            await context.bot.send_message(chat_id=ADMIN_IDS[0], text=error_msg, parse_mode="HTML")
        except:
            pass

# ================== RETRO FIX (BULK BATCH PROCESSING) ==================

async def retro_fix(update, context):
    try:
        # 1. Security Check
        if update.effective_user.id not in ADMIN_IDS:
            return

        # 2. Parse arguments (Now accepts unlimited User IDs!)
        if len(context.args) < 4:
            await update.message.reply_text(
                "Usage: /retrofix [Match_No] [Voted_Team] [Actual_Winner] [User1] [User2] ...\n"
                "Example: /retrofix 53 KKR KKR 12345678 87654321 11223344"
            )
            return

        match_no = str(context.args[0])
        voted_team = context.args[1].upper()
        winner = context.args[2].upper()
        user_ids = context.args[3:] # Grabs every ID you pasted after the teams!

        data = load_data()

        if match_no not in data["polls"]:
            await update.message.reply_text("Invalid match.")
            return

        poll = data["polls"][match_no]
        options = poll["options"]

        # 3. Find the matching option
        target_option_index = None
        for i, opt in enumerate(options):
            if opt.startswith(voted_team):
                target_option_index = i
                break 

        if target_option_index is None:
            await update.message.reply_text(f"Team {voted_team} not found in poll options.")
            return

        # 4. Calculate Points
        low_points = int(options[2].split()[1])
        no_vote_penalty = low_points // 2 
        
        option_text = options[target_option_index]
        team, pts_str = option_text.split()
        pts = int(pts_str)

        results = []
        import pymongo 
        
        # 5. Loop through every ID you provided
        for uid in user_ids:
            if uid not in data["users"]:
                results.append(f"❌ ID {uid} not found in database.")
                continue
                
            user = data["users"][uid]
            
            # Reverse the unfair penalty
            user["points"] += no_vote_penalty 

            # Apply the correct points dynamically
            if team == winner:
                user["points"] += pts
                pts_diff = f"+{pts} (Won)"
            else:
                user["points"] -= (pts // 2)
                pts_diff = f"-{pts // 2} (Lost)"

            # Inject the vote safely into MongoDB
            collection.update_one(
                {"_id": "main"},
                {"$set": {
                    f"payload.polls.{match_no}.votes.{uid}": target_option_index,
                    f"payload.users.{uid}.points": user["points"]
                }}
            )
            
            results.append(f"✅ <b>{user['name']}</b>: Penalty Fixed (+{no_vote_penalty}) | Vote {pts_diff}")

        # 6. Send Leaderboard and Report
        await send_leaderboard(context)
        
        report = f"🔧 <b>Bulk Retrofix Complete (Match {match_no})</b>\n\n" + "\n".join(results)
        await update.message.reply_text(report, parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(f"Retrofix Error: {e}")


# ================== MAIN ==================

def main():
    print("🔥 BOT STARTING")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("update", update_result))
    app.add_handler(CommandHandler("undo", undo_update))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("backup", backup))
    app.add_handler(CommandHandler("checkvote", check_vote))
    app.add_handler(CommandHandler("missingvotes", missing_votes)) # NEW COMMAND
    app.add_handler(CommandHandler("retrofix", retro_fix))
    app.add_handler(PollAnswerHandler(handle_vote))
    app.add_error_handler(error_handler) # THIS WILL NOW MESSAGE YOU IF IT CRASHES

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