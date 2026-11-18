"""Home Assistant MQTT discovery: a device per printer with sensors and pause/resume/stop buttons."""
import json
import time
from datetime import datetime, timezone

from saturn_printer import CurrentStatus, FileStatus, PrintInfoStatus

PREFIX = 'homeassistant'
COMMANDS = ('pause', 'resume', 'stop')


def _name(enum, code):
    try:
        return enum(code).name
    except ValueError:
        return f'UNKNOWN_{code}'


def base_topic(printer_id):
    return f'rpp/{printer_id}'


def state(raw, file_info=None, mix=None, job=None):
    """Flat state for HA from cassini status-full (dict); raw None = printer offline."""
    if not raw:
        return {'online': False, 'activity': 'offline'}
    attr, st = raw['Data']['Attributes'], raw['Data']['Status']
    pi, ft = st.get('PrintInfo', {}), st.get('FileTransferInfo', {})
    machine = _name(CurrentStatus, st.get('CurrentStatus', 0))
    pstat = _name(PrintInfoStatus, pi.get('Status', 0))
    layer, total = pi.get('CurrentLayer', 0), pi.get('TotalLayer', 0)
    idle = pstat in ('CANCELED', 'STOPPED', 'IDLE')
    done = idle or pstat == 'COMPLETE'
    paused = pstat in ('PAUSED_HW', 'PAUSED_LIFTED')
    progress = 0 if idle or not total else layer / total * 100
    elapsed = remaining = None
    if pi.get('TotalTicks'):  # ms; total is the printer's own estimate
        elapsed = round(pi['CurrentTicks'] / 1000)
        remaining = round(max(pi['TotalTicks'] - pi['CurrentTicks'], 0) / 1000)
    mix_state = None
    if mix:  # rpp mix file: the file has spare cycles, show the requested count
        cycles = mix['cycles']
        finished = layer >= cycles
        ended = done or machine == 'READY'
        mix_state = 'finished' if finished else 'stopped' if ended else 'running'
        progress = 100 if finished else 0 if ended else layer / cycles * 100
        remaining = round(elapsed / layer * (cycles - layer)) if elapsed and layer and not finished else 0
        total = cycles
        layer = min(layer, cycles)
    busy = machine == 'BUSY' and not done and mix_state in (None, 'running')
    confirm = done and machine == 'BUSY'
    activity = ('paused' if paused else ('mixing' if mix else 'printing') if busy
                else 'finished' if confirm else 'idle')
    end = (datetime.fromtimestamp(time.time() + remaining, timezone.utc).isoformat(timespec='seconds')
           if busy and remaining else None)
    size = ft.get('FileTotalSize') or 0
    info = file_info or {}
    return {
        'online': True,
        'activity': activity,
        'machine': machine,
        'print': pstat,
        'printing': busy,
        'paused': paused,
        'needs_confirm': confirm,
        'file': pi.get('Filename') or None,
        'layer': layer,
        'total_layers': total,
        'remaining_layers': max(total - layer, 0) if total else 0,
        'progress': round(progress, 1),
        'elapsed_s': elapsed,
        'remaining_s': remaining,
        'end_time': end,
        'layer_height': info.get('layer_height'),
        'height': info.get('height'),
        'error_number': pi.get('ErrorNumber', 0),
        'transfer': _name(FileStatus, ft.get('Status', 0)),
        'transfer_progress': round(ft.get('DownloadOffset', 0) / size * 100, 1) if size else 0,
        'mix_state': mix_state,
        'job': (job or {}).get('error') or (job or {}).get('text'),
        'firmware': attr.get('FirmwareVersion'),
        'ip': attr.get('MainboardIP'),
        'resolution': attr.get('Resolution'),
    }


