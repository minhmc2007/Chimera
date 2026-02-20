#!/usr/bin/env python3
import sys
import os
import subprocess
import json
import shutil
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QStackedWidget, QPushButton, 
                               QRadioButton, QComboBox, QLineEdit, QCheckBox, 
                               QFrame, QListWidget, QListWidgetItem, QMessageBox,
                               QTextEdit, QProgressBar, QSpinBox, QGroupBox, 
                               QDialog, QToolButton, QSizePolicy, QScrollArea, QAbstractItemView)
from PySide6.QtCore import Qt, QSize, QProcess, QTimer, QSettings, QPoint
from PySide6.QtGui import QPixmap, QIcon, QPalette, QColor, QFont, QPainter, QBrush, QTextCursor

# --- Configuration & Constants ---
ASSET_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_LOGO_PATH = os.path.join(ASSET_DIR, "logo.png")
BG_PATH = os.path.join(ASSET_DIR, "backg.png")
BACKEND_SCRIPT = os.path.join(ASSET_DIR, "main.py")
ZONEINFO_PATH = "/usr/share/zoneinfo"

# Colors
COL_SKY_BLUE = "#87CEEB"
COL_DEEP_SKY = "#00BFFF"

# Dark Theme Palette
D_BG_MAIN = "#31363b"
D_BG_SIDE = "#232629"
D_TEXT = "#eff0f1"
D_INPUT_BG = "#232629"
D_INPUT_BORDER = "#565a5e"
D_HIGHLIGHT = "#3daee9"

# Light Theme Palette
L_BG_MAIN = "#eff0f1"
L_BG_SIDE = "#e3e5e7"
L_TEXT = "#232629"
L_INPUT_BG = "#ffffff"
L_INPUT_BORDER = "#bckecc"
L_HIGHLIGHT = "#3daee9"

# --- Utility Functions ---
def get_os_release():
    """Parses /etc/os-release into a dictionary."""
    info = {
        "NAME": "Linux",
        "PRETTY_NAME": "Linux Installer",
        "ID": "arch",
        "LOGO": "chimera"
    }
    try:
        os_release_path = "/etc/os_release" if not os.path.exists("/etc/os-release") else "/etc/os-release"
        if os.path.exists(os_release_path):
            with open(os_release_path) as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        info[k] = v.strip('"').strip("'")
    except Exception as e:
        print(f"Failed to read os-release: {e}")
    return info

