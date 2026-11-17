#!/bin/bash
# One-shot install of RPP in /opt/RPP as a systemd service
set -e

sudo apt update
sudo apt install -y git python3-venv

if [ ! -d /opt/RPP ]; then
    sudo git clone https://github.com/MichielBruijn/Resin-Print-Portal.git /opt/RPP
fi
cd /opt/RPP

sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt

sudo mkdir -p uploads
sudo mkdir -p data
sudo touch printer_ip.txt
sudo chown -R www-data:www-data uploads data printer_ip.txt
sudo chmod -R 775 uploads printer_ip.txt
sudo chmod 700 data

sudo cp rpp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rpp
