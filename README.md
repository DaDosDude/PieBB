# PieBB

A classic bulletin-board frontend for [PieFed](https://codeberg.org/rimu/pyfedi), styled like phpBB2's subSilver.
It talks to PieFed's API only, so it runs alongside your instance without touching its database.
Tested with PieFed 1.8.x.

PieFed topics and feeds become forum categories, communities become forums, posts become topics,
and comments become replies. Replies are shown flat and in order, each quoting the post it answers.

## Features

- **Forum index:** categories with subcategories, topic/post/member counts and a Last Post column.
  Communities sort by latest activity. There's also a flat forum list.
- **Topic lists:** newest first, with phpBB-style paging (First / Last / page numbers / jump to page).
- **Threads:** flat replies with quotes, polls, events, galleries and spoilers. Bare image links display as images.
- **Posting:** new topics, replies, quick reply, edit and delete, preview, a formatting toolbar and image upload.
- **Members:** votes, subscriptions, reports, notifications, private messages, unread topics and mark-as-read.
- **Moderator CP:** lock, sticky, remove, ban, reports and the mod log, for PieFed community moderators.
- **phpBB2 themes:** upload a phpBB2 style zip (e.g. from a phpBB2 styles archive) in the admin panel.
  PieBB reads its `theme_info.cfg` colours, stylesheet and images (folder icons, buttons). Built-in themes:
  subSilver and a dark Charcoal theme. Visitors can pick any installed theme from the footer (admins can turn this off).
- **Admin panel:** for PieFed site admins. Forum settings, a category layout editor (rename, hide, reorder),
  themes and custom CSS.
- **Privacy:** an image proxy so other instances don't see your visitors' IPs, a strict CSP and CSRF protection.
- **Times:** shown in each visitor's own timezone. The layout works on mobile.

## Quick start (Docker)

```sh
git clone https://github.com/DaDosDude/PieBB.git && cd PieBB
cp .env.example .env
nano .env                      # set PIEFED_URL and SECRET_KEY (openssl rand -hex 32)
docker compose up -d --build
```

PieBB listens on `127.0.0.1:8060`. Put it behind your reverse proxy, for example Caddy:

```
forum.example.com {
    reverse_proxy 127.0.0.1:8060
}
```

Logins use the visitor's existing PieFed account. PieBB has no accounts of its own.

## Configuration

Settings live in `.env` (see `.env.example`):

| Variable | Purpose |
|---|---|
| `PIEFED_URL` | Public URL of your PieFed instance (required) |
| `SECRET_KEY` | Random secret for sessions and image-proxy signatures (required) |
| `PIEFED_API_BASE` / `PIEFED_HOST_HEADER` | Reach PieFed directly (e.g. `http://127.0.0.1:8030`) instead of through the proxy |
| `FORUM_TITLE`, `TIMEZONE` | Defaults. Most of these can also be changed in the admin panel |
| `LOGIN_REQUIRED` | `1` = guests only see the login page |
| `GUEST_RATE_LIMIT` | Page loads per minute per IP for guests (default `60`, `0` = off) |
| `IMAGE_PROXY` | `1` (default) = load remote images through PieBB |

Admin settings and uploaded themes are stored in the `piebb-data` volume.
