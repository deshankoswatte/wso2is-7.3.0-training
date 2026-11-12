# Dummy SMTP Server for WSO2 IS

A local SMTP server for testing the emails WSO2 Identity Server sends. It never sends mail anywhere. Instead, it shows every email it receives in a web inbox, with HTML emails displayed the way a recipient would see them.

It is a single Python script with no packages to install (Python 3.9+).

## Run

```bash
python3 dummy_smtp.py
```

- SMTP server: `localhost:2525`
- Web inbox: http://localhost:8025

| Option | Default | Description |
|---|---|---|
| `--smtp-port` | `2525` | SMTP port |
| `--web-port` | `8025` | Web inbox port |
| `--host` | `0.0.0.0` | Bind address |
| `--save-dir` | (none) | Also save each email as a `.eml` file in this folder |

## Configure WSO2 IS

In `<IS_HOME>/repository/conf/deployment.toml`:

```toml
[output_adapter.email]
from_address= "noreply@wso2.com"
username= "test"
password= "test"
hostname= "localhost"
port= 2525
enable_start_tls= false
enable_authentication= true
```

Any username and password are accepted. Restart WSO2 IS after changing the file.

> If WSO2 IS runs in Docker, use `hostname= "host.docker.internal"`.

## Features

- **HTML view:** embedded images display, scripts are blocked and links open in a new tab.
- **Text, Links, Headers and Raw views:** the Links view makes confirmation and password-reset links easy to click.
- **Live inbox:** new mail appears within about 2 seconds, with search and unread markers.
- **Actions:** download an email as `.eml`, download attachments, delete one email or clear all.
- **Login details:** each email shows which username the sender logged in with.

## Notes

- Emails are kept in memory only (the newest 500) and are lost when the script stops, unless you use `--save-dir`.
- TLS is not supported, so `enable_start_tls` must be `false`.
- This server is for local testing only. Don't expose it to a network.
