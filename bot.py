import random
import logging
import subprocess
import sys
import os
import re
import socket
import ipaddress
import sqlite3
import asyncio
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands, tasks
from discord import app_commands
import docker
from dotenv import load_dotenv
import aiohttp

load_dotenv()

TOKEN = os.getenv('TOKEN', 'DISCORD_BOT_TOKEN')
# ADMIN_ID may be a single id or a comma-separated list of ids, e.g. "123, 456"
_raw_admin_ids = os.getenv('ADMIN_ID', '0')
ADMIN_ID = 0
ADMIN_IDS = set()
for _p in _raw_admin_ids.split(','):
    _p = _p.strip()
    if not _p:
        continue
    try:
        _v = int(_p)
        ADMIN_IDS.add(_v)
        if not ADMIN_ID:
            ADMIN_ID = _v
    except ValueError:
        pass
BOT_STATUS_NAME = os.getenv('BOT_STATUS_NAME', 'Notro Cloud')
WATERMARK = os.getenv('WATERMARK', 'Powered by Notro Cloud')
DEFAULT_RAM = os.getenv('DEFAULT_RAM', '2g')
DEFAULT_CPU = os.getenv('DEFAULT_CPU', '1')
DEFAULT_DISK = os.getenv('DEFAULT_DISK', '10G')
VPS_HOSTNAME = os.getenv('VPS_HOSTNAME', 'notrocloud-vps')
SERVER_LIMIT = int(os.getenv('SERVER_LIMIT', 1))
TOTAL_SERVER_LIMIT = int(os.getenv('TOTAL_SERVER_LIMIT', 50))
DATABASE_FILE = os.getenv('DATABASE_FILE', 'bot.db')
GUILD_ID = os.getenv('GUILD_ID')
SUPPORT_DISCORD_LINK = "https://discord.gg/2FJEFTzQ2P"
DEFAULT_DNS = [x.strip() for x in os.getenv('DEFAULT_DNS', '1.1.1.1, 8.8.8.8').split(',') if x.strip()]
REGION = os.getenv('REGION', 'Bangladesh (BD)')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

OS_IMAGES = {
    "ubuntu-24.04": ("Ubuntu 24.04", "ubuntu:24.04"),
    "ubuntu-22.04": ("Ubuntu 22.04", "ubuntu:22.04"),
    "debian-11": ("Debian 11", "debian:11"),
}


def get_os_details(os_type):
    return OS_IMAGES.get(os_type, OS_IMAGES["ubuntu-24.04"])


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

try:
    client = docker.from_env()
    client.ping()
except Exception as e:
    logger.warning(f"Docker daemon not reachable on startup ({e}); VPS commands will report errors until Docker is available.")
    client = None

ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
SSHX_LINK_RE = re.compile(r"https://sshx\.io/\S+")

INSTALL_PRESETS = {
    "docker": "apt-get update && apt-get install -y docker.io docker-compose-v2",
    "python": "apt-get update && apt-get install -y python3 python3-pip python3-venv",
    "node": "curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs",
    "nginx": "apt-get update && apt-get install -y nginx",
    "redis": "apt-get update && apt-get install -y redis-server",
    "postgresql": "apt-get update && apt-get install -y postgresql",
    "java": "apt-get update && apt-get install -y default-jdk",
    "git": "apt-get update && apt-get install -y git",
    "curl": "apt-get update && apt-get install -y curl",
}


def is_admin(member):
    uid = getattr(member, 'id', None)
    return uid is not None and uid in ADMIN_IDS


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


def add_vps(user_id, container_id, container_name, os_type, hostname, ssh_command,
            ram=DEFAULT_RAM, cpu=DEFAULT_CPU, disk=DEFAULT_DISK, expires_at=None):
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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM vps WHERE user_id = ?', (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count


def count_all_vps():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM vps')
    count = cursor.fetchone()[0]
    conn.close()
    return count


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


def parse_duration(duration_str: str):
    if not duration_str:
        return None

    d = str(duration_str).strip().lower()
    if d in ["never", "none", "0", "permanent", "forever"]:
        return None

    num_match = re.search(r'\d+', d)
    if not num_match:
        return None

    amount = int(num_match.group(0))
    unit = d[num_match.end():].strip()

    if not unit:
        return timedelta(days=amount)

    if unit.startswith('h'):
        return timedelta(hours=amount)
    elif unit.startswith('m') and not unit.startswith('mo'):
        return timedelta(minutes=amount)
    else:
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
        output = subprocess.check_output(
            ["docker", "logs", "--tail", str(lines), container_id],
            stderr=subprocess.STDOUT
        ).decode()
        return output[-2000:]
    except Exception:
        return "Failed to fetch logs"


