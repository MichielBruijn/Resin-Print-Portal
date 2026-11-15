// script.js - Printer status polling, file management, upload, print control (pause/resume/stop)

let selectedFile = null;

document.addEventListener('DOMContentLoaded', (event) => {
    updateMixEstimate();
    fetchPrintStatus();
    fetchFiles();
    loadPrinterIP();
});

function loadPrinterIP() {
    fetch('/get-printer-ip')
        .then(response => response.json())
        .then(data => {
            if (data.ip) {
                document.getElementById('printer-ip').value = data.ip;
            } else {
                console.error('Error loading IP:', data.error);
            }
        });
}

// Save the IP address to the server
function saveIP() {
    let ipField = document.getElementById('printer-ip');
    let saveBtn = document.querySelector('.btn-save');
    let newIP = ipField.value;

    fetch('/set-printer-ip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ip: newIP }),
    })
        .then(response => response.json())
        .then(data => {
            if (data.message) {
                notify('Printer address saved.');
                ipField.readOnly = true;
                saveBtn.style.display = 'none';
            } else {
                console.error('Error saving IP:', data.error);
                notify('Error saving: ' + data.error, true);
            }
        });
}

function editIP() {
    let ipField = document.getElementById('printer-ip');
    let saveBtn = document.querySelector('.btn-save');

    ipField.readOnly = false;
    ipField.focus();
    ipField.select();

    saveBtn.style.display = 'inline-block';
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
        showLoadingIndicator('Preparing print...');

        fetch('/print-file', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ filename: selectedFile }),
        })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    notify(data.error, true);
                    hideLoadingIndicator();
                } else {
                    checkProgress(selectedFile); // Start polling for progress
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
        .then(data => {
            document.getElementById('status').innerText = data.status || data.error || '';
            let progressBar = document.getElementById('progress-bar');
            let progressText = document.getElementById('progress-text');

            let progressValue = (parseFloat(data.progress) || 0).toFixed(2);
            if (progressBar) progressBar.style.width = progressValue + '%';
            if (progressText) progressText.innerText = progressValue + '%';

            const onlineStatusElement = document.getElementById('online-status');
            if (onlineStatusElement) {
                if (data.is_online) {
                    onlineStatusElement.className = 'online-status online';
                    onlineStatusElement.textContent = 'ONLINE';
                } else {
                    onlineStatusElement.className = 'online-status offline';
                    onlineStatusElement.textContent = 'OFFLINE';
                }
            }

            const controls = document.getElementById('print-controls');
            const btnPause = document.getElementById('btn-pause');
            const btnResume = document.getElementById('btn-resume');
            const isPausedHW = data.is_online && data.status && data.status.includes('PAUSED_HW');
            const isPausedLifted = !isPausedHW && data.is_online && data.status && data.status.includes('PAUSED_LIFTED');
            const isPaused = isPausedHW || isPausedLifted;
            // After a job the printer stays BUSY with COMPLETE/STOPPED: not active
            const isDone = data.status && /Print Status: (COMPLETE|STOPPED|CANCELED|IDLE)/.test(data.status);
            const isBusy = data.is_online && data.status && data.status.includes('BUSY') && !isDone;
            // Printer keeps BUSY until its 'print complete' screen is confirmed
            const needsConfirm = data.is_online && isDone && data.status.includes('BUSY');
            const hint = document.getElementById('confirm-hint');
            if (hint) hint.style.display = needsConfirm ? 'block' : 'none';
            const btnMix = document.getElementById('btn-mix');
            if (btnMix) btnMix.disabled = needsConfirm;
            // Slicer preview of the file on the printer (if it was uploaded through RPP)
            const img = document.getElementById('print-preview');
            if (img) {
                const jobShown = isBusy || isPaused || needsConfirm;  // printer still shows this job
                const want = (data.is_online && data.file && jobShown) ? '/preview/' + encodeURIComponent(data.file) : '';
                if (img.dataset.src !== want) {
                    img.dataset.src = want;
                    img.style.display = 'none';
                    if (want) img.src = want; else img.removeAttribute('src');
                }
            }
            const timeInfo = document.getElementById('time-info');
            if (timeInfo) {
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
            }
            const mixPanel = document.getElementById('mix-panel');
            if (mixPanel) mixPanel.style.display = (data.is_online && !isBusy && !isPaused) ? 'block' : 'none';
            if (controls) {
                controls.style.display = (isBusy || isPaused) ? 'flex' : 'none';
                if (btnPause) btnPause.style.display = isPaused ? 'none' : 'inline-block';
                if (btnResume) btnResume.style.display = isPaused ? 'inline-block' : 'none';
            }
        })
        .catch(error => {
            console.error('Error:', error);
            document.getElementById('status').innerText = 'Error loading status.';
        });
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

let fileVersions = {};  // name -> upload time, busts the preview cache on re-upload

function previewUrl(name) {
    return '/preview/' + encodeURIComponent(name) + '?v=' + (fileVersions[name] || 0);
}

function fetchFiles() {
    fetch('/files')
        .then(response => response.json())
        .then(filesInfo => {
            const tbody = document.getElementById('file-list').querySelector('tbody');
            tbody.innerHTML = '';
            fileVersions = {};
            filesInfo.forEach(f => { fileVersions[f.name] = f.uploaded; });
            if (selectedFile && !(selectedFile in fileVersions)) selectedFile = null;
            filesInfo.forEach(fileInfo => {
                const tr = document.createElement('tr');
                tr.dataset.name = fileInfo.name;
                if (selectedFile === fileInfo.name) tr.classList.add('selected');
                tr.addEventListener('click', () => selectFile(fileInfo.name));
                const tdThumb = document.createElement('td');
                tdThumb.className = 'thumb-col';
                const thumb = document.createElement('img');
                thumb.className = 'thumb';
                thumb.loading = 'lazy';
                thumb.alt = '';
                thumb.onerror = () => thumb.remove();
                thumb.src = previewUrl(fileInfo.name);
                tdThumb.appendChild(thumb);
                const tdName = document.createElement('td');
                tdName.textContent = fileInfo.name;
                const tdDate = document.createElement('td');
                tdDate.className = 'file-date';
                tdDate.textContent = formatDate(fileInfo.uploaded);
                const tdSize = document.createElement('td');
                tdSize.className = 'file-size';
                tdSize.textContent = fileInfo.size + ' MB';
                tr.appendChild(tdThumb);
                tr.appendChild(tdName);
                tr.appendChild(tdDate);
                tr.appendChild(tdSize);
                tbody.appendChild(tr);
            });
            showFilePreview();
        })
        .catch(error => {
            console.error('Error:', error);
            document.getElementById('file-list').querySelector('tbody').innerHTML = '<tr><td colspan="4">Error loading files.</td></tr>';
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

function selectFile(filename) {
    selectedFile = filename;
    document.querySelectorAll('.file-list tbody tr').forEach(tr => {
        tr.classList.toggle('selected', tr.dataset.name === filename);
    });
    showFilePreview();
}

// Large preview of the selected file under the list
function showFilePreview() {
    const box = document.getElementById('file-preview');
    const img = document.getElementById('file-preview-img');
    if (!box || !img) return;
    if (!selectedFile) {
        box.style.display = 'none';
        img.removeAttribute('src');
        img.dataset.src = '';
        return;
    }
    document.getElementById('file-preview-name').textContent = selectedFile;
    const want = previewUrl(selectedFile);
    if (img.dataset.src !== want) {
        img.dataset.src = want;
        box.style.display = 'none';  // shown by onload if the file has a preview
        img.src = want;
    }
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
