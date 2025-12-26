# ParishStaq Portal

FastAPI web portal for ParishStaq data with username/password + TOTP 2FA authentication.

## Features

- **Authentication**: Username/password with optional TOTP 2FA
- **Dashboard**: Overview of parish data
- **Campus Reports**: View campuses and families
- **Search**: Search individuals by name or email
- **User Management**: CLI tool for managing portal users
- **Campus-based Permissions**: Restrict users to specific campuses

## Quick Start

### 1. Setup

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and edit environment config
cp .env.example .env
nano .env  # Edit DATABASE_URL and SECRET_KEY
```

### 2. Create Admin User

```bash
python manage.py create admin admin@example.com --admin
```

### 3. Run

```bash
# Development (with auto-reload)
./run.sh dev

# Production
./run.sh
```

Visit http://localhost:8000

## User Management

```bash
# Create user
python manage.py create username email@example.com

# Create admin
python manage.py create admin admin@example.com --admin

# Create user with campus restriction
python manage.py create parish_user user@example.com --campuses "83,84,85"

# List users
python manage.py list

# Reset password
python manage.py reset-password username

# Reset 2FA
python manage.py reset-2fa username

# Setup 2FA via CLI
python manage.py setup-2fa username

# Delete user
python manage.py delete username
```

## Production Deployment

### Systemd Service

Create `/etc/systemd/system/parishstaq-portal.service`:

```ini
[Unit]
Description=ParishStaq Portal
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/portal_app/portal
Environment="PATH=/opt/portal_app/portal/venv/bin"
EnvironmentFile=/opt/portal_app/portal/.env
ExecStart=/opt/portal_app/portal/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable parishstaq-portal
sudo systemctl start parishstaq-portal
```

### Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name portal.example.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name portal.example.com;

    ssl_certificate /etc/letsencrypt/live/portal.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/portal.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | (required) | JWT signing key - use `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | sqlite | Database connection string |
| `JWT_EXPIRE_MINUTES` | 60 | Token expiration time |
| `TOTP_ISSUER` | ParishStaq Portal | Name shown in authenticator apps |
| `REQUIRE_2FA` | true | Require 2FA verification for all users |

## Integration with ParishStaq Mirror

The portal reads from the same database as `parishstaq_mirror.py`. Make sure:

1. Both use the same `DATABASE_URL`
2. Run `parishstaq_mirror.py` to sync data before using the portal
3. The `mirror_database.py` file is accessible to the portal

## Security Notes

- Always use HTTPS in production
- Generate a strong `SECRET_KEY`
- Enable 2FA for all users
- Use campus-based permissions to limit access
- Regularly rotate the secret key
