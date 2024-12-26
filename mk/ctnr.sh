#!/bin/sh -e
cd "$(dirname "$0")"/..

# -- Usage --
if [ "$1" = -h ] || [ "$1" = --help ]; then
  printf '%s\n' \
  'Build a container image for Mouse Ran Down' \
  "Usage: $0 [<image>]" \
  '  <image> defaults to quay.io/andykluger/mouse-ran-down'
  exit
fi

# -- Variables --
ctnr="$(buildah from docker.io/library/python:3.13-alpine)"
appdir=/app
image=${1:-quay.io/andykluger/mouse-ran-down}

# -- Functions --
RUN () { buildah run --network host "$ctnr" "$@"; }
APPEND () { RUN sh -c "cat >>$1"; }
COPY () { buildah copy "$ctnr" "$@"; }

# -- Distro Packages --
RUN apk upgrade
RUN apk add ffmpeg s6

# -- Copy App --
RUN mkdir -p "${appdir}"
for src in main.py requirements.txt; do
  COPY "$src" "${appdir}/${src}"
done

# -- Python Packages --
RUN python -m venv "${appdir}/.venv"
RUN "${appdir}/.venv/bin/pip" install -r "${appdir}/requirements.txt"

# -- App Services --
RUN mkdir -p "${appdir}/svcs/app/log" "${appdir}/logs/app" "${appdir}/svcs/logtailer"

<<EOF APPEND "${appdir}/svcs/app/run"
#!/bin/execlineb -P
fdmove -c 2 1
${appdir}/.venv/bin/python ${appdir}/main.py
EOF

<<EOF APPEND "${appdir}/svcs/app/log/run"
#!/bin/execlineb -P
s6-log t s4194304 S41943040 ${appdir}/logs/app
EOF

<<EOF APPEND "${appdir}/svcs/logtailer/run"
#!/bin/execlineb -P
tail -F ${appdir}/logs/app/current
EOF

RUN chmod +x "${appdir}/svcs/app/run" "${appdir}/svcs/app/log/run" "${appdir}/svcs/logtailer/run"
buildah config --cmd "s6-svscan ${appdir}/svcs" "$ctnr"

# -- Commit Image --
imageid="$(buildah commit --rm "$ctnr" "$image")"
buildah tag "$imageid" "$image:$(date +%Y.%m.%d-%s)"

# -- Tips --
printf '%s\n' \
  "-- When running container, mount or copy credentials.py into ${appdir}/ --" \
  '-- For example: --' \
  "-- podman run --rm -d -v ./credentials.py:${appdir}/credentials.py:ro $image  --"
