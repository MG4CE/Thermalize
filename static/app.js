
// State
let currentImageId = null;
let currentSettings = {
    x_offset: 0,
    y_offset: 0,
    auto_fit: true,
    dither_method: 'floyd_steinberg',
    raw_mode: false
};
let debounceTimer = null;
let bluetoothScanTimer = null;
let maxWidth = 640; // Default, will be loaded from config
let globalConfig = null; // Store config for gallery rendering

// UI References
const UI = {
    gallery: document.getElementById('gallery'),
    modals: {
        editor: document.getElementById('editor-modal'),
        settings: document.getElementById('settings-modal')
    },
    // Status
    statusBar: {
        text: document.getElementById('printer-status-text'),
        dot: document.getElementById('status-dot')
    },
    // Inputs
    inputFile: document.getElementById('file-input'),
    previewImage: document.getElementById('preview-image'),
    
    // Controls
    controls: {
        xOffset: document.getElementById('x-offset'),
        yOffset: document.getElementById('y-offset'),
        dither: document.getElementById('dither-select'),
        autoFit: document.getElementById('autofit-check'),
        rawMode: document.getElementById('raw-mode-check'),
    }
};

/**
 * Initialization
 */
document.addEventListener('DOMContentLoaded', () => {
    initListeners();
    loadConfig();
    refreshGallery();
    initPolling();
});

function initListeners() {
    // Global clicks
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('close-btn')) {
            const target = e.target.dataset.target;
            document.getElementById(target).classList.add('hidden');
            
            // Show gallery when modals are closed
            if (target === 'settings-modal' || target === 'editor-modal') {
                UI.gallery.style.display = '';
            }
        }
    });

    // Upload
    document.getElementById('upload-btn').addEventListener('click', () => UI.inputFile.click());
    UI.inputFile.addEventListener('change', handleUpload);

    // Settings Toggle
    document.getElementById('settings-btn').addEventListener('click', async () => {
        openModal('settings');
        // Reload config to ensure settings are up-to-date
        await loadConfig();
    });

    // Editor Tabs
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const tabName = e.target.dataset.tab;
            
            // Toggle buttons
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');

            // Toggle Content
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById(`${tabName}-tab`).classList.add('active');
        });
    });

    // Image Controls (Debounced)
    const controlInputs = [
        UI.controls.xOffset, 
        UI.controls.yOffset, 
        UI.controls.dither, 
        UI.controls.autoFit, 
        UI.controls.rawMode
    ];

    controlInputs.forEach(input => {
        input.addEventListener('input', () => {
            updateValueDisplay();
            scheduleProcessUpdate();
        });
    });

    // Modal Actions
    document.getElementById('print-btn').addEventListener('click', printCurrentImage);
    document.getElementById('delete-btn').addEventListener('click', deleteCurrentImage);
    
    // Settings Actions
    document.getElementById('connection-type').addEventListener('change', (e) => handleConnectionSwitch(e.target.value));
    document.querySelectorAll('.gpio-select').forEach(el => {
        el.addEventListener('change', handleGpioChange);
    });
    document.getElementById('protocol-select').addEventListener('change', handleProtocolSwitch);
    document.getElementById('reconnect-printer-btn').addEventListener('click', reconnectPrinter);
    document.getElementById('test-print-btn').addEventListener('click', () => callApi('/api/printer/test', 'POST'));
    document.getElementById('scan-bt-btn').addEventListener('click', scanBluetooth);
    document.getElementById('disconnect-bt-btn').addEventListener('click', disconnectBluetooth);
    document.getElementById('unpair-bt-btn').addEventListener('click', unpairBluetooth);
}

/**
 * API Wrapper
 */
async function callApi(url, method = 'GET', body = null) {
    try {
        const options = { method, headers: {} };
        if (body) {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(body);
        }
        const res = await fetch(url, options);
        if (!res.ok) throw new Error(`API Error: ${res.status}`);
        return method === 'GET' || res.status !== 204 ? await res.json() : null;
    } catch (err) {
        console.error(err);
        return null; // Handle error gracefully
    }
}

