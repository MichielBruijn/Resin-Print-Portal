// favicon.js - Animated favicon that follows the printer state
// States: offline, idle, printing, paused, done
(function () {
    const S = 64;
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = S;
    const ctx = canvas.getContext('2d');
    const C = {
        bg: '#1e293b', metal: '#cbd5e1', resin: '#f97316', model: '#fdba74',
        uv: '#a855f7', green: '#22c55e', yellow: '#eab308', grey: '#64748b',
    };
    let state = 'idle', progress = 0, frame = 0, timer = null, lastUrl = '';

    // Own <link>, added after load: the static icons stay what bookmarks store
    let link = null;

    function roundRect(x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.arcTo(x + w, y, x + w, y + h, r);
        ctx.arcTo(x + w, y + h, x, y + h, r);
        ctx.arcTo(x, y + h, x, y, r);
        ctx.arcTo(x, y, x + w, y, r);
        ctx.closePath();
    }

    function draw() {
        const off = state === 'offline';
        const col = k => off ? C.grey : C[k];
        ctx.clearRect(0, 0, S, S);
        roundRect(0, 0, S, S, 12);
        ctx.fillStyle = C.bg;
        ctx.fill();

        // Build plate: up when idle, peel cycle (dip into vat, lift) when printing
        const p = Math.min(Math.max(progress, 0), 100) / 100;
        const modelH = state === 'printing' || state === 'paused' || state === 'done' ? 3 + p * 15 : 0;
        let plateY = 10;
        let exposing = false;
        if (state === 'idle') {
            plateY = 10 + 4 * (1 - Math.cos(frame / 30 * Math.PI * 2)) / 2;  // slow bob, 3 s
        } else if (state === 'printing') {
            const t = (frame % 20) / 20;  // 2 s per layer at 10 fps
            const dipY = 40 - modelH - 4;  // model touches the resin
            const k = t < 0.35 ? t / 0.35 : t < 0.6 ? 1 : 1 - (t - 0.6) / 0.4;
            plateY = 10 + (dipY - 10) * (k * k * (3 - 2 * k));
            exposing = t >= 0.38 && t < 0.58;
        }

        // Z-rod
        ctx.fillStyle = col('metal');
        ctx.fillRect(30, 0, 4, plateY);
        // Plate
        roundRect(14, plateY, 36, 5, 1.5);
        ctx.fill();
        // Model hangs upside down under the plate
        if (modelH) {
            ctx.fillStyle = col('model');
            ctx.beginPath();
            ctx.moveTo(22, plateY + 5);
            ctx.lineTo(42, plateY + 5);
            ctx.lineTo(38, plateY + 5 + modelH);
            ctx.lineTo(26, plateY + 5 + modelH);
            ctx.closePath();
            ctx.fill();
        }

        // Vat with resin, surface ripples unless offline
        ctx.fillStyle = col('resin');
        ctx.globalAlpha = 0.85;
        ctx.beginPath();
        ctx.moveTo(10, 52);
        for (let x = 10; x <= 54; x += 2) {
            const w = off ? 0 : 1.8 * Math.sin(x / 5 + frame / 3);
            ctx.lineTo(x, 42 + w);
        }
        ctx.lineTo(54, 52);
        ctx.closePath();
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = col('metal');
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.moveTo(9, 38);
        ctx.lineTo(9, 53);
        ctx.lineTo(55, 53);
        ctx.lineTo(55, 38);
        ctx.stroke();

        // UV exposure under the vat
        if (exposing) {
            ctx.fillStyle = C.uv;
            ctx.shadowColor = C.uv;
            ctx.shadowBlur = 8;
            ctx.fillRect(24, 55, 16, 3);
            ctx.shadowBlur = 0;
        }

        // Progress bar along the bottom
        if (state === 'printing' || state === 'paused') {
            ctx.fillStyle = '#334155';
            ctx.fillRect(4, 59, 56, 3);
            ctx.fillStyle = C.green;
            ctx.fillRect(4, 59, 56 * p, 3);
        }

        // Badges
        const blink = frame % 10 < 6;
        if (state === 'paused' && blink) {
            badge(C.yellow);
            ctx.fillStyle = C.bg;
            ctx.fillRect(44, 7, 4, 14);
            ctx.fillRect(52, 7, 4, 14);
        } else if (state === 'done') {
            const r = 12 + (frame % 10 < 5 ? 1 : 0);
            badge(C.green, r);
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 3.5;
            ctx.lineCap = 'round';
            ctx.beginPath();
            ctx.moveTo(44, 14);
            ctx.lineTo(49, 19);
            ctx.lineTo(57, 9);
            ctx.stroke();
            ctx.lineCap = 'butt';
        }

        // Only push changed frames to the browser
        const url = canvas.toDataURL('image/png');
        if (link && url !== lastUrl) {
            link.type = 'image/png';
            link.href = url;
            lastUrl = url;
        }
        frame++;
    }

    function badge(color, r = 12) {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(50, 14, r, 0, Math.PI * 2);
        ctx.fill();
    }

    window.setFaviconState = function (newState, newProgress) {
        progress = parseFloat(newProgress) || 0;
        if (newState === state && timer) return;
        state = newState;
        clearInterval(timer);
        timer = null;
        if (state !== 'offline') timer = setInterval(draw, 100);
        draw();
    };

    window.setFaviconState('idle', 0);
    window.addEventListener('load', () => setTimeout(() => {
        link = document.createElement('link');
        link.rel = 'icon';
        link.type = 'image/png';
        document.head.appendChild(link);
        lastUrl = '';
        draw();
    }, 2000));
})();
