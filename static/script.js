// script.js - Printer status polling, file management, upload, print control (pause/resume/stop)

let selectedFile = null;
let fileInfo = {};      // name -> {uploaded, layers, height, layer_height}
let printerState = {};  // last /print-status answer + derived flags

document.addEventListener('DOMContentLoaded', () => {
    updateMixEstimate();
    fetchPrintStatus();
    fetchFiles();
    loadSettings();
});

// --- Printers ---
let settings = { printers: [], active: '', ha: {} };

function loadSettings() {
    return fetch('/settings')
        .then(r => r.json())
        .then(data => { settings = data; renderPrinterSelect(); });
}

function renderPrinterSelect() {
    const sel = document.getElementById('printer-select');
    sel.innerHTML = '';
    if (!settings.printers.length) {
        sel.appendChild(new Option('No printer — add one', ''));
    }
    settings.printers.forEach(p => {
        const label = p.name && p.name !== p.host ? `${p.name} (${p.host})` : p.host;
        sel.appendChild(new Option(label, p.host, false, p.host === settings.active));
    });
}

function selectPrinter(host) {
    if (!host) { openPrinters(); return; }
    fetch('/printers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active: host }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.error) { notify(data.error, true); renderPrinterSelect(); return; }
            settings = data;
            notify('Printer: ' + host);
            fetchPrintStatus();
        });
}

function openPrinters() {
    const list = document.createElement('div');
    list.className = 'printer-rows';
    const head = document.createElement('div');
    head.className = 'printer-row printer-head';
    head.innerHTML = '<span>Name</span><span>IP address or hostname</span><span></span>';
    list.appendChild(head);
    const addRow = (p = { name: '', host: '' }) => {
        const row = document.createElement('div');
        row.className = 'printer-row';
        const name = Object.assign(document.createElement('input'), { placeholder: 'Name', value: p.name, className: 'name' });
        const host = Object.assign(document.createElement('input'), { placeholder: 'IP or hostname', value: p.host, className: 'host' });
        const del = Object.assign(document.createElement('button'), { className: 'btn btn-ghost', textContent: '✕', title: 'Remove' });
        del.onclick = () => row.remove();
        row.append(name, host, del);
        list.appendChild(row);
        return row;
    };
    settings.printers.forEach(p => addRow(p));
    if (!settings.printers.length) addRow();
    const add = Object.assign(document.createElement('button'), { className: 'btn btn-ghost', textContent: '+ Add printer' });
    add.onclick = () => addRow().querySelector('input').focus();
    const body = document.createElement('div');
    body.append(list, add, discoverPanel(list, addRow));

    openModal('Saved printers', body, [
        { label: 'Cancel' },
        { label: 'Save', primary: true, action: close => {
            const printers = [...list.querySelectorAll('.printer-row:not(.printer-head)')]
                .map(r => ({ name: r.querySelector('.name').value.trim(), host: r.querySelector('.host').value.trim() }))
                .filter(p => p.name || p.host);
            fetch('/printers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ printers }),
            })
                .then(r => r.json())
                .then(data => {
                    if (data.error) { notify(data.error, true); return; }
                    settings = data;
                    renderPrinterSelect();
                    notify('Printers saved.');
                    fetchPrintStatus();
                    close();
                });
        } },
    ]);
}

