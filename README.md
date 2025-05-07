# Mouse Ran Down

[![Format and Lint](https://github.com/AndydeCleyre/mouse-ran-down/actions/workflows/fmt_lint.yml/badge.svg)](https://github.com/AndydeCleyre/mouse-ran-down/actions/workflows/fmt_lint.yml)
[![Build and push to quay.io](https://github.com/AndydeCleyre/mouse-ran-down/actions/workflows/ctnr.yml/badge.svg)](https://quay.io/andykluger/mouse-ran-down)
[![Update requirements](https://github.com/AndydeCleyre/mouse-ran-down/actions/workflows/reqs.yml/badge.svg?branch=develop)](https://github.com/AndydeCleyre/mouse-ran-down/actions/workflows/reqs.yml)
[![Publish to PyPI](https://github.com/AndydeCleyre/mouse-ran-down/actions/workflows/pypi.yml/badge.svg)](https://pypi.org/project/mouse_ran_down/)

This is a Telegram bot.

<img src="https://github.com/user-attachments/assets/9d68a581-f123-4ffb-aa1e-f65b99063eca" alt="playful image of mouse with clock" width="200"/>

When added to a group, any shared public
X/Reddit/Instagram/TikTok/Bluesky/YouTube/Vimeo/SoundCloud/Bandcamp/Mastodon
posts with media will be replied to with the media
uploaded directly into the chat, along with a preview image.

You can also send links as direct (or forwarded) messages to the bot.

If you have Telegram Premium or a Business account,
you can add the bot to any or all of your one-on-one chats, as well.
In that case, it will look to the other party as if you personally sent the media.

Normally, entire accounts, playlists, and albums won't be loaded.
You can insist that the bot try to load a link by including
the bot's name in the same message (e.g. `@MouseRanDownBot`).

Currently running at [@MouseRanDownBot](https://t.me/MouseRanDownBot).

I do not guarantee any level of service or privacy, so I encourage you to run it yourself.

## Credentials

Copy `credentials.example.nt` to `credentials.nt` and insert at least a Telegram bot token.

Comment out, delete, or use empty values for any unused fields.

### Telegram

Use Telegram's [@BotFather](https://t.me/BotFather) to create a bot and get its token.

Ensure you give it permission to read group messages by disabling privacy mode.

### Cookies

The optional cookies entry helps if using the bot for reddit video links,
and age-restricted content and such on other platforms.
You can get the cookie content in the right format with `yt-dlp`'s
`--cookies` and `--cookies-from-browser` options,
or a browser extension like [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)
(I can't vouch for the security of this or any extension).

## Installing directly

You can use `pip`/`pipx`/`uv`/`zpy` etc. to install the package from PyPI, e.g.:

```console
$ uv tool install mouse-ran-down
$ uv tool install 'mouse-ran-down[sentry]'  # for Sentry/GlitchTip integration
```

To run it effectively you'll need to install `ffmpeg` and `mailcap`,
and you might benefit from installing `atomicparsley`.

Run `mouse-ran-down --help` for usage.

You may prefer to just use the container image from my registry (see below).

## Build the container image

Make container image with `./mk/ctnr.sh`:

```console
$ ./mk/ctnr.sh -h
Build a container image for Mouse Ran Down
Usage: ./mk/ctnr.sh [<image>]
  <image> defaults to quay.io/andykluger/mouse-ran-down
```

## Run the container

If doing any of these for a rootless container on a Systemd-using server,
don't forget to run this once:

```console
$ loginctl enable-linger
```

Otherwise podman will kill the container on logout.

### From a local image

Run the container from a local image `quay.io/andykluger/mouse-ran-down` with:

```console
$ podman run --rm -d -v ./credentials.nt:/app/credentials.nt:ro quay.io/andykluger/mouse-ran-down
```

### From an image pushed to a registry

`./start/podman.sh`:

```console
$ ./start/podman.sh -h
Usage: ./start/podman.sh [-n <name>] [-i <image>] [-t <tag>] [-c] [<credentials-file>]
  -n <name>: name of the container (default: mouse)
  -i <image>: name of the image (default: quay.io/andykluger/mouse-ran-down)
  -t <tag>: tag of the image (default: main)
  -c: remove any dangling images after starting the container
  <credentials-file>: path to credentials.nt (default: ./credentials.nt)
```

### As an auto-updating Systemd service, pulling from a registry

Or you could write an auto-update-friendly quadlet systemd service at
`~/.config/containers/systemd/mouse.container`, changing the values as you like:

```ini
[Container]
AutoUpdate=registry
ContainerName=mouse
Image=quay.io/andykluger/mouse-ran-down:main
Volume=%h/mouse-ran-down/credentials.nt:/app/credentials.nt:ro

[Service]
Restart=always
TimeoutStartSec=120

[Install]
WantedBy=default.target
```

Ensure the service is discovered/generated, and start it:

```console
$ systemctl --user daemon-reload
$ systemctl --user start mouse
```

Ensure the auto-updating timer is enabled:

```console
$ systemctl --user enable --now podman-auto-update.timer
```

## View container logs

```console
$ podman logs CONTAINER_NAME  # e.g. mouse
```
