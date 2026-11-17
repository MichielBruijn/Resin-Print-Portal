# Resin Print Portal (RPP)

A web interface for managing and monitoring Elegoo resin 3D printers over WiFi. Tested on Elegoo Mars 4 Ultra.

Fork of [RPP](https://github.com/jjtronics/RPP) by JJtronics, built on [Cassini](https://github.com/vvuk/cassini) by Vladimir Vukicevic.

## Features

- Upload `.ctb` / `.goo` slice files to the server
- Send files to the printer and start printing in one step
- Real-time print progress (layer counter + progress bar) with elapsed time, remaining time and expected finish time
- Pause, Resume and Stop a running print
- Printer online/offline status indicator
- Large preview image of the selected model (or the one being printed), from the slice file
- Layer count and print height in mm (read from the `.ctb`/`.goo` header, also for encrypted CTB)
- Saved printers: pick the active printer from a list in the header
- **Home Assistant**: publishes the full printer status over MQTT (broker, login, topic and interval set in the web UI)
- **Mix resin**: pump the platform up and down (no exposure) to stir the resin in the vat

## File overview

### `rpp.py` — Flask web server (main entry point)

The central application. Starts a Flask HTTP server on port 5001 (debug mode off) and exposes the REST API that the browser calls:

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the web interface (`index.html`) |
| `/settings` | GET | Saved printers, active printer, Home Assistant settings (password left out) and last publish result |
| `/printers` | POST | Saves the printer list (`printers: [{name, host}]`) and/or the active printer (`active`) |
| `/ha-settings` | POST | Saves the Home Assistant/MQTT settings (empty password keeps the stored one) |
| `/ha-test` | POST | Publishes the current status once with the settings from the form |
| `/print-status` | GET | Polls the printer status via Cassini and returns JSON with machine/print state, current/total layers, layer height, progress %, elapsed/remaining seconds, mix progress and online state |
| `/upload` | POST | Saves an uploaded `.ctb`/`.goo` file to the `uploads/` folder (other extensions are rejected) |
| `/files` | GET | Lists all files in `uploads/` with size in MB, upload time, layer count, layer height and total height, newest first |
| `/print-file` | POST | Starts a background thread that uploads the selected file to the printer and immediately starts the print; with `mix: {cycles, stroke}` it mixes first |
| `/preview/<filename>` | GET | PNG of the slicer preview inside an uploaded `.ctb`/`.goo`, 404 if it has none |
| `/progress/<filename>` | GET | Returns upload/transfer progress for a running print job (0–100), plus `error` if sending failed |
| `/mix` | POST | Generates a resin-mixing file (`cycles`, `stroke` in mm), sends it to the printer and stops it on its last cycle |
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
| `status` | Show current machine status, print progress, layer count and elapsed/total time |
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

The printer reports `CurrentTicks` / `TotalTicks` in milliseconds; `TotalTicks` is the printer's own estimate of the total print time and is updated while printing.

**Status enums used by the printer:**

| Enum | Values |
|---|---|
| `CurrentStatus` | READY (0), BUSY (1) |
| `PrintInfoStatus` | IDLE, PRINTING, EXPOSURE, RETRACTING, LOWERING, PAUSED_HW (7), PAUSED_LIFTED (8), STOPPING, STOPPED, CANCELED (14), COMPLETE (16) |
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

### `preview.py` — Preview images

Extracts the slicer's preview image from a slice file and returns it as PNG (no image libraries needed):

- **`.ctb`**: the preview headers are not encrypted, even in encrypted CTB v4/v5 files, so they are located near the start of the file; the largest image is decoded from ChituBox's RLE-compressed RGB555.
- **`.goo`**: the 290×290 RGB565 preview from the header.

Results are cached per file version. The printer card shows the preview of the file being printed, if that file was uploaded through RPP.

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

Single-page HTML interface. The top part stays in place, only the file list below it scrolls (on small or low screens the whole page scrolls):

- **Header:** online/offline badge, printer picker, and the *Printers…* and *Home Assistant…* dialogs.
- **Printer panel:** large preview of the selected file (or of the job on the printer when nothing is selected); machine/print state, layer and height in mm, progress bar, elapsed/remaining/ready time, Pause/Resume/Stop during a job; the selected file with Print, Mix first, Upload and Delete; and the Mix resin panel (Mix and Print are disabled while the printer is busy).
- **Files:** table of uploaded files with thumbnail, name, layers, height, upload time and size (newest first).

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

### `data/settings.json` — Settings

Saved printers, the active printer and the Home Assistant/MQTT settings, written from the web UI (mode 600: it holds the MQTT password). `data/` must be writable by the service user; `install.sh` and `update.sh` create it. Not tracked by git. On first start the address from the old `printer_ip.txt` is taken over.

### `mqtt_publish.py` — MQTT publisher

Minimal MQTT 3.1.1 client (connect with optional login, publish QoS 0, disconnect), no extra packages. A background thread in `rpp.py` publishes `cassini status-full` as JSON every *interval* seconds when Home Assistant is enabled; this replaces a separate `mosquitto_pub` loop.

---

### `rpp.service` — Systemd service unit

Runs `rpp.py` as a systemd service under the `www-data` user, restarting automatically on failure. Working directory is `/opt/RPP`.

---

### `install.sh` — Installation script

One-shot setup: installs `git` and `python3-venv`, clones the repo to `/opt/RPP`, creates a virtualenv with the packages from `requirements.txt`, creates `uploads/` and `data/` with the right permissions, and enables + starts the systemd service.

---

### `update.sh` — Update script

Pulls the latest version from GitHub (`git reset --hard HEAD && git pull`) while keeping your `printer_ip.txt` and `data/`, updates the virtualenv, resets permissions on the writable files and restarts the service. Don't run it while a file is being sent to the printer (a print that is already running is not affected).

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

Tested on Ubuntu 24.04 and 26.04.

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
sudo mkdir -p uploads data
sudo chown -R www-data:www-data uploads data
sudo cp rpp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rpp
```

Add the printer (IP address or hostname) under *Printers…* in the web UI.

The interface runs on port **5001**. It has no authentication: only expose it on a trusted network, or put a reverse proxy (nginx, Apache) with authentication in front of it.

## Update

```bash
bash /opt/RPP/update.sh
```

**Upgrading from v0.0.3 or older:** `printer_ip.txt` is no longer tracked by git, so the old `update.sh` deletes it during the pull. Run `bash /opt/RPP/update.sh` twice (the second run uses the new script and recreates the file) and enter the printer address again in the web UI. Or save it first: `cp /opt/RPP/printer_ip.txt /tmp/rpp_printer_ip.txt` — the new script restores it from there.

## Mixing resin

Pigments settle on the bottom of the vat. The **Mix resin** panel (shown when the printer is idle) sends a generated job that pumps the platform down to the vat floor and up again, without exposing anything:

- **Cycles**: number of up/down strokes (default 60, about 6 minutes)
- **Stroke**: how far the platform lifts, in mm (default 15)

Each cycle returns the platform to the same floor position. The first and last millimetre near the FEP run at 150 mm/min to avoid suction and impact, the rest at 500 mm/min. Keep the stroke below the resin depth, or the platform leaves the resin and folds air bubbles in. Resin that has stood for weeks still needs a good shake in the bottle first.

The generated file is made for the Mars 4 Ultra (see `goo_file.py`).

A completed job leaves the printer on its "print complete" screen (see below), but a stopped one does not. So the mix file gets a few extra cycles (about 20 s), and RPP watches the layer counter and sends **Stop** once the requested number of cycles is done. Nothing hangs on the platform after mixing, so there is nothing to confirm. If the stop is missed, the mix just completes and the screen needs a confirm as before.

**Mix first** (checkbox next to Print) runs that mix with the cycles/stroke from the Mix panel, waits until the printer is ready again and then sends and starts the selected file, so you don't have to be there in between. If the mix is stopped or fails, the print is not started. The purple line in the printer card shows what the background job is doing.

While mixing, the progress bar counts the requested cycles (not the padding cycles), so it reaches 100% and shows **Mix finished ✓** after the automatic stop; a mix stopped early shows **Mix stopped**.

## Notes on the "print complete" screen

After every job (a print or a mix) the printer shows a completion screen and reports `Machine Status: BUSY` with `Print Status: COMPLETE` until that screen is confirmed **on the printer itself**. In that state it refuses new jobs. RPP shows a yellow hint and disables the Mix button until it is confirmed.

This cannot be done remotely: on firmware V1.3.0 (protocol V1.0.0) a Stop command is acknowledged but leaves the screen, a new Start is rejected, and there is no confirm command.

## Notes on pause and stop

The Elegoo SDCP protocol has two paused states, plus a state that upstream Cassini called PAUSED but that actually means the print was stopped:

| State | Cause | How to resume |
|---|---|---|
| PAUSED_LIFTED (8) | RPP Pause button | RPP Resume button |
| PAUSED_HW (7) | Pause via printer touchscreen | RPP Resume button or printer display |
| CANCELED (14) | Stop (RPP or touchscreen) | Print is abandoned — start a new one |

Pressing **Stop** in RPP sends a stop command and the printer ends the current print. Afterwards the printer reports `Machine Status: READY` with state 14 (shown as CANCELED in RPP); the progress bar goes back to 0.

## Credits

- Original RPP by [JJtronics](https://github.com/jjtronics/RPP)
- Cassini by [Vladimir Vukicevic](https://github.com/vvuk/cassini)
- Licensed under [MIT](LICENSE)