// Network search (SDCP broadcast); found printers can be added as rows
function discoverPanel(list, addRow) {
    const wrap = document.createElement('div');
    wrap.className = 'discover';
    const bar = document.createElement('div');
    bar.className = 'discover-bar';
    const subnets = Object.assign(document.createElement('input'), {
        value: settings.scan_subnets || '', placeholder: 'Extra subnet, e.g. 192.168.1.0/24 (optional)',
        title: 'Broadcasts only reach the local network. For printers behind a router, give their subnet (at most /22).' });
    const btn = Object.assign(document.createElement('button'), { className: 'btn btn-ghost', textContent: 'Search network', type: 'button' });
    bar.append(subnets, btn);
    const results = document.createElement('div');
    results.className = 'discover-results';
    wrap.append(bar, results);

    const hosts = () => [...list.querySelectorAll('.host')].map(i => i.value.trim());
    const search = () => {
        btn.disabled = true;
        results.textContent = 'Searching…';
        fetch('/discover', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ subnets: subnets.value }),
        })
            .then(r => r.json())
            .then(data => {
                results.innerHTML = '';
                if (data.error) { results.textContent = data.error; return; }
                settings.scan_subnets = subnets.value.trim();
                if (!data.printers.length) { results.textContent = 'No printers found.'; return; }
                data.printers.forEach(p => {
                    const row = document.createElement('div');
                    row.className = 'discover-row';
                    const info = document.createElement('span');
                    info.textContent = `${p.machine || p.name || 'Printer'} — ${p.ip}` + (p.firmware ? ` (${p.firmware})` : '');
                    const known = p.saved || (hosts().includes(p.ip) ? p.ip : '');
                    const b = Object.assign(document.createElement('button'), { className: 'btn btn-ghost', type: 'button',
                        textContent: known ? 'Saved' : 'Add', disabled: !!known });
                    if (known && known !== p.ip) b.title = 'Saved as ' + known;
                    b.onclick = () => {
                        // fill an empty row instead of adding one
                        const empty = [...list.querySelectorAll('.printer-row:not(.printer-head)')]
                            .find(r => !r.querySelector('.name').value && !r.querySelector('.host').value);
                        const r = empty || addRow();
                        r.querySelector('.name').value = p.name || p.machine;
                        r.querySelector('.host').value = p.ip;
                        b.textContent = 'Added';
                        b.disabled = true;
                    };
                    row.append(info, b);
                    results.appendChild(row);
                });
            })
            .catch(() => { results.textContent = 'Search failed.'; })
            .finally(() => { btn.disabled = false; });
    };
    btn.onclick = search;
    subnets.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); search(); } };
    return wrap;
}

// --- Home Assistant (MQTT) ---
function openHA() {
    loadSettings().then(() => {
        const ha = settings.ha;
        const form = document.createElement('form');
        form.className = 'settings-form';
        form.onsubmit = e => e.preventDefault();
        const field = (label, name, attrs = {}) => {
            const l = document.createElement('label');
            l.textContent = label;
            const i = Object.assign(document.createElement('input'), { name, ...attrs });
            l.appendChild(i);
            form.appendChild(l);
            return i;
        };
        const en = document.createElement('label');
        en.className = 'check';
        const enBox = Object.assign(document.createElement('input'), { type: 'checkbox', name: 'enabled', checked: ha.enabled });
        en.append(enBox, ' Publish printer status to Home Assistant');
        form.appendChild(en);
        field('Broker host', 'host', { value: ha.host, placeholder: 'homeassistant' });
        field('Port', 'port', { type: 'number', value: ha.port, min: 1, max: 65535 });
        field('Username', 'username', { value: ha.username, autocomplete: 'off' });
        field('Password', 'password', { type: 'password', autocomplete: 'new-password',
            placeholder: ha.password_set ? '•••••• (unchanged)' : '' });
        field('Topic', 'topic', { value: ha.topic });
        field('Interval (s)', 'interval', { type: 'number', value: ha.interval, min: 5, max: 3600 });
        const st = document.createElement('p');
        st.className = 'settings-status';
        const hs = settings.ha_state || {};
        st.textContent = hs.last_error ? 'Last error: ' + hs.last_error
            : hs.last_ok ? 'Last published ' + formatDate(hs.last_ok) : ha.enabled ? 'Not published yet.' : 'Off.';
        if (hs.last_error) st.classList.add('error');
        form.appendChild(st);

        const values = () => {
            const v = Object.fromEntries(new FormData(form));
            v.enabled = enBox.checked;
            return v;
        };
        const post = (url, done) => fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(values()),
        }).then(r => r.json()).then(data => {
            if (data.error) notify(data.error, true); else done(data);
        });

        openModal('Home Assistant (MQTT)', form, [
            { label: 'Test', action: () => post('/ha-test', d => notify(d.message)) },
            { label: 'Cancel' },
            { label: 'Save', primary: true, action: close => post('/ha-settings', d => {
                settings = d;
                notify(d.message);
                close();
            }) },
        ]);
    });
}

