from flask import Flask, Response, jsonify, render_template, request
from werkzeug.utils import secure_filename
import ast
import json
import subprocess
import threading
import time
import os
import re
import sys
import math
import goo_file
import mqtt_publish
import preview

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
IP_FILE = os.path.join(BASE_DIR, 'printer_ip.txt')  # legacy, migrated into settings.json
DATA_DIR = os.path.join(BASE_DIR, 'data')  # must be writable by the service user
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
CASSINI = [sys.executable, os.path.join(BASE_DIR, 'cassini.py')]
ALLOWED_EXTENSIONS = ('.ctb', '.goo')
# IP address or hostname; must not start with '-' (would be parsed as an option)
HOST_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def upload_path(filename):
    """Path inside uploads/ for a client-supplied name, or None if unsafe."""
    if not filename or secure_filename(filename) != filename:
        return None
    return os.path.join(UPLOAD_FOLDER, filename)

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('favicon.ico')


HA_DEFAULTS = {'enabled': False, 'host': '', 'port': 1883, 'username': '', 'password': '',
               'topic': 'elegoo/mars4/status', 'interval': 30}
settings_lock = threading.Lock()


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            s = json.load(f)
    except FileNotFoundError:
        s = {}
    except Exception as e:
        print(f"Error reading settings: {e}", flush=True)
        s = {}
    if 'printers' not in s:  # first run: take over printer_ip.txt
        try:
            with open(IP_FILE) as f:
                ip = f.read().strip()
        except OSError:
            ip = ''
        s['printers'] = [{'name': ip, 'host': ip}] if ip else []
        s['active'] = ip
    s['ha'] = {**HA_DEFAULTS, **s.get('ha', {})}
    return s


def save_settings(s):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SETTINGS_FILE + '.tmp'
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, SETTINGS_FILE)


def read_printer_ip():
    return load_settings().get('active') or None


def public_settings(s):
    ha = dict(s['ha'], password='', password_set=bool(s['ha']['password']))
    return {'printers': s['printers'], 'active': s.get('active', ''), 'ha': ha, 'ha_state': ha_state}


@app.route('/settings')
def get_settings():
    return jsonify(public_settings(load_settings()))


@app.route('/printers', methods=['POST'])
def set_printers():
    """Saved printer list and/or the active printer."""
    data = request.get_json(silent=True) or {}
    with settings_lock:
        s = load_settings()
        if 'printers' in data:
            printers = []
            for p in data['printers'] or []:
                host = str(p.get('host', '')).strip()
                name = str(p.get('name', '')).strip()[:60] or host
                if not HOST_RE.match(host):
                    return jsonify({'error': f'Invalid IP address or hostname: {host or "(empty)"}'})
                printers.append({'name': name, 'host': host})
            s['printers'] = printers
            if s.get('active') not in [p['host'] for p in printers]:
                s['active'] = printers[0]['host'] if printers else ''
        if 'active' in data:
            if data['active'] not in [p['host'] for p in s['printers']]:
                return jsonify({'error': 'Unknown printer.'})
            s['active'] = data['active']
        try:
            save_settings(s)
        except OSError as e:
            return jsonify({'error': f'Could not save settings: {e}'})
    print(f"Printers: {[p['host'] for p in s['printers']]}, active {s['active']}", flush=True)
    return jsonify({'message': 'Saved', **public_settings(s)})


def ha_from_request(data, current):
    """Validated HA settings from the client; empty password keeps the current one."""
    ha = dict(current)
    for k in ('enabled',):
        ha[k] = bool(data.get(k, ha[k]))
    for k in ('host', 'username', 'topic'):
        ha[k] = str(data.get(k, ha[k]) or '').strip()
    if data.get('password'):
        ha['password'] = str(data['password'])
    if data.get('clear_password'):
        ha['password'] = ''
    try:
        ha['port'] = int(data.get('port', ha['port']))
        ha['interval'] = int(data.get('interval', ha['interval']))
    except (TypeError, ValueError):
        return None, 'Port and interval must be numbers.'
    if ha['host'] and not HOST_RE.match(ha['host']):
        return None, 'Invalid broker hostname.'
    if not 1 <= ha['port'] <= 65535 or not 5 <= ha['interval'] <= 3600:
        return None, 'Port 1-65535, interval 5-3600 s.'
    if not ha['topic'] or any(c in ha['topic'] for c in '#+'):
        return None, 'Invalid topic.'
    if ha['enabled'] and not ha['host']:
        return None, 'Fill in the broker host.'
    return ha, None


