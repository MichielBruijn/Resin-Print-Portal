from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename
import subprocess
import threading
import time
import os
import re
import sys
import goo_file

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

@app.route('/print-status')
def print_status():
    printer_ip = read_printer_ip()
    if printer_ip is None:
        return jsonify({'status': 'No printer address configured.', 'progress': 0, 'is_online': False})

    try:
        cmd = CASSINI + ['-p', printer_ip, 'status']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        output = result.stdout.strip()

        is_online = result.returncode == 0 and bool(output)

        match = re.search(r'Layers: (\d+)/(\d+)', output)
        if match:
            current_layer, total_layers = match.groups()
            if int(total_layers) > 0:
                progress = (int(current_layer) / int(total_layers)) * 100
            else:
                progress = 0
        else:
            current_layer, total_layers, progress = 'N/A', 'N/A', 0

        return jsonify({
            'status': output,
            'current_layer': current_layer,
            'total_layers': total_layers,
            'progress': progress,
            'is_online': is_online
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
        size = os.path.getsize(filepath) / (1024 * 1024)  # Convert to MB
        files_info.append({'name': file, 'size': round(size, 2)})
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

def run_print_job(printer_ip, filepath, filename):
    """Wrapper that holds the print lock during the entire job."""
    with print_lock:
        job_errors.pop(filename, None)
        progress_status[filename] = 50
        upload_and_print(printer_ip, filepath, filename)

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

    filename = (request.get_json(silent=True) or {}).get('filename')
    filepath = upload_path(filename)
    if filepath is None:
        return jsonify({'error': 'Invalid filename.'})

    if not os.path.exists(filepath):
        return jsonify({'error': f'File {filename} not found.'})

    if print_lock.locked():
        return jsonify({'error': 'A print job is already in progress. Please wait.'})

    print(f"Queueing print job for {filename}", flush=True)
    job_thread = threading.Thread(target=run_print_job, args=(printer_ip, filepath, filename))
    job_thread.start()

    return jsonify({'message': f'Uploading and printing {filename}...'})


MIX_DIR = os.path.join(UPLOAD_FOLDER, '.generated')  # hidden from /files
MIX_NAME = 'rpp_mix.goo'


@app.route('/mix', methods=['POST'])
def mix_resin():
    """Generate a no-exposure pump file and send it to the printer."""
    printer_ip = read_printer_ip()
    if printer_ip is None:
        return jsonify({'error': 'Could not read printer IP address.'})
    data = request.get_json(silent=True) or {}
    try:
        cycles = int(data.get('cycles', 60))
        stroke = float(data.get('stroke', 15))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid cycles or stroke.'})
    if not (1 <= cycles <= 500 and 2 <= stroke <= 40):
        return jsonify({'error': 'Cycles must be 1-500 and stroke 2-40 mm.'})
    if print_lock.locked():
        return jsonify({'error': 'A print job is already in progress. Please wait.'})

    os.makedirs(MIX_DIR, exist_ok=True)
    filepath = os.path.join(MIX_DIR, MIX_NAME)
    with open(filepath, 'wb') as f:
        f.write(goo_file.build_mix(cycles, stroke))

    print(f"Mixing: {cycles} cycles, {stroke} mm", flush=True)
    threading.Thread(target=run_print_job, args=(printer_ip, filepath, MIX_NAME)).start()
    return jsonify({'message': f'Mixing: {cycles} x {stroke:g} mm', 'filename': MIX_NAME})


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