// Generic dialog: buttons [{label, primary, action(close)}]; no action = close
function openModal(title, body, buttons) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    const box = document.createElement('div');
    box.className = 'modal modal-wide';
    const h = document.createElement('h3');
    h.className = 'modal-title';
    h.textContent = title;
    const row = document.createElement('div');
    row.className = 'modal-buttons';
    const close = () => { document.removeEventListener('keydown', onKey); overlay.remove(); };
    const onKey = e => { if (e.key === 'Escape') close(); };
    buttons.forEach(b => {
        const btn = Object.assign(document.createElement('button'), {
            className: 'btn ' + (b.primary ? 'btn-print' : 'btn-cancel'), textContent: b.label, type: 'button' });
        btn.onclick = () => b.action ? b.action(close) : close();
        row.appendChild(btn);
    });
    box.append(h, body, row);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    document.addEventListener('keydown', onKey);
    overlay.onclick = e => { if (e.target === overlay) close(); };
    const first = box.querySelector('input');
    if (first) first.focus();
}

function uploadFile() {
    let input = document.createElement('input');
    input.type = 'file';
    input.accept = '.ctb,.goo';
    input.onchange = e => {
        let file = e.target.files[0];
        if (!file) return;
        let formData = new FormData();
        formData.append('file', file);

        showLoadingIndicator('Uploading...');

        let xhr = new XMLHttpRequest();
        xhr.open('POST', '/upload', true);

        xhr.upload.onprogress = function (e) {
            if (e.lengthComputable) {
                let percentComplete = (e.loaded / e.total) * 100;
                updateLoadingPercentage(percentComplete);
            }
        };

        xhr.onloadstart = function (e) {
            updateLoadingPercentage(0); // Initialize progress bar at 0%
        };

        xhr.onload = function () {
            if (xhr.status === 200) {
                updateLoadingPercentage(100); // Complete, set progress to 100%
                hideLoadingIndicator();
                notify('Upload complete.');
                fetchFiles(); // Refresh list after upload
            } else {
                hideLoadingIndicator();
                let msg = 'An error occurred during the upload.';
                try { msg = JSON.parse(xhr.responseText).error || msg; } catch (e) {}
                notify(msg, true);
            }
        };

        xhr.onerror = function () {
            hideLoadingIndicator();
            notify('An error occurred during the upload.', true);
        };

        xhr.send(formData);
    };
    input.click(); // Open file selection dialog
}

function printSelectedFile() {
    if (selectedFile) {
        const body = { filename: selectedFile };
        if (document.getElementById('mix-first').checked) {
            body.mix = {
                cycles: parseInt(document.getElementById('mix-cycles').value),
                stroke: parseFloat(document.getElementById('mix-stroke').value),
            };
        }
        showLoadingIndicator(body.mix ? 'Preparing mix...' : 'Preparing print...');

        fetch('/print-file', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(body),
        })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    notify(data.error, true);
                    hideLoadingIndicator();
                } else {
                    checkProgress(data.filename || selectedFile); // Start polling for progress
                    notify(data.message);
                }
            })
            .catch(error => {
                hideLoadingIndicator();
                console.error('Error:', error);
            });
    } else {
        notify('Please select a file first.', true);
    }
}

function updatePrintProgress(percent) {
    showLoadingIndicator(`Sending to printer... (${percent}%)`);
    updateLoadingPercentage(percent);

    if (percent >= 100) {
        // Hide the loading indicator after a short delay so the user sees completion
        setTimeout(hideLoadingIndicator, 1000);
    }
}