/**
 * Core Logic
 */

// 1. Config Loading
async function loadConfig() {
    const config = await callApi('/api/config');
    globalConfig = config; // Store globally
    if (config && config.image_settings) {
        maxWidth = config.image_settings.max_width || 640;
        // Apply width to preview image
        UI.previewImage.style.width = `${maxWidth}px`;
    }

    // Update GPIO mappings
    if (config?.button_assignments) {
        await populateGpioDropdowns(config.button_assignments);
    }
    
    // Update printer settings UI
    if (config?.printer) {
        // Set connection type dropdown
        const connectionTypeEl = document.getElementById('connection-type');
        if (connectionTypeEl && config.printer.type) {
            connectionTypeEl.value = config.printer.type;
            // Show/hide Bluetooth controls based on connection type
            const btControls = document.getElementById('bluetooth-controls');
            if (btControls) {
                if (config.printer.type === 'bluetooth') {
                    btControls.classList.remove('hidden');
                } else {
                    btControls.classList.add('hidden');
                }
            }
        }
        
        // Set protocol dropdown
        const protocolEl = document.getElementById('protocol-select');
        if (protocolEl && config.printer.protocol) {
            protocolEl.value = config.printer.protocol;
        }
    }
    
    // Update Bluetooth device display
    updateBluetoothDeviceDisplay(config);
}

function updateBluetoothDeviceDisplay(config) {
    const currentDeviceEl = document.getElementById('current-bt-device');
    const disconnectRow = document.getElementById('bt-disconnect-row');
    
    if (config?.printer?.bluetooth_mac) {
        currentDeviceEl.textContent = config.printer.bluetooth_mac;
        disconnectRow.style.display = 'flex';
    } else {
        currentDeviceEl.textContent = 'None';
        disconnectRow.style.display = 'none';
    }
}

// 2. Polling System
async function initPolling() {
    await checkStatus();
    setInterval(checkStatus, 5000);
}

async function checkStatus() {
    const data = await callApi('/api/printer/status');
    if (data) {
        const isConnected = data.connected || data.status === 'connected';
        UI.statusBar.text.textContent = isConnected ? 'Online' : 'Offline';
        UI.statusBar.dot.className = `status-indicator ${isConnected ? 'online' : 'offline'}`;
        
        // Update connection type dropdown if needed (optional)
        // const connType = data.type; // if available
    }
}

// 3. Gallery & Upload
async function refreshGallery() {
    const images = await callApi('/api/images');
    UI.gallery.innerHTML = '';
    
    if (images && images.length > 0) {
        images.forEach(img => {
            const card = document.createElement('div');
            card.className = 'image-card';
            
            // Check which button(s) are assigned to this image
            let buttonBadge = '';
            if (globalConfig && globalConfig.button_assignments) {
                const assignedButtons = [];
                for (const [btnNum, imageId] of Object.entries(globalConfig.button_assignments)) {
                    if (imageId === img.id) {
                        assignedButtons.push(btnNum);
                    }
                }
                if (assignedButtons.length > 0) {
                    buttonBadge = `<div class="button-badge">BTN ${assignedButtons.join(', ')}</div>`;
                }
            }
            
            // Get processing info
            const ditherMethod = img.dither_method || 'floyd_steinberg';
            const rawMode = img.raw_mode || false;
            const methodLabel = rawMode ? 'RAW' : ditherMethod.replace('_', ' ').toUpperCase();
            const infoLabel = `<div class="image-info">${methodLabel}</div>`;
            
            // Use timestamp to bust cache
            card.innerHTML = `
                <img src="/api/images/${img.id}/preview?t=${Date.now()}" loading="lazy">
                ${buttonBadge}
                ${infoLabel}
            `;
            card.onclick = () => openEditor(img);
            UI.gallery.appendChild(card);
        });
    } else {
        UI.gallery.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: #999; padding: 40px;">No images yet</div>`;
    }
}

