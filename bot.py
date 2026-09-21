import os
import sqlite3
import discord

from datetime import datetime, timedelta
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv


# -----------------------------
# ENVIRONMENT VARIABLES
# -----------------------------

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")


# -----------------------------
# SETTINGS
# -----------------------------

REMINDER_CHANNEL_ID = 1544872377907683428


# -----------------------------
# DATABASE
# -----------------------------

db = sqlite3.connect("assignments.db")
cursor = db.cursor()

# Create table if it doesn't already exist
cursor.execute("""
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_name TEXT NOT NULL,
    assignment TEXT NOT NULL,
    due TEXT NOT NULL,
    role_id INTEGER,
    reminder_3d_sent INTEGER DEFAULT 0,
    reminder_1d_sent INTEGER DEFAULT 0,
    reminder_3h_sent INTEGER DEFAULT 0
)
""")


# -----------------------------
# UPDATE OLD DATABASES
# -----------------------------

# Check which columns already exist
cursor.execute("PRAGMA table_info(assignments)")
columns = [column[1] for column in cursor.fetchall()]


# Add role_id if needed
if "role_id" not in columns:
    cursor.execute("""
        ALTER TABLE assignments
        ADD COLUMN role_id INTEGER
    """)


# Add 3-day reminder tracking
if "reminder_3d_sent" not in columns:
    cursor.execute("""
        ALTER TABLE assignments
        ADD COLUMN reminder_3d_sent INTEGER DEFAULT 0
    """)


# Add 1-day reminder tracking
if "reminder_1d_sent" not in columns:
    cursor.execute("""
        ALTER TABLE assignments
        ADD COLUMN reminder_1d_sent INTEGER DEFAULT 0
    """)


# Add 3-hour reminder tracking
if "reminder_3h_sent" not in columns:
    cursor.execute("""
        ALTER TABLE assignments
        ADD COLUMN reminder_3h_sent INTEGER DEFAULT 0
    """)


db.commit()


# -----------------------------
# DISCORD BOT
# -----------------------------

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# -----------------------------
# BOT READY
# -----------------------------

@bot.event
async def on_ready():

    await bot.tree.sync()

    # Start reminder checker
    if not check_assignments.is_running():
        check_assignments.start()

    print(f"Logged in as {bot.user}")
    print("Slash commands synced!")
    print("Assignment reminder system started!")


# -----------------------------
# /assignment
# Add a new assignment
# -----------------------------

@bot.tree.command(
    name="assignment",
    description="Add a new assignment"
)
@app_commands.describe(
    class_role="The Discord role for this class",
    assignment="The assignment name",
    due="Due date: MM/DD/YYYY HH:MM AM/PM"
)
async def assignment(
    interaction: discord.Interaction,
    class_role: discord.Role,
    assignment: str,
    due: str
):

    # Convert due date text into a datetime
    try:
        due_date = datetime.strptime(
            due,
            "%m/%d/%Y %I:%M %p"
        )

    except ValueError:

        await interaction.response.send_message(
            "❌ Invalid date format.\n\n"
            "Use: `MM/DD/YYYY HH:MM AM/PM`\n"
            "Example: `09/25/2026 11:59 PM`",
            ephemeral=True
        )

        return

    # Store date in standardized format
    due_database = due_date.isoformat()

    # Add assignment to database
    cursor.execute(
        """
        INSERT INTO assignments
        (class_name, assignment, due, role_id)
        VALUES (?, ?, ?, ?)
        """,
        (
            class_role.name,
            assignment,
            due_database,
            class_role.id
        )
    )

    db.commit()

    # Pretty date for Discord
    pretty_due = due_date.strftime(
        "%B %d, %Y at %I:%M %p"
    )

    # Confirm assignment was added
    await interaction.response.send_message(
        f"✅ **Assignment added!**\n\n"
        f"📚 {class_role.mention}\n"
        f"📝 {assignment}\n"
        f"⏰ Due: {pretty_due}"
    )


# -----------------------------
# /assignments
# View assignments
# -----------------------------

@bot.tree.command(
    name="assignments",
    description="View all assignments"
)
async def assignments(
    interaction: discord.Interaction
):

    cursor.execute("""
        SELECT
            id,
            class_name,
            assignment,
            due
        FROM assignments
        ORDER BY due
    """)

    rows = cursor.fetchall()

    # No assignments
    if not rows:

        await interaction.response.send_message(
            "🎉 No assignments!"
        )

        return

    message = "📚 **Upcoming Assignments**\n\n"

    for (
        assignment_id,
        class_name,
        assignment,
        due
    ) in rows:

        try:

            due_date = datetime.fromisoformat(due)

            pretty_due = due_date.strftime(
                "%B %d at %I:%M %p"
            )

        except ValueError:

            # For old assignments created before
            # we standardized the dates
            pretty_due = due

        message += (
            f"**#{assignment_id} — {class_name}**\n"
            f"📝 {assignment}\n"
            f"⏰ {pretty_due}\n\n"
        )

    await interaction.response.send_message(
        message
    )

