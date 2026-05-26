/**
 * LinguaTaxi — Window Capture Plugin
 *
 * Operator side: getDisplayMedia -> MediaRecorder (H.264/VP8) -> binary chunks -> WebSocket
 * Display side: receives binary chunks via on_binary -> MediaSource -> <video> element
 */

(function() {
  let isOperatorPage = null;
  let stream = null;
  let recorder = null;
  let captureWs = null;
  let videoEl = null;

  let fps = 30;
  let maxHeight = 480;
  let bitrate = 1500000;

  // Display-side MediaSource state
  let mediaSource = null;
  let sourceBuffer = null;
  let pendingChunks = [];
  let mimeType = '';
  let sourceOpen = false;
  let firstChunkReceived = false;

  function $(id) { return document.getElementById(id); }

  document.addEventListener('DOMContentLoaded', function() { detectPage(); });

  async function detectPage() {
    try {
      const resp = await fetch('/api/window-capture/status');
      isOperatorPage = resp.ok;
    } catch(e) {
      isOperatorPage = false;
    }

    if (isOperatorPage) {
      var op = $('wc-operator');
      if (op) op.style.display = 'block';
      loadSettings();
    } else {
      var dp = $('wc-display');
      if (dp) dp.style.display = 'block';
    }
  }

  async function loadSettings() {
    try {
      const resp = await fetch('/api/plugins/window_capture/settings');
      if (!resp.ok) return;
      const data = await resp.json();
      const vals = data.values || {};
      fps = Math.max(1, Math.min(30, parseInt(vals.fps, 10) || 30));
      maxHeight = Math.max(120, Math.min(1080, parseInt(vals.max_height, 10) || 480));
      bitrate = Math.max(500000, Math.min(5000000, parseInt(vals.bitrate, 10) || 1500000));
    } catch(e) {}
  }

  // ── Codec negotiation ──────────────────────────────────────────────────

  function pickMimeType() {
    var candidates = [
      'video/webm;codecs=h264',
      'video/webm;codecs=vp8',
      'video/webm;codecs=vp9',
      'video/webm',
    ];
    for (var i = 0; i < candidates.length; i++) {
      if (MediaRecorder.isTypeSupported(candidates[i])) return candidates[i];
    }
    return '';
  }

  // ── Operator: capture & stream ─────────────────────────────────────────

  window._wcStart = async function() {
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: { ideal: fps }, height: { ideal: maxHeight } },
        audio: false
      });
    } catch(e) {
      var st = $('wc-status');
      if (st) st.textContent = e.name === 'NotAllowedError' ? '' : 'Failed: ' + e.message;
      return;
    }

    videoEl = $('wc-preview');
    videoEl.srcObject = stream;
    var wrap = $('wc-preview-wrap');
    if (wrap) wrap.style.display = 'block';

    $('wc-start').style.display = 'none';
    $('wc-stop').style.display = '';
    $('wc-status').textContent = 'Connecting\u2026';

    await new Promise(function(r) { videoEl.onloadedmetadata = r; });
    await videoEl.play();

    mimeType = pickMimeType();
    if (!mimeType) {
      $('wc-status').textContent = 'No supported codec';
      stopCapture(false);
      return;
    }

    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    captureWs = new WebSocket(proto + '//' + location.host + '/api/window-capture/ws');
    captureWs.binaryType = 'arraybuffer';

    captureWs.onopen = function() {
      // Send the MIME type as JSON so display.js routes it through handleMsg
      captureWs.send(JSON.stringify({ type: 'wc_init', mime: mimeType }));
      startRecording();
      $('wc-status').textContent = 'Streaming (' + mimeType.split(';')[0] + ')';
    };

    captureWs.onerror = function() {
      $('wc-status').textContent = 'Connection error';
    };

    captureWs.onclose = function() {
      stopCapture(false);
    };

    stream.getVideoTracks()[0].onended = function() { window._wcStop(); };
  };

  function startRecording() {
    recorder = new MediaRecorder(stream, {
      mimeType: mimeType,
      videoBitsPerSecond: bitrate,
    });

    recorder.ondataavailable = function(e) {
      if (e.data && e.data.size > 0 && captureWs && captureWs.readyState === WebSocket.OPEN) {
        e.data.arrayBuffer().then(function(buf) {
          captureWs.send(buf);
        });
      }
    };

    // Request data every 100ms for low latency
    recorder.start(100);
  }

  window._wcStop = function() {
    stopCapture(true);
  };

  function stopCapture(closeWs) {
    if (recorder && recorder.state !== 'inactive') {
      try { recorder.stop(); } catch(e) {}
      recorder = null;
    }
    if (stream) { stream.getTracks().forEach(function(t) { t.stop(); }); stream = null; }
    if (closeWs && captureWs) { captureWs.close(); captureWs = null; }

    var v = $('wc-preview');
    if (v) v.srcObject = null;
    var wrap = $('wc-preview-wrap');
    if (wrap) wrap.style.display = 'none';

    var startBtn = $('wc-start');
    var stopBtn = $('wc-stop');
    var st = $('wc-status');
    if (startBtn) startBtn.style.display = '';
    if (stopBtn) stopBtn.style.display = 'none';
    if (st) st.textContent = '';
  }

  // ── Display: receive & render via MediaSource ──────────────────────────

  function resetMediaSource() {
    sourceOpen = false;
    firstChunkReceived = false;
    pendingChunks = [];
    sourceBuffer = null;
    mediaSource = null;

    var vid = $('wc-video');
    if (vid) vid.src = '';
  }

  function initMediaSource(mime) {
    resetMediaSource();

    var vid = $('wc-video');
    if (!vid) return;

    // Check if MediaSource supports this MIME
    if (!MediaSource.isTypeSupported(mime)) {
      // Try without the codecs parameter
      var base = mime.split(';')[0];
      if (MediaSource.isTypeSupported(base)) {
        mime = base;
      } else {
        return;
      }
    }

    mimeType = mime;
    mediaSource = new MediaSource();
    vid.src = URL.createObjectURL(mediaSource);

    mediaSource.addEventListener('sourceopen', function() {
      try {
        sourceBuffer = mediaSource.addSourceBuffer(mimeType);
      } catch(e) {
        return;
      }
      sourceOpen = true;
      sourceBuffer.mode = 'sequence';

      sourceBuffer.addEventListener('updateend', function() {
        flushPending();
      });

      // Flush any chunks that arrived before sourceopen
      flushPending();
    });

    mediaSource.addEventListener('sourceended', function() {
      sourceOpen = false;
    });
  }

  function flushPending() {
    if (!sourceBuffer || sourceBuffer.updating || pendingChunks.length === 0) return;
    var chunk = pendingChunks.shift();
    try {
      sourceBuffer.appendBuffer(chunk);
    } catch(e) {
      // QuotaExceededError — trim old data and retry
      if (e.name === 'QuotaExceededError' && !sourceBuffer.updating) {
        try {
          var buffered = sourceBuffer.buffered;
          if (buffered.length > 0) {
            sourceBuffer.remove(0, buffered.end(buffered.length - 1) - 5);
          }
        } catch(e2) {}
      }
    }
  }

  function appendChunk(data) {
    var buf = (data instanceof ArrayBuffer) ? data : null;
    if (!buf && data instanceof Blob) {
      data.arrayBuffer().then(function(ab) {
        pendingChunks.push(ab);
        flushPending();
      });
      return;
    }
    if (buf) {
      pendingChunks.push(buf);
      flushPending();
    }
  }

  function onBinaryData(data) {
    var empty = $('wc-empty');
    var vid = $('wc-video');
    if (!vid) return;

    // First text message is the MIME type
    if (typeof data === 'string') {
      if (empty) empty.style.display = 'none';
      initMediaSource(data);
      vid.style.display = 'block';
      return;
    }

    if (!firstChunkReceived) {
      firstChunkReceived = true;
      if (empty) empty.style.display = 'none';
      vid.style.display = 'block';
    }

    appendChunk(data);

    // Keep playback near live edge
    if (vid.paused) {
      vid.play().catch(function() {});
    }
    if (vid.buffered.length > 0) {
      var edge = vid.buffered.end(vid.buffered.length - 1);
      if (edge - vid.currentTime > 2) {
        vid.currentTime = edge - 0.3;
      }
    }
  }

  function onStreamEnd() {
    var empty = $('wc-empty');
    var vid = $('wc-video');
    if (empty) empty.style.display = '';
    if (vid) vid.style.display = 'none';
    resetMediaSource();
  }

  // Register with plugin system
  window.LinguaTaxi.plugins.register('window_capture', {
    on_enabled: function() {},
    on_disabled: function() { stopCapture(true); onStreamEnd(); },
    on_binary: function(data) {
      if (!isOperatorPage) onBinaryData(data);
    },
  });
})();
