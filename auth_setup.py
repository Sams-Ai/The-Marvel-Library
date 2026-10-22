#!/usr/bin/env python3
"""Interactively create a private local .env.local; never prints credentials."""
import getpass,hashlib,secrets
from pathlib import Path
p=Path(__file__).resolve().parent/'.env.local'
if p.exists():raise SystemExit('.env.local already exists; refusing to overwrite')
user=input('Local operator username [operator]: ').strip() or 'operator'
pw=getpass.getpass('Choose local operator password: ')
if len(pw)<14:raise SystemExit('Use at least 14 characters')
salt=secrets.token_hex(16);ph=salt+'$'+hashlib.pbkdf2_hmac('sha256',pw.encode(),bytes.fromhex(salt),310000).hex(); key=secrets.token_urlsafe(48)
text=f'WEB_USER={user}\nWEB_PASSWORD_HASH={ph}\nWEB_SESSION_KEY={key}\nWEB_HOST=127.0.0.1\nWEB_PORT=8080\n'
p.write_text(text);p.chmod(0o600);print('Created .env.local with mode 0600. Keep it private; it is gitignored.')