def get_system_theme():
    try:
        kconfig = subprocess.check_output(["kreadconfig6", "--group", "KDE", "--key", "LookAndFeelPackage"], stderr=subprocess.DEVNULL).decode().strip()
        if "Dark" in kconfig or "dark" in kconfig: return "dark"
        if "Light" in kconfig or "light" in kconfig: return "light"
    except: pass
    
    try:
        gsettings = subprocess.check_output(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"], stderr=subprocess.DEVNULL).decode().strip()
        if "dark" in gsettings: return "dark"
    except: pass
        
    return "dark"

# --- Custom Widgets ---
class StepItem(QListWidgetItem):
    def __init__(self, text):
        super().__init__(text)
        self.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        self.setFont(font)

class FancyButton(QPushButton):
    def __init__(self, text, primary=False):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.primary = primary
        self.setFixedHeight(40)
        self.setFont(QFont("Segoe UI", 10))

class DebugDialog(QDialog):
    def __init__(self, parent=None, command="", dry_run=False, is_dark=True):
        super().__init__(parent)
        self.setWindowTitle("Installer Settings (Debug)")
        self.resize(600, 400)
        self.is_dark = is_dark
        
        layout = QVBoxLayout(self)
        self.chk_dry_run = QCheckBox("Enable Dry Run (Do not write to disk)")
        self.chk_dry_run.setChecked(dry_run)
        layout.addWidget(self.chk_dry_run)
        
        self.chk_force_light = QCheckBox("Switch Theme (Dark/Light)")
        self.chk_force_light.setChecked(not is_dark)
        layout.addWidget(self.chk_force_light)

        layout.addWidget(QLabel("Generated Backend Command:"))
        self.txt_cmd = QTextEdit()
        self.txt_cmd.setPlainText(command)
        self.txt_cmd.setReadOnly(True)
        layout.addWidget(self.txt_cmd)
        
        btn_close = QPushButton("Apply & Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)
        self.apply_style()

    def apply_style(self):
        bg = D_BG_MAIN if self.is_dark else L_BG_MAIN
        fg = D_TEXT if self.is_dark else L_TEXT
        txt_bg = "#111" if self.is_dark else "#fff"
        self.setStyleSheet(f"QDialog {{ background-color: {bg}; color: {fg}; }} QLabel, QCheckBox {{ color: {fg}; }} QTextEdit {{ background-color: {txt_bg}; color: {fg}; }}")

# --- Main Window ---
class InstallerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.os_info = get_os_release()
        self.distro_name = self.os_info.get("PRETTY_NAME", "Linux Distro")
        self.distro_id = self.os_info.get("ID", "arch")
        
        self.setWindowTitle(f"Chimera Installer - {self.distro_name}")
        self.resize(1000, 700)
        
        self.theme_mode = get_system_theme()
        self.dry_run = False
        self.install_data = {
            "disk": None, "root": None, "boot": None, "swap": None, "swap_size": 0,
            "method": "whole", "user": "", "pass": "", "host": "chimera-pc", "tz": "UTC"
        }

        self.setup_ui()
        self.apply_stylesheet()
        self.check_root()

    def check_root(self):
        if os.geteuid() != 0:
            QMessageBox.warning(self, "Root Required", "Running without root privileges.\nDisk operations will fail.")

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Sidebar ---
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(240)
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(0, 0, 0, 15)
        side_layout.setSpacing(10)

        logo_key = self.os_info.get("LOGO", "").strip()
        sys_logo_path = f"/usr/share/pixmaps/{logo_key}.png"
        lbl_logo = QLabel()
        lbl_logo.setAlignment(Qt.AlignCenter)
        lbl_logo.setFixedHeight(140)
        
        final_logo = None
        if os.path.exists(sys_logo_path): final_logo = sys_logo_path
        elif os.path.exists(LOCAL_LOGO_PATH): final_logo = LOCAL_LOGO_PATH
            
        if final_logo:
            pix = QPixmap(final_logo).scaled(110, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            lbl_logo.setPixmap(pix)
        else:
            lbl_logo.setText("CHIMERA")
            lbl_logo.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
            
        side_layout.addWidget(lbl_logo)

        self.step_list = QListWidget()
        self.step_list.setFocusPolicy(Qt.NoFocus)
        steps = ["Welcome", "Location", "Disk Setup", "Partitions", "Users", "Summary", "Install"]
        for s in steps: self.step_list.addItem(StepItem(s))
        self.step_list.setCurrentRow(0)
        side_layout.addWidget(self.step_list)

        self.btn_debug = QToolButton()
        self.btn_debug.setText("⚙") 
        self.btn_debug.setCursor(Qt.PointingHandCursor)
        self.btn_debug.clicked.connect(self.open_debug_settings)
        self.btn_debug.setFixedSize(40, 40)
        
        bot_layout = QHBoxLayout()
        bot_layout.setContentsMargins(15, 0, 0, 0)
        bot_layout.addWidget(self.btn_debug)
        bot_layout.addStretch()
        side_layout.addLayout(bot_layout)
        main_layout.addWidget(self.sidebar)

        # --- Content ---
        self.content_container = QWidget()
        content_layout = QVBoxLayout(self.content_container)
        content_layout.setContentsMargins(40, 40, 40, 40)

        self.lbl_header = QLabel(f"Welcome to {self.distro_name}")
        self.lbl_header.setFont(QFont("Segoe UI", 24, QFont.Bold))
        self.lbl_header.setStyleSheet(f"color: {COL_SKY_BLUE}; margin-bottom: 20px;")
        content_layout.addWidget(self.lbl_header)

        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages)

        nav_layout = QHBoxLayout()
        nav_layout.setContentsMargins(0, 20, 0, 0)
        self.btn_back = FancyButton("Back")
        self.btn_next = FancyButton("Next", primary=True)
        self.btn_back.clicked.connect(self.go_back)
        self.btn_next.clicked.connect(self.go_next)
        
        nav_layout.addStretch()
        nav_layout.addWidget(self.btn_back)
        nav_layout.addWidget(self.btn_next)
        content_layout.addLayout(nav_layout)
        main_layout.addWidget(self.content_container)

        self.init_pages()
        self.update_nav()

    def init_pages(self):
        # 0. Welcome
        p_welcome = QWidget()
        vbox = QVBoxLayout(p_welcome)
        lbl_hero = QLabel()
        lbl_hero.setAlignment(Qt.AlignCenter)
        if os.path.exists(BG_PATH):
            pix = QPixmap(BG_PATH).scaled(700, 350, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            lbl_hero.setPixmap(pix)
        else:
            lbl_hero.setText(f"{self.distro_name}\nInstaller")
            lbl_hero.setStyleSheet("font-size: 30px; font-weight: bold; color: #555; border: 2px dashed #555; padding: 60px;")
        
        welcome_str = f"This wizard will guide you through the installation of {self.distro_name}.\n\nPlease ensure you are connected to the internet."
        lbl_text = QLabel(welcome_str)
        lbl_text.setAlignment(Qt.AlignCenter)
        lbl_text.setWordWrap(True)
        lbl_text.setStyleSheet("font-size: 15px; margin-top: 30px;")
        vbox.addStretch()
        vbox.addWidget(lbl_hero)
        vbox.addWidget(lbl_text)
        vbox.addStretch()
        self.pages.addWidget(p_welcome)

        # 1. Location (Improved)
        p_loc = QWidget()
        vbox = QVBoxLayout(p_loc)
        
        vbox.addWidget(QLabel("Select Region:"))
        self.cmb_region = QComboBox()
        self.cmb_region.currentTextChanged.connect(self.populate_cities)
        vbox.addWidget(self.cmb_region)
        
        vbox.addWidget(QLabel("Select Zone/City:"))
        self.cmb_city = QComboBox()
        vbox.addWidget(self.cmb_city)
        
        # Populate Timezones
        self.populate_regions()
        
        vbox.addStretch()
        self.pages.addWidget(p_loc)

        # 2. Disk Setup
        p_disk = QWidget()
        vbox = QVBoxLayout(p_disk)
        vbox.addWidget(QLabel("Select Storage Drive:"))
        self.cmb_disk = QComboBox()
        self.refresh_disks()
        vbox.addWidget(self.cmb_disk)

        grp = QGroupBox("Partitioning Method")
        gv = QVBoxLayout()
        self.rad_erase = QRadioButton("Erase Whole Disk (Automated)")
        self.rad_erase.setChecked(True)
        self.rad_manual = QRadioButton("Manual Partitioning")
        self.rad_erase.toggled.connect(self.toggle_swap_input)
        gv.addWidget(self.rad_erase)
        gv.addWidget(self.rad_manual)
        grp.setLayout(gv)
        vbox.addWidget(grp)

        self.wid_swap = QWidget()
        sl = QHBoxLayout(self.wid_swap)
        sl.setContentsMargins(0,10,0,0)
        sl.addWidget(QLabel("Swap Size (GB) [0 = No Swap]:"))
        self.spin_swap = QSpinBox()
        self.spin_swap.setRange(0, 64)
        self.spin_swap.setValue(4)
        sl.addWidget(self.spin_swap)
        vbox.addWidget(self.wid_swap)
        vbox.addStretch()
        self.pages.addWidget(p_disk)

        # 3. Partitions
        p_part = QWidget()
        vbox = QVBoxLayout(p_part)
        info = QLabel("<b>Partition Manager</b><br>Launch the tool below to modify partitions, then Refresh and assign mount points.")
        vbox.addWidget(info)
        btn_cfdisk = QPushButton(" Launch Partition Tool (cfdisk)")
        btn_cfdisk.setIcon(QIcon.fromTheme("utilities-terminal"))
        btn_cfdisk.setStyleSheet(f"background: {COL_DEEP_SKY}; color: white; padding: 10px; font-weight: bold; border-radius: 5px;")
        btn_cfdisk.clicked.connect(self.launch_cfdisk)
        vbox.addWidget(btn_cfdisk)

        part_grid = QGroupBox("Mount Point Assignment")
        pg_layout = QVBoxLayout(part_grid)
        pg_layout.addWidget(QLabel("Root Partition (/):"))
        self.cmb_root = QComboBox()
        pg_layout.addWidget(self.cmb_root)
        pg_layout.addWidget(QLabel("Boot Partition (/boot or EFI):"))
        self.cmb_boot = QComboBox()
        pg_layout.addWidget(self.cmb_boot)
        pg_layout.addWidget(QLabel("Swap Partition (Optional):"))
        self.cmb_swap = QComboBox()
        pg_layout.addWidget(self.cmb_swap)
        btn_refresh = QPushButton("Refresh Partition List")
        btn_refresh.clicked.connect(self.populate_partitions)
        pg_layout.addWidget(btn_refresh)
        vbox.addWidget(part_grid)
        vbox.addStretch()
        self.pages.addWidget(p_part)

        # 4. Users
        p_user = QWidget()
        form = QVBoxLayout(p_user)
        self.inp_host = QLineEdit("chimera-pc")
        self.inp_user = QLineEdit()
        self.inp_pass = QLineEdit()
        self.inp_pass.setEchoMode(QLineEdit.Password)
        form.addWidget(QLabel("Computer Name (Hostname):"))
        form.addWidget(self.inp_host)
        form.addWidget(QLabel("Username:"))
        form.addWidget(self.inp_user)
        form.addWidget(QLabel("Password (Root & User):"))
        form.addWidget(self.inp_pass)
        form.addStretch()
        self.pages.addWidget(p_user)

        # 5. Summary
        p_sum = QWidget()
        vbox = QVBoxLayout(p_sum)
        self.txt_sum = QTextEdit()
        self.txt_sum.setReadOnly(True)
        vbox.addWidget(QLabel("Installation Summary:"))
        vbox.addWidget(self.txt_sum)
        self.pages.addWidget(p_sum)

        # 6. Install
        p_inst = QWidget()
        vbox = QVBoxLayout(p_inst)
        self.lbl_progress = QLabel("Waiting to start...")
        self.lbl_progress.setAlignment(Qt.AlignCenter)
        self.pbar = QProgressBar()
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("font-family: monospace; font-size: 11px;")
        vbox.addStretch()
        vbox.addWidget(self.lbl_progress)
        vbox.addWidget(self.pbar)
        vbox.addWidget(self.txt_log)
        vbox.addStretch()
        self.pages.addWidget(p_inst)

    # --- Timezone Logic ---
    def populate_regions(self):
        """Step 1: Populate Regions from directories in /usr/share/zoneinfo"""
        self.cmb_region.blockSignals(True)
        self.cmb_region.clear()
        
        if not os.path.exists(ZONEINFO_PATH):
            self.cmb_region.addItem("UTC")
            return

        regions = []
        has_global_files = False
        
        # Scan directories
        for entry in os.listdir(ZONEINFO_PATH):
            full_path = os.path.join(ZONEINFO_PATH, entry)
            if os.path.isdir(full_path):
                # Filter out technical folders
                if entry not in ["posix", "right", "SystemV", "Etc", "posixrules"]:
                    regions.append(entry)
            elif os.path.isfile(full_path) and entry[0].isupper() and not entry.endswith(".tab"):
                has_global_files = True

        regions.sort()
        if has_global_files:
            regions.insert(0, "Global") # For root files like UTC, Japan, Turkey
        
        self.cmb_region.addItems(regions)
        self.cmb_region.blockSignals(False)
        
        # Auto-Select based on /etc/localtime
        try:
            if os.path.islink("/etc/localtime"):
                real_path = os.readlink("/etc/localtime") # e.g., /usr/share/zoneinfo/Asia/Ho_Chi_Minh
                parts = real_path.split("zoneinfo/")
                if len(parts) > 1:
                    tz_str = parts[1] # Asia/Ho_Chi_Minh
                    if "/" in tz_str:
                        region, city = tz_str.split("/", 1)
                        idx = self.cmb_region.findText(region)
                        if idx >= 0:
                            self.cmb_region.setCurrentIndex(idx)
                            self.populate_cities(region)
                            # Now select city
                            idx_c = self.cmb_city.findData(tz_str)
                            if idx_c >= 0: self.cmb_city.setCurrentIndex(idx_c)
                    else:
                        # It's a global file like UTC
                        self.cmb_region.setCurrentText("Global")
                        idx_c = self.cmb_city.findText(tz_str)
                        if idx_c >= 0: self.cmb_city.setCurrentIndex(idx_c)
        except:
            pass
            
        if self.cmb_city.count() == 0:
            self.populate_cities(self.cmb_region.currentText())

    def populate_cities(self, region):
        """Step 2: Populate Cities based on Region"""
        self.cmb_city.clear()
        
        if region == "Global":
            # List files in root
            for entry in sorted(os.listdir(ZONEINFO_PATH)):
                full = os.path.join(ZONEINFO_PATH, entry)
                if os.path.isfile(full) and entry[0].isupper() and not entry.endswith(".tab"):
                    self.cmb_city.addItem(entry, entry) # Text=Entry, Data=Entry
        else:
            # Recursively find files in subdir
            base_path = os.path.join(ZONEINFO_PATH, region)
            if not os.path.exists(base_path): return

            zones = []
            for root, dirs, files in os.walk(base_path):
                for f in files:
                    if f.startswith(".") or f.endswith(".tab"): continue
                    
                    abs_path = os.path.join(root, f)
                    # Relative path from base_path (e.g., Argentina/Buenos_Aires)
                    # If base_path is .../America, and file is .../America/Argentina/Buenos_Aires
                    # rel_path is Argentina/Buenos_Aires
                    # But we want the full TZ string for data: America/Argentina/Buenos_Aires
                    
                    # Display name: Argentina/Buenos_Aires
                    rel_display = os.path.relpath(abs_path, base_path)
                    
                    # Backend Data: Region/Display
                    full_tz = f"{region}/{rel_display}"
                    
                    zones.append((rel_display, full_tz))
            
            zones.sort(key=lambda x: x[0])
            for display, data in zones:
                self.cmb_city.addItem(display, data)

    # --- Other Logic ---
    def toggle_swap_input(self):
        self.wid_swap.setVisible(self.rad_erase.isChecked())

    def apply_stylesheet(self):
        is_dark = self.theme_mode == "dark"
        bg_main = D_BG_MAIN if is_dark else L_BG_MAIN
        bg_side = D_BG_SIDE if is_dark else L_BG_SIDE
        text = D_TEXT if is_dark else L_TEXT
        input_bg = D_INPUT_BG if is_dark else L_INPUT_BG
        input_border = D_INPUT_BORDER if is_dark else L_INPUT_BORDER
        highlight = D_HIGHLIGHT if is_dark else L_HIGHLIGHT

        css = f"""
        QMainWindow, QWidget {{ background-color: {bg_main}; color: {text}; }}
        QFrame {{ border: none; }}
        QListWidget {{ background-color: {bg_side}; outline: 0; }}
        QListWidget::item {{ color: #888; padding: 15px; border-left: 4px solid transparent; }}
        QListWidget::item:selected {{ color: {text}; background: {bg_main}; border-left: 4px solid {COL_SKY_BLUE}; }}
        QLabel, QCheckBox, QRadioButton {{ color: {text}; font-size: 14px; background: transparent; }}
        QLineEdit, QComboBox, QSpinBox {{ 
            background-color: {input_bg}; color: {text}; 
            border: 1px solid {input_border}; padding: 8px; border-radius: 4px; font-size: 13px;
        }}
        QLineEdit:focus, QComboBox:focus {{ border: 1px solid {COL_SKY_BLUE}; }}
        QComboBox::drop-down {{ border: none; width: 20px; }}
        QComboBox QAbstractItemView {{
            background-color: {input_bg}; color: {text};
            selection-background-color: {COL_SKY_BLUE}; selection-color: black;
            border: 1px solid {input_border};
        }}
        QGroupBox {{ 
            border: 1px solid {input_border}; margin-top: 20px; font-weight: bold; border-radius: 5px; background: transparent;
        }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {COL_SKY_BLUE}; }}
        QPushButton {{ 
            background-color: {input_bg}; color: {text}; 
            border: 1px solid {input_border}; border-radius: 4px; padding: 6px; 
        }}
        QPushButton:hover {{ background-color: {highlight}; color: white; border: 1px solid {highlight}; }}
        QToolButton {{ color: {text}; border: none; background: transparent; font-size: 20px; }}
        QToolButton:hover {{ color: {COL_SKY_BLUE}; }}
        QScrollBar:vertical {{ background: {bg_main}; width: 10px; }}
        QScrollBar::handle:vertical {{ background: #555; border-radius: 5px; }}
        QPushButton[primary="true"] {{ 
            background-color: {COL_SKY_BLUE}; color: #000; font-weight: bold; border: none; 
        }}
        QPushButton[primary="true"]:hover {{ background-color: {COL_DEEP_SKY}; }}
        """
        self.setStyleSheet(css)
        self.sidebar.setStyleSheet(f"background-color: {bg_side};")

    def refresh_disks(self):
        self.cmb_disk.clear()
        try:
            cmd = ["lsblk", "-d", "-n", "-o", "NAME,SIZE,MODEL,TYPE", "-J"]
            out = subprocess.check_output(cmd).decode()
            data = json.loads(out)
            valid = False
            for d in data.get('blockdevices', []):
                if d['type'] in ['loop', 'rom'] or d['name'].startswith('zram'): continue
                model = d.get('model', 'Unknown Drive') or "Unknown Drive"
                self.cmb_disk.addItem(f"{model} ({d['size']}) - /dev/{d['name']}", f"/dev/{d['name']}")
                valid = True
            if not valid: self.cmb_disk.addItem("No valid disks found", None)
        except Exception as e: self.cmb_disk.addItem(f"Error: {e}", None)

    def launch_cfdisk(self):
        disk = self.cmb_disk.currentData()
        if not disk: return QMessageBox.warning(self, "No Disk", "Please select a valid disk first.")
        terms = ["konsole", "xterm", "gnome-terminal", "alacritty", "kitty", "xfce4-terminal"]
        term_cmd = None
        for t in terms:
            if shutil.which(t):
                if t == "konsole": term_cmd = [t, "--hide-menubar", "-e", "cfdisk", disk]
                elif t == "gnome-terminal": term_cmd = [t, "--", "cfdisk", disk]
                else: term_cmd = [t, "-e", "cfdisk", disk]
                break
        if term_cmd:
            subprocess.run(term_cmd)
            self.populate_partitions()
        else: QMessageBox.critical(self, "Error", "No terminal found.")

    def populate_partitions(self):
        self.cmb_root.clear()
        self.cmb_boot.clear()
        self.cmb_swap.clear()
        self.cmb_swap.addItem("None", None)
        sel_disk = self.cmb_disk.currentData()
        if not sel_disk: return
        try:
            cmd = ["lsblk", "-l", "-n", "-o", "NAME,SIZE,FSTYPE,TYPE,PKNAME", "-J"]
            out = subprocess.check_output(cmd).decode()
            data = json.loads(out)
            disk_name = sel_disk.replace("/dev/", "")
            for dev in data.get('blockdevices', []):
                if dev['type'] == 'part' and dev.get('pkname') == disk_name:
                    fstype = dev.get('fstype') or "Unformatted"
                    txt = f"/dev/{dev['name']} ({dev['size']}) - {fstype}"
                    val = f"/dev/{dev['name']}"
                    self.cmb_root.addItem(txt, val)
                    self.cmb_boot.addItem(txt, val)
                    self.cmb_swap.addItem(txt, val)
        except Exception as e: print(f"Part error: {e}")

    def get_cmd_list(self):
        cmd = ["python3", "-u", BACKEND_SCRIPT]
        d = self.install_data
        if d['method'] == 'whole':
            disk_val = d['disk'] if d['disk'] else "[NO_DISK]"
            cmd.extend(["--disk", disk_val])
            if d.get('swap_size', 0) > 0: cmd.extend(["--swap", f"{d['swap_size']}G"])
        else:
            root_val = d['root'] if d['root'] else "[NO_ROOT]"
            boot_val = d['boot'] if d['boot'] else "[NO_BOOT]"
            cmd.extend(["--rootfs", root_val])
            cmd.extend(["--boot", boot_val])
            if d['swap']: cmd.extend(["--swap", d['swap']])
        cmd.extend(["--user", d['user']])
        cmd.extend(["--passwd", d['pass']])
        cmd.extend(["--timezone", d['tz']])
        distro_name = self.os_info.get("NAME", "Linux")
        target_os = "bal" if "Blue Archive Linux" in distro_name else self.os_info.get("ID", "arch")
        cmd.extend(["--target", target_os])
        cmd.append("--i-am-very-stupid") 
        cmd.append("--debug")
        return cmd

    def open_debug_settings(self):
        try:
            if self.pages.currentIndex() == 2: self.install_data['disk'] = self.cmb_disk.currentData()
        except: pass
        dlg = DebugDialog(self, " ".join(self.get_cmd_list()), self.dry_run, self.theme_mode=="dark")
        if dlg.exec():
            self.dry_run = dlg.chk_dry_run.isChecked()
            new_theme = "light" if dlg.chk_force_light.isChecked() else "dark"
            if new_theme != self.theme_mode:
                self.theme_mode = new_theme
                self.apply_stylesheet()

    def go_next(self):
        idx = self.pages.currentIndex()
        if idx == 1:
            # Capture Timezone Data (The secret data in cmb_city contains the full path)
            self.install_data['tz'] = self.cmb_city.currentData()
            
        if idx == 2:
            self.install_data['disk'] = self.cmb_disk.currentData()
            if not self.install_data['disk']: return QMessageBox.warning(self, "Error", "Please select a disk.")
            self.install_data['method'] = "whole" if self.rad_erase.isChecked() else "manual"
            self.install_data['swap_size'] = self.spin_swap.value()
            if self.install_data['method'] == "whole":
                self.pages.setCurrentIndex(4) 
                self.step_list.setCurrentRow(4)
                self.update_nav()
                return
            else: self.populate_partitions()
        if idx == 3: 
            r, b = self.cmb_root.currentData(), self.cmb_boot.currentData()
            if not r or not b: return QMessageBox.warning(self, "Error", "Root and Boot partitions required.")
            if r == b: return QMessageBox.warning(self, "Error", "Root and Boot cannot be the same.")
            self.install_data['root'] = r
            self.install_data['boot'] = b
            self.install_data['swap'] = self.cmb_swap.currentData()
        if idx == 4:
            u, p = self.inp_user.text(), self.inp_pass.text()
            if not u or not p: return QMessageBox.warning(self, "Error", "User and Password required.")
            self.install_data['user'] = u
            self.install_data['pass'] = p
            self.install_data['host'] = self.inp_host.text()
            self.generate_summary()
        if idx == 5:
            if not self.dry_run:
                if QMessageBox.question(self, "Confirm", "Disk changes are permanent. Proceed?", QMessageBox.Yes|QMessageBox.No) != QMessageBox.Yes: return
            self.start_install()
            return
        if idx < self.pages.count() - 1:
            self.pages.setCurrentIndex(idx + 1)
            self.step_list.setCurrentRow(idx + 1)
        self.update_nav()

    def go_back(self):
        idx = self.pages.currentIndex()
        if idx == 4 and self.install_data['method'] == 'whole':
            self.pages.setCurrentIndex(2)
            self.step_list.setCurrentRow(2)
        elif idx > 0:
            self.pages.setCurrentIndex(idx - 1)
            self.step_list.setCurrentRow(idx - 1)
        self.update_nav()

    def update_nav(self):
        idx = self.pages.currentIndex()
        if idx < self.step_list.count():
             self.step_list.setCurrentRow(idx)
             self.lbl_header.setText(self.step_list.item(idx).text())
        self.btn_back.setVisible(idx > 0 and idx < 6)
        self.btn_next.setVisible(idx < 6)
        if idx == 5:
            self.btn_next.setText("Install Now")
            self.btn_next.setStyleSheet(f"background-color: #e74c3c; color: white; font-weight: bold; border-radius: 4px; padding: 6px;")
        else:
            self.btn_next.setText("Next")
            self.btn_next.setStyleSheet(f"background-color: {COL_SKY_BLUE}; color: black; font-weight: bold; border-radius: 4px; padding: 6px;")

    def generate_summary(self):
        d = self.install_data
        html = f"""
        <h3 style="color:{COL_SKY_BLUE}">System Configuration</h3>
        <b>Distro:</b> {self.distro_name}<br><b>Hostname:</b> {d['host']}<br>
        <b>Timezone:</b> {d['tz']}<br><b>User:</b> {d['user']}<br>
        <h3 style="color:{COL_SKY_BLUE}">Storage Configuration</h3>
        """
        if d['method'] == 'whole': html += f"<b>Mode:</b> Erase Whole Disk<br><b>Target:</b> {d['disk']}<br><b>Swap:</b> {d['swap_size']} GB"
        else: html += f"<b>Mode:</b> Manual Partitioning<br><b>Root:</b> {d['root']}<br><b>Boot:</b> {d['boot']}<br><b>Swap:</b> {d['swap']}"
        self.txt_sum.setHtml(html)

    def start_install(self):
        self.pages.setCurrentIndex(6)
        self.step_list.setCurrentRow(6)
        self.update_nav()
        cmd = self.get_cmd_list()
        if self.dry_run:
            self.txt_log.append("--- DRY RUN MODE ---")
            self.txt_log.append(f"Command:\n{' '.join(cmd)}")
            self.pbar.setValue(100)
            self.lbl_progress.setText("Dry Run Complete")
            return
        self.process = QProcess()
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.install_finished)
        self.process.start(cmd[0], cmd[1:])
        self.pbar.setRange(0, 0)

    def read_output(self):
        data = self.process.readAllStandardOutput().data().decode()
        self.txt_log.moveCursor(QTextCursor.End)
        self.txt_log.insertPlainText(data)
        self.txt_log.moveCursor(QTextCursor.End)
        lower = data.lower()
        if "partitioning" in lower: self.lbl_progress.setText("Partitioning Disk...")
        if "installing base" in lower: self.lbl_progress.setText("Installing Base System...")
        if "configuring" in lower: self.lbl_progress.setText("Configuring System...")
        if "bootloader" in lower: self.lbl_progress.setText("Installing Bootloader...")

    def install_finished(self):
        if self.process.exitCode() == 0:
            self.pbar.setRange(0, 100)
            self.pbar.setValue(100)
            self.lbl_progress.setText("Installation Successful!")
            QMessageBox.information(self, "Done", "Installation finished successfully.")
        else:
            self.pbar.setRange(0, 100)
            self.pbar.setValue(0)
            self.lbl_progress.setText("Installation Failed")
            QMessageBox.critical(self, "Error", "Installation failed. Check the log.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = InstallerWindow()
    win.show()
    sys.exit(app.exec())
