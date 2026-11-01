// 3MF Addon promo — shown on every generator page when the addon is absent.
// Runs after SETTINGS_STATE is defined (same script block) and after the DOM
// is ready (this script runs at the bottom of the page). SETTINGS_STATE.has3mf
// is set by Blender via build_settings_row_state(); if true, this whole block
// is skipped immediately.
(function () {
    if (SETTINGS_STATE.has3mf) return;

    var GITHUB_URL = 'https://github.com/Clonephaze/3MF-Blender-Add-on---Maintained';

    // ── Styles ────────────────────────────────────────────────────────────────
    var style = document.createElement('style');
    style.textContent = [
        '.tp3d-3mf-install-btn {',
        '  display: block;',
        '  width: 100%;',
        '  margin-bottom: 0;',
        '  padding: 7px 10px;',
        '  background: var(--color-accent, #3d7aff);',
        '  color: #fff;',
        '  border: none;',
        '  border-radius: 5px;',
        '  font-size: 13px;',
        '  font-weight: 600;',
        '  cursor: pointer;',
        '  text-align: center;',
        '  opacity: 0.92;',
        '}',
        '.tp3d-3mf-install-btn:hover { opacity: 1; }',
        '.tp3d-3mf-install-btn:disabled { opacity: 0.5; cursor: default; }',

        '.tp3d-3mf-overlay {',
        '  position: fixed; inset: 0;',
        '  background: rgba(0,0,0,0.55);',
        '  z-index: 9998;',
        '  display: flex; align-items: center; justify-content: center;',
        '}',

        '.tp3d-3mf-dialog {',
        '  background: var(--color-panel-bg, #2a2a2a);',
        '  color: var(--color-text, #e8e8e8);',
        '  border: 1px solid var(--color-border, #444);',
        '  border-radius: 10px;',
        '  padding: 24px 28px 20px;',
        '  max-width: 380px;',
        '  width: 90%;',
        '  box-shadow: 0 8px 32px rgba(0,0,0,0.6);',
        '  z-index: 9999;',
        '}',

        '.tp3d-3mf-dialog h3 {',
        '  margin: 0 0 12px;',
        '  font-size: 16px;',
        '  font-weight: 700;',
        '}',

        '.tp3d-3mf-dialog p {',
        '  margin: 0 0 10px;',
        '  font-size: 13px;',
        '  line-height: 1.5;',
        '}',

        '.tp3d-3mf-dialog ul {',
        '  margin: 4px 0 12px 18px;',
        '  padding: 0;',
        '  font-size: 13px;',
        '  line-height: 1.6;',
        '}',

        '.tp3d-3mf-dialog-actions {',
        '  display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px;',
        '}',

        '.tp3d-3mf-dialog-actions button {',
        '  padding: 8px 18px;',
        '  border-radius: 6px;',
        '  font-size: 13px;',
        '  font-weight: 600;',
        '  cursor: pointer;',
        '  border: none;',
        '}',

        '.tp3d-3mf-btn-install {',
        '  background: var(--color-accent, #3d7aff);',
        '  color: #fff;',
        '}',
        '.tp3d-3mf-btn-install:hover { opacity: 0.88; }',
        '.tp3d-3mf-btn-install:disabled { opacity: 0.5; cursor: default; }',

        '.tp3d-3mf-btn-decline {',
        '  background: var(--color-panel-bg2, #363636);',
        '  color: var(--color-text-muted, #aaa);',
        '  border: 1px solid var(--color-border, #444) !important;',
        '}',
        '.tp3d-3mf-btn-decline:hover { color: var(--color-text, #e8e8e8); }',

        '.tp3d-3mf-github-link {',
        '  display: inline-block;',
        '  margin-top: 4px;',
        '  font-size: 12px;',
        '  color: var(--color-accent, #3d7aff);',
        '  text-decoration: none;',
        '}',
        '.tp3d-3mf-github-link:hover { text-decoration: underline; }',

        '.tp3d-3mf-status {',
        '  font-size: 12px;',
        '  color: var(--color-text-muted, #aaa);',
        '  margin-top: 8px;',
        '  min-height: 16px;',
        '  text-align: center;',
        '}',
        /* the install button takes over the flex margin-top:auto that keeps these at the
           bottom of the sidebar, so whichever of Send / Select Export Folder is showing
           sits 4px below it instead of splitting the free space with it */
        '.tp3d-3mf-install-btn ~ #send, .tp3d-3mf-install-btn ~ #exportFolderSlot { margin-top: 4px !important; }',
        '.tp3d-3mf-install-btn { margin-top: auto; }'
    ].join('\n');
    document.head.appendChild(style);

    // ── Install button above "Send to Blender" ────────────────────────────────
    function insertInstallButton() {
        if (document.getElementById('tp3d3mfInstallBtn')) return;
        var sendBtn = document.getElementById('send');
        if (!sendBtn) return;
        var btn = document.createElement('button');
        btn.id = 'tp3d3mfInstallBtn';
        btn.className = 'tp3d-3mf-install-btn';
        btn.title = 'The free 3MF Addon adds 3MF Export and Map Texturing';
        btn.textContent = 'Install free 3MF Addon';
        btn.addEventListener('click', function () { showDialog(); });
        // Before the export folder slot, not between it and Send --
        // export_folder.js shows one or the other in the same spot.
        var anchor = document.getElementById('exportFolderSlot') || sendBtn;
        anchor.parentNode.insertBefore(btn, anchor);
    }

    function removeInstallButton() {
        var btn = document.getElementById('tp3d3mfInstallBtn');
        if (btn) btn.parentNode.removeChild(btn);
    }

    // ── Popup dialog ──────────────────────────────────────────────────────────
    function showDialog() {
        if (document.getElementById('tp3d3mfOverlay')) return; // already open

        var overlay = document.createElement('div');
        overlay.id = 'tp3d3mfOverlay';
        overlay.className = 'tp3d-3mf-overlay';

        var dialog = document.createElement('div');
        dialog.className = 'tp3d-3mf-dialog';

        dialog.innerHTML = [
            '<h3>Before we start: Install 3MF Addon?</h3>',
            '<p>The <strong>3MF Import/Export addon</strong> by Clonephaze is <strong>free</strong> and unlocks:</p>',
            '<ul>',
            '  <li><strong>3MF Export</strong> — export your maps with filament colors</li>',
            '  <li><strong>Map Texturing</strong> — bake element colors into a paint texture</li>',
            '</ul>',
            '<p>It installs directly from the Blender Extensions repository in one click.</p>',
            '<a class="tp3d-3mf-github-link" href="' + GITHUB_URL + '" target="_blank">View on GitHub ↗</a>',
            '<div class="tp3d-3mf-status" id="tp3d3mfStatus"></div>',
            '<div class="tp3d-3mf-dialog-actions">',
            '  <button class="tp3d-3mf-btn-decline" id="tp3d3mfDeclineBtn">No thanks</button>',
            '  <button class="tp3d-3mf-btn-install" id="tp3d3mfConfirmBtn">Install</button>',
            '</div>'
        ].join('');

        overlay.appendChild(dialog);
        document.body.appendChild(overlay);

        document.getElementById('tp3d3mfConfirmBtn').addEventListener('click', function () {
            requestInstall();
        });

        document.getElementById('tp3d3mfDeclineBtn').addEventListener('click', function () {
            decline();
        });

        // Clicking the backdrop closes without action
        overlay.addEventListener('click', function (e) {
            if (e.target === overlay) decline();
        });
    }

    function closeDialog() {
        var overlay = document.getElementById('tp3d3mfOverlay');
        if (overlay) overlay.parentNode.removeChild(overlay);
    }

    function setDialogStatus(msg) {
        var el = document.getElementById('tp3d3mfStatus');
        if (el) el.textContent = msg;
    }

    function setInstallButtonsDisabled(disabled) {
        var confirmBtn = document.getElementById('tp3d3mfConfirmBtn');
        var declineBtn = document.getElementById('tp3d3mfDeclineBtn');
        if (confirmBtn) confirmBtn.disabled = disabled;
        if (declineBtn) declineBtn.disabled = disabled;
    }

    // ── Install flow ──────────────────────────────────────────────────────────
    function requestInstall() {
        setInstallButtonsDisabled(true);
        setDialogStatus('Installing… Blender may be busy for a moment.');
        fetch('http://127.0.0.1:' + PORT + '/install_3mf_addon', { method: 'POST', body: '' })
            .catch(function () {}) // server might not respond if Blender is busy
            .then(function () {
                pollForInstallComplete(0);
            });
    }

    function pollForInstallComplete(attempts) {
        if (attempts > 30) { // 60 s timeout
            setDialogStatus('Install may have failed. Check Blender\'s System Console.');
            setInstallButtonsDisabled(false);
            return;
        }
        setTimeout(function () {
            fetch('http://127.0.0.1:' + PORT + '/get_source_state')
                .then(function (r) { return r.json(); })
                .then(function (state) {
                    if (state && state.settingsState && state.settingsState.has3mf) {
                        onInstallComplete();
                    } else {
                        pollForInstallComplete(attempts + 1);
                    }
                })
                .catch(function () {
                    pollForInstallComplete(attempts + 1);
                });
        }, 2000);
    }

    function onInstallComplete() {
        closeDialog();
        removeInstallButton();
        // Update local SETTINGS_STATE so any later check in this page session sees the new state
        if (typeof SETTINGS_STATE !== 'undefined') {
            SETTINGS_STATE.has3mf = true;
        }
        // Unlocks the settings modal's texture fields (settings_modal.js) --
        // absent on pages without that modal.
        if (typeof tp3dRefreshMapTabVisibility === 'function') tp3dRefreshMapTabVisibility();
    }

    // ── Decline flow ──────────────────────────────────────────────────────────
    function decline() {
        closeDialog();
        fetch('http://127.0.0.1:' + PORT + '/set_threemf_promo_declined', { method: 'POST', body: '' })
            .catch(function () {});
        insertInstallButton();
    }

    // ── Entry point ───────────────────────────────────────────────────────────
    fetch('http://127.0.0.1:' + PORT + '/get_threemf_promo_state')
        .then(function (r) { return r.json(); })
        .then(function (state) {
            if (state && state.declined) {
                insertInstallButton();
            } else {
                showDialog();
            }
        })
        .catch(function () {
            // Server not yet ready or an error — show the button as a safe fallback
            insertInstallButton();
        });
}());
