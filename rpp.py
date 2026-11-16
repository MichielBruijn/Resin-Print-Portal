from flask import Flask, Response, jsonify, render_template, request
from werkzeug.utils import secure_filename
import subprocess
import threading
import time
import os
import re
import sys
import math
import goo_file
import preview

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
IP_FILE = os.path.join(BASE_DIR, 'printer_ip.txt')
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


@app.route('/get-printer-ip', methods=['GET'])
def get_printer_ip():
    return jsonify({'ip': read_printer_ip() or ''})

def read_printer_ip():
    try:
        with open(IP_FILE, 'r') as file:
            return file.read().strip() or None
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"Error reading printer IP: {e}", flush=True)
        return None

@app.route('/set-printer-ip', methods=['POST'])
def set_printer_ip():
    try:
        new_ip = ((request.get_json(silent=True) or {}).get('ip') or '').strip()
        if not HOST_RE.match(new_ip):
            return jsonify({'error': 'Invalid IP address or hostname.'})
        print(f"Updating printer IP to: {new_ip}", flush=True)
        with open(IP_FILE, 'w') as file:
            file.write(new_ip + '\n')
        print("Printer IP updated successfully.", flush=True)
        return jsonify({'message': 'IP updated'})
    except Exception as e:
        print(f"Error updating printer IP: {e}", flush=True)
        return jsonify({'error': str(e)})

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


@app.route('/print-status')
def print_status():
    printer_ip = read_printer_ip()
    if printer_ip is None:
        return jsonify({'status': 'No printer address configured.', 'progress': 0, 'is_online': False})

    try:
        output, is_online = query_status(printer_ip)

        match = re.search(r'Layers: (\d+)/(\d+)', output)
        if match:
            current_layer, total_layers = match.groups()
            if int(total_layers) > 0:
                progress = (int(current_layer) / int(total_layers)) * 100
            else:
                progress = 0
        else:
            current_layer, total_layers, progress = 'N/A', 'N/A', 0
        if re.search(r'Print Status: (CANCELED|STOPPED|IDLE)', output):
            progress = 0  # nothing running; the layer count is from the aborted job

        elapsed_s = remaining_s = None
        tmatch = re.search(r'Time: (\d+):(\d\d):(\d\d) / (\d+):(\d\d):(\d\d)', output)
        if tmatch:
            t = [int(x) for x in tmatch.groups()]
            elapsed_s = t[0] * 3600 + t[1] * 60 + t[2]
            remaining_s = max(t[3] * 3600 + t[4] * 60 + t[5] - elapsed_s, 0)

        fmatch = re.search(r'File: (.+)', output)
        return jsonify({
            'file': fmatch.group(1).strip() if fmatch else None,
            'elapsed_s': elapsed_s,
            'remaining_s': remaining_s,
            'status': output,
            'current_layer': current_layer,
            'total_layers': total_layers,
            'progress': progress,
            'is_online': is_online,
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
        files_info.append({'name': file, 'size': round(st.st_size / (1024 * 1024), 2),
                           'uploaded': int(st.st_mtime)})  # upload time, epoch seconds
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

def run_print_job(printer_ip, filepath, filename):
    """Wrapper that holds the print lock during the entire job."""
    with print_lock:
        job_errors.pop(filename, None)
        progress_status[filename] = 50
        upload_and_print(printer_ip, filepath, filename)

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

    mix = None
    if data.get('mix'):
        mix, err = mix_params(data['mix'])
        if err:
            return jsonify({'error': err})

    if print_lock.locked():
        return jsonify({'error': 'A print job is already in progress. Please wait.'})

    if mix:
        print(f"Queueing mix ({mix[0]} x {mix[1]} mm) + print job for {filename}", flush=True)
        threading.Thread(target=run_mix_job, args=(printer_ip, *mix, (filepath, filename))).start()
        return jsonify({'message': f'Mixing first, then printing {filename}', 'filename': MIX_NAME})

    print(f"Queueing print job for {filename}", flush=True)
    job_thread = threading.Thread(target=run_print_job, args=(printer_ip, filepath, filename))
    job_thread.start()

    return jsonify({'message': f'Uploading and printing {filename}...', 'filename': filename})


MIX_DIR = os.path.join(UPLOAD_FOLDER, '.generated')  # hidden from /files
MIX_NAME = 'rpp_mix.goo'


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

    filepath, filename = then_print
    time.sleep(3)
    set_job(f'Mixed; sending {filename} to the printer')
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