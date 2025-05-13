#!/bin/sh -e

# -- Usage --
usage () {
  printf '%s\n' \
  "Usage: $0 [-n <name>] [-i <image>] [-t <tag>] [-c] [<credentials-file>]" \
  "  -n <name>: name of the container (default: mouse)" \
  "  -i <image>: name of the image (default: ghcr.io/andykluger/mouse-ran-down)" \
  "  -t <tag>: tag of the image (default: main)" \
  "  -c: remove any dangling images after starting the container" \
  "  <credentials-file>: path to credentials.nt (default: ./credentials.nt)"
}
if [ "$1" = -h ] || [ "$1" = --help ]; then
  usage
  exit
fi

# -- Parse arguments --
while getopts ":n:i:t:c" opt; do
  case $opt in
    n) name=$OPTARG ;;
    i) image=$OPTARG ;;
    t) tag=$OPTARG ;;
    c) clean=true ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      usage
      exit 1
      ;;
    :)
      echo "Option -$OPTARG requires an argument." >&2
      usage
      exit 1
      ;;
  esac
done
shift $((OPTIND - 1))
name=${name:-mouse}
image=${image:-ghcr.io/andykluger/mouse-ran-down}
tag=${tag:-main}
clean=${clean:-false}
credentials=${1:-$PWD/credentials.nt}
credentials=$(realpath "$credentials")

# -- Pull, stop, and run the container --
podman pull "${image}:${tag}"
podman stop "$name" || true
podman run --name "$name" --rm -d -v "$credentials":/app/credentials.nt:ro "${image}:${tag}"

# -- Show logs --
podman ps -a
podman logs "$name"

# -- Clean up --
if [ "$clean" = true ]; then
  podman image prune -f
fi