# key, component, name, extra config
_ENTITIES = [
    ('activity', 'sensor', 'Status', {'device_class': 'enum', 'icon': 'mdi:printer-3d',
        'options': ['offline', 'idle', 'printing', 'mixing', 'paused', 'finished']}),
    ('machine', 'sensor', 'Machine status', {'icon': 'mdi:state-machine'}),
    ('print', 'sensor', 'Print status', {'icon': 'mdi:printer-3d-nozzle'}),
    ('progress', 'sensor', 'Progress', {'unit_of_measurement': '%', 'state_class': 'measurement',
        'icon': 'mdi:progress-clock', 'suggested_display_precision': 0}),
    ('layer', 'sensor', 'Current layer', {'icon': 'mdi:layers', 'state_class': 'measurement'}),
    ('total_layers', 'sensor', 'Total layers', {'icon': 'mdi:layers-triple'}),
    ('remaining_layers', 'sensor', 'Remaining layers', {'icon': 'mdi:layers-minus'}),
    ('elapsed_s', 'sensor', 'Elapsed time', {'device_class': 'duration', 'unit_of_measurement': 's',
        'suggested_unit_of_measurement': 'min'}),
    ('remaining_s', 'sensor', 'Remaining time', {'device_class': 'duration', 'unit_of_measurement': 's',
        'suggested_unit_of_measurement': 'min'}),
    ('end_time', 'sensor', 'End time', {'device_class': 'timestamp'}),
    ('file', 'sensor', 'File', {'icon': 'mdi:file-cad'}),
    ('layer_height', 'sensor', 'Layer height', {'unit_of_measurement': 'mm', 'icon': 'mdi:arrow-collapse-vertical'}),
    ('height', 'sensor', 'Print height', {'unit_of_measurement': 'mm', 'icon': 'mdi:arrow-expand-vertical'}),
    ('job', 'sensor', 'RPP job', {'icon': 'mdi:message-text'}),
    ('transfer', 'sensor', 'File transfer', {'icon': 'mdi:file-upload', 'entity_category': 'diagnostic'}),
    ('transfer_progress', 'sensor', 'File transfer progress', {'unit_of_measurement': '%',
        'icon': 'mdi:file-upload', 'entity_category': 'diagnostic'}),
    ('error_number', 'sensor', 'Error number', {'icon': 'mdi:alert-circle', 'entity_category': 'diagnostic'}),
    ('ip', 'sensor', 'IP address', {'icon': 'mdi:ip-network', 'entity_category': 'diagnostic'}),
    ('resolution', 'sensor', 'Screen resolution', {'icon': 'mdi:monitor', 'entity_category': 'diagnostic'}),
    ('printing', 'binary_sensor', 'Printing', {'device_class': 'running'}),
    ('paused', 'binary_sensor', 'Paused', {'icon': 'mdi:pause-circle'}),
    ('needs_confirm', 'binary_sensor', 'Confirm screen', {'device_class': 'problem'}),
]


def configs(printer_id, raw, avail):
    """[(topic, payload)] discovery configs (publish retained); avail = RPP's own availability topic."""
    attr = raw['Data']['Attributes']
    base = base_topic(printer_id)
    device = {'identifiers': [f'rpp_{printer_id}'], 'name': attr.get('Name') or 'Resin printer',
              'manufacturer': 'Elegoo', 'model': attr.get('MachineName'),
              'sw_version': attr.get('FirmwareVersion'), 'serial_number': printer_id}
    origin = {'name': 'Resin Print Portal', 'support_url': 'https://github.com/MichielBruijn/Resin-Print-Portal'}
    # Entities are unavailable when RPP is gone or the printer does not answer
    both = {'availability': [{'topic': avail},
                             {'topic': f'{base}/state', 'value_template':
                              "{{ 'online' if value_json.online else 'offline' }}"}],
            'availability_mode': 'all'}
    out = []

    def add(component, key, cfg):
        uid = f'rpp_{printer_id}_{key}'
        cfg = {'unique_id': uid, 'device': device, 'origin': origin, **cfg}
        out.append((f'{PREFIX}/{component}/rpp_{printer_id}/{key}/config', json.dumps(cfg)))

    for key, comp, name, extra in _ENTITIES:
        cfg = {'name': name, 'state_topic': f'{base}/state', **both, **extra}
        if comp == 'binary_sensor':
            cfg['value_template'] = f"{{{{ 'ON' if value_json.{key} else 'OFF' }}}}"
        else:
            cfg['value_template'] = f"{{{{ value_json.{key} }}}}"  # null renders 'None' = unknown
        add(comp, key, cfg)
    # Online: only needs RPP itself, so it can show the printer as off
    add('binary_sensor', 'online', {'name': 'Online', 'device_class': 'connectivity',
        'state_topic': f'{base}/state', 'availability_topic': avail,
        'value_template': "{{ 'ON' if value_json.online else 'OFF' }}"})
    for cmd in COMMANDS:
        add('button', cmd, {'name': f'{cmd.capitalize()} print', 'command_topic': f'{base}/command',
            'payload_press': cmd, **both,
            'icon': {'pause': 'mdi:pause', 'resume': 'mdi:play', 'stop': 'mdi:stop'}[cmd]})
    return out