async function handleUpload(e) {
    const file = e.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    document.getElementById('upload-btn').textContent = 'Uploading...';
    
    try {
        const res = await fetch('/api/upload', { method: 'POST', body: formData });
        if (res.ok) {
            await refreshGallery();
        }
    } finally {
        e.target.value = '';
        document.getElementById('upload-btn').innerHTML = '<span class="plus-icon">+</span> Upload Image';
    }
}

// 4. Editor Logic
function openEditor(image) {
    currentImageId = image.id;
    
    // Load Settings
    UI.controls.xOffset.value = image.position?.x || 0;
    UI.controls.yOffset.value = image.position?.y || 0;
    UI.controls.dither.value = image.dither_method || 'floyd_steinberg';
    UI.controls.autoFit.checked = image.auto_fit !== false; // Default true
    UI.controls.rawMode.checked = image.raw_mode || false;
    
    updateValueDisplay();
    updatePreviewImage(); // Load initial preview
    openModal('editor');
}

function updateValueDisplay() {
    document.getElementById('x-offset-val').textContent = UI.controls.xOffset.value;
    document.getElementById('y-offset-val').textContent = UI.controls.yOffset.value;
}

function updatePreviewImage() {
    if(!currentImageId) return;
    UI.previewImage.src = `/api/images/${currentImageId}/preview?t=${Date.now()}`;
}

function scheduleProcessUpdate() {
    if (debounceTimer) clearTimeout(debounceTimer);
    
    debounceTimer = setTimeout(async () => {
        if (!currentImageId) return;
        
        const payload = {
            x_offset: parseInt(UI.controls.xOffset.value),
            y_offset: parseInt(UI.controls.yOffset.value),
            auto_fit: UI.controls.autoFit.checked,
            dither_method: UI.controls.dither.value,
            raw_mode: UI.controls.rawMode.checked
        };
        
        UI.previewImage.style.opacity = '0.5'; // Visual feedback
        
        await callApi(`/api/images/${currentImageId}/process`, 'POST', payload);
        updatePreviewImage();
        UI.previewImage.style.opacity = '1';
        
        // Refresh gallery to update processing info badge
        await refreshGallery();
        
    }, 600); // 600ms debounce
}

async function printCurrentImage() {
    if(!currentImageId) return;
    const btn = document.getElementById('print-btn');
    const originalText = btn.textContent;
    
    try {
        btn.textContent = 'Printing...';
        btn.disabled = true;
        
        // First ensure latest settings are processed
        // (optional, but safer)
        
        await callApi(`/api/images/${currentImageId}/print`, 'POST');
        // We could show a toast here
    } finally {
        setTimeout(() => {
            btn.textContent = originalText;
            btn.disabled = false;
        }, 2000);
    }
}

async function deleteCurrentImage() {
    if(!currentImageId) return;
    if(confirm('Are you sure you want to delete this image?')) {
        await callApi(`/api/images/${currentImageId}`, 'DELETE');
        document.getElementById('editor-modal').classList.add('hidden');
        refreshGallery();
    }
}

// 4. Settings Logic
async function handleConnectionSwitch(type) {
    const btControls = document.getElementById('bluetooth-controls');
    if (btControls) {
        if (type === 'bluetooth') {
            btControls.classList.remove('hidden');
        } else {
            btControls.classList.add('hidden');
        }
    }
    
    const result = await callApi('/api/printer/switch', 'POST', { type });
    
    if (result) {
        // Reload config to update current device display
        const config = await callApi('/api/config');
        if (config) {
            updateBluetoothDeviceDisplay(config);
        }
    }
    
    checkStatus(); // Refresh status immediately
}

