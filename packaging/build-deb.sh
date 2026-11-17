#!/bin/bash
# Build dist/resin-print-portal_<version>_all.deb
# Version: argument, else the latest tag (v0.0.9 -> 0.0.9; commits after it -> 0.0.9+git3.gabc1234)
set -euo pipefail
cd "$(dirname "$0")/.."
PKG=resin-print-portal
if [ $# -ge 1 ]; then
    VERSION=$1
else
    git fetch -q --tags 2>/dev/null || true
    d=$(git describe --tags --long)            # v0.0.9-3-gabc1234
    tag=${d%-*-g*}; rest=${d#"$tag"-}; n=${rest%%-*}
    VERSION=${tag#v}
    [ "$n" != 0 ] && VERSION="$VERSION+git$n.${rest#*-}"
fi

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
APP=$STAGE/usr/share/$PKG
install -d "$APP" "$STAGE/DEBIAN" "$STAGE/usr/lib/systemd/system" "$STAGE/etc/default" "$STAGE/usr/share/doc/$PKG"
install -m 644 rpp.py cassini.py saturn_printer.py simple_http_server.py simple_mqtt_server.py \
    goo_file.py preview.py mqtt_publish.py "$APP/"
cp -r static templates "$APP/"
install -m 644 packaging/debian/$PKG.service "$STAGE/usr/lib/systemd/system/"
install -m 644 packaging/debian/default "$STAGE/etc/default/$PKG"
install -m 644 README.md "$STAGE/usr/share/doc/$PKG/"
install -m 644 LICENSE "$STAGE/usr/share/doc/$PKG/copyright"
install -m 755 packaging/debian/postinst packaging/debian/prerm packaging/debian/postrm "$STAGE/DEBIAN/"
echo "/etc/default/$PKG" > "$STAGE/DEBIAN/conffiles"
chmod 755 "$STAGE" "$STAGE/etc" "$STAGE/etc/default"
find "$STAGE/usr" -type d -exec chmod 755 {} +
find "$STAGE/usr" -type f ! -path '*/DEBIAN/*' -exec chmod 644 {} +

SIZE=$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)
sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$SIZE/" packaging/debian/control.in > "$STAGE/DEBIAN/control"

mkdir -p dist
OUT=dist/${PKG}_${VERSION}_all.deb
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$OUT" >/dev/null
echo "$OUT"