@app.route('/ha-settings', methods=['POST'])
def set_ha_settings():
    with settings_lock:
        s = load_settings()
        ha, err = ha_from_request(request.get_json(silent=True) or {}, s['ha'])
        if err:
            return jsonify({'error': err})
        s['ha'] = ha
        try:
            save_settings(s)
        except OSError as e:
            return jsonify({'error': f'Could not save settings: {e}'})
    ha_wake.set()
    return jsonify({'message': 'Home Assistant settings saved.', **public_settings(s)})


@app.route('/ha-test', methods=['POST'])
def test_ha():
    """Publish the current printer status once with the settings from the form."""
    ha, err = ha_from_request(request.get_json(silent=True) or {}, load_settings()['ha'])
    if err:
        return jsonify({'error': err})
    if not ha['host']:
        return jsonify({'error': 'Fill in the broker host.'})
    try:
        payload = full_status_json(read_printer_ip())
        mqtt_publish.publish(ha['host'], ha['port'], ha['topic'], payload,
                             ha['username'], ha['password'])
    except Exception as e:
        return jsonify({'error': f'Broker: {e}'})
    if not payload:
        return jsonify({'message': 'Broker login OK; printer did not answer, nothing published.'})
    return jsonify({'message': f'Published printer status to {ha["topic"]}.'})


# --- Home Assistant: publish the full printer status over MQTT ---
ha_state = {'last_ok': None, 'last_error': None}
ha_wake = threading.Event()


