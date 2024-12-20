# Mouse Ran Down

This is a *tiny* Telegram bot for personal use.

Nothing to see here.

## Notes:

Make container image with `./mk/ctnr.sh`:

```console
$ ./mk/ctnr.sh -h
Build a container image for Mouse Ran Down
Usage: ./mk/ctnr.sh [<image>]
  <image> defaults to quay.io/andykluger/mouse-ran-down
```

Run the container from a local image `quay.io/andykluger/mouse-ran-down` with:

```console
$ podman run --rm -d -v ./credentials.py:/app/credentials.py:ro quay.io/andykluger/mouse-ran-down
```

or from an image pushed to a registry, with `./start/podman.sh`:

```console
$ ./start/podman.sh -h
Usage: ./start/podman.sh [-n <name>] [-i <image>] [-t <tag>] [-c] [<credentials-file>]
  -n <name>: name of the container (default: mouse)
  -i <image>: name of the image (default: quay.io/andykluger/mouse-ran-down)
  -t <tag>: tag of the image (default: latest)
  -c: remove any dangling images after starting the container
  <credentials-file>: path to credentials.py (default: ./credentials.py)
```

View logs with:

```console
$ podman logs CONTAINER_NAME
```
