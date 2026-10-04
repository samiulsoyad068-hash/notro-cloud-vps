# Notro Cloud VPS Deploy Bot

**Notro Cloud** is a Discord VPS management bot. It deploys and manages Docker-based VPS containers and provides SSHX web terminal access.

## Features

- Docker-based VPS deployment
- Ubuntu 24.04, Ubuntu 22.04, and Debian 11 support
- SSHX web terminal access
- VPS start, stop, restart and reinstall
- `/install` command to install software (docker, python, node, nginx, ...) inside your VPS
- User and admin VPS management
- Custom resources for admin-created VPS
- SQLite database persistence
- Automatic Docker container management
- Startup state reconciliation (VPS keep running when the bot is offline)
- Systemd service support

## Requirements

- Ubuntu/Debian Linux VPS or server
- Python 3
- Docker
- Root access
- Discord Bot Token
- Discord User ID for admin access

## Installation

### 0. Invite the bot to your server

Slash commands only appear if the bot is invited **with the `applications.commands` scope**.
Use this URL (replace `YOUR_CLIENT_ID` with your bot's Application/Client ID from the
Discord Developer Portal):

```text
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=274878179392&scope=bot%20applications.commands
```

The `applications.commands` scope is required for `/slash` commands to register.

> Tip: put the bot in a channel/role it can manage, or it must share a server with the
> user running the commands.

### 1. Install Docker

```bash
apt update -y
apt install -y docker.io
systemctl enable docker
systemctl start docker
docker --version
```

### 2. Clone the repository

```bash
git clone https://github.com/samiulsoyad068-hash/notro-cloud-vps.git
cd notro-cloud-vps
```

### 3. Configure environment

```bash
cp notro.env .env
nano .env
```

Example:

```env
TOKEN=YOUR_DISCORD_BOT_TOKEN
ADMIN_ID=123456789, 987654321
BOT_STATUS_NAME=Notro Cloud
WATERMARK=Powered by Notro Cloud
DEFAULT_RAM=2g
DEFAULT_CPU=1
DEFAULT_DISK=10G
VPS_HOSTNAME=notrocloud-vps
DEFAULT_DNS=1.1.1.1, 8.8.8.8
REGION=Bangladesh (BD)
WATERMARK=Powered by Notro Cloud
DEFAULT_RAM=2g
DEFAULT_CPU=1
DEFAULT_DISK=10G
VPS_HOSTNAME=notrocloud-vps
```

### 4. Create the Python Virtual Environment

A project-local virtual environment is used so the bot does not depend on the system Python path of the VPS.

```bash
apt install -y python3-venv
python3 -m venv venv
```

### 5. Install Dependencies

```bash
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

### 6. Test the Bot

Before creating the systemd service, test the bot manually:

```bash
./venv/bin/python bot.py
```

If the bot starts successfully, press:

```text
Ctrl+C
```

to stop the test.

## Systemd Service

Create the systemd service:

```bash
nano /etc/systemd/system/notro.service
```

Paste:

```ini
[Unit]
Description=Notro Cloud VPS Discord Bot
After=network.target docker.service

[Service]
User=root
WorkingDirectory=/root/notro-cloud-vps
ExecStart=/root/notro-cloud-vps/venv/bin/python /root/notro-cloud-vps/bot.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Save the file and run:

```bash
systemctl daemon-reload
systemctl enable notro
systemctl restart notro
```

Check the bot status:

```bash
systemctl status notro --no-pager
```

View live logs:

```bash
journalctl -u notro -f
```

If everything is working correctly, the bot should appear online in Discord.

## Default VPS Configuration

| Resource       | Default |
|---|---|
| RAM           | 2GB      |
| CPU           | 1 Core   |
| Disk          | 10GB     |
| VPS Limit / User | 1    |
| Total VPS Limit   | 60 |
| Hostname      | notrocloud-vps |
| DNS           | 1.1.1.1, 8.8.8.8 |
| Region        | Bangladesh (BD) |

## Supported Operating Systems

Notro Cloud currently supports:

- Ubuntu 24.04
- Ubuntu 22.04
- Debian 11

## SSHX Access

Notro Cloud uses SSHX for web-based VPS terminal access.

When a VPS is created, Notro Cloud automatically:

1. Creates the Docker VPS container
2. Installs the required packages
3. Installs SSHX
4. Starts an SSHX session
5. Generates an SSHX web access link
6. Sends the access link to the user

The `/ssh` command can also be used to generate a new SSHX access link for an existing VPS.

## VPS Resources

Default VPS resources:

```text
RAM: 2GB
CPU: 1 Core
Disk: 10GB
```

Docker memory and CPU limits are applied when the VPS container is created.

## Commands

### User Commands

```text
/create            Deploy a standard VPS instance
/list              List your VPS instances
/vps-info          Detailed information about a VPS
/start             Start your stopped VPS
/stop              Stop your running VPS
/restart           Restart your VPS
/ssh               Regenerate your SSH access link
/reinstall         Reinstall and wipe your VPS clean
/delete            Permanently delete a VPS
/install           Install software inside your VPS
/logs              View recent container logs
/about             Show Notro Cloud information
/ping              Check bot latency
/help              Show this command list
```

### Admin Commands

```text
/admin-create      Deploy a custom VPS (RAM, CPU, Disk, nameserver, duration)
/admin-list        View all global VPS instances
/admin-users       View user statistics
/admin-stats       View host hardware stats
/admin-vps-info    Get details on a specific user's VPS
/admin-logs        View logs of a specific user's VPS
/admin-stop-all    Stop every running VPS
/admin-manage      Start/stop/restart a user's VPS
/admin-del-user    Force delete a user's VPS
/admin-ban         Ban a user from creating a VPS
/admin-unban       Unban a user
```