def full_status_json(printer_ip):
    """cassini status-full as JSON (what HA's MQTT sensors read), or None."""
    if not printer_ip:
        return None
    r = subprocess.run(CASSINI + ['-p', printer_ip, 'status-full'], capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return json.dumps(ast.literal_eval(r.stdout.strip()))


def ha_loop():
    while True:
        ha = load_settings()['ha']
        if ha['enabled'] and ha['host']:
            try:
                payload = full_status_json(read_printer_ip())
                if payload:
                    mqtt_publish.publish(ha['host'], ha['port'], ha['topic'], payload,
                                         ha['username'], ha['password'])
                    ha_state.update(last_ok=int(time.time()), last_error=None)
            except Exception as e:
                if ha_state['last_error'] != str(e):
                    print(f"HA publish failed: {e}", flush=True)
                ha_state['last_error'] = str(e)
        ha_wake.wait(ha['interval'])
        ha_wake.clear()


threading.Thread(target=ha_loop, daemon=True, name='ha-mqtt').start()


def query_status(printer_ip, timeout=10):
    """cassini status output and whether the printer answered."""
    cmd = CASSINI + ['-p', printer_ip, 'status']
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    output = result.stdout.strip()
    return output, result.returncode == 0 and bool(output)


def parse_status(output):
    """Machine/print status, layer and file from cassini status output."""
    def grab(pattern):
        m = re.search(pattern, output)
        return m.group(1).strip() if m else None
    layer = grab(r'Layers: (\d+)/')
    return {'machine': grab(r'Machine Status: (\S+)'), 'print': grab(r'Print Status: (\S+)'),
            'layer': int(layer) if layer else None, 'file': grab(r'File: (.+)')}


def mix_meta():
    try:
        with open(MIX_META) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


@app.route('/print-status')
def print_status():
    printer_ip = read_printer_ip()
    if printer_ip is None:
        return jsonify({'status': 'No printer configured.', 'progress': 0, 'is_online': False})

    try:
        output, is_online = query_status(printer_ip)
        st = parse_status(output)

        match = re.search(r'Layers: (\d+)/(\d+)', output)
        layer, total = (int(match.group(1)), int(match.group(2))) if match else (None, None)
        progress = layer / total * 100 if total else 0
        idle = st['print'] in ('CANCELED', 'STOPPED', 'IDLE')
        if idle:
            progress = 0  # nothing running; the layer count is from the aborted job

        elapsed_s = remaining_s = None
        tmatch = re.search(r'Time: (\d+):(\d\d):(\d\d) / (\d+):(\d\d):(\d\d)', output)
        if tmatch:
            t = [int(x) for x in tmatch.groups()]
            elapsed_s = t[0] * 3600 + t[1] * 60 + t[2]
            remaining_s = max(t[3] * 3600 + t[4] * 60 + t[5] - elapsed_s, 0)

        mix = None
        meta = mix_meta() if st['file'] == MIX_NAME else None
        if meta and layer is not None:
            # The mix file has extra cycles we stop before; show it against the requested count
            cycles = meta['cycles']
            done = layer >= cycles
            ended = idle or st['print'] == 'COMPLETE' or st['machine'] == 'READY'
            mix = {'cycles': cycles, 'cycle': min(layer, cycles),
                   'state': 'finished' if done else 'stopped' if ended else 'running'}
            progress = 100 if done else 0 if ended else layer / cycles * 100
            total = cycles
            remaining_s = (elapsed_s / layer * (cycles - layer)) if (elapsed_s and layer and not done) else 0

        info = None
        if st['file'] and st['file'] != MIX_NAME:
            path = upload_path(st['file'])
            info = preview.file_info(path) if path else None
        if info and total == info['layers'] + 1:  # raise-when-done layer
            total = info['layers']
            layer = min(layer, total)
            if not idle:
                progress = layer / total * 100

        return jsonify({
            'file': st['file'],
            'machine': st['machine'],
            'print': st['print'],
            'elapsed_s': elapsed_s,
            'remaining_s': remaining_s,
            'status': output,
            'current_layer': layer,
            'total_layers': total,
            'layer_height': info['layer_height'] if info else None,
            'progress': progress,
            'mix': mix,
            'is_online': is_online,
            'printer': printer_ip,
            'job': job_info,
        })
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Printer status request timed out.'})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files.get('file')
    if file and file.filename:
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            return jsonify({'error': 'Only .ctb and .goo files are allowed.'}), 400
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        return jsonify({'message': 'File uploaded successfully', 'filename': filename})
    return jsonify({'error': 'No file'}), 400


@app.route('/files')
def list_files():
    files = [f for f in os.listdir(app.config['UPLOAD_FOLDER']) if not f.startswith('.')]
    files_info = []
    for file in files:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file)
        st = os.stat(filepath)
        info = preview.file_info(filepath) or {}
        files_info.append({'name': file, 'size': round(st.st_size / (1024 * 1024), 2),
                           'uploaded': int(st.st_mtime),  # upload time, epoch seconds
                           'layers': info.get('layers'), 'height': info.get('height'),
                           'layer_height': info.get('layer_height')})
    files_info.sort(key=lambda f: f['uploaded'], reverse=True)  # newest first
    return jsonify(files_info)


UPLOAD_TIMEOUT = 900
progress_status = {}
job_errors = {}
print_lock = threading.Lock()


def _cassini_error(stderr, default):
    """Last 'ERROR: ...' message from cassini's log output."""
    lines = [l for l in stderr.strip().splitlines() if 'ERROR' in l]
    return lines[-1].split('ERROR:')[-1].strip() if lines else default