async function scanBluetooth() {
    const list = document.getElementById('bt-device-list');
    const loader = document.getElementById('bt-loader');
    
    list.innerHTML = '';
    loader.classList.remove('hidden');
    
    try {
        const response = await callApi('/api/printer/bluetooth/scan?timeout=20');
        loader.classList.add('hidden');
        
        // Backend returns {success, devices: [], count}
        const devices = response?.devices || [];
        
        console.log('Bluetooth scan found devices:', devices);
        
        if (devices && devices.length > 0) {
            devices.forEach(d => {
                const li = document.createElement('li');
                // Backend returns 'mac' not 'address'
                const macAddr = d.mac || d.address;
                li.textContent = `${d.name || 'Unknown'} (${macAddr})`;
                li.onclick = () => connectBluetooth(macAddr);
                list.appendChild(li);
            });
        } else {
            list.innerHTML = '<li>No devices found</li>';
        }
    } catch (error) {
        loader.classList.add('hidden');
        list.innerHTML = `<li>Error: ${error.message || 'Scan failed'}</li>`;
        console.error('Bluetooth scan error:', error);
    }
}

async function connectBluetooth(mac) {
    const list = document.getElementById('bt-device-list');
    console.log('Attempting to connect to Bluetooth MAC:', mac);
    list.innerHTML = `<li>Connecting to ${mac}...</li>`;
    
    const result = await callApi('/api/printer/bluetooth/connect', 'POST', { mac });
    console.log('Bluetooth connection result:', result);
    checkStatus();
    
    if (result?.success) {
        list.innerHTML = `<li>✓ Connected to ${mac}</li>`;
        
        // Reload config to update current device display
        const config = await callApi('/api/config');
        updateBluetoothDeviceDisplay(config);
    } else {
        list.innerHTML = `<li>✗ Failed to connect to ${mac}<br>${result?.error || 'See logs'}</li>`;
    }
}

async function reconnectPrinter() {
    const btn = document.getElementById('reconnect-printer-btn');
    const originalText = btn.textContent;
    
    // Show loading state
    btn.disabled = true;
    btn.textContent = 'Reconnecting...';
    
    try {
        const result = await callApi('/api/printer/reconnect', 'POST');
        
        if (result && result.success) {
            btn.textContent = '✓ Connected';
            setTimeout(() => {
                btn.textContent = originalText;
                btn.disabled = false;
            }, 2000);
        } else {
            btn.textContent = '✗ Failed';
            setTimeout(() => {
                btn.textContent = originalText;
                btn.disabled = false;
            }, 2000);
        }
        
        // Update status display and reload config
        checkStatus();
        
        // Reload config to update current device display if reconnecting to bluetooth
        const config = await callApi('/api/config');
        if (config) {
            updateBluetoothDeviceDisplay(config);
        }
    } catch (error) {
        console.error('Reconnect error:', error);
        btn.textContent = '✗ Error';
        setTimeout(() => {
            btn.textContent = originalText;
            btn.disabled = false;
        }, 2000);
    }
}

async function disconnectBluetooth() {
    const btn = document.getElementById('disconnect-bt-btn');
    const originalText = btn.textContent;
    
    // Prevent double-clicks
    if (btn.disabled) return;
    
    if (!confirm('Disconnect from Bluetooth printer?')) return;
    
    btn.disabled = true;
    btn.textContent = 'Disconnecting...';
    
    try {
        const result = await callApi('/api/printer/bluetooth/disconnect', 'POST');
        
        if (result?.success) {
            btn.textContent = '✓ Disconnected';
            setTimeout(() => {
                btn.textContent = originalText;
                btn.disabled = false;
            }, 2000);
        } else {
            btn.textContent = '✗ Failed';
            setTimeout(() => {
                btn.textContent = originalText;
                btn.disabled = false;
            }, 2000);
        }
        
        checkStatus();
    } catch (error) {
        btn.textContent = '✗ Error';
        setTimeout(() => {
            btn.textContent = originalText;
            btn.disabled = false;
        }, 2000);
    }
}

