#!/usr/bin/env python3
import sys
import os
import subprocess
import json
import socket
import base64
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import QProcess, QObject, Slot, Signal, QProcessEnvironment
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

# --- Configuration ---
ASSET_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_SCRIPT = "/usr/share/chimera/chimera.py"
LOCAL_LOGO = os.path.join(ASSET_DIR, "logo.png")

# --- System Data Scrapers ---
def get_os_release():
    info = {"NAME": "Linux", "PRETTY_NAME": "Linux Installer", "ID": "linux"}
    try:
        path = "/etc/os-release" if os.path.exists("/etc/os-release") else "/etc/os_release"
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        info[k] = v.strip('"').strip("'")
    except: pass
    return info

def load_logo_data_uri(logo_name=""):
    paths = []
    if logo_name:
        paths.extend([f"/usr/share/pixmaps/{logo_name}.png", f"/usr/share/pixmaps/{logo_name}.svg",
                      f"/usr/share/icons/{logo_name}.png", f"/usr/share/icons/{logo_name}.svg"])
    paths.extend([LOCAL_LOGO, "/usr/share/pixmaps/chimera.png"])

    for path in paths:
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
                ext = os.path.splitext(path)[1][1:].lower()
                mime = "image/svg+xml" if ext == "svg" else f"image/{ext}"
                return f"data:{mime};base64,{data}"
    return ""

def get_timezones():
    try:
        return subprocess.check_output(["timedatectl", "list-timezones"]).decode().splitlines()
    except:
        return ["UTC", "America/New_York", "Europe/London", "Asia/Tokyo"]

def get_keymaps():
    try:
        return subprocess.check_output(["localectl", "list-keymaps"]).decode().splitlines()
    except:
        return ["us", "uk", "de", "fr", "es"]

def get_locales():
    try:
        with open("/usr/share/i18n/SUPPORTED") as f:
            return list(dict.fromkeys([line.split()[0] for line in f if not line.startswith("#")]))
    except:
        return ["en_US.UTF-8"]

def get_fonts():
    try:
        fonts = os.listdir("/usr/share/kbd/consolefonts")
        return sorted([f.split('.')[0] for f in fonts if f.endswith('.gz')])
    except:
        return ["default8x16", "Lat2-Terminus16"]

