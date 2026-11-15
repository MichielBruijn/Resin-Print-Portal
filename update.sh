#!/bin/bash
# Update RPP to the latest version. Restarts the service: an upload to the
# printer that is in progress is aborted (a running print is not affected).
set -e
cd /opt/RPP/

# printer_ip.txt used to be tracked by git; keep the local setting across the pull
cp -p printer_ip.txt /tmp/rpp_printer_ip.txt 2>/dev/null || true

sudo git reset --hard HEAD
sudo git pull

if [ -s /tmp/rpp_printer_ip.txt ] && [ ! -s printer_ip.txt ]; then
    sudo cp /tmp/rpp_printer_ip.txt printer_ip.txt
fi
rm -f /tmp/rpp_printer_ip.txt

sudo venv/bin/pip install -q -r requirements.txt

sudo mkdir -p uploads
sudo touch printer_ip.txt
sudo chown -R www-data:www-data uploads printer_ip.txt
sudo chmod -R 775 uploads printer_ip.txt

sudo systemctl restart rpp