// Show the loading indicator with message and percentage
function showLoadingIndicator(message) {
    let loadingDiv = document.getElementById('loading-indicator');
    if (!loadingDiv) {
        loadingDiv = document.createElement('div');
        loadingDiv.id = 'loading-indicator';
        loadingDiv.innerHTML = `
            <div class="loading-spinner"></div>
            <p id="loading-msg"></p>
            <p id="loading-percentage">0%</p>`;
        document.body.appendChild(loadingDiv);
        document.getElementById('loading-msg').innerText = message;
    } else {
        document.getElementById('loading-msg').innerText = message;
    }
}

function hideLoadingIndicator() {
    let loadingDiv = document.getElementById('loading-indicator');
    if (loadingDiv) {
        loadingDiv.remove();
    }
}

function updateLoadingPercentage(percent) {
    let percentageText = document.getElementById('loading-percentage');
    if (percentageText) {
        percentageText.innerText = `${percent.toFixed(2)}%`;
    }
}

function checkProgress(filename) {
    fetch(`/progress/${encodeURIComponent(filename)}`)
        .then(response => response.json())
        .then(data => {
            updatePrintProgress(data.progress);
            if (data.error) {
                hideLoadingIndicator();
                notify('Error: ' + data.error, true);
                return;
            }
            if (data.progress < 100) {
                setTimeout(() => checkProgress(filename), 1000); // Poll every second
            }
        })
        .catch(error => console.error('Error:', error));
}

function fetchPrintStatus() {
    fetch('/print-status')
        .then(response => response.json())
        .then(renderStatus)
        .catch(error => {
            console.error('Error:', error);
            renderStatus({ error: 'Error loading status.' });
        });
}