def resolve_nameservers(*servers):
    resolved = []
    for s in servers:
        s = (s or "").strip()
        if not s:
            continue
        if s in resolved:
            continue
        if is_ip(s):
            resolved.append(s)
        else:
            ip = resolve_host(s)
            if ip:
                resolved.append(ip)
            else:
                logger.warning(f"Could not resolve nameserver '{s}' to an IP; skipping.")
    return resolved


def is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def resolve_host(hostname):
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            infos = socket.getaddrinfo(hostname, None, family)
            if infos:
                return infos[0][4][0]
        except socket.gaierror:
            continue
    return None


def parse_mem_to_bytes(mem):
    s = str(mem).strip().lower()
    if not s:
        return None
    try:
        if s.endswith('g'):
            return int(float(s[:-1]) * 1024 ** 3)
        if s.endswith('m'):
            return int(float(s[:-1]) * 1024 ** 2)
        if s.endswith('k'):
            return int(float(s[:-1]) * 1024)
        return int(float(s))
    except ValueError:
        return None


def check_resources(ram, cpu):
    """Validate requested RAM against the host's real memory. Docker enforces RAM
    strictly (no oversubscription), so 16g/24g on an 8 GB host is rejected with a
    clear message instead of silently being capped. CPU is a cgroup quota and is
    always allowed (oversubscription is valid)."""
    mem_bytes = parse_mem_to_bytes(ram)
    if mem_bytes is None:
        return False, f"Invalid RAM value '{ram}' (use e.g. 16g, 8g, 512m)."
    if client is not None:
        try:
            info = client.info()
            total = info.get('MemTotal')
            if total and mem_bytes > total:
                host_gb = total / (1024 ** 3)
                req_gb = mem_bytes / (1024 ** 3)
                return False, (f"Requested RAM `{ram}` (~{req_gb:.0f} GB) exceeds this host's total memory "
                               f"(~{host_gb:.1f} GB). Use a smaller value or move Notro Cloud to a host with more RAM.")
        except Exception:
            pass
    return True, ""


def driver_supports_disk_quota():
    """Docker only hard-caps the container's rootfs disk size on the vfs driver.
    Returns True if `--storage-opt size=` is expected to be honored."""
    if client is None:
        return False
    try:
        drv = client.info().get('Driver', '')
        return drv == 'vfs'
    except Exception:
        return False



async def async_docker_run(image, hostname, ram, cpu, container_name, nameservers=None, disk=None):
    cmd = [
        "docker", "run", "-d",
        "--privileged", "--cap-add=ALL",
        "--restart", "always",
        f"--memory={ram}",
        f"--cpus={cpu}",
        f"--hostname={hostname}",
        f"--name={container_name}",
    ]
    for ns in resolve_nameservers(*nameservers) if nameservers else []:
        cmd += ["--dns", ns]
    if disk and driver_supports_disk_quota():
        dbytes = parse_mem_to_bytes(disk)
        if dbytes:
            cmd += ["--storage-opt", f"size={dbytes}"]
    cmd += [image, "tail", "-f", "/dev/null"]
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


async def async_docker_exec(container_id, script, timeout=300.0, capture=True):
    try:
        kwargs = {"stdout": asyncio.subprocess.PIPE, "stderr": asyncio.subprocess.PIPE} if capture else {
            "stdout": asyncio.subprocess.DEVNULL, "stderr": asyncio.subprocess.PIPE}
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "bash", "-c", script, **kwargs
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return None, "", "timeout"
        return stdout.decode('utf-8', errors='ignore'), stderr.decode('utf-8', errors='ignore'), proc.returncode
    except Exception as e:
        logger.error(f"docker exec error for {container_id}: {e}")
        return None, str(e), -1


async def async_docker_start(container_id):
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "start", container_id,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.communicate(), timeout=30.0)
        return proc.returncode == 0
    except Exception:
        return False


async def async_docker_stop(container_id):
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "stop", container_id,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.communicate(), timeout=30.0)
        return proc.returncode == 0
    except Exception:
        return False


async def async_docker_restart(container_id):
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "restart", container_id,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.communicate(), timeout=30.0)
        return proc.returncode == 0
    except Exception:
        return False


async def async_docker_rm(container_id):
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", container_id,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.communicate()
        return proc.returncode == 0
    except Exception:
        return False


async def async_setup_sshx(container_id):
    cmd = (
        "if ! command -v sshx >/dev/null 2>&1; then "
        "apt-get update >/dev/null 2>&1 && "
        "apt-get install -y curl wget sudo openssh-client >/dev/null 2>&1 && "
        "(curl -sSf https://sshx.io/get | sh) >/dev/null 2>&1; fi; "
        "pkill sshx 2>/dev/null; sleep 1; "
        "nohup sshx > /tmp/sshx_session.log 2>&1 & "
        "sleep 5; "
        "cat /tmp/sshx_session.log 2>/dev/null"
    )
    stdout, stderr, rc = await async_docker_exec(container_id, cmd, timeout=180.0)
    if stdout:
        cleaned = ANSI_ESCAPE_RE.sub('', stdout)
        m = SSHX_LINK_RE.search(cleaned)
        if m:
            return m.group(0).rstrip(".,)]}")
    logger.warning(f"SSHX link not found for {container_id} (rc={rc}). Output tail: {stderr[-300:] if stderr else ''}")
    return None


