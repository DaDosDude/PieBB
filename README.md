# PieBB

A classic bulletin-board frontend for [PieFed](https://codeberg.org/rimu/pyfedi), styled like phpBB2's subSilver.
It talks to PieFed's API only, so it runs alongside your instance without touching its database.
Requires PieFed 1.8.11 or newer. Private instances work too: guests are sent to the login page.

PieFed topics and feeds become forum categories, communities become forums, posts become topics,
and comments become replies. Replies are shown flat and in order, each quoting the post it answers.

## Features

- **Forum index:** categories with subcategories, topic/post/member counts and a Last Post column.
  Communities sort by latest activity. There's also a flat forum list.
- **Topic lists:** the first page is ordered by latest reply, like phpBB, with phpBB-style paging (First / Last / page numbers / jump to page).
- **Threads:** flat replies with quotes, polls, events, galleries and spoilers. Bare image links display as images.
- **Posting:** new topics (with an optional poll or event, flair and language), crossposting, replies, quick reply,
  edit and delete (polls and events too), preview, a formatting toolbar and image upload.
- **Members:** profile and settings (name, avatar, bio, preferences), your forums, votes, subscriptions, following topics,
  alerts for new topics in a forum, category or feed,
  saved posts, blocking, reports, notifications, private messages, unread topics and mark-as-read.
- **Search:** topics, replies, forums and users, with sorting; search inside a single forum from its page.
- **Moderator CP:** lock, sticky, remove, ban, reports and the mod log, for PieFed community moderators.
  Forum settings for local forums: name, icon, description, options, moderators, flair and banned users.
- **phpBB2 themes:** upload a phpBB2 style zip (e.g. from a phpBB2 styles archive) in the admin panel.
  PieBB reads its `theme_info.cfg` colours, stylesheet and images (folder icons, buttons). Built-in themes:
  subSilver and a dark Charcoal theme. Visitors can pick any installed theme from the footer (admins can turn this off).
- **Admin panel:** for PieFed site admins. Forum settings, a category layout editor (rename, hide, reorder),
  sign-up approvals, reports and the mod log, themes and custom CSS.
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

## Updating

From the PieBB folder:

```sh
./update.sh
```

It switches to the newest release, rebuilds and starts it, and goes back to the previous version if the new one
doesn't come up. Your `.env` and the `piebb-data` volume (settings, themes) are left alone.

To try a build before it's released: `./update.sh --test piebb.tar.gz`. The next plain `./update.sh` removes it
again and returns to the latest release.

## Configuration

Settings live in `.env` (see `.env.example`):

| Variable | Purpose |
|---|---|
| `PIEFED_URL` | Public URL of your PieFed instance (required) |
| `SECRET_KEY` | Random secret for sessions and image-proxy signatures (required) |
| `PIEFED_API_BASE` / `PIEFED_HOST_HEADER` | Reach PieFed directly (e.g. `http://127.0.0.1:8030`) instead of through the proxy |
| `FORUM_TITLE` | Forum name (default: the PieFed site name). Can also be changed in the admin panel |
| `LOGIN_REQUIRED` | `1` = guests only see the login page. Can also be changed in the admin panel |
| `TIMEZONE` | Timezone for dates until the visitor's browser shows them in their own (default `Europe/Amsterdam`) |
| `PIEFED_TIMEOUT` | Seconds to wait for PieFed (default `15`) |
| `SECURE_COOKIES` | `1` (default); `0` only for testing over plain http |
| `GUEST_RATE_LIMIT` | Page loads per minute per IP for guests (default `60`, `0` = off) |
| `IMAGE_PROXY` | `1` (default) = load remote images through PieBB |

Admin settings and uploaded themes are stored in the `piebb-data` volume.