def upload_and_print(printer_ip, filepath, filename):
    """Upload file to printer and start printing in one MQTT session."""
    print(f"Starting upload+print for {filename}", flush=True)
    ok = False

    try:
        # Single command: upload AND start printing over one MQTT connection
        cmd = CASSINI + ['--printer', printer_ip, 'upload', '--start-printing', filepath]
        print(f"Running: {cmd}", flush=True)

        # Large files over the printer's WiFi can take several minutes
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=UPLOAD_TIMEOUT)
        print(f"stdout: {result.stdout}", flush=True)
        if result.stderr:
            print(f"stderr: {result.stderr}", flush=True)
        if result.returncode != 0:
            print(f"Exit code: {result.returncode}", flush=True)
            job_errors[filename] = _cassini_error(result.stderr, 'Sending to printer failed')
        else:
            print(f"Upload+print completed successfully for {filename}", flush=True)
            ok = True

    except subprocess.TimeoutExpired:
        print(f"Command timed out after {UPLOAD_TIMEOUT}s for {filename}", flush=True)
        job_errors[filename] = 'Sending to printer timed out.'
    except Exception as e:
        print(f"Error: {e}", flush=True)
        job_errors[filename] = str(e)

    progress_status[filename] = 100
    # Clean up after a delay to avoid memory leak
    def cleanup():
        time.sleep(10)
        progress_status.pop(filename, None)
        job_errors.pop(filename, None)
    threading.Thread(target=cleanup, daemon=True).start()
    return ok

def run_print_job(printer_ip, filepath, filename, raise_mm=None):
    """Wrapper that holds the print lock during the entire job."""
    with print_lock:
        job_errors.pop(filename, None)
        progress_status[filename] = 50
        try:
            filepath = prepare_file(filepath, filename, raise_mm)
        except Exception as e:
            print(f"Preparing {filename} failed: {e}", flush=True)
            job_errors[filename] = f'Could not add the raise layer: {e}'
            progress_status[filename] = 100
            return
        upload_and_print(printer_ip, filepath, filename)


def prepare_file(filepath, filename, raise_mm):
    """Path to send: a copy with a raise-when-done layer if asked (.goo only)."""
    if not raise_mm or not filename.lower().endswith('.goo'):
        return filepath
    with open(filepath, 'rb') as f:
        data = goo_file.add_raise(f.read(), raise_mm)
    if data is None:
        print(f"{filename}: already at {raise_mm:g} mm or higher, no raise layer", flush=True)
        return filepath
    os.makedirs(MIX_DIR, exist_ok=True)
    out = os.path.join(MIX_DIR, filename)  # same name on the printer
    with open(out, 'wb') as f:
        f.write(data)
    print(f"{filename}: raise layer to {raise_mm:g} mm added", flush=True)
    return out

@app.route('/preview/<filename>')
def get_preview(filename):
    """Slicer preview image of an uploaded file (PNG), 404 if there is none."""
    filepath = upload_path(filename)
    png = preview.preview_png(filepath) if filepath else None
    if png is None:
        return Response(status=404)
    return Response(png, mimetype='image/png', headers={'Cache-Control': 'max-age=300'})


@app.route('/progress/<filename>')
def get_progress(filename):
    resp = {'progress': progress_status.get(filename, 0)}
    if filename in job_errors:
        resp['error'] = job_errors[filename]
    return jsonify(resp)


@app.route('/print-file', methods=['POST'])
def print_file():
    printer_ip = read_printer_ip()
    if printer_ip is None:
        return jsonify({'error': 'Could not read printer IP address.'})

    data = request.get_json(silent=True) or {}
    filename = data.get('filename')
    filepath = upload_path(filename)
    if filepath is None:
        return jsonify({'error': 'Invalid filename.'})

    if not os.path.exists(filepath):
        return jsonify({'error': f'File {filename} not found.'})

    raise_mm = None
    if data.get('raise'):
        try:
            raise_mm = float(data['raise'])
        except (TypeError, ValueError):
            raise_mm = -1
        if not RAISE_MIN <= raise_mm <= RAISE_MAX:
            return jsonify({'error': f'Raise height must be {RAISE_MIN}-{RAISE_MAX:g} mm.'})

    mix = None
    if data.get('mix'):
        mix, err = mix_params(data['mix'])
        if err:
            return jsonify({'error': err})

    if print_lock.locked():
        return jsonify({'error': 'A print job is already in progress. Please wait.'})

    if mix:
        print(f"Queueing mix ({mix[0]} x {mix[1]} mm) + print job for {filename}", flush=True)
        threading.Thread(target=run_mix_job, args=(printer_ip, *mix, (filepath, filename, raise_mm))).start()
        return jsonify({'message': f'Mixing first, then printing {filename}', 'filename': MIX_NAME})

    print(f"Queueing print job for {filename}", flush=True)
    job_thread = threading.Thread(target=run_print_job, args=(printer_ip, filepath, filename, raise_mm))
    job_thread.start()

    return jsonify({'message': f'Uploading and printing {filename}...', 'filename': filename})