def _container_status(cid):
    try:
        c = client.containers.get(cid)
        return "running" if c.status == "running" else "stopped"
    except docker.errors.NotFound:
        return None
    except Exception:
        return None


async def reconcile_vps_state():
    await asyncio.sleep(2)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT container_id, status FROM vps")
    rows = cursor.fetchall()
    conn.close()
    reconciled = 0
    for row in rows:
        cid = row['container_id']
        actual = await asyncio.to_thread(_container_status, cid)
        if actual is None:
            logger.info(f"Container {cid} not found in Docker; marking stopped.")
            update_vps_status(cid, "stopped")
        elif actual != row['status']:
            logger.info(f"Reconciled {cid}: {row['status']} -> {actual}")
            update_vps_status(cid, actual)
            reconciled += 1
    logger.info(f"Reconciled {len(rows)} VPS states with Docker ({reconciled} changed).")


async def regen_ssh_command(interaction: discord.Interaction, vps_identifier, send_response=True, target_user=None):
    if target_user is None:
        target_user = interaction.user
    vps = get_vps_by_identifier(target_user.id, vps_identifier)
    if not vps:
        if send_response and not interaction.response.is_done():
            await interaction.response.send_message(
                embed=discord.Embed(description="No active VPS found.", color=discord.Color.red()), ephemeral=True)
        return False
    if vps['status'] != "running":
        if send_response and not interaction.response.is_done():
            await interaction.response.send_message(
                embed=discord.Embed(description="VPS must be running to generate SSHX access.", color=discord.Color.red()), ephemeral=True)
        return False
    if send_response and not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    ssh_line = await async_setup_sshx(vps['container_id'])
    if ssh_line:
        update_vps_ssh(vps['container_id'], ssh_line)
        embed = discord.Embed(
            title="Notro Cloud • New SSH Session",
            description=f"[Connect to VPS]({ssh_line})",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=WATERMARK)
        try:
            await target_user.send(embed=embed)
        except discord.Forbidden:
            if send_response and not interaction.response.is_done():
                await interaction.followup.send(
                    embed=discord.Embed(description="Generated but could not DM you due to privacy settings.",
                                        color=discord.Color.orange()), ephemeral=True)
            elif not send_response:
                return True
        if send_response and not interaction.response.is_done():
            await interaction.followup.send(
                embed=discord.Embed(description="New SSH link sent to DMs.", color=discord.Color.green()), ephemeral=True)
        return True
    else:
        if send_response and not interaction.response.is_done():
            await interaction.followup.send(
                embed=discord.Embed(description="Failed to generate link.", color=discord.Color.red()), ephemeral=True)
        return False


async def manage_vps(interaction: discord.Interaction, vps_identifier, action, target_user=None):
    if target_user is None:
        target_user = interaction.user
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    vps = get_vps_by_identifier(target_user.id, vps_identifier)
    if not vps:
        await interaction.followup.send(
            embed=discord.Embed(description="No VPS found.", color=discord.Color.red()), ephemeral=True)
        return
    if action == "start" and vps['suspended'] and target_user == interaction.user:
        await interaction.followup.send(
            embed=discord.Embed(description="VPS is suspended by Notro Cloud admins.", color=discord.Color.red()), ephemeral=True)
        return

    success = False
    if action == "start":
        success = await async_docker_start(vps['container_id'])
        if success:
            update_vps_status(vps['container_id'], "running")
    elif action == "stop":
        success = await async_docker_stop(vps['container_id'])
        if success:
            update_vps_status(vps['container_id'], "stopped")
    elif action == "restart":
        success = await async_docker_restart(vps['container_id'])
        if success:
            update_vps_status(vps['container_id'], "running")

    if success:
        embed = discord.Embed(
            title=f"Notro Cloud • VPS {action.title()}ed",
            description=f"OS: {get_os_details(vps['os_type'])[0]}",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=WATERMARK)
        if action in ["start", "restart"]:
            await regen_ssh_command(interaction, vps_identifier, send_response=False, target_user=target_user)
            embed.description += "\nNew SSH access link sent to DMs."
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.followup.send(
            embed=discord.Embed(description=f"Failed to {action} VPS.", color=discord.Color.red()), ephemeral=True)


