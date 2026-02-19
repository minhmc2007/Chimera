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
from PySide6.QtGui import QPixmap, QIcon, QPalette, QColor, QFont, QPainter, QBrush

# --- Configuration & Constants ---
ASSET_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(ASSET_DIR, "logo.png")
BG_PATH = os.path.join(ASSET_DIR, "backg.png")
BACKEND_SCRIPT = os.path.join(ASSET_DIR, "main.py")

# Colors
COL_SKY_BLUE = "#87CEEB"
COL_DEEP_SKY = "#00BFFF"

# Dark Theme Palette
D_BG_MAIN = "#31363b"      # Main Window Background
D_BG_SIDE = "#232629"      # Sidebar Background
D_TEXT = "#eff0f1"         # Text Color
D_INPUT_BG = "#232629"     # Input Field Background
D_INPUT_BORDER = "#565a5e" # Input Border
D_HIGHLIGHT = "#3daee9"    # Selection Highlight

# Light Theme Palette
L_BG_MAIN = "#eff0f1"
L_BG_SIDE = "#e3e5e7"
L_TEXT = "#232629"
L_INPUT_BG = "#ffffff"
L_INPUT_BORDER = "#bckecc"
L_HIGHLIGHT = "#3daee9"

# --- Utility Functions ---
def get_system_theme():
    """Attempt to detect system theme (Dark/Light). Defaults to Dark."""
    try:
        # Check KDE
        kconfig = subprocess.check_output(["kreadconfig6", "--group", "KDE", "--key", "LookAndFeelPackage"], stderr=subprocess.DEVNULL).decode().strip()
        if "Dark" in kconfig or "dark" in kconfig: return "dark"
        if "Light" in kconfig or "light" in kconfig: return "light"
    except:
        pass

    try:
        # Check GNOME/GTK
        gsettings = subprocess.check_output(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"], stderr=subprocess.DEVNULL).decode().strip()
        if "dark" in gsettings: return "dark"
    except:
        pass

    return "dark" # Default safety

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

        # Dry Run Toggle
        self.chk_dry_run = QCheckBox("Enable Dry Run (Do not write to disk)")
        self.chk_dry_run.setChecked(dry_run)
        layout.addWidget(self.chk_dry_run)

        # Theme Toggle
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

        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; color: {fg}; }}
            QLabel, QCheckBox {{ color: {fg}; font-size: 13px; }}
            QTextEdit {{ background-color: {txt_bg}; color: {fg}; border: 1px solid #555; }}
            QPushButton {{ padding: 8px; }}
        """)

# --- Main Window ---
class InstallerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chimera Installer")
        self.resize(1000, 700)

        # State
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
        # Main Container
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

        # Logo
        lbl_logo = QLabel()
        lbl_logo.setAlignment(Qt.AlignCenter)
        lbl_logo.setFixedHeight(140)
        if os.path.exists(LOGO_PATH):
            pix = QPixmap(LOGO_PATH).scaled(110, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            lbl_logo.setPixmap(pix)
        else:
            lbl_logo.setText("CHIMERA")
            lbl_logo.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        side_layout.addWidget(lbl_logo)

        # Steps List
        self.step_list = QListWidget()
        self.step_list.setFocusPolicy(Qt.NoFocus)
        steps = ["Welcome", "Location", "Disk Setup", "Partitions", "Users", "Summary", "Install"]
        for s in steps:
            self.step_list.addItem(StepItem(s))
        self.step_list.setCurrentRow(0)
        side_layout.addWidget(self.step_list)

        # Settings Icon
        self.btn_debug = QToolButton()
        self.btn_debug.setText("⚙")
        self.btn_debug.setToolTip("Settings / Debug")
        self.btn_debug.setCursor(Qt.PointingHandCursor)
        self.btn_debug.clicked.connect(self.open_debug_settings)
        # Ensure it has size
        self.btn_debug.setFixedSize(40, 40)

        # Bottom left container
        bot_layout = QHBoxLayout()
        bot_layout.setContentsMargins(15, 0, 0, 0)
        bot_layout.addWidget(self.btn_debug)
        bot_layout.addStretch()

        side_layout.addLayout(bot_layout)
        main_layout.addWidget(self.sidebar)

        # --- Content Area ---
        self.content_container = QWidget()
        content_layout = QVBoxLayout(self.content_container)
        content_layout.setContentsMargins(40, 40, 40, 40)

        # Header Title
        self.lbl_header = QLabel("Welcome to Chimera")
        self.lbl_header.setFont(QFont("Segoe UI", 24, QFont.Bold))
        self.lbl_header.setStyleSheet(f"color: {COL_SKY_BLUE}; margin-bottom: 20px;")
        content_layout.addWidget(self.lbl_header)

        # Stacked Pages
        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages)

        # Navigation Bar
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

        # --- Init Pages ---
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
            lbl_hero.setText("Blue Archive Linux\nInstaller")
            lbl_hero.setStyleSheet("font-size: 30px; font-weight: bold; color: #555; border: 2px dashed #555; padding: 60px;")

        lbl_text = QLabel("This wizard will guide you through the installation of Blue Archive Linux.\n\nPlease ensure you are connected to the internet.")
        lbl_text.setAlignment(Qt.AlignCenter)
        lbl_text.setWordWrap(True)
        lbl_text.setStyleSheet("font-size: 15px; margin-top: 30px;")

        vbox.addStretch()
        vbox.addWidget(lbl_hero)
        vbox.addWidget(lbl_text)
        vbox.addStretch()
        self.pages.addWidget(p_welcome)

        # 1. Location
        p_loc = QWidget()
        vbox = QVBoxLayout(p_loc)
        vbox.addWidget(QLabel("Select your Timezone:"))
        self.cmb_tz = QComboBox()
        self.cmb_tz.addItems(["UTC", "Asia/Ho_Chi_Minh", "Asia/Tokyo", "America/New_York", "Europe/London", "Europe/Berlin"])
        self.cmb_tz.setEditable(True)
        vbox.addWidget(self.cmb_tz)
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

        # 3. Partitions (Manual)
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

    def toggle_swap_input(self):
        self.wid_swap.setVisible(self.rad_erase.isChecked())

    def apply_stylesheet(self):
        is_dark = self.theme_mode == "dark"

        # Color Assignment
        bg_main = D_BG_MAIN if is_dark else L_BG_MAIN
        bg_side = D_BG_SIDE if is_dark else L_BG_SIDE
        text = D_TEXT if is_dark else L_TEXT

        input_bg = D_INPUT_BG if is_dark else L_INPUT_BG
        input_border = D_INPUT_BORDER if is_dark else L_INPUT_BORDER
        highlight = D_HIGHLIGHT if is_dark else L_HIGHLIGHT

        css = f"""
        QMainWindow, QWidget {{ background-color: {bg_main}; color: {text}; }}

        /* Sidebar Styling */
        QFrame {{ border: none; }}
        QListWidget {{ background-color: {bg_side}; outline: 0; }}
        QListWidget::item {{ color: #888; padding: 15px; border-left: 4px solid transparent; }}
        QListWidget::item:selected {{ color: {text}; background: {bg_main}; border-left: 4px solid {COL_SKY_BLUE}; }}

        /* Text & Labels */
        QLabel, QCheckBox, QRadioButton {{ color: {text}; font-size: 14px; background: transparent; }}

        /* Input Fields - The Fix for White Boxes */
        QLineEdit, QComboBox, QSpinBox {{
            background-color: {input_bg};
            color: {text};
            border: 1px solid {input_border};
            padding: 8px;
            border-radius: 4px;
            font-size: 13px;
        }}
        QLineEdit:focus, QComboBox:focus {{ border: 1px solid {COL_SKY_BLUE}; }}

        /* ComboBox Dropdown Fix */
        QComboBox::drop-down {{
            border: none; width: 20px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {input_bg};
            color: {text};
            selection-background-color: {COL_SKY_BLUE};
            selection-color: black;
            border: 1px solid {input_border};
        }}

        /* Groups */
        QGroupBox {{
            border: 1px solid {input_border};
            margin-top: 20px;
            font-weight: bold;
            border-radius: 5px;
            background: transparent;
        }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {COL_SKY_BLUE}; }}

        /* Buttons */
        QPushButton {{
            background-color: {input_bg}; color: {text};
            border: 1px solid {input_border}; border-radius: 4px; padding: 6px;
        }}
        QPushButton:hover {{ background-color: {highlight}; color: white; border: 1px solid {highlight}; }}

        /* Settings Icon */
        QToolButton {{ color: {text}; border: none; background: transparent; font-size: 20px; }}
        QToolButton:hover {{ color: {COL_SKY_BLUE}; }}

        /* Scrollbars */
        QScrollBar:vertical {{ background: {bg_main}; width: 10px; }}
        QScrollBar::handle:vertical {{ background: #555; border-radius: 5px; }}
        """

        # Primary Button Override
        css += f"""
        QPushButton[primary="true"] {{
            background-color: {COL_SKY_BLUE}; color: #000; font-weight: bold; border: none;
        }}
        QPushButton[primary="true"]:hover {{ background-color: {COL_DEEP_SKY}; }}
        """

        self.setStyleSheet(css)
        self.sidebar.setStyleSheet(f"background-color: {bg_side};")

    # --- Logic ---
    def refresh_disks(self):
        self.cmb_disk.clear()
        try:
            cmd = ["lsblk", "-d", "-n", "-o", "NAME,SIZE,MODEL,TYPE", "-J"]
            out = subprocess.check_output(cmd).decode()
            data = json.loads(out)

            valid_found = False
            for d in data.get('blockdevices', []):
                # FILTER: Skip loop, zram, and rom (cd)
                if d['type'] in ['loop', 'rom'] or d['name'].startswith('zram'):
                    continue

                model = d.get('model', 'Unknown Drive')
                if model is None: model = "Unknown Drive"

                text = f"{model} ({d['size']}) - /dev/{d['name']}"
                self.cmb_disk.addItem(text, f"/dev/{d['name']}")
                valid_found = True

            if not valid_found:
                self.cmb_disk.addItem("No valid disks found", None)

        except Exception as e:
            self.cmb_disk.addItem(f"Error detection: {str(e)}", None)

    def launch_cfdisk(self):
        disk = self.cmb_disk.currentData()
        if not disk:
            QMessageBox.warning(self, "No Disk", "Please select a valid disk first.")
            return

        terms = ["konsole", "xterm", "gnome-terminal", "alacritty", "kitty", "xfce4-terminal"]
        term_cmd = None

        for t in terms:
            if shutil.which(t):
                if t == "konsole":
                    term_cmd = [t, "--hide-menubar", "-e", "cfdisk", disk]
                elif t == "gnome-terminal":
                    term_cmd = [t, "--", "cfdisk", disk]
                else:
                    term_cmd = [t, "-e", "cfdisk", disk]
                break

        if term_cmd:
            subprocess.run(term_cmd)
            self.populate_partitions()
        else:
            QMessageBox.critical(self, "Error", "No terminal found (konsole/xterm). Cannot launch cfdisk.")

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
                if dev['type'] == 'part':
                    # Only show partitions of selected disk
                    if dev.get('pkname') == disk_name:
                        fstype = dev.get('fstype')
                        if fstype is None: fstype = "Unformatted"

                        txt = f"/dev/{dev['name']} ({dev['size']}) - {fstype}"
                        val = f"/dev/{dev['name']}"

                        self.cmb_root.addItem(txt, val)
                        self.cmb_boot.addItem(txt, val)
                        self.cmb_swap.addItem(txt, val)
        except Exception as e:
            print(f"Part error: {e}")

    def get_cmd_list(self):
        cmd = ["python3", "-u", BACKEND_SCRIPT]
        d = self.install_data

        # --- FIX: Handle NoneType for Debug View ---
        if d['method'] == 'whole':
            disk_val = d['disk'] if d['disk'] else "[NO_DISK]"
            cmd.extend(["--disk", disk_val])
            if d.get('swap_size', 0) > 0:
                cmd.extend(["--swap", f"{d['swap_size']}G"])
        else:
            root_val = d['root'] if d['root'] else "[NO_ROOT]"
            boot_val = d['boot'] if d['boot'] else "[NO_BOOT]"

            cmd.extend(["--rootfs", root_val])
            cmd.extend(["--boot", boot_val])
            if d['swap']:
                cmd.extend(["--swap", d['swap']])

        cmd.extend(["--user", d['user']])
        cmd.extend(["--passwd", d['pass']])
        cmd.extend(["--timezone", d['tz']])
        cmd.extend(["--target", "arch"])
        cmd.append("--debug")

        return cmd

    def open_debug_settings(self):
        # Update install data from current form state just for the debug view
        try:
            if self.pages.currentIndex() == 2:
                self.install_data['disk'] = self.cmb_disk.currentData()
        except: pass

        cmd_str = " ".join(self.get_cmd_list())
        dlg = DebugDialog(self, cmd_str, self.dry_run, self.theme_mode=="dark")
        if dlg.exec():
            self.dry_run = dlg.chk_dry_run.isChecked()
            # Theme Switch Logic
            new_theme = "light" if dlg.chk_force_light.isChecked() else "dark"
            if new_theme != self.theme_mode:
                self.theme_mode = new_theme
                self.apply_stylesheet()

    def go_next(self):
        idx = self.pages.currentIndex()

        # 1. Location
        if idx == 1:
            self.install_data['tz'] = self.cmb_tz.currentText()

        # 2. Disk Selection
        if idx == 2:
            self.install_data['disk'] = self.cmb_disk.currentData()
            if not self.install_data['disk']:
                QMessageBox.warning(self, "Error", "Please select a disk.")
                return

            self.install_data['method'] = "whole" if self.rad_erase.isChecked() else "manual"
            self.install_data['swap_size'] = self.spin_swap.value()

            if self.install_data['method'] == "whole":
                self.pages.setCurrentIndex(4) # Skip to Users
                self.step_list.setCurrentRow(4)
                self.update_nav()
                return
            else:
                self.populate_partitions() # Prep Manual page

        # 3. Manual Partitioning
        if idx == 3:
            r = self.cmb_root.currentData()
            b = self.cmb_boot.currentData()
            if not r or not b:
                QMessageBox.warning(self, "Error", "Root and Boot partitions are required.")
                return
            if r == b:
                QMessageBox.warning(self, "Error", "Root and Boot cannot be the same.")
                return
            self.install_data['root'] = r
            self.install_data['boot'] = b
            self.install_data['swap'] = self.cmb_swap.currentData()

        # 4. Users
        if idx == 4:
            u = self.inp_user.text()
            p = self.inp_pass.text()
            if not u or not p:
                QMessageBox.warning(self, "Error", "User and Password required.")
                return
            self.install_data['user'] = u
            self.install_data['pass'] = p
            self.install_data['host'] = self.inp_host.text()
            self.generate_summary()

        # 5. Summary -> Install
        if idx == 5:
            if self.dry_run:
                QMessageBox.information(self, "Dry Run", "Dry Run Enabled.\nSee Debug settings for command.")
            else:
                confirm = QMessageBox.question(self, "Confirm Install", "WARNING: Disk changes are permanent.\nProceed?", QMessageBox.Yes|QMessageBox.No)
                if confirm != QMessageBox.Yes: return

            self.start_install()
            return

        if idx < self.pages.count() - 1:
            self.pages.setCurrentIndex(idx + 1)
            self.step_list.setCurrentRow(idx + 1)
        self.update_nav()

    def go_back(self):
        idx = self.pages.currentIndex()
        if idx == 4 and self.install_data['method'] == 'whole':
            self.pages.setCurrentIndex(2) # Skip manual back
            self.step_list.setCurrentRow(2)
        elif idx > 0:
            self.pages.setCurrentIndex(idx - 1)
            self.step_list.setCurrentRow(idx - 1)
        self.update_nav()

    def update_nav(self):
        idx = self.pages.currentIndex()

        # Safe Sidebar Update
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
        <b>Hostname:</b> {d['host']}<br>
        <b>Timezone:</b> {d['tz']}<br>
        <b>User:</b> {d['user']}<br>

        <h3 style="color:{COL_SKY_BLUE}">Storage Configuration</h3>
        """
        if d['method'] == 'whole':
            html += f"<b>Mode:</b> Erase Whole Disk<br><b>Target:</b> {d['disk']}<br><b>Swap:</b> {d['swap_size']} GB"
        else:
            html += f"<b>Mode:</b> Manual Partitioning<br><b>Root:</b> {d['root']}<br><b>Boot:</b> {d['boot']}<br><b>Swap:</b> {d['swap']}"

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
        self.txt_log.moveCursor(self.txt_log.textCursor().End)
        self.txt_log.insertPlainText(data)

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