# -----------------------------
# /deleteassignment
# Delete an assignment
# -----------------------------

@bot.tree.command(
    name="deleteassignment",
    description="Delete an assignment"
)
@app_commands.describe(
    assignment_id="The ID of the assignment to delete"
)
async def deleteassignment(
    interaction: discord.Interaction,
    assignment_id: int
):

    # Find assignment first
    cursor.execute(
        """
        SELECT class_name, assignment
        FROM assignments
        WHERE id = ?
        """,
        (assignment_id,)
    )

    row = cursor.fetchone()

    # Assignment doesn't exist
    if row is None:
        await interaction.response.send_message(
            f"❌ Assignment #{assignment_id} doesn't exist.",
            ephemeral=True
        )
        return

    class_name, assignment_name = row

    # Delete it
    cursor.execute(
        """
        DELETE FROM assignments
        WHERE id = ?
        """,
        (assignment_id,)
    )

    db.commit()

    await interaction.response.send_message(
        f"🗑️ **Assignment deleted!**\n\n"
        f"📚 {class_name}\n"
        f"📝 {assignment_name}"
    )


# -----------------------------
# AUTOMATIC REMINDER CHECK
# -----------------------------

@tasks.loop(minutes=1)
async def check_assignments():

    # Get reminder channel
    channel = bot.get_channel(
        REMINDER_CHANNEL_ID
    )

    if channel is None:

        print("Reminder channel not found.")
        return

    # Current time
    now = datetime.now()

    # Get assignments
    cursor.execute("""
        SELECT
            id,
            assignment,
            due,
            role_id,
            reminder_3d_sent,
            reminder_1d_sent,
            reminder_3h_sent
        FROM assignments
    """)

    rows = cursor.fetchall()

    for (
        assignment_id,
        assignment,
        due,
        role_id,
        reminder_3d_sent,
        reminder_1d_sent,
        reminder_3h_sent
    ) in rows:

        # Convert stored date back into datetime
        try:

            due_date = datetime.fromisoformat(due)

        except ValueError:

            continue

        # Assignment already passed
        if now >= due_date:
            continue

        # Find Discord class role
        role = channel.guild.get_role(role_id)

        if role is None:
            continue

        # Calculate time remaining
        time_left = due_date - now

        pretty_due = due_date.strftime(
            "%B %d at %I:%M %p"
        )


        # -------------------------
        # 3 HOUR REMINDER
        # -------------------------

        if (
            time_left <= timedelta(hours=3)
            and not reminder_3h_sent
        ):

            await channel.send(
                f"🚨 **Assignment Due Soon!**\n\n"
                f"{role.mention}\n"
                f"📝 **{assignment}**\n"
                f"⏰ Due in less than 3 hours!\n"
                f"📅 Due: {pretty_due}"
            )

            cursor.execute("""
                UPDATE assignments
                SET reminder_3h_sent = 1
                WHERE id = ?
            """, (assignment_id,))

            db.commit()


        # -------------------------
        # 1 DAY REMINDER
        # -------------------------

        elif (
            time_left <= timedelta(days=1)
            and not reminder_1d_sent
        ):

            await channel.send(
                f"⚠️ **Assignment Due Tomorrow!**\n\n"
                f"{role.mention}\n"
                f"📝 **{assignment}**\n"
                f"📅 Due: {pretty_due}"
            )

            cursor.execute("""
                UPDATE assignments
                SET reminder_1d_sent = 1
                WHERE id = ?
            """, (assignment_id,))

            db.commit()


        # -------------------------
        # 3 DAY REMINDER
        # -------------------------

        elif (
            time_left <= timedelta(days=3)
            and not reminder_3d_sent
        ):

            await channel.send(
                f"📅 **3 Day Assignment Reminder!**\n\n"
                f"{role.mention}\n"
                f"📝 **{assignment}**\n"
                f"📅 Due: {pretty_due}"
            )

            cursor.execute("""
                UPDATE assignments
                SET reminder_3d_sent = 1
                WHERE id = ?
            """, (assignment_id,))

            db.commit()


# -----------------------------
# WAIT UNTIL BOT IS READY
# -----------------------------

@check_assignments.before_loop
async def before_check_assignments():

    await bot.wait_until_ready()


# -----------------------------
# START BOT
# -----------------------------

bot.run(TOKEN)