async def create_vps(interaction: discord.Interaction, os_type, ram=DEFAULT_RAM, cpu=DEFAULT_CPU, disk=DEFAULT_DISK, duration="never", target_user=None, nameservers=None):
    if target_user is None:
        target_user = interaction.user
    add_user(target_user.id, target_user.name)

    if is_banned(target_user.id):
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=discord.Embed(description="You are banned from Notro Cloud.", color=discord.Color.red()), ephemeral=True)
        return

    if count_user_vps(target_user.id) >= SERVER_LIMIT and not is_admin(interaction.user):
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=discord.Embed(description=f"User reached limit of {SERVER_LIMIT} VPS instances.", color=discord.Color.red()), ephemeral=True)
        return

    if count_all_vps() >= TOTAL_SERVER_LIMIT and not is_admin(interaction.user):
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=discord.Embed(description=f"Global limit of {TOTAL_SERVER_LIMIT} VPS instances reached.", color=discord.Color.red()), ephemeral=True)
        return

    ok, msg = check_resources(ram, cpu)
    if not ok:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=discord.Embed(description=msg, color=discord.Color.red()), ephemeral=True)
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(
        f"Creating Notro Cloud VPS for {target_user.mention} ({ram} RAM, {cpu} CPU, {disk} Disk)...", ephemeral=True)

    dur_delta = parse_duration(duration)
    expires_at_dt = (datetime.now(timezone.utc) + dur_delta) if dur_delta else None
    expires_at_str = expires_at_dt.isoformat() if expires_at_dt else None

    ipv4, ipv6 = await get_egress_ips()

    if not nameservers:
        nameservers = DEFAULT_DNS

    hostname = f"{VPS_HOSTNAME}-{target_user.id}"
    container_name = f"{os_type}-vps-{target_user.id}-{random.randint(1000, 9999)}"

    container_id = await async_docker_run(get_os_details(os_type)[1], hostname, ram, cpu, container_name, nameservers=nameservers, disk=disk)
    if not container_id:
        await interaction.followup.send(
            embed=discord.Embed(description="Failed to create container. Is Docker running?", color=discord.Color.red()), ephemeral=True)
        return

    await asyncio.sleep(5)
    ssh_line = await async_setup_sshx(container_id)

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
                 f"🌍 **Hosted Region:** {REGION}\n"
                 f"🔧 **DNS:** {', '.join(nameservers)}\n"
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

        await interaction.followup.send(
            embed=discord.Embed(description=f"Notro Cloud VPS deployed for {target_user.mention}! Check DMs for details.", color=discord.Color.green()), ephemeral=True)
    else:
        await interaction.followup.send(
            embed=discord.Embed(description="Creation failed: unable to generate SSH link.", color=discord.Color.red()), ephemeral=True)
        await async_docker_rm(container_id)


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
                                f"If you think this is a bug, an issue, or want to pay/renew your hosting, contact support here:\n{SUPPORT_DISCORD_LINK}"
                            ),
                            color=discord.Color.red()
                        )
                        embed.set_footer(text=WATERMARK)
                        await user.send(embed=embed)
                except Exception as e:
                    logger.warning(f"Failed to DM deletion notification to user {user_id}: {e}")

            elif remaining.total_seconds() <= 259200 and not vps['alert_sent']:
                user_id = vps['user_id']
                try:
                    user = await bot.fetch_user(user_id)
                    if user:
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


@bot.event
async def on_ready():
    try:
        if GUILD_ID:
            try:
                guild_obj = discord.Object(id=int(GUILD_ID))
            except (ValueError, TypeError):
                guild_obj = None
            if guild_obj is not None:
                synced = await bot.tree.sync(guild=guild_obj)
                logger.info(f"Synced {len(synced)} commands to guild {GUILD_ID}.")
        else:
            logger.warning("GUILD_ID not set: syncing GLOBAL commands (up to 1 hour to appear in all servers).")
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} commands globally.")
    except Exception as e:
        logger.warning(f"Failed to sync slash commands: {e}")

    if not check_vps_expirations.is_running():
        check_vps_expirations.start()

    asyncio.create_task(reconcile_vps_state())

    await bot.change_presence(status=discord.Status.online, activity=discord.Game(name=BOT_STATUS_NAME))

    bot_id = getattr(bot.user, 'id', '?')
    logger.info(f'Logged in as {bot.user} (ID: {bot_id})')
    logger.info('Notro Cloud system is Online; startup VPS/ Docker reconciliation scheduled.')