async function unpairBluetooth() {
    const btn = document.getElementById('unpair-bt-btn');
    const originalText = btn.textContent;
    
    // Prevent double-clicks
    if (btn.disabled) return;
    
    if (!confirm('This will unpair the device at OS level. Continue?')) return;
    
    btn.disabled = true;
    btn.textContent = 'Unpairing...';
    
    try {
        const result = await callApi('/api/printer/bluetooth/unpair', 'POST', {});
        
        if (result?.success) {
            btn.textContent = '✓ Unpaired';
            
            // Reload config to update UI
            const config = await callApi('/api/config');
            updateBluetoothDeviceDisplay(config);
            
            setTimeout(() => {
                btn.textContent = originalText;
                btn.disabled = false;
            }, 2000);
        } else {
            alert('Failed to unpair: ' + (result?.error || 'Unknown error'));
            btn.textContent = originalText;
            btn.disabled = false;
        }
        
        checkStatus();
    } catch (error) {
        alert('Error unpairing device: ' + error.message);
        btn.textContent = originalText;
        btn.disabled = false;
    }
}

async function handleProtocolSwitch(e) {
    const result = await callApi('/api/printer/protocol', 'POST', { protocol: e.target.value });
    
    // Reload config to confirm the change
    if (result) {
        const config = await callApi('/api/config');
        if (config) {
            console.log('Protocol switched to:', config.printer.protocol);
        }
    }
    
    checkStatus();
}

// GPIO Logic
async function populateGpioDropdowns(assignments) {
    // Get all images
    const images = await callApi('/api/images');
    if (!images) return;
    
    // Sort images by date (newest first)
    images.sort((a, b) => b.timestamp - a.timestamp);
    
    // Create options HTML
    const optionsHtml = images.map(img => {
        // Format date: "Jan 25, 10:30 PM"
        let dateStr = "Unknown Date";
        if (img.timestamp) {
            try {
                dateStr = new Date(img.timestamp * 1000).toLocaleDateString(undefined, {
                    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                });
            } catch (e) {
                console.warn("Invalid timestamp for image", img.id);
            }
        }
        return `<option value="${img.id}">${dateStr} (ID: ${img.id.substring(0,4)})</option>`;
    }).join('');
    
    const defaultOption = '<option value="">-- None --</option>';
    
    // Populate each select
    ['1', '2', '3', '4'].forEach(num => {
        const el = document.getElementById(`gpio-btn-${num}`);
        if(el) {
            const currentVal = assignments[num];
            el.innerHTML = defaultOption + optionsHtml;
            
            if (currentVal) {
                el.value = currentVal;
                updateGpioPreview(num, currentVal);
            } else {
                el.value = "";
                updateGpioPreview(num, null);
            }
        }
    });
}

function updateGpioPreview(btnNum, imageId) {
    const imgEl = document.getElementById(`gpio-preview-${btnNum}`);
    if (!imgEl) return;
    
    if (imageId) {
        imgEl.src = `/api/images/${imageId}/preview?t=${Date.now()}`;
        imgEl.classList.remove('hidden');
    } else {
        imgEl.src = '';
        imgEl.classList.add('hidden');
    }
}

async function handleGpioChange(e) {
    // Get button number from parent ID or dataset (simplified: inferred from ID)
    const targetId = e.target.id; // gpio-btn-1
    const parts = targetId.split('-');
    const btnNum = parts[parts.length - 1];
    
    // Update preview immediately
    updateGpioPreview(btnNum, e.target.value);
    
    const assignments = {};
    
    ['1', '2', '3', '4'].forEach(num => {
        const el = document.getElementById(`gpio-btn-${num}`);
        if(el) {
            assignments[num] = el.value || null;
        }
    });
    
    console.log('Updating GPIO assignments:', assignments);
    await callApi('/api/config', 'POST', { 
        button_assignments: assignments 
    });
    
    // Reload config and refresh gallery to update button badges
    await loadConfig();
    await refreshGallery();
}



/**
 * Utils
 */
function openModal(name) {
    UI.modals[name].classList.remove('hidden');
    
    // Hide gallery when modals are open
    if (name === 'settings' || name === 'editor') {
        UI.gallery.style.display = 'none';
    }
}