# --- HTML Template ---
def build_html(distro_name, logo_uri, hostname):
    logo_css = f"url('{logo_uri}') center/contain no-repeat" if logo_uri else "linear-gradient(135deg, var(--accent-1), var(--accent-2))"
    logo_inner = "none" if logo_uri else "block"

    tz_json = json.dumps(get_timezones())
    km_json = json.dumps(get_keymaps())
    lc_json = json.dumps(get_locales())
    fn_json = json.dumps(get_fonts())
    rg_json = json.dumps(["Worldwide", "United States", "Germany", "France", "United Kingdom", "Canada", "Australia", "Japan", "Singapore", "China"])

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{distro_name} Installer</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Noto+Sans+SC:wght@400;500;700&family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet">
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <style>
        :root {{
            --bg-color: #000000;
            --text-primary: rgba(255, 255, 255, 0.95);
            --text-secondary: rgba(255, 255, 255, 0.6);
            --accent-1: #2997ff;
            --accent-2: #6e5cff;
            --glass-bg: rgba(255, 255, 255, 0.06);
            --glass-border: rgba(255, 255, 255, 0.1);
            --spring-curve: cubic-bezier(0.34, 1.56, 0.64, 1);
            --smooth-curve: cubic-bezier(0.22, 1, 0.36, 1);
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; user-select: none; -webkit-font-smoothing: antialiased; }}
        body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Noto Sans', sans-serif; background: var(--bg-color); color: var(--text-primary); overflow: hidden; height: 100vh; width: 100vw; cursor: default; transition: opacity 0.5s ease; }}

        #bg-canvas {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; filter: blur(80px) saturate(150%); opacity: 0.4; pointer-events: none; }}
        .blob {{ position: absolute; border-radius: 50%; mix-blend-mode: screen; animation: float 20s infinite ease-in-out; }}
        .blob:nth-child(1) {{ width: 40vmax; height: 40vmax; background: var(--accent-1); top: -10vmax; left: -10vmax; }}
        .blob:nth-child(2) {{ width: 35vmax; height: 35vmax; background: var(--accent-2); bottom: -10vmax; right: -10vmax; animation-delay: -5s; }}
        .blob:nth-child(3) {{ width: 20vmax; height: 20vmax; background: #ff2d55; top: 40%; left: 50%; animation-delay: -10s; }}
        @keyframes float {{ 0%, 100% {{ transform: translate(0, 0) scale(1); }} 33% {{ transform: translate(10vw, 10vh) scale(1.1); }} 66% {{ transform: translate(-10vw, 5vh) scale(0.9); }} }}

        #installer {{ position: relative; z-index: 1; width: 100%; height: 100%; display: flex; justify-content: center; align-items: center; }}
        .screen {{ position: absolute; width: 100%; max-width: 600px; padding: 40px; display: flex; flex-direction: column; align-items: center; opacity: 0; pointer-events: none; transform: translateY(20px) scale(0.98); transition: opacity 0.6s var(--smooth-curve), transform 0.8s var(--spring-curve); }}
        .screen.active {{ opacity: 1; pointer-events: auto; transform: translateY(0) scale(1); }}

        .screen-content {{ width: 100%; max-height: 60vh; overflow-y: auto; padding-right: 10px; display: flex; flex-direction: column; gap: 15px; margin-bottom: 25px; }}
        .screen-content::-webkit-scrollbar {{ width: 6px; }}
        .screen-content::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,0.2); border-radius: 10px; }}

        #welcome-logo {{ width: 100px; height: 100px; background: {logo_css}; border-radius: 28px; margin-bottom: 30px; box-shadow: 0 10px 40px rgba(41, 151, 255, 0.3); display: flex; justify-content: center; align-items: center; }}
        .logo-inner {{ display: {logo_inner}; width: 40px; height: 40px; border: 4px solid white; border-radius: 50%; border-right-color: transparent; transform: rotate(45deg); }}

        h2 {{ font-size: 28px; margin-bottom: 20px; align-self: flex-start; }}

        .nav-buttons {{ display: flex; gap: 15px; margin-top: 10px; width: 100%; justify-content: center; }}
        .btn-primary {{ background: rgba(255, 255, 255, 0.1); backdrop-filter: blur(20px); border: 1px solid var(--glass-border); color: white; padding: 14px 40px; border-radius: 980px; font-size: 17px; font-weight: 500; cursor: pointer; transition: transform 0.2s var(--spring-curve), background 0.2s ease; }}
        .btn-primary:hover {{ background: rgba(255, 255, 255, 0.15); transform: scale(1.03); }}
        .btn-primary:active {{ transform: scale(0.98); }}

        .btn-secondary {{ background: transparent; border: 1px solid var(--glass-border); color: var(--text-secondary); padding: 14px 30px; border-radius: 980px; font-size: 17px; font-weight: 500; cursor: pointer; transition: all 0.2s ease; }}
        .btn-secondary:hover {{ background: rgba(255, 255, 255, 0.08); color: white; }}

        .card {{ background: var(--glass-bg); backdrop-filter: blur(40px) saturate(180%); border: 1px solid var(--glass-border); border-radius: 20px; width: 100%; padding: 8px; box-shadow: 0 20px 50px rgba(0,0,0,0.4); }}
        .list-item {{ display: flex; align-items: center; padding: 16px; border-radius: 12px; cursor: pointer; transition: background 0.15s ease; }}
        .list-item:hover {{ background: rgba(255,255,255,0.08); }}
        .list-item.selected {{ background: rgba(41, 151, 255, 0.2); border: 1px solid rgba(41, 151, 255, 0.5); }}

        .form-group {{ width: 100%; display: flex; flex-direction: column; }}
        .form-label {{ font-size: 14px; color: var(--text-secondary); margin-bottom: 6px; }}
        .form-input {{ width: 100%; padding: 12px 14px; border-radius: 12px; background: rgba(255,255,255,0.05); border: 1px solid var(--glass-border); color: white; font-size: 15px; outline: none; transition: border-color 0.2s; font-family: inherit; }}
        .form-input:focus {{ border-color: var(--accent-1); }}

        select.form-input {{ appearance: none; background-image: url("data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22%23FFFFFF%22%20d%3D%22M287%2069.4a17.6%2017.6%200%200%200-13-5.4H18.4c-5%200-9.3%201.8-12.9%205.4A17.6%2017.6%200%200%200%200%2082.2c0%205%201.8%209.3%205.4%2012.9l128%20127.9c3.6%203.6%207.8%205.4%2012.8%205.4s9.2-1.8%2012.8-5.4L287%2095c3.5-3.5%205.4-7.8%205.4-12.8%200-5-1.9-9.2-5.5-12.8z%22%2F%3E%3C%2Fsvg%3E"); background-repeat: no-repeat; background-position: right 1rem top 50%; background-size: 0.65rem auto; cursor: pointer; }}
        select.form-input option {{ background: #1c1c1e; color: white; }}

        .checkbox-label {{ display: flex; align-items: center; gap: 10px; font-size: 15px; cursor: pointer; padding: 10px 0; }}
        input[type="checkbox"] {{ width: 18px; height: 18px; accent-color: var(--accent-1); cursor: pointer; }}

        .progress-ring-container {{ position: relative; width: 160px; height: 160px; margin-bottom: 30px; }}
        .progress-ring {{ transform: rotate(-90deg); width: 100%; height: 100%; }}
        .ring-bg {{ stroke: rgba(255,255,255,0.1); stroke-width: 8; fill: transparent; }}
        .ring-fill {{ stroke: var(--accent-1); stroke-width: 8; fill: transparent; stroke-linecap: round; stroke-dasharray: 502; stroke-dashoffset: 502; transition: stroke-dashoffset 0.2s linear; }}
        .progress-text {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 40px; font-weight: 600; }}

        #terminal-log {{ width: 100%; height: 120px; background: rgba(0,0,0,0.6); border: 1px solid var(--glass-border); border-radius: 12px; padding: 12px; color: #0aff; font-family: monospace; font-size: 11px; overflow-y: auto; display: none; white-space: pre-wrap; }}
        .btn-text {{ background: none; border: none; color: var(--accent-1); cursor: pointer; font-size: 14px; margin-bottom: 10px; }}

        #settings-btn {{ position: fixed; bottom: 20px; left: 20px; width: 40px; height: 40px; background: var(--glass-bg); border: 1px solid var(--glass-border); border-radius: 50%; display: flex; justify-content: center; align-items: center; cursor: pointer; z-index: 100; backdrop-filter: blur(20px); transition: transform 0.2s; }}
        #settings-btn:hover {{ transform: rotate(45deg); }}
    </style>
</head>
<body>
    <div id="bg-canvas"><div class="blob"></div><div class="blob"></div><div class="blob"></div></div>
    <div id="settings-btn" onclick="openSettings()">⚙</div>

    <div id="installer">
        <!-- 1. Welcome -->
        <section id="welcome-screen" class="screen active">
            <div id="welcome-logo"><div class="logo-inner"></div></div>
            <h1 style="font-size: 48px; margin-bottom: 10px;">Welcome</h1>
            <p class="subtitle" style="margin-bottom: 40px;">Let's set up {distro_name}</p>
            <div class="nav-buttons">
                <button class="btn-primary" onclick="transitionTo('mode-screen')">Continue</button>
            </div>
        </section>

        <!-- 2. Mode -->
        <section id="mode-screen" class="screen">
            <h2>Install Mode</h2>
            <div class="card screen-content">
                <div class="list-item selected" onclick="selectOption(this, 'install_type', 'online')">Online Install (Downloads latest packages)</div>
                <div class="list-item" onclick="selectOption(this, 'install_type', 'offline')">Offline Install (Uses local ISO packages)</div>
            </div>
            <div class="nav-buttons">
                <button class="btn-secondary" onclick="transitionTo('welcome-screen')">Back</button>
                <button class="btn-primary" onclick="transitionTo('locale-screen')">Continue</button>
            </div>
        </section>

        <!-- 3. Locale -->
        <section id="locale-screen" class="screen">
            <h2>Locale Settings</h2>
            <div class="screen-content">
                <div class="form-group"><label class="form-label">Keyboard Layout</label><select id="sel-keymap" class="form-input"></select></div>
                <div class="form-group"><label class="form-label">Locale Language</label><select id="sel-locale" class="form-input"></select></div>
                <div class="form-group"><label class="form-label">Locale Encoding</label><select id="sel-encoding" class="form-input"><option>UTF-8</option><option>ISO-8859-1</option></select></div>
                <div class="form-group"><label class="form-label">Console Font</label><select id="sel-font" class="form-input"></select></div>
            </div>
            <div class="nav-buttons">
                <button class="btn-secondary" onclick="transitionTo('mode-screen')">Back</button>
                <button class="btn-primary" onclick="transitionTo('mirror-screen')">Continue</button>
            </div>
        </section>

        <!-- 4. Mirror -->
        <section id="mirror-screen" class="screen">
            <h2>Mirror Region</h2>
            <div class="screen-content">
                <div class="form-group"><label class="form-label">Select Region</label><select id="sel-region" class="form-input"></select></div>
            </div>
            <div class="nav-buttons">
                <button class="btn-secondary" onclick="transitionTo('locale-screen')">Back</button>
                <button class="btn-primary" onclick="transitionTo('disk-screen')">Continue</button>
            </div>
        </section>

        <!-- 5. Disk -->
        <section id="disk-screen" class="screen">
            <h2>Disk Setup</h2>
            <div class="screen-content">
                <select id="sel-disk-mode" class="form-input" onchange="toggleDiskMode()">
                    <option value="auto">Auto (Best Effort Partitioning)</option>
                    <option value="manual">Manual Partitioning</option>
                </select>
                <div id="disk-auto" class="card" style="margin-top: 15px;">
                    <!-- Disks injected here via JS -->
                </div>
                <div id="disk-manual" style="display:none; margin-top: 15px; width: 100%; gap: 15px; display: flex; flex-direction: column;">
                    <div class="form-group"><label class="form-label">Root Partition</label><select id="sel-rootfs" class="form-input"></select></div>
                    <div class="form-group"><label class="form-label">Boot Partition (Optional)</label><select id="sel-boot" class="form-input"></select></div>
                    <div class="form-group"><label class="form-label">Swap Partition (Optional)</label><select id="sel-swap" class="form-input"></select></div>
                </div>
            </div>
            <div class="nav-buttons">
                <button class="btn-secondary" onclick="transitionTo('mirror-screen')">Back</button>
                <button class="btn-primary" onclick="transitionTo('system-screen')">Continue</button>
            </div>
        </section>

        <!-- 6. System Apps -->
        <section id="system-screen" class="screen">
            <h2>System Options</h2>
            <div class="screen-content">
                <div class="form-group"><label class="form-label">Kernel</label>
                    <select id="sel-kernel" class="form-input">
                        <option value="linux">Linux (Normal)</option>
                        <option value="linux-zen">Linux ZEN</option>
                        <option value="linux-lts">Linux LTS</option>
                        <option value="linux-hardened">Linux Hardened</option>
                    </select>
                </div>
                <label class="checkbox-label"><input type="checkbox" id="chk-zram" checked onchange="toggleZram()"> Enable Zram Swap</label>
                <div class="form-group" id="zram-options"><label class="form-label">Zram Compression</label>
                    <select id="sel-zram-comp" class="form-input"><option value="lz4">lz4</option><option value="zstd">zstd</option><option value="lzo-rle">lzo-rle</option></select>
                </div>
                <div class="form-group"><label class="form-label">Audio Server</label>
                    <select id="sel-audio" class="form-input"><option value="pipewire">Pipewire</option><option value="pulseaudio">Pulseaudio</option><option value="none">None</option></select>
                </div>
                <label class="checkbox-label"><input type="checkbox" id="chk-bluetooth" checked> Enable Bluetooth</label>
                <div class="form-group"><label class="form-label">Timezone</label><select id="sel-timezone" class="form-input"></select></div>
            </div>
            <div class="nav-buttons">
                <button class="btn-secondary" onclick="transitionTo('disk-screen')">Back</button>
                <button class="btn-primary" onclick="transitionTo('user-screen')">Continue</button>
            </div>
        </section>

        <!-- 7. User -->
        <section id="user-screen" class="screen">
            <h2>User Account</h2>
            <div class="screen-content">
                <div class="form-group"><label class="form-label">Hostname</label><input type="text" class="form-input" id="inp-host" value="{hostname}"></div>
                <div class="form-group"><label class="form-label">Root Password</label><input type="password" class="form-input" id="inp-root-pass"></div>
                <div class="form-group"><label class="form-label">Username</label><input type="text" class="form-input" id="inp-user"></div>
                <div class="form-group"><label class="form-label">User Password</label><input type="password" class="form-input" id="inp-user-pass"></div>
            </div>
            <div class="nav-buttons">
                <button class="btn-secondary" onclick="transitionTo('system-screen')">Back</button>
                <button class="btn-primary" onclick="startInstall()">Install Now</button>
            </div>
        </section>

        <!-- 8. Installation -->
        <section id="install-screen" class="screen">
            <div class="progress-ring-container">
                <svg class="progress-ring" viewBox="0 0 160 160"><circle class="ring-bg" cx="80" cy="80" r="70"></circle><circle class="ring-fill" cx="80" cy="80" r="70"></circle></svg>
                <div class="progress-text" id="progress-percent">0%</div>
            </div>
            <div class="status-text" id="install-status">Preparing installation...</div>
            <button class="btn-text" onclick="toggleLog()">Toggle Details</button>
            <div id="terminal-log"></div>
        </section>

        <!-- 9. Complete -->
        <section id="complete-screen" class="screen">
            <h1 style="font-size: 40px; margin-bottom: 20px;">All Set.</h1>
            <p class="subtitle" style="margin-bottom: 40px;">{distro_name} has been successfully installed.</p>
            <button class="btn-primary" onclick="restartSystem()">Restart</button>
        </section>

        <!-- Debug Settings Screen -->
        <section id="debug-screen" class="screen">
            <h2>Debug Settings</h2>
            <div class="screen-content">
                <label class="checkbox-label">
                    <input type="checkbox" id="chk-dry-run" onchange="toggleDryRun()"> Enable Dry Run (Do not write to disk)
                </label>
                <div class="form-group">
                    <label class="form-label">Generated Backend Command (Passwords passed via Env Vars)</label>
                    <textarea id="txt-debug-cmd" class="form-input" style="height: 120px; resize: none; font-family: monospace;" readonly></textarea>
                </div>
            </div>
            <div class="nav-buttons">
                <button class="btn-primary" onclick="closeSettings()">Apply & Close</button>
            </div>
        </section>
    </div>

    <script>
        const TIMEZONES = {tz_json};
        const KEYMAPS = {km_json};
        const LOCALES = {lc_json};
        const FONTS = {fn_json};
        const REGIONS = {rg_json};

        let STATE = {{
            install_type: 'online',
            disk_mode: 'auto',
            disk: null
        }};
        let pyBackend = null;
        let previousScreen = 'welcome-screen';

        function populateSelect(id, items, defaultVal) {{
            const el = document.getElementById(id);
            items.forEach(i => {{
                const opt = document.createElement('option');
                opt.value = i; opt.textContent = i;
                if (i === defaultVal) opt.selected = true;
                el.appendChild(opt);
            }});
        }}

        window.onload = () => {{
            populateSelect('sel-keymap', KEYMAPS, 'us');
            populateSelect('sel-locale', LOCALES, 'en_US.UTF-8');
            populateSelect('sel-font', FONTS, 'default8x16');
            populateSelect('sel-timezone', TIMEZONES, 'UTC');
            populateSelect('sel-region', REGIONS, 'Worldwide');

            document.getElementById('disk-manual').style.display = 'none';
        }};

        new QWebChannel(qt.webChannelTransport, function(channel) {{
            pyBackend = channel.objects.backend;
            pyBackend.loadDisks();
        }});

        function transitionTo(screenId) {{
            const currentScreen = document.querySelector('.screen.active');
            if (currentScreen && currentScreen.id === screenId) return;

            const nextScreen = document.getElementById(screenId);

            if (currentScreen) {{
                currentScreen.style.opacity = '0';
                currentScreen.style.transform = 'scale(0.95)';
                setTimeout(() => {{
                    currentScreen.classList.remove('active');
                    nextScreen.classList.add('active');

                    nextScreen.style.opacity = '';
                    nextScreen.style.transform = '';
                }}, 300);
            }} else {{
                nextScreen.classList.add('active');
                nextScreen.style.opacity = '';
                nextScreen.style.transform = '';
            }}
        }}

        function renderDisks(disks, partitions) {{
            const container = document.getElementById('disk-auto');
            container.innerHTML = '';

            if (disks.length === 0) {{
                STATE.disk = null;
            }} else {{
                disks.forEach((d, i) => {{
                    if (i === 0) STATE.disk = d.path;
                    let div = document.createElement('div');
                    div.className = 'list-item' + (i === 0 ? ' selected' : '');
                    div.onclick = function() {{ selectOption(this, 'disk', d.path); }};

                    let span = document.createElement('span');
                    span.className = 'item-name';
                    span.textContent = d.name;

                    div.appendChild(span);
                    container.appendChild(div);
                }});
            }}

            ['sel-rootfs', 'sel-boot', 'sel-swap'].forEach(id => {{
                const el = document.getElementById(id);
                el.innerHTML = '';

                if (id !== 'sel-rootfs') {{
                    const emptyOpt = document.createElement('option');
                    emptyOpt.value = '';
                    emptyOpt.textContent = 'None / Skip';
                    el.appendChild(emptyOpt);
                }}

                partitions.forEach(p => {{
                    const opt = document.createElement('option');
                    opt.value = p.path;
                    opt.textContent = p.label;
                    el.appendChild(opt);
                }});
            }});
        }}

        function selectOption(el, key, val) {{
            el.parentElement.querySelectorAll('.list-item').forEach(i => i.classList.remove('selected'));
            el.classList.add('selected');
            STATE[key] = val;
        }}

        function toggleDiskMode() {{
            const mode = document.getElementById('sel-disk-mode').value;
            STATE.disk_mode = mode;
            document.getElementById('disk-auto').style.display = mode === 'auto' ? 'block' : 'none';
            document.getElementById('disk-manual').style.display = mode === 'manual' ? 'flex' : 'none';
        }}

        function toggleZram() {{
            document.getElementById('zram-options').style.display = document.getElementById('chk-zram').checked ? 'block' : 'none';
        }}

        function toggleLog() {{
            const log = document.getElementById('terminal-log');
            log.style.display = log.style.display === 'block' ? 'none' : 'block';
        }}

        function appendLog(text) {{
            const log = document.getElementById('terminal-log');
            log.appendChild(document.createTextNode(text));
            log.scrollTop = log.scrollHeight;
        }}

        function gatherState() {{
            return {{
                install_type: STATE.install_type,
                disk_mode: STATE.disk_mode,
                disk: STATE.disk,
                rootfs: document.getElementById('sel-rootfs').value,
                boot: document.getElementById('sel-boot').value,
                swap: document.getElementById('sel-swap').value,
                keyboard: document.getElementById('sel-keymap').value,
                locale_lang: document.getElementById('sel-locale').value,
                locale_enc: document.getElementById('sel-encoding').value,
                console_font: document.getElementById('sel-font').value,
                mirror_region: document.getElementById('sel-region').value,
                kernel: document.getElementById('sel-kernel').value,
                zram: document.getElementById('chk-zram').checked,
                zram_comp: document.getElementById('sel-zram-comp').value,
                audio: document.getElementById('sel-audio').value,
                bluetooth: document.getElementById('chk-bluetooth').checked,
                timezone: document.getElementById('sel-timezone').value,
                hostname: document.getElementById('inp-host').value,
                root_pass: document.getElementById('inp-root-pass').value,
                user: document.getElementById('inp-user').value,
                user_pass: document.getElementById('inp-user-pass').value
            }};
        }}

        function startInstall() {{
            if (STATE.disk_mode === 'manual' && !document.getElementById('sel-rootfs').value) {{
                alert("Root partition field is required in manual mode.");
                return;
            }}
            transitionTo('install-screen');
            pyBackend.startInstall(JSON.stringify(gatherState()));
        }}

        function openSettings() {{
            const activeScreen = document.querySelector('.screen.active');
            if (activeScreen && activeScreen.id !== 'debug-screen') {{
                previousScreen = activeScreen.id;
            }}
            pyBackend.getDebugCommand(JSON.stringify(gatherState()));
            transitionTo('debug-screen');
        }}

        function closeSettings() {{
            transitionTo(previousScreen);
        }}

        function setDebugCommand(cmdStr) {{
            document.getElementById('txt-debug-cmd').value = cmdStr;
        }}

        function toggleDryRun() {{
            pyBackend.setDryRun(document.getElementById('chk-dry-run').checked);
        }}

        function restartSystem() {{
            document.body.style.opacity = '0';
            setTimeout(() => {{ if (pyBackend) pyBackend.rebootSystem(); }}, 600);
        }}
    </script>
</body>
</html>
"""

class BackendBridge(QObject):
    log_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.dry_run = False
        self.process = None

    @Slot()
    def loadDisks(self):
        disks = []
        partitions = []

        try:
            out = subprocess.check_output(["lsblk", "-d", "-n", "-o", "NAME,SIZE,MODEL,TYPE", "-J"]).decode()
            for d in json.loads(out).get('blockdevices', []):
                if d['type'] in ['loop', 'rom'] or d['name'].startswith('zram'): continue
                model = d.get('model', 'Unknown Drive') or "Unknown Drive"
                disks.append({"name": f"{model} ({d['size']}) - /dev/{d['name']}", "path": f"/dev/{d['name']}"})
        except Exception as e:
            disks.append({"name": f"Error loading disks: {e}", "path": ""})

        try:
            out = subprocess.check_output(["lsblk", "-l", "-n", "-o", "NAME,PATH,SIZE,TYPE,FSTYPE", "-J"]).decode()
            for p in json.loads(out).get('blockdevices', []):
                if p['type'] == 'part' and not p['name'].startswith('zram'):
                    fs = p.get('fstype', 'unknown') or 'unknown'
                    label = f"{p['path']} ({p.get('size', '')} - {fs})"
                    partitions.append({"path": p['path'], "label": label})
        except: pass

        js_code = f"renderDisks({json.dumps(disks)}, {json.dumps(partitions)});"
        self.parent_window.view.page().runJavaScript(js_code)

    def build_command(self, state):
        cmd = ["python3", "-u", BACKEND_SCRIPT]

        if state['disk_mode'] == 'auto':
            if state.get('disk'): cmd.extend(["--disk", state['disk']])
            cmd.extend(["--swap", "4G"])
        else:
            if state.get('rootfs'): cmd.extend(["--rootfs", state['rootfs']])
            if state.get('boot'): cmd.extend(["--boot", state['boot']])
            if state.get('swap'): cmd.extend(["--swap", state['swap']])

        os_info = get_os_release()
        cmd.extend(["--target", os_info.get("ID", "arch")])
        if state['install_type'] == 'online': cmd.append("--online")

        cmd.extend(["--keyboard", state['keyboard']])
        cmd.extend(["--locale-lang", state['locale_lang']])
        cmd.extend(["--locale-enc", state['locale_enc']])
        cmd.extend(["--console-font", state['console_font']])
        cmd.extend(["--mirror-region", state['mirror_region']])

        cmd.extend(["--kernel", state['kernel']])
        cmd.extend(["--audio", state['audio']])
        cmd.extend(["--timezone", state['timezone']])
        cmd.extend(["--hostname", state['hostname']])

        if state['zram']:
            cmd.append("--zram")
            cmd.extend(["--zram-comp", state['zram_comp']])

        if state['bluetooth']: cmd.append("--bluetooth")
        if state.get('user'): cmd.extend(["--user", state['user']])

        cmd.extend(["--i-am-very-stupid", "--debug"])

        env = os.environ.copy()
        env["CHIMERA_ROOT_PASS"] = state.get('root_pass', '')
        env["CHIMERA_USER_PASS"] = state.get('user_pass', '')

        return cmd, env

    @Slot(str)
    def startInstall(self, state_json):
        state = json.loads(state_json)
        cmd, env = self.build_command(state)

        if self.dry_run:
            self.log_message.emit("--- DRY RUN MODE ---\n" + " ".join(cmd) + "\n")
            self.update_progress(100, "Dry Run Complete")
            return

        # Parent QProcess to self so PySide lifecycle handles it safely
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)

        qenv = QProcessEnvironment.systemEnvironment()
        qenv.insert("CHIMERA_ROOT_PASS", env["CHIMERA_ROOT_PASS"])
        qenv.insert("CHIMERA_USER_PASS", env["CHIMERA_USER_PASS"])
        self.process.setProcessEnvironment(qenv)

        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.install_finished)
        self.process.start(cmd[0], cmd[1:])

    def read_output(self):
        data = self.process.readAllStandardOutput().data().decode()
        self.log_message.emit(data)

        lower = data.lower()
        if "partitioning" in lower: self.update_progress(20, "Partitioning Disk...")
        elif "installing base" in lower or "rsync" in lower or "copying base" in lower: self.update_progress(50, "Installing Base System...")
        elif "configuring system" in lower: self.update_progress(75, "Configuring System...")
        elif "bootloader" in lower: self.update_progress(90, "Installing Bootloader...")

    @Slot(int, QProcess.ExitStatus)
    def install_finished(self, exit_code, exit_status):
        if exit_code == 0 and exit_status == QProcess.NormalExit:
            self.update_progress(100, "Installation Successful!")
        else:
            self.update_progress(0, "Installation Failed")

    def update_progress(self, val, status):
        js_code = f"""
        document.getElementById('progress-percent').innerText = '{val}%';
        document.getElementById('install-status').style.opacity = 0;
        setTimeout(() => {{
            document.getElementById('install-status').innerText = '{status}';
            document.getElementById('install-status').style.opacity = 1;
        }}, 150);
        var ring = document.querySelector('.ring-fill');
        var offset = 502 - ({val} / 100) * 502;
        ring.style.strokeDashoffset = offset;
        if({val} == 100) {{
            setTimeout(() => transitionTo('complete-screen'), 1000);
        }}
        """
        self.parent_window.view.page().runJavaScript(js_code)

    @Slot(str)
    def getDebugCommand(self, state_json):
        state = json.loads(state_json)
        cmd, _ = self.build_command(state)
        cmd_str = " ".join(cmd)
        self.parent_window.view.page().runJavaScript(f"setDebugCommand({json.dumps(cmd_str)});")

    @Slot(bool)
    def setDryRun(self, val):
        self.dry_run = val

    @Slot()
    def rebootSystem(self):
        if self.dry_run:
            QApplication.quit()
            return
        try:
            subprocess.run(["systemctl", "reboot"], check=False)
            subprocess.run(["reboot"], check=False)
        except: pass
        QApplication.quit()

class InstallerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        os_info = get_os_release()
        distro_name = os_info.get("PRETTY_NAME", os_info.get("NAME", "Linux Installer"))
        logo_name = os_info.get("LOGO", "")
        logo_uri = load_logo_data_uri(logo_name)
        hostname = f"{os_info.get('ID', 'linux')}-pc"

        self.setWindowTitle(f"{distro_name} Installer")
        self.resize(850, 650)
        self.setStyleSheet("background: black;")

        self.view = QWebEngineView()
        self.setCentralWidget(self.view)

        self.channel = QWebChannel()
        self.backend = BackendBridge(self)
        self.channel.registerObject("backend", self.backend)
        self.view.page().setWebChannel(self.channel)

        html_content = build_html(distro_name, logo_uri, hostname)
        self.view.setHtml(html_content)

        self.backend.log_message.connect(self.append_log)

    def append_log(self, text):
        safe_text = json.dumps(text)
        js_code = f"appendLog({safe_text});"
        self.view.page().runJavaScript(js_code)

if __name__ == "__main__":
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --use-gl=egl"
    app = QApplication(sys.argv)
    win = InstallerWindow()
    win.show()
    sys.exit(app.exec())
