# Resin Print Portal (RPP)

A web interface for managing and monitoring Elegoo resin 3D printers over WiFi. Tested on Elegoo Mars 4 Ultra.

Fork of [RPP](https://github.com/jjtronics/RPP) by JJtronics, built on [Cassini](https://github.com/vvuk/cassini) by Vladimir Vukicevic.

## Features

- Upload `.ctb` / `.goo` slice files to the server
- Send files to the printer and start printing in one step
- Real-time print progress (layer counter + progress bar)
- Pause, Resume and Stop a running print
- Printer online/offline status indicator
- Configure printer IP address or hostname via the web UI
- **Mix resin**: pump the platform up and down (no exposure) to stir the resin in the vat

## File overview

### `rpp.py` — Flask web server (main entry point)

The central application. Starts a Flask HTTP server on port 5001 (debug mode off) and exposes the REST API that the browser calls:

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the web interface (`index.html`) |
| `/get-printer-ip` | GET | Returns the currently configured printer IP |
| `/set-printer-ip` | POST | Saves a new printer IP to `printer_ip.txt` |
| `/print-status` | GET | Polls the printer status via Cassini and returns JSON with current layer, total layers, progress %, and online state |
| `/upload` | POST | Saves an uploaded `.ctb`/`.goo` file to the `uploads/` folder (other extensions are rejected) |
| `/files` | GET | Lists all files in `uploads/` with their size in MB |
| `/print-file` | POST | Starts a background thread that uploads the selected file to the printer and immediately starts the print |
| `/progress/<filename>` | GET | Returns upload/transfer progress for a running print job (0–100), plus `error` if sending failed |
| `/mix` | POST | Generates a resin-mixing file (`cycles`, `stroke` in mm) and sends it to the printer |
| `/stop-print` | POST | Sends a stop command to the printer via Cassini |
| `/pause-print` | POST | Sends a pause command to the printer via Cassini |
| `/resume-print` | POST | Sends a resume command to the printer via Cassini |
| `/delete-file` | POST | Deletes a file from the `uploads/` folder |

Print jobs run in a background thread with a mutex (`print_lock`) so only one job runs at a time. Filenames sent by the browser are validated, so `/print-file` and `/delete-file` can only touch files inside `uploads/`.

---

### `cassini.py` — Printer command-line interface

A CLI tool (also imported by `rpp.py`) that handles all communication with the printer. Originally written by Vladimir Vukicevic.

**How it works:**

1. Sends a UDP broadcast (`M99999`) on port 3000 to discover printers on the network.
2. Instructs the printer to connect to a temporary local MQTT server (`M66666 <port>`).
3. Over that MQTT connection, sends commands and receives status updates.
4. For file uploads, also starts a temporary HTTP server so the printer can pull the file by URL.

**Available CLI commands:**

| Command | Description |
|---|---|
| `status` | Show current machine status, print progress, and layer count |
| `status-full` | Show the full raw JSON response from the printer |
| `watch` | Continuously poll and display print progress |
| `upload <file>` | Upload a `.ctb`/`.goo` file to the printer |
| `upload --start-printing <file>` | Upload and immediately start printing |
| `print <file>` | Start printing a file already stored on the printer |
| `stop` | Stop the current print |
| `pause` | Pause the current print |
| `resume` | Resume a paused print |
| `connect-mqtt <host:port>` | Point the printer at an external MQTT broker |

`rpp.py` calls this script as a subprocess using the `-p <ip>` flag to target a specific printer.

---

### `saturn_printer.py` — Printer protocol implementation

Implements the Elegoo SDCP protocol. Originally part of Cassini.

- **Discovery:** broadcasts `M99999` over UDP to find printers; parses the JSON response into a `SaturnPrinter` object.
- **Connection:** sends `M66666 <port>` to make the printer connect to the local MQTT server.
- **Commands:** encodes and sends JSON commands over MQTT topics `/sdcp/request/<id>` and reads replies from `/sdcp/response/<id>` and `/sdcp/status/<id>`.
- **File upload:** registers the file on the built-in HTTP server, sends an `UPLOAD_FILE` command with the URL and MD5 checksum, then monitors download progress via status messages.
- **Print control:** implements `stop_print()`, `pause_print()`, `resume_print()`, and `print_file()` with correct handling of all pause states (see table below).

**Status enums used by the printer:**

| Enum | Values |
|---|---|
| `CurrentStatus` | READY (0), BUSY (1) |
| `PrintInfoStatus` | IDLE, PRINTING, EXPOSURE, RETRACTING, LOWERING, PAUSED_HW (7), PAUSED_LIFTED (8), STOPPING, STOPPED, PAUSED (14), COMPLETE (16) |
| `FileStatus` | NONE, DONE, ERROR |

---

### `goo_file.py` — .goo writer and resin mixing

Minimal reader/writer for Elegoo's `.goo` (V3.0) slice format, and `build_mix(cycles, stroke)`, which generates the file behind the **Mix** button:

- every layer is fully black and the LED power is 0, so nothing is exposed;
- every layer has the same Z (0.01 mm), so the platform returns to the vat floor each cycle;
- per cycle the platform lifts `stroke` mm and comes back down: the first/last 1 mm near the FEP at 150 mm/min, the rest at 500 mm/min.

The header values are for the **ELEGOO Mars 4 Ultra** (`MARS4_ULTRA`). Other printers need their own resolution and display size there. The generated file is stored in `uploads/.generated/` (hidden from the file list) and sent to the printer as `rpp_mix.goo`.

Tip: keep the stroke below the resin depth, or the platform leaves the resin and folds air bubbles in.

---

### `simple_mqtt_server.py` — Embedded MQTT broker

A minimal MQTT broker written from scratch using Python asyncio. The Elegoo printer requires an MQTT connection to receive commands — this server provides that without needing an external broker like Mosquitto.

Supports: CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK, DISCONNECT. Binds on a random free port each time so multiple sessions don't conflict.

---

### `simple_http_server.py` — Embedded HTTP file server

A minimal async HTTP server used to serve the print file to the printer during upload. The printer fetches the file itself over HTTP after receiving the URL in the upload command.

Computes MD5 and file size when a route is registered — these are sent to the printer as part of the upload command so it can verify integrity. Binds on a random free port.

---

### `templates/index.html` — Web interface

Single-page HTML interface with two cards:

- **Printer card:** shows online/offline status, the configured IP (editable), current print status text, a progress bar, Pause/Resume/Stop buttons (visible only during an active print) and the Mix resin panel (visible when the printer is idle).
- **Files card:** table of uploaded files with name and size. Buttons to Upload, Print, and Delete.

---

### `static/script.js` — Frontend logic

All browser-side behaviour:

- On load: fetches printer IP, current print status, and file list.
- Polls `/print-status` every 5 seconds to update the progress bar and control buttons.
- File upload uses `XMLHttpRequest` with `upload.onprogress` for a real-time upload percentage overlay.
- After triggering a print, polls `/progress/<filename>` every second until the transfer reaches 100%.
- Pause/Resume/Stop call their respective endpoints and refresh the status afterwards.
- The Pause button is hidden when paused; the Resume button is shown instead.

---

### `static/styles.css` — Stylesheet

Dark-theme CSS for the web interface. Card layout, progress bar styling, button states, and the loading overlay with spinner.

---

### `printer_ip.txt` — Printer address

Plain-text file containing the IP address or hostname of the printer. Read on every request by `rpp.py`; written when the user saves a new address via the web UI. Not tracked by git (it's in `.gitignore`), so updates don't overwrite it.

---

### `rpp.service` — Systemd service unit

Runs `rpp.py` as a systemd service under the `www-data` user, restarting automatically on failure. Working directory is `/opt/RPP`.

---

### `install.sh` — Installation script

One-shot setup: installs `git` and `python3-venv`, clones the repo to `/opt/RPP`, creates a virtualenv with the packages from `requirements.txt`, creates `uploads/` and `printer_ip.txt` with the right permissions, and enables + starts the systemd service.

---

### `update.sh` — Update script

Pulls the latest version from GitHub (`git reset --hard HEAD && git pull`) while keeping your `printer_ip.txt`, updates the virtualenv, resets permissions on the writable files and restarts the service. Don't run it while a file is being sent to the printer (a print that is already running is not affected).

---

### `requirements.txt`

Python dependencies, installed into `/opt/RPP/venv`:

```
flask>=3.0
alive-progress==3.1.4
```

`alive-progress` is only used for animated progress bars in the CLI.

---

## Installation

Tested on Ubuntu 24.04.

```bash
curl -fsSL https://raw.githubusercontent.com/MichielBruijn/Resin-Print-Portal/main/install.sh | bash
```

Or step by step:

```bash
sudo apt update
sudo apt install -y git python3-venv
sudo git clone https://github.com/MichielBruijn/Resin-Print-Portal.git /opt/RPP
cd /opt/RPP
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt
sudo mkdir -p uploads && sudo touch printer_ip.txt
sudo chown -R www-data:www-data uploads printer_ip.txt
sudo cp rpp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rpp
```

Set the printer IP address or hostname in the web UI (or `echo 192.168.1.50 | sudo tee printer_ip.txt`).

The interface runs on port **5001**. It has no authentication: only expose it on a trusted network, or put a reverse proxy (nginx, Apache) with authentication in front of it.

## Update

```bash
bash /opt/RPP/update.sh
```

## Notes on pause and stop

The Elegoo SDCP protocol has three distinct paused states:

| State | Cause | How to resume |
|---|---|---|
| PAUSED_LIFTED (8) | RPP Pause button | RPP Resume button |
| PAUSED_HW (7) | Pause via printer touchscreen | RPP Resume button or printer display |
| PAUSED (14) | RPP Stop button | Print is abandoned — start a new one |

Pressing **Stop** in RPP sends a stop command and the printer ends the current print. The printer firmware briefly reports state 14 before returning to idle.

## Credits

- Original RPP by [JJtronics](https://github.com/jjtronics/RPP)
- Cassini by [Vladimir Vukicevic](https://github.com/vvuk/cassini)
- Licensed under [MIT](LICENSE)