MIX_DIR = os.path.join(UPLOAD_FOLDER, '.generated')  # hidden from /files
RAISE_MIN, RAISE_MAX = 20, 165  # mm; Mars 4 Ultra Z travel is 165 mm
MIX_NAME = 'rpp_mix.goo'
MIX_META = os.path.join(MIX_DIR, 'rpp_mix.json')  # requested cycles, for the progress display


@app.route('/mix', methods=['POST'])
def mix_resin():
    """Generate a no-exposure pump file and send it to the printer."""
    printer_ip = read_printer_ip()
    if printer_ip is None:
        return jsonify({'error': 'Could not read printer IP address.'})
    mix, err = mix_params(request.get_json(silent=True) or {})
    if err:
        return jsonify({'error': err})
    if print_lock.locked():
        return jsonify({'error': 'A print job is already in progress. Please wait.'})

    cycles, stroke = mix
    print(f"Mixing: {cycles} cycles, {stroke} mm", flush=True)
    threading.Thread(target=run_mix_job, args=(printer_ip, cycles, stroke)).start()
    return jsonify({'message': f'Mixing: {cycles} x {stroke:g} mm', 'filename': MIX_NAME})


def mix_params(data):
    """(cycles, stroke), error"""
    try:
        cycles = int(data.get('cycles', 60))
        stroke = float(data.get('stroke', 15))
    except (TypeError, ValueError, AttributeError):
        return None, 'Invalid cycles or stroke.'
    if not (1 <= cycles <= 500 and 2 <= stroke <= 40):
        return None, 'Cycles must be 1-500 and stroke 2-40 mm.'
    return (cycles, stroke), None


# Background job shown in the UI (mix watcher, print after mix)
job_info = {'id': 0, 'text': None, 'error': None}


def set_job(text=None, error=None):
    job_info.update(id=job_info['id'] + 1, text=text, error=error)
    if text or error:
        print(f"Job: {text or ''} {error or ''}", flush=True)


