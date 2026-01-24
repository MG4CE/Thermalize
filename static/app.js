
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
        }
    });

    // Upload
    document.getElementById('upload-btn').addEventListener('click', () => UI.inputFile.click());
    UI.inputFile.addEventListener('change', handleUpload);

    // Settings Toggle
    document.getElementById('settings-btn').addEventListener('click', () => {
        openModal('settings');
        // Pre-fill settings logic could go here
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
    document.getElementById('protocol-select').addEventListener('change', handleProtocolSwitch);
    document.getElementById('reconnect-printer-btn').addEventListener('click', reconnectPrinter);
    document.getElementById('test-print-btn').addEventListener('click', () => callApi('/api/printer/test', 'POST'));
    document.getElementById('scan-bt-btn').addEventListener('click', scanBluetooth);
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
    if (config && config.image_settings) {
        maxWidth = config.image_settings.max_width || 640;
        // Apply width to preview image
        UI.previewImage.style.width = `${maxWidth}px`;
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
            // Use timestamp to bust cache
            card.innerHTML = `<img src="/api/images/${img.id}/preview?t=${Date.now()}" loading="lazy">`;
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
    if (type === 'bluetooth') {
        document.getElementById('bluetooth-controls').classList.remove('hidden');
    } else {
        document.getElementById('bluetooth-controls').classList.add('hidden');
    }
    
    await callApi('/api/printer/switch', 'POST', { type });
    checkStatus(); // Refresh status immediately
}

async function scanBluetooth() {
    const list = document.getElementById('bt-device-list');
    const loader = document.getElementById('bt-loader');
    
    list.innerHTML = '';
    loader.classList.remove('hidden');
    
    const devices = await callApi('/api/printer/bluetooth/scan?timeout=5');
    loader.classList.add('hidden');
    
    if (devices && devices.length) {
        devices.forEach(d => {
            const li = document.createElement('li');
            li.textContent = `${d.name || 'Unknown'} (${d.address})`;
            li.onclick = () => connectBluetooth(d.address);
            list.appendChild(li);
        });
    } else {
        list.innerHTML = '<li>No devices found</li>';
    }
}

async function connectBluetooth(mac) {
    const list = document.getElementById('bt-device-list');
    list.innerHTML = `<li>Connecting to ${mac}...</li>`;
    
    await callApi('/api/printer/bluetooth/connect', 'POST', { mac });
    checkStatus();
    list.innerHTML = '<li>Connection attempt finished. Check status.</li>';
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
        
        // Update status display
        checkStatus();
    } catch (error) {
        btn.textContent = '✗ Error';
        setTimeout(() => {
            btn.textContent = originalText;
            btn.disabled = false;
        }, 2000);
    }
}

async function handleProtocolSwitch(e) {
    await callApi('/api/printer/protocol', 'POST', { protocol: e.target.value });
}


/**
 * Utils
 */
function openModal(name) {
    UI.modals[name].classList.remove('hidden');
}