@bot.tree.command(name="resync", description="Admin: Force re-register/refresh slash commands")
async def resync(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("Unauthorized: admin only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        synced = await bot.tree.sync()
        await interaction.followup.send(
            embed=discord.Embed(description=f"Resynced **{len(synced)}** slash commands globally.", color=discord.Color.green()), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Resync failed: {e}", ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    logger.exception(f"Command error: {error}")
    if not interaction.response.is_done():
        try:
            await interaction.response.send_message(
                embed=discord.Embed(description="⚠️ An unexpected error occurred.", color=discord.Color.red()), ephemeral=True)
        except Exception:
            pass


# ----------------- SLASH COMMANDS (USERS) ----------------- #

@bot.tree.command(name="help", description="List all available Notro Cloud commands")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="Notro Cloud • Command List", color=discord.Color.from_rgb(88, 101, 242))
    embed.add_field(name="User Commands", value="""
`/create` - Deploy a standard VPS instance
`/list` - List your VPS instances
`/vps-info` - Detailed info about a VPS
`/start` - Start your stopped VPS
`/stop` - Stop your running VPS
`/restart` - Restart your VPS
`/ssh` - Regenerate your SSH access link
`/reinstall` - Reinstall and wipe your VPS clean
`/delete` - Permanently delete a VPS
`/install` - Install software inside your VPS (docker, python, node, nginx, redis, postgresql, ...)
`/logs` - View recent container logs
`/about` - Show Notro Cloud information
`/ping` - Check bot latency
`/help` - Show this command list
    """, inline=False)

    if is_admin(interaction.user):
        embed.add_field(name="Admin Commands", value="""
`/admin-create` - Deploy custom VPS with RAM, CPU, Disk, nameservers & Expiration
`/admin-list` - View all global VPS instances
`/admin-users` - View user statistics
`/admin-stats` - View host hardware stats
`/admin-vps-info` - Get details on a specific user's VPS
`/admin-logs` - View logs of a specific user's VPS
`/admin-stop-all` - Stop every running VPS
`/admin-manage` - Start/stop/restart a user's VPS
`/admin-del-user` - Force delete a user's VPS
`/admin-ban` - Ban a user from creating VPS
`/admin-unban` - Unban a user
`/resync` - Force-refresh slash commands
        """, inline=False)
    embed.set_footer(text=WATERMARK)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="list", description="List your VPS instances")
async def user_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    vps_list = get_user_vps(interaction.user.id)
    if not vps_list:
        await interaction.followup.send("You have no VPS instances.", ephemeral=True)
        return
    embed = discord.Embed(title="Your VPS Instances", color=discord.Color.blue())
    for vps in vps_list:
        if vps['status'] == "running":
            emoji = "🟢"
        elif vps['status'] == "stopped":
            emoji = "🔴"
        else:
            emoji = "⚪"
        exp = vps['expires_at'] or "Never"
        embed.add_field(
            name=f"{emoji} {vps['container_name']}",
            value=f"ID: `{vps['container_id']}` | OS: {vps['os_type']} | Status: {vps['status']} | Expires: `{exp}`",
            inline=False)
    embed.set_footer(text=WATERMARK)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="vps-info", description="Get detailed information about a VPS")
@app_commands.describe(vps_identifier="VPS ID or Name (optional)")
async def user_vps_info(interaction: discord.Interaction, vps_identifier: str = ""):
    vps = get_vps_by_identifier(interaction.user.id, vps_identifier)
    if not vps:
        await interaction.response.send_message(
            embed=discord.Embed(description="VPS not found.", color=discord.Color.red()), ephemeral=True)
        return
    embed = discord.Embed(title=f"VPS Info: {vps['container_name']}", color=discord.Color.blue())
    embed.add_field(name="Container ID", value=f"`{vps['container_id']}`", inline=True)
    embed.add_field(name="OS", value=get_os_details(vps['os_type'])[0], inline=True)
    embed.add_field(name="Status", value=vps['status'], inline=True)
    embed.add_field(name="Resources", value=f"RAM: {vps['ram']} | CPU: {vps['cpu']} | Disk: {vps['disk']}", inline=False)
    embed.add_field(name="Hostname", value=vps['hostname'], inline=True)
    embed.add_field(name="Suspended", value="Yes" if vps['suspended'] else "No", inline=True)
    embed.add_field(name="Expires At", value=vps['expires_at'] or "Never (Permanent)", inline=True)
    if vps['ssh_command']:
        embed.add_field(name="SSH Link", value=f"[Connect]({vps['ssh_command']})", inline=False)
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
        await interaction.followup.send(
            embed=discord.Embed(description="VPS not found.", color=discord.Color.red()), ephemeral=True)
        return

    old_container_id = vps['container_id']
    await async_docker_stop(old_container_id)
    await async_docker_rm(old_container_id)
    delete_vps(old_container_id)

    await create_vps(interaction, os_type, ram=vps['ram'], cpu=vps['cpu'], disk=vps['disk'],
                     duration="never", target_user=interaction.user)


@bot.tree.command(name="delete", description="Permanently delete one of your VPS instances")
@app_commands.describe(vps_identifier="VPS ID or Name (optional)")
async def user_delete(interaction: discord.Interaction, vps_identifier: str = ""):
    await interaction.response.defer(ephemeral=True)
    vps = get_vps_by_identifier(interaction.user.id, vps_identifier)
    if not vps:
        await interaction.followup.send(
            embed=discord.Embed(description="No VPS found.", color=discord.Color.red()), ephemeral=True)
        return
    await async_docker_stop(vps['container_id'])
    removed = await async_docker_rm(vps['container_id'])
    if removed:
        delete_vps(vps['container_id'])
        await interaction.followup.send(
            embed=discord.Embed(description=f"VPS `{vps['container_name']}` deleted.", color=discord.Color.green()), ephemeral=True)
    else:
        await interaction.followup.send(
            embed=discord.Embed(description="Failed to delete VPS container.", color=discord.Color.red()), ephemeral=True)