def wait_status(printer_ip, done, timeout, interval=1.0):
    """Poll the printer until done(status) returns a value; None on timeout."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            output, online = query_status(printer_ip)
        except subprocess.TimeoutExpired:
            online = False
        if online:
            result = done(parse_status(output))
            if result is not None:
                return result
        time.sleep(interval)
    return None


def run_mix_job(printer_ip, cycles, stroke, then_print=None):
    """Mix, stop it on the last cycle (a stopped job needs no confirmation on the
    printer, a completed one does), then optionally print a file."""
    with print_lock:
        job_errors.pop(MIX_NAME, None)
        progress_status[MIX_NAME] = 50
        try:
            _mix_and_print(printer_ip, cycles, stroke, then_print)
        except Exception as e:
            set_job(error=f'Mix job failed: {e}')


MIX_PAD_S = 20  # extra pumping in the file so our stop arrives before the end


def _mix_and_print(printer_ip, cycles, stroke, then_print):
    per_cycle = goo_file.mix_seconds(1, stroke)
    pad = max(3, math.ceil(MIX_PAD_S / per_cycle))
    os.makedirs(MIX_DIR, exist_ok=True)
    filepath = os.path.join(MIX_DIR, MIX_NAME)
    with open(filepath, 'wb') as f:
        f.write(goo_file.build_mix(cycles + pad, stroke))
    with open(MIX_META, 'w') as f:
        json.dump({'cycles': cycles, 'stroke': stroke, 'pad': pad}, f)

    after = f', then print {then_print[1]}' if then_print else ''
    set_job(f'Mixing {cycles} x {stroke:g} mm{after}')
    if not upload_and_print(printer_ip, filepath, MIX_NAME):
        set_job(error=f'Mix could not start: {job_errors.get(MIX_NAME, "unknown error")}')
        return

    started = {'at': time.time(), 'seen': False}

    def mix_progress(st):
        running = st['file'] == MIX_NAME and st['machine'] == 'BUSY'
        if not started['seen']:
            # The printer may still report the previous job for a moment
            if running:
                started['seen'] = True
            elif time.time() - started['at'] > 90:
                return 'other'
            return None
        if st['file'] != MIX_NAME:
            return 'other'
        if st['print'] == 'COMPLETE':
            return 'complete'
        if st['machine'] == 'READY' or st['print'] in ('CANCELED', 'STOPPED', 'IDLE'):
            return 'stopped'  # by the user
        if st['layer'] is not None and st['layer'] >= cycles \
                and st['print'] not in ('PAUSED_HW', 'PAUSED_LIFTED', 'STOPPING'):
            return 'last'
        return None

    result = wait_status(printer_ip, mix_progress,
                         timeout=goo_file.mix_seconds(cycles + pad, stroke) * 2 + 600)
    if result == 'last':
        r = subprocess.run(CASSINI + ['-p', printer_ip, 'stop'], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            set_job(error=f'Could not stop the mix: {_cassini_error(r.stderr, "stop failed")}')
            return
        print("Mix stopped on its last cycle", flush=True)
    elif result == 'complete':
        set_job(error='Mix ran to the end before it could be stopped: confirm the printer screen.')
        return
    elif result == 'stopped':
        set_job(error='Mix was stopped; print not started.' if then_print else None)
        return
    else:
        set_job(error='Lost track of the mix' + ('; print not started.' if then_print else '.'))
        return

    # Printer goes READY once the stop is handled and the platform is up
    ready = wait_status(printer_ip, lambda st: True if st['machine'] == 'READY' else None, timeout=180)
    if not ready:
        set_job(error='Printer did not become ready after mixing' + ('; print not started.' if then_print else '.'))
        return
    if not then_print:
        set_job()
        return

    filepath, filename, raise_mm = then_print
    time.sleep(3)
    set_job(f'Mixed; sending {filename} to the printer')
    try:
        filepath = prepare_file(filepath, filename, raise_mm)
    except Exception as e:
        set_job(error=f'Could not add the raise layer to {filename}: {e}')
        return
    if upload_and_print(printer_ip, filepath, filename):
        set_job()
    else:
        set_job(error=f'Print after mix failed: {job_errors.get(filename, "unknown error")}')


def _run_print_control(command):
    printer_ip = read_printer_ip()
    if printer_ip is None:
        return jsonify({'error': 'Could not read printer IP address.'})
    try:
        cmd = CASSINI + ['-p', printer_ip, command]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return jsonify({'message': f'{command} successful'})
        return jsonify({'error': _cassini_error(result.stderr, f'{command} failed')})
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Request timed out.'})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/stop-print', methods=['POST'])
def stop_print():
    return _run_print_control('stop')

@app.route('/pause-print', methods=['POST'])
def pause_print():
    return _run_print_control('pause')

@app.route('/resume-print', methods=['POST'])
def resume_print():
    return _run_print_control('resume')

@app.route('/delete-file', methods=['POST'])
def delete_file():
    filename = (request.get_json(silent=True) or {}).get('filename')
    filepath = upload_path(filename)
    if filepath is None:
        return jsonify({'error': 'Invalid filename.'})

    try:
        os.remove(filepath)
        return jsonify({'message': f'File {filename} deleted successfully'})
    except Exception as e:
        return jsonify({'error': str(e)})

if __name__ == '__main__':
    # Never debug=True here: the Werkzeug debugger allows code execution from the LAN
    app.run(debug=False, port=5001, host='0.0.0.0')