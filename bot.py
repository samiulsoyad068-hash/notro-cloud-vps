import random
import logging
import subprocess
import sys
import os
import re
import time
import sqlite3
import asyncio
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands, tasks
from discord import app_commands
import docker
from dotenv import load_dotenv
import aiohttp

# Load environment variables
load_dotenv()

# Configuration from .env
TOKEN = os.getenv('TOKEN', 'DISCORD_BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))  # Admin user ID for checks
BOT_STATUS_NAME = os.getenv('BOT_STATUS_NAME', 'Notro Cloud')
WATERMARK = os.getenv('WATERMARK', 'Powered by Notro Cloud')
# VPS Defaults from .env
DEFAULT_RAM = os.getenv('DEFAULT_RAM', '16g')
DEFAULT_CPU = os.getenv('DEFAULT_CPU', '2')
DEFAULT_DISK = os.getenv('DEFAULT_DISK', '20G')
VPS_HOSTNAME = os.getenv('VPS_HOSTNAME', 'notro-vps')
SERVER_LIMIT = int(os.getenv('SERVER_LIMIT', 1))
TOTAL_SERVER_LIMIT = int(os.getenv('TOTAL_SERVER_LIMIT', 50))
DATABASE_FILE = os.getenv('DATABASE_FILE', 'bot.db')
SUPPORT_DISCORD_LINK = "https://discord.gg/2FJEFTzQ2P"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Supported operating systems
OS_IMAGES = {
    "ubuntu-24.04": ("Ubuntu 24.04", "ubuntu:24.04"),
    "ubuntu-22.04": ("Ubuntu 22.04", "ubuntu:22.04"),
    "debian-11": ("Debian 11", "debian:11"),
}

def get_os_details(os_type):
    return OS_IMAGES.get(os_type, OS_IMAGES["ubuntu-24.04"])

# Intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)
client = docker.from_env()

def is_admin(member):
    if not isinstance(member, discord.Member):
        return False
    return member.id == ADMIN_ID

# Database setup with SQLite3
def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS vps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            container_id TEXT UNIQUE NOT NULL,
            container_name TEXT NOT NULL,
            os_type TEXT NOT NULL,
            hostname TEXT NOT NULL,
            status TEXT DEFAULT 'stopped',
            ssh_command TEXT,
            ram TEXT DEFAULT '{DEFAULT_RAM}',
            cpu TEXT DEFAULT '{DEFAULT_CPU}',
            disk TEXT DEFAULT '{DEFAULT_DISK}',
            suspended INTEGER DEFAULT 0,
            expires_at TIMESTAMP,
            alert_sent INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Migrations for existing DBs
    cursor.execute("PRAGMA table_info(vps)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'suspended' not in columns:
        cursor.execute("ALTER TABLE vps ADD COLUMN suspended INTEGER DEFAULT 0")
    if 'expires_at' not in columns:
        cursor.execute("ALTER TABLE vps ADD COLUMN expires_at TIMESTAMP")
    if 'alert_sent' not in columns:
        cursor.execute("ALTER TABLE vps ADD COLUMN alert_sent INTEGER DEFAULT 0")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bans (
            user_id INTEGER PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def add_user(user_id, username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    conn.commit()
    conn.close()

def add_ban(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO bans (user_id) VALUES (?)', (user_id,))
    conn.commit()
    conn.close()

def remove_ban(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM bans WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def is_banned(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM bans WHERE user_id = ?', (user_id,))
    banned = cursor.fetchone() is not None
    conn.close()
    return banned

def add_vps(user_id, container_id, container_name, os_type, hostname, ssh_command, ram=DEFAULT_RAM, cpu=DEFAULT_CPU, disk=DEFAULT_DISK, expires_at=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO vps (user_id, container_id, container_name, os_type, hostname, status, ssh_command, ram, cpu, disk, suspended, expires_at, alert_sent)
        VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, 0, ?, 0)
    ''', (user_id, container_id, container_name, os_type, hostname, ssh_command, ram, cpu, disk, expires_at))
    conn.commit()
    conn.close()

def get_user_vps(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM vps WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
    vps_list = cursor.fetchall()
    conn.close()
    return vps_list

def count_user_vps(user_id):
    return len(get_user_vps(user_id))

def get_vps_by_identifier(user_id, identifier):
    vps_list = get_user_vps(user_id)
    if not identifier:
        return vps_list[0] if vps_list else None
    identifier_lower = identifier.lower()
    for vps in vps_list:
        if (identifier_lower in vps['container_id'].lower() or
            identifier_lower in vps['container_name'].lower()):
            return vps
    return None

def update_vps_status(container_id, status):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE vps SET status = ? WHERE container_id = ?', (status, container_id))
    conn.commit()
    conn.close()

def update_vps_ssh(container_id, ssh_command):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE vps SET ssh_command = ? WHERE container_id = ?', (ssh_command, container_id))
    conn.commit()
    conn.close()

def delete_vps(container_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM vps WHERE container_id = ?', (container_id,))
    conn.commit()
    conn.close()

# ---- FIXED PARSE DURATION LOGIC ----
def parse_duration(duration_str: str):
    """
    Indestructible duration parser. 
    Accepts: "6", "6 day", "6 days", "6d", "24 hours", "24h"
    """
    if not duration_str:
        return None
        
    d = str(duration_str).strip().lower()
    if d in ["never", "none", "0", "permanent", "forever"]:
        return None
        
    # Find the very first number in whatever they typed
    num_match = re.search(r'\d+', d)
    if not num_match:
        return None
        
    amount = int(num_match.group(0))
    # Extract the letters that come after the number to figure out if it's days/hours
    unit = d[num_match.end():].strip()
    
    if not unit:
        # If they just type "6", default to 6 days
        return timedelta(days=amount)
    
    if unit.startswith('h'): # "h", "hour", "hours"
        return timedelta(hours=amount)
    elif unit.startswith('m') and not unit.startswith('mo'): # "m", "min", "minutes"
        return timedelta(minutes=amount)
    else:
        # If it starts with 'd' or anything else ("day", "days"), assume days.
        return timedelta(days=amount)

async def get_egress_ips():
    ipv4, ipv6 = "N/A", "N/A"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        try:
            async with session.get("https://api4.ipify.org?format=json") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ipv4 = data.get("ip", "N/A")
        except Exception as e:
            logger.warning(f"Failed to fetch IPv4: {e}")
        
        try:
            async with session.get("https://api6.ipify.org?format=json") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ipv6 = data.get("ip", "N/A")
        except Exception as e:
            logger.warning(f"Failed to fetch IPv6: {e}")
            
    return ipv4, ipv6

def get_logs(container_id, lines=50):
    try:
        output = subprocess.check_output(["docker", "logs", "--tail", str(lines), container_id], stderr=subprocess.STDOUT).decode()
        return output[-2000:]
    except Exception:
        return "Failed to fetch logs"

# Async Docker helpers
async def async_docker_run(image, hostname, ram, cpu, disk, container_name):
    cmd = [
        "docker", "run", "-d",
        "--privileged", "--cap-add=ALL",
        "--restart", "always",  # Continuous 24/7 running host policy
        f"--memory={ram}",
        f"--cpus={cpu}",
        f"--hostname={hostname}",
        f"--name={container_name}",
        image,
        "tail", "-f", "/dev/null"
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        if proc.returncode != 0:
            logger.error(f"Docker run failed: {stderr.decode()}")
            return None
        return stdout.decode().strip()
    except Exception as e:
        logger.error(f"Docker run error: {e}")
        return None

async def async_docker_start(container_id):
    try:
        proc = await asyncio.create_subprocess_exec("docker", "start", container_id, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.communicate(), timeout=30.0)
        return proc.returncode == 0
    except Exception:
        return False

async def async_docker_stop(container_id):
    try:
        proc = await asyncio.create_subprocess_exec("docker", "stop", container_id, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.communicate(), timeout=30.0)
        return proc.returncode == 0
    except Exception:
        return False

async def async_docker_restart(container_id):
    try:
        proc = await asyncio.create_subprocess_exec("docker", "restart", container_id, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.communicate(), timeout=30.0)
        return proc.returncode == 0
    except Exception:
        return False

async def async_docker_rm(container_id):
    try:
        proc = await asyncio.create_subprocess_exec("docker", "rm", "-f", container_id, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.communicate()
        return proc.returncode == 0
    except Exception:
        return False

async def async_install_sshx(container_id, os_type):
    install_cmd = "apt-get update && apt-get install -y curl wget sudo openssh-client && curl -sSf https://sshx.io/get | sh"
    try:
        proc = await asyncio.create_subprocess_exec("docker", "exec", container_id, "bash", "-c", install_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(proc.communicate(), timeout=120.0)
    except Exception as e:
        logger.error(f"Failed to install SSHX in {container_id}: {e}")

ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
async def capture_sshx_link(process):
    while True:
        try:
            output = await asyncio.wait_for(process.stdout.readline(), timeout=30.0)
            if not output:
                break
            output = output.decode('utf-8', errors='ignore').strip()
            output = ANSI_ESCAPE_RE.sub('', output)
            if "sshx.io/" in output.lower():
                match = re.search(r"https://sshx\.io/\S+", output)
                if match:
                    return match.group(0).rstrip(".,)]}")
        except asyncio.TimeoutError:
            break
    return None

async def docker_exec_sshx(container_id):
    try:
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "sshx", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        return exec_cmd
    except Exception:
        return None

async def regen_ssh_command(interaction: discord.Interaction, vps_identifier, send_response=True, target_user=None):
    if target_user is None:
        target_user = interaction.user
    vps = get_vps_by_identifier(target_user.id, vps_identifier)
    if not vps:
        embed = discord.Embed(description="No active VPS found.", color=discord.Color.red())
        if send_response: await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    if vps['status'] != "running":
        embed = discord.Embed(description="VPS must be running to generate SSHX access.", color=discord.Color.red())
        if send_response: await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    if send_response: await interaction.response.defer(ephemeral=True)
    
    exec_process = await docker_exec_sshx(vps['container_id'])
    if exec_process:
        ssh_line = await capture_sshx_link(exec_process)
        if ssh_line:
            update_vps_ssh(vps['container_id'], ssh_line)
            embed = discord.Embed(title="Notro Cloud • New SSH Session", description=f"[Connect to VPS]({ssh_line})", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
            embed.set_footer(text=WATERMARK)
            try:
                await target_user.send(embed=embed)
            except discord.Forbidden:
                if send_response: await interaction.followup.send(embed=discord.Embed(description="Generated but could not DM you due to privacy settings.", color=discord.Color.orange()), ephemeral=True)
                else: return True
            if send_response: await interaction.followup.send(embed=discord.Embed(description="New SSH link sent to DMs.", color=discord.Color.green()), ephemeral=True)
            return True
        else:
            if send_response: await interaction.followup.send(embed=discord.Embed(description="Failed to generate link.", color=discord.Color.red()), ephemeral=True)
            return False
    else:
        if send_response: await interaction.followup.send(embed=discord.Embed(description="Failed to execute SSHX.", color=discord.Color.red()), ephemeral=True)
        return False

async def manage_vps(interaction: discord.Interaction, vps_identifier, action, target_user=None):
    if target_user is None: target_user = interaction.user
    await interaction.response.defer(ephemeral=True)
    vps = get_vps_by_identifier(target_user.id, vps_identifier)
    if not vps:
        await interaction.followup.send(embed=discord.Embed(description="No VPS found.", color=discord.Color.red()), ephemeral=True)
        return
    if action == "start" and vps['suspended'] and target_user == interaction.user:
        await interaction.followup.send(embed=discord.Embed(description="VPS is suspended by Notro Cloud admins.", color=discord.Color.red()), ephemeral=True)
        return
    
    success = False
    if action == "start":
        success = await async_docker_start(vps['container_id'])
        if success: update_vps_status(vps['container_id'], "running")
    elif action == "stop":
        success = await async_docker_stop(vps['container_id'])
        if success: update_vps_status(vps['container_id'], "stopped")
    elif action == "restart":
        success = await async_docker_restart(vps['container_id'])
        if success: update_vps_status(vps['container_id'], "running")
        
    if success:
        embed = discord.Embed(title=f"Notro Cloud • VPS {action.title()}ed", description=f"OS: {get_os_details(vps['os_type'])[0]}", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
        embed.set_footer(text=WATERMARK)
        if action in ["start", "restart"]:
            await regen_ssh_command(interaction, vps_identifier, send_response=False, target_user=target_user)
            embed.description += "\nNew SSH access link sent to DMs."
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.followup.send(embed=discord.Embed(description=f"Failed to {action} VPS.", color=discord.Color.red()), ephemeral=True)

# Create VPS logic
async def create_vps(interaction: discord.Interaction, os_type, ram=DEFAULT_RAM, cpu=DEFAULT_CPU, disk=DEFAULT_DISK, duration="never", target_user=None):
    if target_user is None: 
        target_user = interaction.user
    add_user(target_user.id, str(target_user))
    if is_banned(target_user.id):
        await interaction.response.send_message(embed=discord.Embed(description="You are banned from Notro Cloud.", color=discord.Color.red()), ephemeral=True)
        return
    if count_user_vps(target_user.id) >= SERVER_LIMIT and not is_admin(interaction.user):
        await interaction.response.send_message(embed=discord.Embed(description=f"User reached limit of {SERVER_LIMIT} VPS instances.", color=discord.Color.red()), ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(f"Creating Notro Cloud VPS for {target_user.mention} ({ram} RAM, {cpu} CPU, {disk} Disk)...", ephemeral=True)
    
    # Calculate Expiration Date
    dur_delta = parse_duration(duration)
    expires_at_dt = (datetime.now(timezone.utc) + dur_delta) if dur_delta else None
    expires_at_str = expires_at_dt.isoformat() if expires_at_dt else None
    
    # Fetch real IPv4 and IPv6 egress addresses
    ipv4, ipv6 = await get_egress_ips()

    hostname = f"{VPS_HOSTNAME}-{target_user.id}"
    container_name = f"{os_type}-vps-{target_user.id}-{random.randint(1000, 9999)}"
    
    container_id = await async_docker_run(get_os_details(os_type)[1], hostname, ram, cpu, disk, container_name)
    if not container_id:
        await interaction.followup.send(embed=discord.Embed(description="Failed to create container.", color=discord.Color.red()), ephemeral=True)
        return
        
    await asyncio.sleep(5)
    await async_install_sshx(container_id, os_type)
    await asyncio.sleep(10)
    exec_process = await docker_exec_sshx(container_id)
    ssh_line = await capture_sshx_link(exec_process)
    
    if ssh_line:
        add_vps(target_user.id, container_id, container_name, os_type, hostname, ssh_line, ram, cpu, disk, expires_at=expires_at_str)
        
        exp_display = expires_at_dt.strftime("%Y-%m-%d %H:%M UTC") if expires_at_dt else "Never (Permanent)"
        
        embed = discord.Embed(
            title="Notro Cloud • VPS Created",
            description=(
                f"**OS:** {get_os_details(os_type)[0]}\n"
                f"**RAM:** {ram} | **CPU:** {cpu} | **Disk:** {disk}\n"
                f"🌐 **Egress IPv4:** `{ipv4}`\n"
                f"🌐 **Egress IPv6:** `{ipv6}`\n"
                f"⏰ **Expires At:** `{exp_display}`\n\n"
                f"🔗 [Connect to VPS]({ssh_line})"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=WATERMARK)
        
        try:
            await target_user.send(embed=embed)
        except discord.Forbidden:
            pass
            
        await interaction.followup.send(embed=discord.Embed(description=f"Notro Cloud VPS successfully deployed for {target_user.mention}! Check DMs for details.", color=discord.Color.green()), ephemeral=True)
    else:
        await interaction.followup.send(embed=discord.Embed(description="Creation failed: Unable to generate SSH link.", color=discord.Color.red()), ephemeral=True)
        await async_docker_rm(container_id)

# ----------------- BACKGROUND MONITORING TASK ----------------- #

@tasks.loop(minutes=1)
async def check_vps_expirations():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vps WHERE expires_at IS NOT NULL")
    vps_list = cursor.fetchall()
    conn.close()

    now = datetime.now(timezone.utc)
    for vps in vps_list:
        try:
            exp_time = datetime.fromisoformat(vps['expires_at'])
            remaining = exp_time - now

            # Deletion logic when expired
            if remaining.total_seconds() <= 0:
                container_id = vps['container_id']
                user_id = vps['user_id']
                
                await async_docker_stop(container_id)
                await async_docker_rm(container_id)
                delete_vps(container_id)

                try:
                    user = await bot.fetch_user(user_id)
                    if user:
                        embed = discord.Embed(
                            title="Notro Cloud • VPS Expired & Deleted",
                            description=(
                                f"Your VPS `{vps['container_name']}` has reached its expiration date and has been automatically deleted.\n\n"
                                f"If you think this is a bug, issue, or want to pay/renew your hosting, contact support here:\n{SUPPORT_DISCORD_LINK}"
                            ),
                            color=discord.Color.red()
                        )
                        embed.set_footer(text=WATERMARK)
                        await user.send(embed=embed)
                except Exception as e:
                    logger.warning(f"Failed to DM deletion notification to user {user_id}: {e}")

            # Warning Alert logic: Trigger warning if 3 Days (259200 seconds) or less remain
            elif remaining.total_seconds() <= 259200 and not vps['alert_sent']:
                user_id = vps['user_id']
                try:
                    user = await bot.fetch_user(user_id)
                    if user:
                        # Determine if we show hours or days in the warning
                        days_left = remaining.total_seconds() / 86400
                        time_display = f"~{int(days_left)} days" if days_left >= 1 else f"~{max(1, int(remaining.total_seconds() // 3600))} hours"
                        
                        embed = discord.Embed(
                            title="⚠️ Notro Cloud • VPS Expiration Alert",
                            description=(
                                f"Alert: Your VPS `{vps['container_name']}` will be automatically deleted in **{time_display}**.\n\n"
                                f"If this is a bug, an issue, or you want to pay/renew, please join our support Discord server:\n{SUPPORT_DISCORD_LINK}"
                            ),
                            color=discord.Color.gold(),
                            timestamp=now
                        )
                        embed.set_footer(text=WATERMARK)
                        await user.send(embed=embed)
                except Exception as e:
                    logger.warning(f"Failed to send alert DM to user {user_id}: {e}")

                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("UPDATE vps SET alert_sent = 1 WHERE container_id = ?", (vps['container_id'],))
                conn.commit()
                conn.close()

        except Exception as e:
            logger.error(f"Error processing expiration for container {vps['container_id']}: {e}")


# ----------------- SLASH COMMANDS (USERS) ----------------- #

@bot.tree.command(name="help", description="List all available Notro Cloud commands")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="Notro Cloud • Command List", color=discord.Color.from_rgb(88, 101, 242))
    embed.add_field(name="User Commands", value="""
`/create` - Deploy a standard VPS instance
`/start` - Start your stopped VPS
`/stop` - Stop your running VPS
`/restart` - Restart your VPS
`/ssh` - Regenerate your SSH access link
`/reinstall` - Reinstall and wipe your VPS clean
`/logs` - View recent container logs
`/about` - Show Notro Cloud information
`/help` - Show this command list
    """, inline=False)
    
    if is_admin(interaction.user):
        embed.add_field(name="Admin Commands", value="""
`/admin-create` - Deploy custom VPS with RAM, CPU, Disk, & Expiration
`/admin-list` - View all global VPS instances
`/admin-users` - View user statistics
`/admin-stats` - View host hardware stats
`/admin-vps-info` - Get details on a specific VPS
`/admin-logs` - View logs of a specific user's VPS
`/admin-del-user` - Force delete a user's VPS
`/admin-ban` - Ban a user from creating VPS
`/admin-unban` - Unban a user
        """, inline=False)
    embed.set_footer(text=WATERMARK)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="create", description="Deploy a new Notro Cloud VPS instance")
@app_commands.describe(os_type="The OS type for the VPS")
@app_commands.choices(os_type=[
    app_commands.Choice(name="Ubuntu 24.04", value="ubuntu-24.04"),
    app_commands.Choice(name="Ubuntu 22.04", value="ubuntu-22.04"),
    app_commands.Choice(name="Debian 11", value="debian-11")
])
async def deploy(interaction: discord.Interaction, os_type: str):
    await create_vps(interaction, os_type)

@bot.tree.command(name="start", description="Start your stopped VPS")
@app_commands.describe(vps_identifier="VPS ID or Name (optional)")
async def user_start(interaction: discord.Interaction, vps_identifier: str = ""):
    await manage_vps(interaction, vps_identifier, "start")

@bot.tree.command(name="stop", description="Stop your running VPS")
@app_commands.describe(vps_identifier="VPS ID or Name (optional)")
async def user_stop(interaction: discord.Interaction, vps_identifier: str = ""):
    await manage_vps(interaction, vps_identifier, "stop")

@bot.tree.command(name="restart", description="Restart your running VPS")
@app_commands.describe(vps_identifier="VPS ID or Name (optional)")
async def user_restart(interaction: discord.Interaction, vps_identifier: str = ""):
    await manage_vps(interaction, vps_identifier, "restart")

@bot.tree.command(name="ssh", description="Regenerate your SSH access link")
@app_commands.describe(vps_identifier="VPS ID or Name (optional)")
async def user_ssh(interaction: discord.Interaction, vps_identifier: str = ""):
    await regen_ssh_command(interaction, vps_identifier)

@bot.tree.command(name="reinstall", description="Reinstall and wipe your VPS clean")
@app_commands.describe(vps_identifier="VPS ID or Name", os_type="New OS type")
@app_commands.choices(os_type=[
    app_commands.Choice(name="Ubuntu 24.04", value="ubuntu-24.04"),
    app_commands.Choice(name="Ubuntu 22.04", value="ubuntu-22.04"),
    app_commands.Choice(name="Debian 11", value="debian-11")
])
async def user_reinstall(interaction: discord.Interaction, vps_identifier: str, os_type: str):
    await interaction.response.defer(ephemeral=True)
    vps = get_vps_by_identifier(interaction.user.id, vps_identifier)
    if not vps:
        await interaction.followup.send(embed=discord.Embed(description="VPS not found.", color=discord.Color.red()), ephemeral=True)
        return
    
    old_container_id = vps['container_id']
    await async_docker_stop(old_container_id)
    await async_docker_rm(old_container_id)
    delete_vps(old_container_id)
    
    await create_vps(interaction, os_type, ram=vps['ram'], cpu=vps['cpu'], disk=vps['disk'], duration="never", target_user=interaction.user)

@bot.tree.command(name="logs", description="View recent logs for your VPS")
@app_commands.describe(vps_identifier="VPS ID or Name", lines="Lines (default 50)")
async def user_logs(interaction: discord.Interaction, vps_identifier: str = "", lines: int = 50):
    vps = get_vps_by_identifier(interaction.user.id, vps_identifier)
    if not vps:
        await interaction.response.send_message(embed=discord.Embed(description="VPS not found.", color=discord.Color.red()), ephemeral=True)
        return
    logs = get_logs(vps['container_id'], lines)
    embed = discord.Embed(title=f"Logs for {vps['container_name']}", description=f"```{logs}```", color=discord.Color.blue())
    embed.set_footer(text=WATERMARK)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="about", description="Show Notro Cloud information")
async def about(interaction: discord.Interaction):
    embed = discord.Embed(
        title="☁️ Notro Cloud • About",
        description="**A powerful, fast, and user-friendly Discord bot for managing 24/7 VPS servers.**\nDesigned with **speed**, **stability**, and **simplicity** in mind 🚀",
        color=discord.Color.from_rgb(88, 101, 242)
    )
    embed.add_field(name="📌 Details", value=f"➜ **Name:** Notro Cloud\n➜ **Support:** {SUPPORT_DISCORD_LINK}\n➜ **Status:** 🟢 Online & Active 24/7", inline=False)
    embed.set_footer(text=WATERMARK)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ----------------- SLASH COMMANDS (ADMIN) ----------------- #

@bot.tree.command(name="admin-create", description="Admin: Custom VPS deployment with expiration controls")
@app_commands.describe(
    target_user="Target user receiving the VPS",
    os_type="Operating system",
    ram="Memory limit (e.g. 16g, 8g)",
    cpu="CPU core limit (e.g. 2, 4)",
    disk="Disk quota (e.g. 20G, 50G)",
    duration="Duration before auto-deletion (e.g. 1d, 3d, 6, 6 days, never)"
)
@app_commands.choices(os_type=[
    app_commands.Choice(name="Ubuntu 24.04", value="ubuntu-24.04"),
    app_commands.Choice(name="Ubuntu 22.04", value="ubuntu-22.04"),
    app_commands.Choice(name="Debian 11", value="debian-11")
])
async def admin_create(
    interaction: discord.Interaction,
    target_user: discord.User,
    os_type: str,
    ram: str = DEFAULT_RAM,
    cpu: str = DEFAULT_CPU,
    disk: str = DEFAULT_DISK,
    duration: str = "never"
):
    if not is_admin(interaction.user):
        await interaction.response.send_message(embed=discord.Embed(description="Unauthorized admin command.", color=discord.Color.red()), ephemeral=True)
        return
    await create_vps(
        interaction=interaction,
        os_type=os_type,
        ram=ram,
        cpu=cpu,
        disk=disk,
        duration=duration,
        target_user=target_user
    )

@bot.tree.command(name="admin-list", description="Admin: List all VPS instances")
async def admin_list(interaction: discord.Interaction):
    if not is_admin(interaction.user): return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT u.username, v.container_id, v.container_name, v.os_type, v.status, v.ram, v.expires_at FROM vps v JOIN users u ON v.user_id = u.user_id ORDER BY v.created_at DESC')
    all_vps = cursor.fetchall()
    conn.close()
    embed = discord.Embed(title="Global VPS Instances", color=discord.Color.blue())
    for row in all_vps[:25]:
        emoji = "🟢" if row['status'] == "running" else "🔴"
        exp = row['expires_at'] or "Never"
        embed.add_field(name=f"{emoji} {row['username']} - {row['container_name']}", value=f"ID: `{row['container_id']}` | RAM: {row['ram']} | Exp: `{exp}`", inline=False)
    embed.set_footer(text=WATERMARK)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="admin-users", description="Admin: View active users list")
async def admin_users(interaction: discord.Interaction):
    if not is_admin(interaction.user): return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT u.username, COUNT(v.id) as vps_count FROM users u JOIN vps v ON u.user_id = v.user_id GROUP BY u.user_id')
    users_data = cursor.fetchall()
    conn.close()
    embed = discord.Embed(title="Notro Cloud Active Users", color=discord.Color.blue())
    for row in users_data[:25]:
        embed.add_field(name=f"👤 {row['username']}", value=f"Active VPS Count: {row['vps_count']}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="admin-stats", description="Admin: View basic host hardware stats")
async def admin_stats(interaction: discord.Interaction):
    if not is_admin(interaction.user): return
    try:
        host_info = client.info()
        containers = host_info['Containers']
        containers_running = host_info['ContainersRunning']
        ncpu = host_info['NCPU']
        mem_total = host_info['MemTotal'] / (1024 ** 3)
        
        embed = discord.Embed(title="Host Machine Statistics", color=discord.Color.purple())
        embed.add_field(name="Containers", value=f"Total: {containers}\nRunning: {containers_running}")
        embed.add_field(name="Hardware", value=f"CPUs: {ncpu}\nTotal RAM: {mem_total:.2f} GB")
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Failed to fetch stats: {e}", ephemeral=True)

@bot.tree.command(name="admin-vps-info", description="Admin: Get deep details on a user's VPS")
async def admin_vps_info(interaction: discord.Interaction, target_user: discord.User):
    if not is_admin(interaction.user): return
    vps_list = get_user_vps(target_user.id)
    if not vps_list:
        await interaction.response.send_message(embed=discord.Embed(description="User has no active VPS.", color=discord.Color.red()), ephemeral=True)
        return
    embed = discord.Embed(title=f"VPS Info for {target_user.name}", color=discord.Color.blue())
    for vps in vps_list:
        embed.add_field(name=vps['container_name'], value=f"ID: `{vps['container_id']}`\nRAM: {vps['ram']} | CPU: {vps['cpu']}\nOS: {vps['os_type']}\nExpires: {vps['expires_at'] or 'Never'}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="admin-logs", description="Admin: View logs for any user's VPS")
async def admin_logs(interaction: discord.Interaction, target_user: discord.User, lines: int = 50):
    if not is_admin(interaction.user): return
    vps = get_user_vps(target_user.id)
    if not vps:
        await interaction.response.send_message("No VPS found for user.", ephemeral=True)
        return
    logs = get_logs(vps[0]['container_id'], lines)
    embed = discord.Embed(title=f"Logs for {vps[0]['container_name']}", description=f"```{logs}```", color=discord.Color.orange())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="admin-del-user", description="Admin: Force delete a user's VPS")
async def admin_del_user(interaction: discord.Interaction, target_user: discord.User):
    if not is_admin(interaction.user): return
    await interaction.response.defer(ephemeral=True)
    vps_list = get_user_vps(target_user.id)
    if not vps_list:
        await interaction.followup.send("User has no active VPS.", ephemeral=True)
        return
    for vps in vps_list:
        await async_docker_stop(vps['container_id'])
        await async_docker_rm(vps['container_id'])
        delete_vps(vps['container_id'])
    await interaction.followup.send(embed=discord.Embed(description=f"Deleted all VPS instances for {target_user.mention}.", color=discord.Color.green()), ephemeral=True)

@bot.tree.command(name="admin-ban", description="Admin: Ban a user from using the bot")
async def admin_ban(interaction: discord.Interaction, target_user: discord.User):
    if not is_admin(interaction.user): return
    add_ban(target_user.id)
    await interaction.response.send_message(embed=discord.Embed(description=f"Banned {target_user.mention} from Notro Cloud.", color=discord.Color.red()), ephemeral=True)

@bot.tree.command(name="admin-unban", description="Admin: Unban a user")
async def admin_unban(interaction: discord.Interaction, target_user: discord.User):
    if not is_admin(interaction.user): return
    remove_ban(target_user.id)
    await interaction.response.send_message(embed=discord.Embed(description=f"Unbanned {target_user.mention}.", color=discord.Color.green()), ephemeral=True)


@bot.event
async def on_ready():
    await bot.tree.sync()
    if not check_vps_expirations.is_running():
        check_vps_expirations.start()
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logger.info('Notro Cloud system is Online with expiration monitoring active.')

# Run bot
bot.run(TOKEN)