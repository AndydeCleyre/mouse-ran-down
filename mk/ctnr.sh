#!/bin/sh -e
cd "$(dirname "$0")"/..

ctnr="$(buildah from alpine:edge)"
appdir=/app
image=quay.io/andykluger/mouse-ran-down

buildah run "$ctnr" apk upgrade
buildah run "$ctnr" apk add python3 s6

buildah run "$ctnr" mkdir -p "${appdir}"
buildah copy "$ctnr" main.py "${appdir}"/main.py
buildah copy "$ctnr" requirements.txt "${appdir}"/requirements.txt

buildah run "$ctnr" python -m venv "${appdir}"/.venv
buildah run "$ctnr" "${appdir}"/.venv/bin/pip install -r "${appdir}"/requirements.txt

buildah run "$ctnr" mkdir -p "${appdir}/logs/app" "${appdir}/svcs/app/log"
printf '%s\n' \
  '#!/bin/execlineb -P' 'fdmove -c 2 1' "${appdir}/.venv/bin/python ${appdir}/main.py" \
  | buildah run "$ctnr" sh -c "cat >>${appdir}/svcs/app/run"
printf '%s\n' \
  '#!/bin/execlineb -P' s6-log T s4194304 S41943040 "${appdir}/logs/app" \
  | buildah run "$ctnr" sh -c "cat >>${appdir}/svcs/app/log/run"
buildah run "$ctnr" chmod +x "${appdir}/svcs/app/run" "${appdir}/svcs/app/log/run"

buildah config --cmd "s6-svscan ${appdir}/svcs" "$ctnr"

imageid="$(buildah commit --rm "$ctnr" "$image")"
buildah tag "$imageid:$(date +%Y.%m.%d-%s)"

printf '%s\n' "-- When running container, mount or copy credentials.py into ${appdir}/ --" \
  '-- For example: --' \
  "-- podman run -d -v ./credentials.py:${appdir}/credentials.py:ro $image  --"