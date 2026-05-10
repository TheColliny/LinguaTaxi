/**
 * LinguaTaxi — Live Dial Testing Plugin Panel
 * Operator dashboard: tunnel control, QR code, real-time sentiment graph.
 */

(function() {
  let ws = null;
  let tunnelActive = false;
  let tunnelUrl = '';
  let history = [];        // [{time, avg, count, speaker}]
  const MAX_POINTS = 300;  // 5 minutes of 1/sec samples
  let reconnectAttempts = 0;

  let elTunnelBtn, elTunnelInfo, elQR, elAudienceCount;
  let elSpeakerInput, elAvgDisplay, elCanvas;
  let ctx = null;

  function $(id) { return document.getElementById(id); }

  document.addEventListener('DOMContentLoaded', () => {
    elTunnelBtn     = $('ld-tunnel-btn');
    elTunnelInfo    = $('ld-tunnel-info');
    elQR            = $('ld-qr');
    elAudienceCount = $('ld-audience-count');
    elSpeakerInput  = $('ld-speaker-input');
    elAvgDisplay    = $('ld-avg-display');
    elCanvas        = $('ld-canvas');

    if (elCanvas) {
      // Scale canvas for sharp rendering
      const rect = elCanvas.parentElement.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      elCanvas.width = Math.floor(rect.width * dpr);
      elCanvas.height = 140 * dpr;
      elCanvas.style.width = rect.width + 'px';
      elCanvas.style.height = '140px';
      ctx = elCanvas.getContext('2d');
      ctx.scale(dpr, dpr);

      // Redraw on container resize
      const ro = new ResizeObserver(() => {
        if (!elCanvas) return;
        const rect = elCanvas.parentElement.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        elCanvas.width = Math.floor(rect.width * dpr);
        elCanvas.height = 140 * dpr;
        elCanvas.style.width = rect.width + 'px';
        elCanvas.style.height = '140px';  // lock CSS height so backing store doesn't grow 2x on HiDPI
        ctx = elCanvas.getContext('2d');
        ctx.scale(dpr, dpr);
        drawGraph();
      });
      if (elCanvas.parentElement) ro.observe(elCanvas.parentElement);
    }

    if (elSpeakerInput) {
      elSpeakerInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') window._ldSetSpeaker();
      });
    }

    connectWS();
    // Note: intentionally NOT calling fetchStatus() here — the WS 'init' message
    // carries the same data, avoiding a race.
  });

  window.addEventListener('beforeunload', () => {
    if (ws) {
      try { ws.close(); } catch(e) {}
    }
  });

  // ── WebSocket to operator endpoint ──

  function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/api/dial/operator-ws`);

    ws.onopen = () => { reconnectAttempts = 0; };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        switch (msg.type) {
          case 'init':
            history = msg.history || [];
            if (history.length > MAX_POINTS) history = history.slice(-MAX_POINTS);
            if (elSpeakerInput && msg.speaker) elSpeakerInput.value = msg.speaker;
            if (msg.tunnel_url) setTunnelUrl(msg.tunnel_url);
            updateCount(msg.audience_count);
            drawGraph();
            break;
          case 'sample':
            history.push(msg);
            if (history.length > MAX_POINTS) history.shift();
            updateAvg(msg.avg, msg.count);
            drawGraph();
            break;
          case 'count':
            updateCount(msg.audience_count);
            break;
          case 'speaker':
            if (elSpeakerInput) elSpeakerInput.value = msg.name || '';
            break;
          case 'reset':
            history = [];
            drawGraph();
            updateAvg(50, 0);
            break;
          case 'tunnel_down':
            tunnelActive = false;
            tunnelUrl = '';
            if (elTunnelBtn) elTunnelBtn.textContent = 'Start Tunnel';
            if (elTunnelInfo) elTunnelInfo.innerHTML = '<span class="ld-tunnel-pending">Tunnel disconnected</span>';
            if (elQR) elQR.innerHTML = '';
            break;
        }
      } catch(err) {}
    };

    ws.onclose = () => {
      reconnectAttempts++;
      if (reconnectAttempts > 50) {
        // give up — leave state visible to user (handled elsewhere)
        return;
      }
      const delay = Math.min(1000 * Math.pow(1.5, reconnectAttempts), 10000);
      setTimeout(connectWS, delay);
    };
    ws.onerror = () => ws.close();
  }

  // ── Status fetch ──

  async function fetchStatus() {
    try {
      const resp = await fetch('/api/dial/status');
      if (!resp.ok) return;
      const s = await resp.json();
      updateCount(s.audience_count);
      if (s.tunnel_url) setTunnelUrl(s.tunnel_url);
      if (s.current_avg != null) updateAvg(s.current_avg, s.audience_count);
    } catch(e) {}
  }

  // ── Tunnel control ──

  window._ldToggleTunnel = async function() {
    if (tunnelActive) {
      elTunnelBtn.disabled = true;
      elTunnelBtn.textContent = 'Stopping...';
      await fetch('/api/dial/tunnel/stop', { method: 'POST' });
      tunnelActive = false;
      tunnelUrl = '';
      elTunnelBtn.textContent = 'Start Tunnel';
      elTunnelBtn.disabled = false;
      if (elTunnelInfo) elTunnelInfo.innerHTML = '';
      if (elQR) elQR.innerHTML = '';
    } else {
      elTunnelBtn.disabled = true;
      elTunnelBtn.textContent = 'Starting...';
      const resp = await fetch('/api/dial/tunnel/start', { method: 'POST' });
      const data = await resp.json();
      if (data.tunnel_url) {
        setTunnelUrl(data.tunnel_url);
      } else {
        if (elTunnelInfo) elTunnelInfo.innerHTML = '<span class="ld-tunnel-pending">Tunnel starting... check back in a few seconds</span>';
      }
      elTunnelBtn.disabled = false;
    }
  };

  function setTunnelUrl(url) {
    if (!/^https?:\/\//i.test(url)) return;
    tunnelUrl = url;
    tunnelActive = true;
    if (elTunnelBtn) elTunnelBtn.textContent = 'Stop Tunnel';

    const audienceUrl = url.replace(/\/$/, '') + '/api/dial/audience';
    if (elTunnelInfo) {
      elTunnelInfo.innerHTML = `<a href="${esc(audienceUrl)}" target="_blank" class="ld-tunnel-link">${esc(audienceUrl)}</a>`;
    }
    // QR code via api.qrserver.com (Google Charts QR API is deprecated)
    if (elQR) {
      const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(audienceUrl)}`;
      elQR.innerHTML = `<img src="${esc(qrUrl)}" alt="QR Code" class="ld-qr-img">`;
    }
  }

  // ── Speaker control ──

  window._ldSetSpeaker = async function() {
    const name = elSpeakerInput ? elSpeakerInput.value.trim() : '';
    await fetch('/api/dial/speaker', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
  };

  // ── Reset ──

  window._ldReset = async function() {
    await fetch('/api/dial/reset', { method: 'POST' });
    if (elSpeakerInput) elSpeakerInput.value = '';
  };

  // ── Display updates ──

  function updateCount(n) {
    if (elAudienceCount) elAudienceCount.textContent = `${n} connected`;
  }

  function updateAvg(avg, count) {
    if (!elAvgDisplay) return;
    const rounded = Math.round(avg);
    elAvgDisplay.textContent = rounded;
    if (rounded >= 60) elAvgDisplay.style.color = '#4CAF50';
    else if (rounded >= 40) elAvgDisplay.style.color = '#FFC107';
    else elAvgDisplay.style.color = '#f44336';
  }

  // ── Graph rendering (Canvas) ──

  function drawGraph() {
    if (!ctx || !elCanvas) return;
    const W = elCanvas.width / (window.devicePixelRatio || 1);
    const H = 140;
    const pad = { top: 8, bottom: 8, left: 4, right: 4 };
    const gW = W - pad.left - pad.right;
    const gH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);

    // Grid lines at 25, 50, 75
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 1;
    [25, 50, 75].forEach(v => {
      const y = pad.top + gH * (1 - v / 100);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(W - pad.right, y);
      ctx.stroke();
    });

    // 50-line (neutral) — slightly brighter
    const midY = pad.top + gH * 0.5;
    ctx.strokeStyle = 'rgba(255,255,255,0.15)';
    ctx.beginPath();
    ctx.moveTo(pad.left, midY);
    ctx.lineTo(W - pad.right, midY);
    ctx.stroke();

    if (history.length < 2) return;

    const points = history.slice(-MAX_POINTS);
    const step = gW / (MAX_POINTS - 1);

    // Fill gradient under the line
    const grad = ctx.createLinearGradient(0, pad.top, 0, H - pad.bottom);
    grad.addColorStop(0, 'rgba(76,175,80,0.15)');
    grad.addColorStop(0.5, 'rgba(255,193,7,0.05)');
    grad.addColorStop(1, 'rgba(244,67,54,0.15)');

    ctx.beginPath();
    const startX = pad.left + (MAX_POINTS - points.length) * step;
    ctx.moveTo(startX, pad.top + gH * (1 - points[0].avg / 100));
    for (let i = 1; i < points.length; i++) {
      const x = startX + i * step;
      const y = pad.top + gH * (1 - points[i].avg / 100);
      ctx.lineTo(x, y);
    }

    // Fill
    const lastX = startX + (points.length - 1) * step;
    ctx.lineTo(lastX, H - pad.bottom);
    ctx.lineTo(startX, H - pad.bottom);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // Stroke the line
    ctx.beginPath();
    ctx.moveTo(startX, pad.top + gH * (1 - points[0].avg / 100));
    for (let i = 1; i < points.length; i++) {
      const x = startX + i * step;
      const y = pad.top + gH * (1 - points[i].avg / 100);
      ctx.lineTo(x, y);
    }
    ctx.strokeStyle = '#4FC3F7';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Current value dot
    const lastPt = points[points.length - 1];
    const dotX = startX + (points.length - 1) * step;
    const dotY = pad.top + gH * (1 - lastPt.avg / 100);
    ctx.beginPath();
    ctx.arc(dotX, dotY, 4, 0, Math.PI * 2);
    ctx.fillStyle = '#4FC3F7';
    ctx.fill();
  }

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ── Plugin registration ──
  window.LinguaTaxi.plugins.register('live_dial', {
    on_session_start: () => { window._ldReset(); }
  });
})();
