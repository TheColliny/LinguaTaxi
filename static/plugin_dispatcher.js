/**
 * LinguaTaxi — Plugin Dispatcher
 * Provides window.LinguaTaxi.plugins API for plugin registration and event dispatch.
 * Injected into operator.html by the plugin loader.
 */
window.LinguaTaxi = window.LinguaTaxi || {};
window.LinguaTaxi.plugins = (() => {
  const _registry = {};
  const _panels = {};

  function register(pluginId, handlers) {
    _registry[pluginId] = handlers;
  }

  function fire(eventName, data) {
    Object.entries(_registry).forEach(([pid, handlers]) => {
      if (typeof handlers[eventName] === 'function') {
        try {
          handlers[eventName](data);
        } catch (e) {
          console.error(`Plugin '${pid}' error on ${eventName}:`, e);
        }
      }
    });
  }

  function togglePanel(pluginId) {
    const body = document.getElementById('plugin-body-' + pluginId);
    const chevron = document.getElementById('plugin-chevron-' + pluginId);
    if (!body) return;
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    if (chevron) chevron.textContent = open ? '\u25B8' : '\u25BE';
  }

  function toggleEnabled(pluginId) {
    const btn = document.getElementById('plugin-toggle-' + pluginId);
    const indicator = document.getElementById('plugin-indicator-' + pluginId);
    if (!btn) return;
    const enabling = btn.textContent.trim() === 'Enable';
    const fd = new FormData();
    fd.append('enabled', enabling ? 'true' : 'false');
    fetch('/api/plugins/' + pluginId + '/enabled', { method: 'POST', body: fd });
    btn.textContent = enabling ? 'Disable' : 'Enable';
    if (indicator) indicator.className = 'plugin-indicator' + (enabling ? ' plugin-indicator--on' : '');
    fire(enabling ? 'on_enabled' : 'on_disabled', { pluginId });
  }

  function openSettings(pluginId) {
    fetch('/api/plugins/' + pluginId + '/settings')
      .then(r => r.json())
      .then(data => {
        _showSettingsDialog(pluginId, data.schema || {}, data.values || {});
      });
  }

  function _showSettingsDialog(pluginId, schema, values) {
    const existing = document.getElementById('plugin-settings-dlg');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'plugin-settings-dlg';
    overlay.className = 'plugin-settings-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const dlg = document.createElement('div');
    dlg.className = 'plugin-settings-dialog';

    let html = '<div class="plugin-settings-title">' + pluginId.replace(/_/g, ' ') + ' Settings</div>';
    html += '<div class="plugin-settings-fields">';

    Object.entries(schema).forEach(([key, def]) => {
      const val = values[key] !== undefined ? values[key] : (def.default || '');
      const type = def.type === 'password' ? 'password' : def.type === 'number' ? 'number' : 'text';
      if (def.type === 'toggle') {
        html += `<label class="plugin-settings-label">${def.label}
          <input type="checkbox" data-key="${key}" ${val ? 'checked' : ''}></label>`;
      } else {
        html += `<label class="plugin-settings-label">${def.label}
          <input type="${type}" data-key="${key}" value="${String(val).replace(/"/g, '&quot;')}" class="plugin-settings-input"></label>`;
      }
    });

    html += '</div>';
    html += '<div class="plugin-settings-btns">';
    html += '<button class="plugin-settings-save" id="ps-save">Save</button>';
    html += '<button class="plugin-settings-cancel" onclick="this.closest(\'.plugin-settings-overlay\').remove()">Cancel</button>';
    html += '</div>';
    dlg.innerHTML = html;
    overlay.appendChild(dlg);
    document.body.appendChild(overlay);

    document.getElementById('ps-save').onclick = () => {
      const fd = new FormData();
      dlg.querySelectorAll('[data-key]').forEach(el => {
        fd.append(el.dataset.key, el.type === 'checkbox' ? el.checked : el.value);
      });
      fetch('/api/plugins/' + pluginId + '/settings', { method: 'POST', body: fd })
        .then(() => overlay.remove());
    };
  }

  function getSettings(pluginId) {
    return fetch('/api/plugins/' + pluginId + '/settings')
      .then(r => r.json())
      .then(d => d.values || {});
  }

  return { register, fire, togglePanel, toggleEnabled, openSettings, getSettings };
})();
