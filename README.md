# IL8 VPS Deploy Bot

IL8 is a Discord VPS management bot by INFINITE LABS. It deploys and manages Docker-based VPS containers and provides SSHX web terminal access.

## Features

- Docker-based VPS deployment
- Ubuntu 22.04 support
- Debian 11 support
- SSHX web terminal access
- VPS start, stop, restart and reinstall
- User and admin VPS management
- Custom resources for admin-created VPS
- SQLite database persistence
- Automatic Docker container management
- Systemd service support

## Requirements

- Ubuntu/Debian Linux VPS or server
- Python 3
- Docker
- Root access
- Discord Bot Token
- Discord User ID for admin access

## Installation

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
git clone https://github.com/nxtinfinite481-png/vps-deploy-bot.git
cd vps-deploy-bot
```

### 3. Configure environment

```bash
cp il8.env .env
nano .env
```

Example:

```env
TOKEN=YOUR_DISCORD_BOT_TOKEN
ADMIN_ID=paste your discord user id
BOT_STATUS_NAME=IL8
WATERMARK=Made by INFINITE
DEFAULT_RAM=2g
DEFAULT_CPU=1
DEFAULT_DISK=10G
VPS_HOSTNAME=infinite-vps
```

### 4. Create the Python Virtual Environment

A project-local virtual environment is used so the bot does not depend on the system Python path of the VPS.

```bash
apt install -y python3-venv
python3 -m venv venv
```

### 5. Install Dependencies

Install all bot dependencies inside the virtual environment:

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
nano /etc/systemd/system/il8.service
```

Paste:

```ini
[Unit]
Description=IL8 VPS Discord Bot
After=network.target docker.service

[Service]
User=root
WorkingDirectory=/root/vps-deploy-bot
ExecStart=/root/vps-deploy-bot/venv/bin/python /root/vps-deploy-bot/bot.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Save the file and run:

```bash
systemctl daemon-reload
systemctl enable il8
systemctl restart il8
```

Check the bot status:

```bash
systemctl status il8 --no-pager
```

View live logs:

```bash
journalctl -u il8 -f
```

If everything is working correctly, the bot should appear online in Discord.

## Default VPS Configuration

| Resource | Default |
|---|---|
| RAM | 2GB |
| CPU | 1 Core |
| Disk | 10GB |
| VPS Limit / User | 1 |
| Total VPS Limit | 50 |
| Hostname | infinite-vps |

## Supported Operating Systems

IL8 currently supports:

- Ubuntu 22.04
- Debian 11

## SSHX Access

IL8 uses SSHX for web-based VPS terminal access.

When a VPS is created, IL8 automatically:

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
/create
/list
/vps-info
/ssh
/start
/stop
/restart
/reinstall
/delete
/help
```

### Admin Commands

```text
/admin-create
/admin-list
/admin-users
/admin-vps-info
/admin-del-user
/admin-stop-all
```

### Additional Commands

```text
/about
/logs
/ping
/admin-manage
/admin-stats
/admin-logs
/admin-ban
/admin-unban
```

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
/admin-create
```

Administrators can manage VPS instances using:

```text
/admin-list
/admin-vps-info
/admin-stop-all
/admin-del-user
```

## Database

IL8 uses SQLite for persistent VPS and user data.

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
journalctl -u il8 -f
```

## Project Files

```text
vps-deploy-bot/
├── bot.py
├── requirements.txt
├── il8.env
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
cd /root/vps-deploy-bot
```

Replace `bot.py` with the latest version.

Then restart the service:

```bash
systemctl restart il8
```

Check the status:

```bash
systemctl status il8 --no-pager
```

## Useful Commands

Stop the bot:

```bash
systemctl stop il8
```

Start the bot:

```bash
systemctl start il8
```

Restart the bot:

```bash
systemctl restart il8
```

Check status:

```bash
systemctl status il8 --no-pager
```

View logs:

```bash
journalctl -u il8 -f
```

## Developer

**IL8**

**Version:** v1.0

**Developer:** INFINITE

**YouTube:** https://www.youtube.com/@infinite8labs

**GitHub:** https://github.com/nxtinfinite481-png

**Discord:** https://discord.gg/pG22dSmAZD

Made by INFINITE