@bot.tree.command(name="install", description="Install software inside your VPS (docker, python, node, nginx, redis, postgresql, ...)")
@app_commands.describe(
    vps_identifier="VPS ID or Name (optional)",
    package="Software to install (preset name or raw apt package)",
    target_user="Target user (admin only)",
)
async def install_cmd(interaction: discord.Interaction, vps_identifier: str = "", package: str = "docker", target_user: discord.User = None):
    await interaction.response.defer(ephemeral=True)

    user = target_user if target_user else interaction.user
    if target_user is not None and not is_admin(interaction.user):
        await interaction.followup.send("Only admins can install for other users.", ephemeral=True)
        return

    vps = get_vps_by_identifier(user.id, vps_identifier)
    if not vps:
        await interaction.followup.send("No VPS found.", ephemeral=True)
        return
    if vps['status'] != "running":
        await interaction.followup.send("VPS must be running to install software.", ephemeral=True)
        return

    pkg_lower = package.lower().strip()
    if pkg_lower in INSTALL_PRESETS:
        script = INSTALL_PRESETS[pkg_lower]
    else:
        script = f"apt-get update && apt-get install -y {package}"

    await interaction.followup.send(f"Installing `{package}` on `{vps['container_name']}`...", ephemeral=True)

    stdout, stderr, rc = await async_docker_exec(vps['container_id'], script, timeout=300.0)
    if rc == 0:
        await interaction.followup.send(
            embed=discord.Embed(description=f"✅ `{package}` installed on `{vps['container_name']}`.", color=discord.Color.green()), ephemeral=True)
    elif rc == "timeout":
        await interaction.followup.send(f"❌ Install timed out for `{package}`.", ephemeral=True)
    else:
        err_tail = (stderr or stdout or "")[-600:]
        await interaction.followup.send(
            embed=discord.Embed(description=f"❌ Install failed for `{package}`:\n```\n{err_tail}\n```", color=discord.Color.red()), ephemeral=True)


@bot.tree.command(name="logs", description="View recent logs for your VPS")
@app_commands.describe(vps_identifier="VPS ID or Name", lines="Lines (default 50)")
async def user_logs(interaction: discord.Interaction, vps_identifier: str = "", lines: int = 50):
    vps = get_vps_by_identifier(interaction.user.id, vps_identifier)
    if not vps:
        await interaction.response.send_message(
            embed=discord.Embed(description="VPS not found.", color=discord.Color.red()), ephemeral=True)
        return
    logs = get_logs(vps['container_id'], lines)
    embed = discord.Embed(title=f"Logs for {vps['container_name']}", description=f"```{logs}```", color=discord.Color.blue())
    embed.set_footer(text=WATERMARK)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="about", description="Show Notro Cloud information")
async def about(interaction: discord.Interaction):
    embed = discord.Embed(
        title="☁️ Notro Cloud • About",
        description="A powerful, fast, and user-friendly Discord bot for managing 24/7 VPS servers.\n"
                    "Designed with speed, stability, and simplicity in mind.",
        color=discord.Color.from_rgb(88, 101, 242)
    )
    embed.add_field(name="📌 Details",
                    value=f"➜ **Name:** Notro Cloud\n➜ **Support:** {SUPPORT_DISCORD_LINK}\n➜ **Status:** 🟢 Online & Active 24/7", inline=False)
    embed.set_footer(text=WATERMARK)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ping", description="Check bot latency / responsiveness")
async def ping(interaction: discord.Interaction):
    lat = round(bot.latency * 1000) if bot.latency else 0
    embed = discord.Embed(title="🏓 Pong", description=f"WebSocket latency: `{lat}ms`", color=discord.Color.green())
    embed.set_footer(text=WATERMARK)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ----------------- SLASH COMMANDS (ADMIN) ----------------- #

