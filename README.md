# Mouse Ran Down

This is a *tiny* Telegram bot for personal use.

Nothing to see here.

## Notes:

Make container image with:

```console
$ ./mk/ctnr.sh
```

Run container with:

```console
$ podman run --rm -d -v ./credentials.py:/app/credentials.py:ro quay.io/andykluger/mouse-ran-down
```

View logs with:

```console
$ podman exec -it CONTAINER tail -F /app/logs/app/current
```