function renderStatus(data) {
    const online = !!data.is_online;
    const status = data.status || '';
    const mix = online ? data.mix : null;
    const mixEnded = mix && mix.state !== 'running';
    const isPausedHW = online && status.includes('PAUSED_HW');
    const isPaused = isPausedHW || (online && status.includes('PAUSED_LIFTED'));
    // After a job the printer stays BUSY with COMPLETE/STOPPED: not active
    const isDone = /Print Status: (COMPLETE|STOPPED|CANCELED|IDLE)/.test(status);
    const isBusy = online && status.includes('BUSY') && !isDone && !mixEnded;
    // Printer keeps BUSY until its 'print complete' screen is confirmed
    const needsConfirm = online && isDone && status.includes('BUSY');
    const jobShown = online && data.file && (isBusy || isPaused || needsConfirm);
    printerState = { ...data, isBusy, isPaused, needsConfirm, jobShown };
    markPrintingRow();

    const badge = document.getElementById('online-status');
    badge.className = 'online-status ' + (online ? 'online' : 'offline');
    badge.textContent = online ? 'ONLINE' : 'OFFLINE';

    // Status grid
    const grid = document.getElementById('status-grid');
    grid.innerHTML = '';
    const item = (label, value, extra) => {
        const d = document.createElement('div');
        d.className = 'status-item';
        const l = document.createElement('span');
        l.className = 'field-label';
        l.textContent = label;
        const v = document.createElement('span');
        v.className = 'field-value';
        v.textContent = value;
        d.append(l, v);
        if (extra) v.appendChild(extra);
        grid.appendChild(d);
    };
    if (!online) {
        item('Printer', data.error || (data.printer ? data.printer + ' does not answer' : data.status || 'Offline'));
    } else {
        item('Machine', data.machine || '—');
        if (mix) {
            item('Mix', mix.state === 'finished' ? 'Finished ✓'
                : mix.state === 'stopped' ? `Stopped at cycle ${mix.cycle} / ${mix.cycles}`
                : `Cycle ${mix.cycle} / ${mix.cycles}`);
            item('File', 'Resin mix');
        } else {
            item('Print', data.print || '—');
            let layers = '—';
            if (data.total_layers) {
                layers = `${data.current_layer} / ${data.total_layers}`;
                const lh = data.layer_height;
                if (lh) layers += `  ·  ${fmtMM(data.current_layer * lh)} / ${fmtMM(data.total_layers * lh)} mm`;
            }
            item('Layer', layers);
            let thumb = null;
            if (data.file && data.file !== selectedFile && data.file in fileInfo) {
                thumb = Object.assign(document.createElement('img'), { className: 'mini-thumb', alt: '', src: previewUrl(data.file) });
                thumb.onerror = () => thumb.remove();
            }
            item('File', data.file || '—', thumb);
        }
    }

    document.getElementById('confirm-hint').style.display = needsConfirm ? 'block' : 'none';

    // Progress
    const bar = document.getElementById('progress-bar');
    const text = document.getElementById('progress-text');
    const pct = online ? (parseFloat(data.progress) || 0) : 0;
    bar.style.width = pct.toFixed(2) + '%';
    bar.classList.toggle('done', !!(mix && mix.state === 'finished'));
    bar.classList.toggle('mixing', !!(mix && mix.state === 'running'));
    text.textContent = !mix ? pct.toFixed(1) + '%'
        : mix.state === 'finished' ? 'Mix finished ✓'
        : mix.state === 'stopped' ? 'Mix stopped'
        : 'Mixing ' + pct.toFixed(0) + '%';

    const timeInfo = document.getElementById('time-info');
    const showTime = (isBusy || isPaused) && data.remaining_s != null;
    timeInfo.style.display = showTime ? 'flex' : 'none';
    if (showTime) {
        const eta = new Date(Date.now() + data.remaining_s * 1000)
            .toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
        timeInfo.innerHTML = '';
        [['Elapsed', formatDuration(data.elapsed_s)],
         ['Remaining', formatDuration(data.remaining_s)],
         ['Ready at', isPaused ? '—' : eta]].forEach(([label, value]) => {
            const span = document.createElement('span');
            span.textContent = label + ' ';
            const b = document.createElement('strong');
            b.textContent = value;
            span.appendChild(b);
            timeInfo.appendChild(span);
        });
    }

    if (window.setFaviconState) {
        const fav = !online ? 'offline' : isPaused ? 'paused'
            : isBusy ? 'printing' : needsConfirm ? 'done' : 'idle';
        setFaviconState(fav, data.progress);
    }
    showJob(data.job);

    const blocked = !online || isBusy || isPaused || needsConfirm;
    const why = !online ? 'Printer offline' : needsConfirm ? 'Confirm the screen on the printer first'
        : blocked ? 'Printer is busy' : '';
    for (const id of ['btn-mix', 'btn-print']) {
        const b = document.getElementById(id);
        b.disabled = blocked;
        b.title = why;
    }

    const controls = document.getElementById('print-controls');
    controls.style.display = (isBusy || isPaused) ? 'flex' : 'none';
    document.getElementById('btn-pause').style.display = isPaused ? 'none' : 'inline-block';
    document.getElementById('btn-resume').style.display = isPaused ? 'inline-block' : 'none';

    showBigPreview();
}

function fmtMM(mm) {
    return (Math.round(mm * 10) / 10).toFixed(1);
}

// Server-side background job (mix watcher, print after mix)
let lastJobId = null;
function showJob(job) {
    const el = document.getElementById('job-info');
    if (!job || !el) return;
    el.style.display = job.text ? 'block' : 'none';
    el.textContent = job.text || '';
    if (lastJobId !== null && job.id !== lastJobId && job.error) notify(job.error, true);
    lastJobId = job.id;
}

// Same estimate as goo_file.mix_seconds()
function updateMixEstimate() {
    const cycles = parseInt(document.getElementById('mix-cycles').value) || 0;
    const stroke = parseFloat(document.getElementById('mix-stroke').value) || 0;
    const perCycle = 2 * (1 / 150 + Math.max(stroke - 1, 0) / 500) * 60 + 1.6;
    document.getElementById('mix-estimate').textContent = `≈ ${Math.max(1, Math.round(perCycle * cycles / 60))} min`;
}

async function startMix() {
    const cycles = parseInt(document.getElementById('mix-cycles').value);
    const stroke = parseFloat(document.getElementById('mix-stroke').value);
    if (!await askConfirm(`Mix resin: ${cycles} cycles of ${stroke} mm?`, 'Mix')) return;
    showLoadingIndicator('Preparing mix...');
    fetch('/mix', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cycles: cycles, stroke: stroke }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                hideLoadingIndicator();
                notify(data.error, true);
            } else {
                checkProgress(data.filename);
            }
        })
        .catch(error => {
            hideLoadingIndicator();
            console.error('Error:', error);
        });
}