## The `/install` Command

`/install` lets you install software directly inside your running VPS. Any
recognized preset is installed via `apt`; an unknown name is treated as a raw
`apt-get install` package, so arbitrary packages work too.

**Presets:**

```text
docker        (docker.io + docker-compose-v2)
python        (python3 + pip + venv)
node          (Node.js 20 LTS from NodeSource)
nginx         (NGINX web server)
redis         (Redis server)
postgresql    (PostgreSQL)
java          (default JDK)
git           (Git)
curl          (curl)
```

Usage (admin can target another user with `target_user`):

```text
/install [vps_identifier] [package] [target_user]
```

Examples:

```text
/install            -> installs "docker" into your default VPS
/install myvps nginx
/install 3c3e python
```

## VPS Keep Running When the Bot Is Offline

Each VPS container is created with `--restart=always`, so Docker keeps the
container alive independently of the Discord bot. When the bot starts or
reconnects, it automatically **reconciles** the database state with the real
Docker container states, so a VPS that was running while the bot was offline is
correctly shown as `running` again.

## VPS Management

Users can manage their VPS using:

```text
/start
/stop
/restart
/reinstall
/delete
```

SSHX access can be generated using:

```text
/ssh
```

VPS information can be viewed using:

```text
/vps-info
```

VPS list can be viewed using:

```text
/list
```

## Admin VPS Management

Administrators can create VPS instances with custom resources using:

```text
/admin-create target_user os_type ram cpu disk duration nameserver1 nameserver2
```

Resource **choices** on `/admin-create`:

| Option   | Allowed values |
|---|---|
| RAM    | 2 GB, 4 GB, 8 GB, 16 GB |
| CPU    | 100% (1 core), 300% (3), 500% (5) |
| Disk   | 30 GB, 80 GB, 100 GB, 150 GB, 200 GB, 300 GB, 500 GB, 800 GB, 1000 GB |

`nameserver1` and `nameserver2` are applied to the container via Docker `--dns`.
Each can be a **public IP** (e.g. `1.1.1.1`, `8.8.8.8`) or a hostname, which is
resolved to an IP before being applied. If you leave them blank, the VPS still
gets real public DNS from `DEFAULT_DNS` (Cloudflare `1.1.1.1` + Google `8.8.8.8`
by default) so the VPS has working Internet/DNS out of the box.

RAM and CPU are **real Docker limits** (`--memory` / `--cpus`), so `16 GB` is a
real 16 GB limit and `500%` is 5 real CPU cores. Disk is stored as the configured
quota/metadata (Docker does not enforce a hard disk size per container).

### VPS Region (location)

The VPS egress IP and region are determined by **where you deploy the bot** (the
VPS host). Deploy the bot on a Bangladesh-based VPS and the egress will be a
BD address. `REGION` in `.env` is a display label (defaults to
`Bangladesh (BD)`) shown to users; the actual egress IPv4/IPv6 reported is the
host's **real public IP**.

Administrators can manage VPS instances using:

```text
/admin-list
/admin-vps-info
/admin-stop-all
/admin-manage
/admin-del-user
```

### 24/7 operation

Each VPS container is created with `--restart=always`, so containers keep running
independently of the Discord bot. If the bot (or the PC running it) is offline,
the VPS stays up. Deleting a user's VPS with `/admin-del-user` stops **and removes**
the container, so it stops working immediately.

## Database

Notro Cloud uses SQLite for persistent VPS and user data.

Database file:

```text
bot.db
```

The database is automatically created and maintained by the bot.

## Logs

Bot logs are stored in:

```text
bot.log
```

Systemd logs can be viewed with:

```bash
journalctl -u notro -f
```

## Project Files

```text
notro-cloud-vps/
├── bot.py
├── requirements.txt
├── notro.env
├── .env
├── bot.db
├── bot.log
└── venv/
```

## Requirements

`requirements.txt`:

```text
discord.py>=2.3,<3
docker>=7.0,<8
python-dotenv>=1.0,<2
```

## Updating the Bot

Go to the bot directory:

```bash
cd /root/notro-cloud-vps
```

Replace `bot.py` with the latest version.

Then restart the service:

```bash
systemctl restart notro
```

Check the status:

```bash
systemctl status notro --no-pager
```

## Useful Commands

Stop the bot:

```bash
systemctl stop notro
```

Start the bot:

```bash
systemctl start notro
```

Restart the bot:

```bash
systemctl restart notro
```

Check status:

```bash
systemctl status notro --no-pager
```

View logs:

```bash
journalctl -u notro -f
```

## Troubleshooting

### Slash commands are not showing

1. The bot must be invited with the **`applications.commands`** scope (see "Invite the bot" above).
2. By default `on_ready` syncs **globally**, which can take **up to 1 hour** to appear in every server.
3. For **instant** (a few minutes) command availability, set `GUILD_ID` to your server's ID in `.env`:
   ```env
   GUILD_ID=123456789012345678
   ```
   Get the ID with Developer Mode enabled (right-click the server → Copy ID). Restart the bot afterwards.
4. You can also force a refresh at any time with the admin-only `/resync` command.

### VPS is running but the bot shows it as stopped (or vice-versa)

This is normal if the bot was offline. On every startup/reconnect the bot automatically
**reconciles** its database with the real Docker container states, so statuses correct themselves.

## Developer

**Notro Cloud**

**GitHub:** https://github.com/samiulsoyad068-hash/notro-cloud-vps

**Support:** https://discord.gg/2FJEFTzQ2P

Made by Notro Cloud