@bot.tree.command(name="admin-create", description="Admin: Custom VPS deployment with RAM/CPU/Disk, nameservers and expiration")
@app_commands.describe(
    target_user="Target user receiving the VPS",
    os_type="Operating system",
    ram="RAM (Docker enforced, e.g. 16g = real 16 GB)",
    cpu="CPU cores (100% = 1 core, 300% = 3, 500% = 5, 800% = 8, 1000% = 10)",
    disk="Disk quota (metadata, e.g. 30G, 1000G)",
    duration="Duration before auto-deletion (e.g. 1d, 3d, 6, never)",
    nameserver1="Primary DNS (IP or hostname, e.g. 1.1.1.1 or ns1.example.fun)",
    nameserver2="Secondary DNS (IP or hostname)",
)
@app_commands.choices(os_type=[
    app_commands.Choice(name="Ubuntu 24.04", value="ubuntu-24.04"),
    app_commands.Choice(name="Ubuntu 22.04", value="ubuntu-22.04"),
    app_commands.Choice(name="Debian 11", value="debian-11")
])
@app_commands.choices(ram=[
    app_commands.Choice(name="2 GB", value="2g"),
    app_commands.Choice(name="4 GB", value="4g"),
    app_commands.Choice(name="6 GB", value="6g"),
    app_commands.Choice(name="8 GB", value="8g"),
    app_commands.Choice(name="10 GB", value="10g"),
    app_commands.Choice(name="12 GB", value="12g"),
    app_commands.Choice(name="14 GB", value="14g"),
    app_commands.Choice(name="16 GB", value="16g"),
    app_commands.Choice(name="24 GB", value="24g"),
])
@app_commands.choices(cpu=[
    app_commands.Choice(name="100%", value="1"),
    app_commands.Choice(name="300%", value="3"),
    app_commands.Choice(name="500%", value="5"),
    app_commands.Choice(name="800%", value="8"),
    app_commands.Choice(name="1000%", value="10"),
])
@app_commands.choices(disk=[
    app_commands.Choice(name="30 GB", value="30G"),
    app_commands.Choice(name="80 GB", value="80G"),
    app_commands.Choice(name="100 GB", value="100G"),
    app_commands.Choice(name="150 GB", value="150G"),
    app_commands.Choice(name="200 GB", value="200G"),
    app_commands.Choice(name="300 GB", value="300G"),
    app_commands.Choice(name="500 GB", value="500G"),
    app_commands.Choice(name="800 GB", value="800G"),
    app_commands.Choice(name="1000 GB", value="1000G"),
])
async def admin_create(
    interaction: discord.Interaction,
    target_user: discord.User,
    os_type: str,
    ram: str = "2g",
    cpu: str = "1",
    disk: str = "30G",
    duration: str = "never",
    nameserver1: str = "",
    nameserver2: str = ""
):
    if not is_admin(interaction.user):
        await interaction.response.send_message("Unauthorized: admin only.", ephemeral=True)
        return
    ns_list = [n for n in (nameserver1, nameserver2) if (n or "").strip()]
    await create_vps(interaction=interaction, os_type=os_type, ram=ram, cpu=cpu, disk=disk,
                     duration=duration, target_user=target_user, nameservers=ns_list)


@bot.tree.command(name="admin-list", description="Admin: List all VPS instances")
async def admin_list(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("Unauthorized: admin only.", ephemeral=True)
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT u.username, v.container_id, v.container_name, v.os_type, v.status, v.ram, v.expires_at FROM vps v JOIN users u ON v.user_id = u.user_id ORDER BY v.created_at DESC')
    all_vps = cursor.fetchall()
    conn.close()
    embed = discord.Embed(title="Global VPS Instances", color=discord.Color.blue())
    for row in all_vps[:25]:
        emoji = "🟢" if row['status'] == "running" else "🔴"
        exp = row['expires_at'] or "Never"
        embed.add_field(name=f"{emoji} {row['username']} - {row['container_name']}",
                        value=f"ID: `{row['container_id']}` | RAM: {row['ram']} | Exp: `{exp}`", inline=False)
    embed.set_footer(text=WATERMARK)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="admin-users", description="Admin: View active users list")
async def admin_users(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("Unauthorized: admin only.", ephemeral=True)
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT u.username, COUNT(v.id) as vps_count FROM users u JOIN vps v ON u.user_id = v.user_id GROUP BY u.user_id')
    users_data = cursor.fetchall()
    conn.close()
    embed = discord.Embed(title="Notro Cloud Active Users", color=discord.Color.blue())
    for row in users_data[:25]:
        embed.add_field(name=f"👤 {row['username']}", value=f"Active VPS Count: {row['vps_count']}", inline=False)
    embed.set_footer(text=WATERMARK)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="admin-stats", description="Admin: View basic host hardware stats")