async function stopPrint() {
    if (!await askConfirm('Stop the current print?', 'Stop', true)) return;
    fetch('/stop-print', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.error) notify('Error: ' + data.error, true);
            else fetchPrintStatus();
        });
}

function pausePrint() {
    fetch('/pause-print', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.error) notify('Error: ' + data.error, true);
            else fetchPrintStatus();
        });
}

function resumePrint() {
    fetch('/resume-print', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.error) notify('Error: ' + data.error, true);
            else fetchPrintStatus();
        });
}

function previewUrl(name) {
    return '/preview/' + encodeURIComponent(name) + '?v=' + ((fileInfo[name] || {}).uploaded || 0);
}

function fetchFiles() {
    fetch('/files')
        .then(response => response.json())
        .then(files => {
            const tbody = document.getElementById('file-list').querySelector('tbody');
            tbody.innerHTML = '';
            fileInfo = {};
            files.forEach(f => { fileInfo[f.name] = f; });
            if (selectedFile && !(selectedFile in fileInfo)) selectedFile = null;
            if (!files.length) {
                tbody.innerHTML = '<tr class="empty"><td colspan="6">No files yet &mdash; upload a .ctb or .goo.</td></tr>';
            }
            files.forEach(f => {
                const tr = document.createElement('tr');
                tr.dataset.name = f.name;
                if (selectedFile === f.name) tr.classList.add('selected');
                tr.addEventListener('click', () => selectFile(f.name));
                const td = (text, cls) => {
                    const c = document.createElement('td');
                    if (cls) c.className = cls;
                    if (text != null) c.textContent = text;
                    tr.appendChild(c);
                    return c;
                };
                const thumb = Object.assign(document.createElement('img'), { className: 'thumb', loading: 'lazy', alt: '' });
                thumb.onerror = () => thumb.remove();
                thumb.src = previewUrl(f.name);
                td(null, 'thumb-col').appendChild(thumb);
                td(f.name, 'file-name');
                td(f.layers ?? '', 'num muted');
                td(f.height != null ? fmtMM(f.height) + ' mm' : '', 'num muted');
                td(formatDate(f.uploaded), 'file-date');
                td(f.size + ' MB', 'num muted');
                tbody.appendChild(tr);
            });
            showSelected();
            markPrintingRow();
        })
        .catch(error => {
            console.error('Error:', error);
            document.getElementById('file-list').querySelector('tbody').innerHTML = '<tr><td colspan="6">Error loading files.</td></tr>';
        });
}

