#!/usr/bin/env python3
import sys
import os
import subprocess
import json
import shutil
import socket
import base64
from PySide6.QtWidgets import (QApplication, QMainWindow, QMessageBox, QDialog,
                               QVBoxLayout, QCheckBox, QTextEdit, QPushButton, QLabel)
from PySide6.QtCore import Qt, QProcess, QTimer, QObject, Slot, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

# --- Configuration ---
ASSET_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_SCRIPT = "/usr/share/chimera/chimera.py"
ZONEINFO_PATH = "/usr/share/zoneinfo"
LOCAL_LOGO = os.path.join(ASSET_DIR, "logo.png")

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
        paths.append(f"/usr/share/pixmaps/{logo_name}.png")
        paths.append(f"/usr/share/pixmaps/{logo_name}.svg")
        paths.append(f"/usr/share/icons/{logo_name}.png")
        paths.append(f"/usr/share/icons/{logo_name}.svg")

    paths.append(LOCAL_LOGO)
    paths.append("/usr/share/pixmaps/chimera.png")

    for path in paths:
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
                ext = os.path.splitext(path)[1][1:].lower()
                mime = "image/svg+xml" if ext == "svg" else f"image/{ext}"
                return f"data:{mime};base64,{data}"
    return ""

def build_html(distro_name, logo_uri, hostname):
    logo_css = f"url('{logo_uri}') center/contain no-repeat" if logo_uri else "linear-gradient(135deg, var(--accent-1), var(--accent-2))"
    logo_inner_display = "none" if logo_uri else "block"

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{distro_name} Installer</title>
    <!-- Inter + Noto Sans CJK Webfonts -->
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
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Noto Sans', 'Noto Sans CJK SC', 'Noto Sans SC', 'Noto Sans CJK JP', 'Noto Sans JP', 'WenQuanYi Micro Hei', sans-serif;
            background: var(--bg-color);
            color: var(--text-primary);
            overflow: hidden;
            height: 100vh;
            width: 100vw;
            cursor: default;
            transition: opacity 0.5s ease;
        }}

        #bg-canvas {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; filter: blur(80px) saturate(150%); opacity: 0.4; }}
        .blob {{ position: absolute; border-radius: 50%; mix-blend-mode: screen; animation: float 20s infinite ease-in-out; }}
        .blob:nth-child(1) {{ width: 40vmax; height: 40vmax; background: var(--accent-1); top: -10vmax; left: -10vmax; }}
        .blob:nth-child(2) {{ width: 35vmax; height: 35vmax; background: var(--accent-2); bottom: -10vmax; right: -10vmax; animation-delay: -5s; }}
        .blob:nth-child(3) {{ width: 20vmax; height: 20vmax; background: #ff2d55; top: 40%; left: 50%; animation-delay: -10s; }}
        @keyframes float {{ 0%, 100% {{ transform: translate(0, 0) scale(1); }} 33% {{ transform: translate(10vw, 10vh) scale(1.1); }} 66% {{ transform: translate(-10vw, 5vh) scale(0.9); }} }}

        #installer {{ position: relative; z-index: 1; width: 100%; height: 100%; display: flex; justify-content: center; align-items: center; }}
        .screen {{ position: absolute; width: 100%; max-width: 600px; padding: 40px; display: flex; flex-direction: column; align-items: center; opacity: 0; pointer-events: none; transform: translateY(20px) scale(0.98); transition: opacity 0.6s var(--smooth-curve), transform 0.8s var(--spring-curve); }}
        .screen.active {{ opacity: 1; pointer-events: auto; transform: translateY(0) scale(1); }}

        #welcome-logo {{ width: 100px; height: 100px; background: {logo_css}; border-radius: 28px; margin-bottom: 40px; box-shadow: 0 10px 40px rgba(41, 151, 255, 0.3); animation: breathe 4s infinite ease-in-out; display: flex; justify-content: center; align-items: center; }}
        @keyframes breathe {{ 0%, 100% {{ transform: scale(1); }} 50% {{ transform: scale(1.05); }} }}
        .logo-inner {{ display: {logo_inner_display}; width: 40px; height: 40px; border: 4px solid white; border-radius: 50%; border-right-color: transparent; transform: rotate(45deg); }}

        #welcome-text {{ font-size: 56px; font-weight: 700; letter-spacing: -0.03em; margin-bottom: 20px; text-align: center; transition: opacity 0.3s ease; }}
        .subtitle {{ font-size: 18px; color: var(--text-secondary); font-weight: 400; margin-bottom: 60px; text-align: center; }}

        .btn-primary {{ background: rgba(255, 255, 255, 0.1); backdrop-filter: blur(20px); border: 1px solid var(--glass-border); color: white; padding: 14px 40px; border-radius: 980px; font-size: 17px; font-weight: 500; cursor: pointer; transition: transform 0.2s var(--spring-curve), background 0.2s ease; }}
        .btn-primary:hover {{ background: rgba(255, 255, 255, 0.15); transform: scale(1.03); }}
        .btn-primary:active {{ transform: scale(0.98); }}

        .card {{ background: var(--glass-bg); backdrop-filter: blur(40px) saturate(180%); border: 1px solid var(--glass-border); border-radius: 20px; width: 100%; padding: 8px; box-shadow: 0 20px 50px rgba(0,0,0,0.4); margin-bottom: 30px; }}
        .list-item {{ display: flex; align-items: center; padding: 16px; border-radius: 12px; cursor: pointer; transition: background 0.15s ease; opacity: 0; transform: translateY(10px); }}
        .screen.active .list-item {{ animation: cascadeIn 0.5s var(--spring-curve) forwards; }}
        .screen.active .list-item:nth-child(1) {{ animation-delay: 0.1s; }}
        .screen.active .list-item:nth-child(2) {{ animation-delay: 0.15s; }}
        .screen.active .list-item:nth-child(3) {{ animation-delay: 0.2s; }}
        .screen.active .list-item:nth-child(4) {{ animation-delay: 0.25s; }}
        @keyframes cascadeIn {{ to {{ opacity: 1; transform: translateY(0); }} }}
        .list-item:hover {{ background: rgba(255,255,255,0.08); }}
        .item-name {{ font-size: 17px; flex-grow: 1; }}
        .item-status {{ font-size: 14px; color: var(--accent-1); opacity: 0; transition: opacity 0.3s ease; }}
        .list-item.selected .item-status {{ opacity: 1; }}

        .form-group {{ width: 100%; margin-bottom: 20px; }}
        .form-label {{ font-size: 14px; color: var(--text-secondary); margin-bottom: 8px; display: block; }}
        .form-input {{ width: 100%; padding: 14px; border-radius: 12px; background: rgba(255,255,255,0.05); border: 1px solid var(--glass-border); color: white; font-size: 16px; outline: none; transition: background 0.2s; }}
        .form-input:focus {{ background: rgba(255,255,255,0.1); border-color: var(--accent-1); }}

        .progress-ring-container {{ position: relative; width: 180px; height: 180px; margin-bottom: 40px; }}
        .progress-ring {{ transform: rotate(-90deg); width: 100%; height: 100%; }}
        .ring-bg {{ stroke: rgba(255,255,255,0.1); stroke-width: 8; fill: transparent; }}
        .ring-fill {{ stroke: url(#gradient); stroke-width: 8; fill: transparent; stroke-linecap: round; stroke-dasharray: 502; stroke-dashoffset: 502; transition: stroke-dashoffset 0.1s linear; filter: drop-shadow(0 0 10px var(--accent-1)); }}
        .progress-text {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 48px; font-weight: 600; letter-spacing: -0.02em; }}
        .status-text {{ font-size: 18px; color: var(--text-secondary); height: 24px; transition: opacity 0.3s ease; text-align: center; margin-bottom: 20px; }}

        #terminal-log {{ width: 100%; height: 150px; background: rgba(0,0,0,0.6); border: 1px solid var(--glass-border); border-radius: 12px; padding: 15px; color: #0aff; font-family: monospace; font-size: 12px; overflow-y: auto; display: none; margin-top: 20px; }}
        .btn-text {{ background: none; border: none; color: var(--text-secondary); cursor: pointer; font-size: 14px; text-decoration: underline; margin-top: 10px; }}
        .btn-text:hover {{ color: white; }}

        #settings-btn {{ position: fixed; bottom: 20px; left: 20px; width: 40px; height: 40px; background: var(--glass-bg); border: 1px solid var(--glass-border); border-radius: 50%; display: flex; justify-content: center; align-items: center; cursor: pointer; z-index: 100; backdrop-filter: blur(20px); transition: transform 0.2s, background 0.2s; }}
        #settings-btn:hover {{ background: rgba(255,255,255,0.15); transform: rotate(45deg); }}

        .checkmark {{ width: 100px; height: 100px; border-radius: 50%; background: var(--accent-1); display: flex; justify-content: center; align-items: center; margin-bottom: 40px; animation: popIn 0.6s var(--spring-curve); box-shadow: 0 0 50px rgba(41, 151, 255, 0.5); }}
        @keyframes popIn {{ 0% {{ transform: scale(0); opacity: 0; }} 100% {{ transform: scale(1); opacity: 1; }} }}
        .checkmark svg {{ width: 50px; height: 50px; stroke: white; stroke-width: 3; fill: none; stroke-dasharray: 50; stroke-dashoffset: 50; animation: drawCheck 0.4s 0.3s ease forwards; }}
        @keyframes drawCheck {{ to {{ stroke-dashoffset: 0; }} }}
    </style>
</head>
<body>
    <div id="bg-canvas"><div class="blob"></div><div class="blob"></div><div class="blob"></div></div>
    <svg width="0" height="0"><defs><linearGradient id="gradient" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#2997ff" /><stop offset="100%" stop-color="#6e5cff" /></linearGradient></defs></svg>

    <!-- Settings Gear (Bottom Left) -->
    <div id="settings-btn" onclick="openSettings()">⚙</div>

    <div id="installer">
        <!-- Welcome -->
        <section id="welcome-screen" class="screen active">
            <div id="welcome-logo"><div class="logo-inner"></div></div>
            <h1 id="welcome-text">Welcome</h1>
            <p class="subtitle">Let's get your system set up.</p>
            <button class="btn-primary" onclick="transitionTo('type-screen')">Continue</button>
        </section>

        <!-- Install Type -->
        <section id="type-screen" class="screen">
            <h2 style="font-size: 32px; margin-bottom: 30px; align-self: flex-start;">Installation Mode</h2>
            <div class="card">
                <div class="list-item selected" onclick="selectOption(this, 'online')">
                    <span class="item-name">Online Install (Downloads latest packages)</span>
                    <span class="item-status">Selected</span>
                </div>
                <div class="list-item" onclick="selectOption(this, 'offline')">
                    <span class="item-name">Offline Install (Uses local packages)</span>
                    <span class="item-status">Selected</span>
                </div>
            </div>
            <button class="btn-primary" onclick="transitionTo('disk-screen')">Continue</button>
        </section>

        <!-- Disk Selection -->
        <section id="disk-screen" class="screen">
            <h2 style="font-size: 32px; margin-bottom: 30px; align-self: flex-start;">Choose Storage Drive</h2>
            <div class="card" id="disk-list">
                <!-- Disks injected by Python -->
            </div>
            <button class="btn-primary" onclick="transitionTo('user-screen')">Continue</button>
        </section>

        <!-- User Setup -->
        <section id="user-screen" class="screen">
            <h2 style="font-size: 32px; margin-bottom: 30px; align-self: flex-start;">Create User Account</h2>
            <div class="form-group">
                <label class="form-label">Computer Name (Hostname)</label>
                <input type="text" class="form-input" id="inp-host" value="{hostname}">
            </div>
            <div class="form-group">
                <label class="form-label">Username</label>
                <input type="text" class="form-input" id="inp-user">
            </div>
            <div class="form-group">
                <label class="form-label">Password (Root & User)</label>
                <input type="password" class="form-input" id="inp-pass">
            </div>
            <button class="btn-primary" onclick="transitionTo('install-screen'); startInstall();">Install Now</button>
        </section>

        <!-- Installation -->
        <section id="install-screen" class="screen">
            <div class="progress-ring-container">
                <svg class="progress-ring" viewBox="0 0 180 180"><circle class="ring-bg" cx="90" cy="90" r="80"></circle><circle class="ring-fill" cx="90" cy="90" r="80"></circle></svg>
                <div class="progress-text" id="progress-percent">0%</div>
            </div>
            <div class="status-text" id="install-status">Preparing installation...</div>
            <button class="btn-text" onclick="toggleLog()">Show Details</button>
            <div id="terminal-log"></div>
        </section>

        <!-- Complete -->
        <section id="complete-screen" class="screen">
            <div class="checkmark"><svg viewBox="0 0 50 50"><path d="M14 27l5 5 16-16" /></svg></div>
            <h1 style="font-size: 40px; font-weight: 700; margin-bottom: 20px;">All Set.</h1>
            <p class="subtitle" style="margin-bottom: 40px;">{distro_name} has been successfully installed.</p>
            <button class="btn-primary" onclick="restartSystem()">Restart</button>
        </section>
    </div>

    <script>
        let selectedInstallType = 'online';
        let selectedDisk = null;
        let pyBackend = null;

        new QWebChannel(qt.webChannelTransport, function(channel) {{
            pyBackend = channel.objects.backend;
            pyBackend.loadDisks();
        }});

        function transitionTo(screenId) {{
            const currentScreen = document.querySelector('.screen.active');
            const nextScreen = document.getElementById(screenId);
            currentScreen.style.opacity = '0';
            currentScreen.style.transform = 'scale(0.95)';
            setTimeout(() => {{
                currentScreen.classList.remove('active');
                nextScreen.classList.add('active');
            }}, 400);
        }}

        const welcomeText = document.getElementById('welcome-text');
        const languages = ['Welcome', 'Bienvenue', 'Willkommen', '欢迎', 'ようこそ'];
        let langIndex = 0;
        setInterval(() => {{
            if (document.getElementById('welcome-screen').classList.contains('active')) {{
                welcomeText.style.opacity = '0';
                setTimeout(() => {{
                    langIndex = (langIndex + 1) % languages.length;
                    welcomeText.textContent = languages[langIndex];
                    welcomeText.style.opacity = '1';
                }}, 200);
            }}
        }}, 2500);

        function selectOption(el, type) {{
            document.querySelectorAll('#type-screen .list-item').forEach(i => i.classList.remove('selected'));
            el.classList.add('selected');
            selectedInstallType = type;
        }}

        function selectDisk(el, diskPath) {{
            document.querySelectorAll('#disk-screen .list-item').forEach(i => i.classList.remove('selected'));
            el.classList.add('selected');
            selectedDisk = diskPath;
        }}

        function toggleLog() {{
            const log = document.getElementById('terminal-log');
            const btn = event.target;
            if (log.style.display === 'block') {{
                log.style.display = 'none';
                btn.textContent = 'Show Details';
            }} else {{
                log.style.display = 'block';
                btn.textContent = 'Hide Details';
            }}
        }}

        function startInstall() {{
            const userData = {{
                host: document.getElementById('inp-host').value,
                user: document.getElementById('inp-user').value,
                pass: document.getElementById('inp-pass').value
            }};
            pyBackend.startInstall(selectedInstallType, selectedDisk, JSON.stringify(userData));
        }}

        function openSettings() {{
            const currentState = {{
                install_type: selectedInstallType,
                disk: selectedDisk,
                host: document.getElementById('inp-host').value,
                user: document.getElementById('inp-user').value,
                pass: document.getElementById('inp-pass').value
            }};
            pyBackend.openDebugSettings(JSON.stringify(currentState));
        }}

        function restartSystem() {{
            document.body.style.opacity = '0';
            setTimeout(() => {{
                if (pyBackend && pyBackend.rebootSystem) {{
                    pyBackend.rebootSystem();
                }}
            }}, 600);
        }}
    </script>
</body>
</html>
"""

# --- Debug Dialog (Rendered by Python) ---
class DebugDialog(QDialog):
    def __init__(self, parent=None, command="", dry_run=False):
        super().__init__(parent)
        self.setWindowTitle("Installer Settings (Debug)")
        self.resize(500, 300)
        self.setStyleSheet("background: #1c1c1e; color: white;")

        layout = QVBoxLayout(self)
        self.chk_dry_run = QCheckBox("Enable Dry Run (Do not write to disk)")
        self.chk_dry_run.setChecked(dry_run)
        self.chk_dry_run.setStyleSheet("color: white;")
        layout.addWidget(self.chk_dry_run)

        layout.addWidget(QLabel("Generated Backend Command:"))
        self.txt_cmd = QTextEdit()
        self.txt_cmd.setPlainText(command)
        self.txt_cmd.setReadOnly(True)
        layout.addWidget(self.txt_cmd)

        btn_close = QPushButton("Apply & Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

# --- Python / JS Bridge ---
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
        try:
            cmd = ["lsblk", "-d", "-n", "-o", "NAME,SIZE,MODEL,TYPE", "-J"]
            out = subprocess.check_output(cmd).decode()
            data = json.loads(out)
            for d in data.get('blockdevices', []):
                if d['type'] in ['loop', 'rom'] or d['name'].startswith('zram'): continue
                model = d.get('model', 'Unknown Drive') or "Unknown Drive"
                disks.append({"name": f"{model} ({d['size']}) - /dev/{d['name']}", "path": f"/dev/{d['name']}"})
        except Exception as e:
            disks.append({"name": f"Error: {e}", "path": ""})

        js_code = "document.getElementById('disk-list').innerHTML = '';"
        for d in disks:
            js_code += f"""
            var div = document.createElement('div');
            div.className = 'list-item';
            div.onclick = function() {{ selectDisk(this, '{d["path"]}') }};
            div.innerHTML = '<span class="item-name">{d["name"]}</span><span class="item-status">Selected</span>';
            document.getElementById('disk-list').appendChild(div);
            """
        self.parent_window.view.page().runJavaScript(js_code)

    @Slot(str, str, str)
    def startInstall(self, install_type, disk_path, user_data_json):
        user_data = json.loads(user_data_json)
        cmd = self.build_command(install_type, disk_path, user_data)

        if self.dry_run:
            self.log_message.emit("--- DRY RUN MODE ---\n" + " ".join(cmd) + "\n")
            self.update_progress(100, "Dry Run Complete")
            return

        self.process = QProcess()
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.install_finished)
        self.process.start(cmd[0], cmd[1:])

    def build_command(self, install_type, disk_path, user_data):
        cmd = ["python3", "-u", BACKEND_SCRIPT]

        if disk_path:
            cmd.extend(["--disk", disk_path])
            cmd.extend(["--swap", "4G"])

        cmd.extend(["--user", user_data.get('user', '')])
        cmd.extend(["--passwd", user_data.get('pass', '')])
        cmd.extend(["--timezone", "UTC"])

        os_info = get_os_release()
        cmd.extend(["--target", os_info.get("ID", "arch")])

        if install_type == "online": cmd.append("--online")
        cmd.extend(["--i-am-very-stupid", "--debug"])
        return cmd

    def read_output(self):
        data = self.process.readAllStandardOutput().data().decode()
        self.log_message.emit(data)

        lower = data.lower()
        if "partitioning" in lower: self.update_progress(20, "Partitioning Disk...")
        elif "installing base" in lower: self.update_progress(50, "Installing Base System...")
        elif "configuring" in lower: self.update_progress(75, "Configuring System...")
        elif "bootloader" in lower: self.update_progress(90, "Installing Bootloader...")

    def install_finished(self):
        if self.process.exitCode() == 0:
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
    def openDebugSettings(self, state_json):
        state = json.loads(state_json)
        cmd = self.build_command(state['install_type'], state['disk'], state)

        dlg = DebugDialog(self.parent_window, " ".join(cmd), self.dry_run)
        if dlg.exec():
            self.dry_run = dlg.chk_dry_run.isChecked()

    @Slot()
    def rebootSystem(self):
        """Triggers the actual system reboot or closes cleanly in Dry Run mode."""
        if self.dry_run:
            self.log_message.emit("--- DRY RUN: System reboot requested ---")
            QApplication.quit()
            return

        try:
            # Issue system reboot
            subprocess.run(["systemctl", "reboot"], check=False)
            subprocess.run(["reboot"], check=False)
        except Exception as e:
            print(f"Reboot command error: {e}")

        QApplication.quit()

# --- Main Window ---
class InstallerWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        os_info = get_os_release()
        distro_name = os_info.get("PRETTY_NAME", os_info.get("NAME", "Linux Installer"))
        logo_name = os_info.get("LOGO", "")
        logo_uri = load_logo_data_uri(logo_name)
        hostname = f"{os_info.get('ID', 'linux')}-pc"

        self.setWindowTitle(f"{distro_name} Installer")
        self.resize(800, 600)
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
        safe_text = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        js_code = f"""
        var log = document.getElementById('terminal-log');
        log.innerHTML += '{safe_text}';
        log.scrollTop = log.scrollHeight;
        """
        self.view.page().runJavaScript(js_code)


if __name__ == "__main__":
    # Fix for running as root + hardware acceleration in VMs/VirGL
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --use-gl=egl"

    app = QApplication(sys.argv)
    win = InstallerWindow()
    win.show()
    sys.exit(app.exec())