async def admin_stats(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("Unauthorized: admin only.", ephemeral=True)
        return
    if client is None:
        await interaction.response.send_message("Docker daemon is not connected on the host.", ephemeral=True)
        return
    try:
        host_info = client.info()
        containers = host_info['Containers']
        containers_running = host_info['ContainersRunning']
        ncpu = host_info['NCPU']
        mem_total = host_info['MemTotal'] / (1024 ** 3)
        embed = discord.Embed(title="Host Machine Statistics", color=discord.Color.purple())
        embed.add_field(name="Containers", value=f"Total: {containers}\nRunning: {containers_running}")
        embed.add_field(name="Hardware", value=f"CPUs: {ncpu}\nTotal RAM: {mem_total:.2f} GB")
        embed.set_footer(text=WATERMARK)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Failed to fetch stats: {e}", ephemeral=True)


@bot.tree.command(name="admin-vps-info", description="Admin: Get deep details on a user's VPS")
async def admin_vps_info(interaction: discord.Interaction, target_user: discord.User):
    if not is_admin(interaction.user):
        await interaction.response.send_message("Unauthorized: admin only.", ephemeral=True)
        return
    vps_list = get_user_vps(target_user.id)
    if not vps_list:
        await interaction.response.send_message(
            embed=discord.Embed(description="User has no active VPS.", color=discord.Color.red()), ephemeral=True)
        return
    embed = discord.Embed(title=f"VPS Info for {target_user.name}", color=discord.Color.blue())
    for vps in vps_list:
        embed.add_field(name=vps['container_name'],
                        value=f"ID: `{vps['container_id']}`\nRAM: {vps['ram']} | CPU: {vps['cpu']}\n"
                              f"OS: {vps['os_type']}\nStatus: {vps['status']}\nExpires: {vps['expires_at'] or 'Never'}",
                        inline=False)
    embed.set_footer(text=WATERMARK)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="admin-logs", description="Admin: View logs for any user's VPS")
async def admin_logs(interaction: discord.Interaction, target_user: discord.User, lines: int = 50):
    if not is_admin(interaction.user):
        await interaction.response.send_message("Unauthorized: admin only.", ephemeral=True)
        return
    vps_list = get_user_vps(target_user.id)
    if not vps_list:
        await interaction.response.send_message("No VPS found for user.", ephemeral=True)
        return
    logs = get_logs(vps_list[0]['container_id'], lines)
    embed = discord.Embed(title=f"Logs for {vps_list[0]['container_name']}", description=f"```{logs}```", color=discord.Color.orange())
    embed.set_footer(text=WATERMARK)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="admin-stop-all", description="Admin: Stop every running VPS")
async def admin_stop_all(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("Unauthorized: admin only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT container_id, container_name FROM vps WHERE status = 'running'")
    rows = cursor.fetchall()
    conn.close()
    stopped = 0
    for row in rows:
        if await async_docker_stop(row['container_id']):
            update_vps_status(row['container_id'], "stopped")
            stopped += 1
    await interaction.followup.send(
        embed=discord.Embed(description=f"Stopped {stopped}/{len(rows)} running VPS instances.", color=discord.Color.orange()), ephemeral=True)


@bot.tree.command(name="admin-manage", description="Admin: Start/stop/restart a user's VPS")
@app_commands.describe(
    target_user="Target user",
    vps_identifier="VPS ID or Name (optional)",
    action="Action to perform",
)
@app_commands.choices(action=[
    app_commands.Choice(name="start", value="start"),
    app_commands.Choice(name="stop", value="stop"),
    app_commands.Choice(name="restart", value="restart"),
])
async def admin_manage(interaction: discord.Interaction, target_user: discord.User, vps_identifier: str = "", action: str = "start"):
    if not is_admin(interaction.user):
        await interaction.response.send_message("Unauthorized: admin only.", ephemeral=True)
        return
    await manage_vps(interaction, vps_identifier, action, target_user=target_user)


@bot.tree.command(name="admin-del-user", description="Admin: Force delete a user's VPS")
async def admin_del_user(interaction: discord.Interaction, target_user: discord.User):
    if not is_admin(interaction.user):
        await interaction.response.send_message("Unauthorized: admin only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    vps_list = get_user_vps(target_user.id)
    if not vps_list:
        await interaction.followup.send("User has no active VPS.", ephemeral=True)
        return
    for vps in vps_list:
        await async_docker_stop(vps['container_id'])
        await async_docker_rm(vps['container_id'])
        delete_vps(vps['container_id'])
    await interaction.followup.send(
        embed=discord.Embed(description=f"Deleted all VPS instances for {target_user.mention}.", color=discord.Color.green()), ephemeral=True)


@bot.tree.command(name="admin-ban", description="Admin: Ban a user from using the bot")
async def admin_ban(interaction: discord.Interaction, target_user: discord.User):
    if not is_admin(interaction.user):
        await interaction.response.send_message("Unauthorized: admin only.", ephemeral=True)
        return
    add_ban(target_user.id)
    await interaction.response.send_message(
        embed=discord.Embed(description=f"Banned {target_user.mention} from Notro Cloud.", color=discord.Color.red()), ephemeral=True)


@bot.tree.command(name="admin-unban", description="Admin: Unban a user")
async def admin_unban(interaction: discord.Interaction, target_user: discord.User):
    if not is_admin(interaction.user):
        await interaction.response.send_message("Unauthorized: admin only.", ephemeral=True)
        return
    remove_ban(target_user.id)
    await interaction.response.send_message(
        embed=discord.Embed(description=f"Unbanned {target_user.mention}.", color=discord.Color.green()), ephemeral=True)


# Run bot
if not TOKEN or TOKEN == "your_discord_bot_token_here":
    logger.error("No Discord TOKEN configured in .env. Exiting.")
    sys.exit(1)
bot.run(TOKEN)