// seconds -> "1 h 05 min" / "12 min"
function formatDuration(sec) {
    const m = Math.round(sec / 60);
    return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min` : `${m} min`;
}

// e.g. 15-11-2026 20:40
function formatDate(epoch) {
    if (!epoch) return '';
    return new Date(epoch * 1000).toLocaleString('nl-NL', { dateStyle: 'short', timeStyle: 'short' }).replace(',', '');
}

// Mark the file that is on the printer in the file list
function markPrintingRow() {
    const ps = printerState;
    const name = ps.jobShown && !ps.mix ? ps.file : null;
    const text = !name ? '' : ps.isPaused ? 'Paused'
        : ps.needsConfirm ? 'Finished' : 'Printing ' + Math.floor(ps.progress || 0) + '%';
    document.querySelectorAll('.file-list tbody tr').forEach(tr => {
        const on = !!name && tr.dataset.name === name;
        tr.classList.toggle('on-printer', on);
        tr.classList.toggle('paused', on && ps.isPaused);
        const cell = tr.querySelector('.file-name');
        if (!cell) return;
        let badge = cell.querySelector('.row-badge');
        if (on && !badge) {
            badge = Object.assign(document.createElement('span'), { className: 'row-badge' });
            cell.appendChild(badge);
        } else if (!on && badge) {
            badge.remove();
        }
        if (on) badge.textContent = text;
    });
}

function selectFile(filename) {
    selectedFile = filename;
    document.querySelectorAll('.file-list tbody tr').forEach(tr => {
        tr.classList.toggle('selected', tr.dataset.name === filename);
    });
    showSelected();
}

function showSelected() {
    const f = selectedFile && fileInfo[selectedFile];
    document.getElementById('selected-name').textContent = selectedFile || '—';
    const del = document.getElementById('btn-delete');
    del.disabled = !selectedFile;
    del.title = selectedFile ? 'Delete ' + selectedFile : 'Select a file first';
    document.getElementById('selected-meta').textContent = f && f.layers
        ? `${f.layers} layers · ${fmtMM(f.height)} mm · ${f.layer_height} mm/layer` : '';
    showBigPreview();
}

// Big preview: the selected file, otherwise the job on the printer
function showBigPreview() {
    const img = document.getElementById('big-preview');
    const ps = printerState;
    let name = null, label = '';
    if (selectedFile) { name = selectedFile; label = ps.jobShown && ps.file === selectedFile ? 'Printing' : ''; }
    else if (ps.jobShown && !ps.mix && ps.file) { name = ps.file; label = 'Printing'; }
    document.getElementById('preview-label').textContent = label;
    document.getElementById('preview-name').textContent = name || 'Select a file below';
    const want = name ? previewUrl(name) : '';
    if (img.dataset.src === want) return;
    img.dataset.src = want;
    img.style.display = 'none';
    img.nextElementSibling.style.display = 'flex';
    if (want) img.src = want; else img.removeAttribute('src');
}

async function deleteSelectedFile() {
    if (!selectedFile) {
        notify('Please select a file first.', true);
        return;
    }
    const filename = selectedFile;
    if (!await askConfirm(`Delete ${filename}?`, 'Delete', true)) return;

    fetch('/delete-file', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: filename }),
    })
        .then(response => response.json())
        .then(data => {
            if (data.message) {
                notify(data.message);
                selectedFile = null;
            } else {
                notify('Error: ' + data.error, true);
            }
            fetchFiles();
        })
        .catch(error => {
            console.error('Error:', error);
            notify('Could not reach the server.', true);
        });
}

// Automatically update status every 5 seconds
setInterval(fetchPrintStatus, 5000);
// In-page notifications and confirmations: browsers can silently block
// alert()/confirm() ("Don't allow this page to prompt again").
function notify(message, isError = false) {
    let box = document.getElementById('toasts');
    if (!box) {
        box = document.createElement('div');
        box.id = 'toasts';
        document.body.appendChild(box);
    }
    const t = document.createElement('div');
    t.className = 'toast' + (isError ? ' toast-error' : '');
    t.textContent = message;
    t.onclick = () => t.remove();
    box.appendChild(t);
    setTimeout(() => t.remove(), isError ? 8000 : 4000);
}

function askConfirm(message, okLabel = 'OK', danger = false) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        const box = document.createElement('div');
        box.className = 'modal';
        const p = document.createElement('p');
        p.textContent = message;
        const row = document.createElement('div');
        row.className = 'modal-buttons';
        const cancel = document.createElement('button');
        cancel.className = 'btn btn-cancel';
        cancel.textContent = 'Cancel';
        const ok = document.createElement('button');
        ok.className = 'btn ' + (danger ? 'btn-delete' : 'btn-print');
        ok.textContent = okLabel;
        row.append(cancel, ok);
        box.append(p, row);
        overlay.appendChild(box);
        document.body.appendChild(overlay);
        ok.focus();
        const close = result => {
            document.removeEventListener('keydown', onKey);
            overlay.remove();
            resolve(result);
        };
        const onKey = e => {
            if (e.key === 'Escape') close(false);
            if (e.key === 'Enter') close(true);
        };
        document.addEventListener('keydown', onKey);
        cancel.onclick = () => close(false);
        ok.onclick = () => close(true);
        overlay.onclick = e => { if (e.target === overlay) close(false); };
    });
}
