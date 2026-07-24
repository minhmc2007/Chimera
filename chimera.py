#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
import shutil
import socket
import shlex
import glob
import re
import fcntl
import getpass

# --- Configuration & Constants ---
COLORS = {
    'HEADER': '\033[95m', 'BLUE': '\033[94m', 'GREEN': '\033[92m',
    'WARN': '\033[93m', 'FAIL': '\033[91m', 'ENDC': '\033[0m', 'BOLD': '\033[1m'
}
MOUNT_POINT = "/mnt/chimera_target"

# --- Utility Functions ---
def log(msg, level="info"):
    icon = "[*]"
    color = COLORS['BLUE']
    if level == "error": icon, color = "[!]", COLORS['FAIL']
    elif level == "success": icon, color = "[+]", COLORS['GREEN']
    elif level == "warn": icon, color = "[?]", COLORS['WARN']
    elif level == "HEADER": icon, color = "[#]", COLORS['HEADER']
    elif level == "DEBUG": icon, color = "[D]", COLORS['WARN']

    print(f"{color}{icon} {msg}{COLORS['ENDC']}", flush=True)

def set_progress(percent, status_msg):
    print(f"[PROGRESS:{percent}] {status_msg}", flush=True)

def check_connection():
    for host, port in [("8.8.8.8", 53), ("1.1.1.1", 53), ("google.com", 443)]:
        try:
            socket.create_connection((host, port), timeout=3)
            return True
        except OSError:
            continue
    return False

# --- Main Installer Class ---
class ChimeraInstaller:
    def __init__(self, args):
        self.args = args
        self.debug = args.debug
        self.uefi = os.path.exists("/sys/firmware/efi")
        self.target_os = args.target.lower()
        self.disk = args.disk if args.disk else self._detect_disk(args.rootfs)

        self.root_pass = os.environ.get("CHIMERA_ROOT_PASS", "")
        self.user_pass = os.environ.get("CHIMERA_USER_PASS", "")

        if self.args.timezone:
            tz_path = os.path.abspath(f"/usr/share/zoneinfo/{self.args.timezone}")
            if not tz_path.startswith("/usr/share/zoneinfo/"):
                sys.exit(f"{COLORS['FAIL']}Error: Invalid timezone path.{COLORS['ENDC']}")

        if self.args.swap:
            if self.args.disk:
                if not re.match(r"^\d+[MGmg]$", self.args.swap):
                    sys.exit(f"{COLORS['FAIL']}Error: --swap must be a size (e.g. 2G, 512M) in auto-partition mode.{COLORS['ENDC']}")
            else:
                if not os.path.exists(self.args.swap):
                    sys.exit(f"{COLORS['FAIL']}Error: Swap partition {self.args.swap} not found.{COLORS['ENDC']}")

        if not self.disk and not self.uefi:
            sys.exit(f"{COLORS['FAIL']}Error: Could not detect root disk for BIOS bootloader. Please provide --disk.{COLORS['ENDC']}")

    def run_cmd(self, cmd, shell=False, check=True, chroot=False, env=None, stream=False, input_data=None):
        show_output = stream or self.debug

        if chroot:
            if isinstance(cmd, list):
                cmd_str = " ".join(shlex.quote(arg) for arg in cmd)
            else:
                cmd_str = cmd

            if shutil.which("arch-chroot"):
                cmd = ["arch-chroot", MOUNT_POINT, "/bin/sh", "-c", cmd_str]
            else:
                cmd = ["chroot", MOUNT_POINT, "/bin/sh", "-c", cmd_str]
            shell = False

        if self.debug:
            log(f"CMD: {cmd}", "DEBUG")

        kwargs = {'shell': shell, 'env': env}

        if input_data is not None:
            kwargs['input'] = input_data.encode('utf-8')
        else:
            kwargs['stdin'] = subprocess.DEVNULL

        if not show_output:
            kwargs['stdout'] = subprocess.PIPE
            kwargs['stderr'] = subprocess.PIPE

        proc = subprocess.run(cmd, **kwargs)

        if proc.returncode != 0:
            if check:
                log(f"Command Failed: {cmd}", "error")
                raise subprocess.CalledProcessError(proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr)
            return False
        return True

    def _detect_disk(self, partition):
        try:
            if not partition: return None
            parent = subprocess.check_output(["lsblk", "-no", "pkname", partition], stderr=subprocess.PIPE).decode().strip()
            if not parent: return None
            return f"/dev/{parent}"
        except subprocess.CalledProcessError:
            return None

    def tools_check(self):
        tools = ["parted", "wipefs", "mkfs.ext4", "rsync", "lsblk", "mount", "umount", "findmnt", "partprobe", "udevadm"]
        if self.uefi:
            tools.append("mkfs.vfat")
        if self.args.swap:
            tools.extend(["mkswap", "swapon", "swapoff"])

        missing = [t for t in tools if not shutil.which(t)]
        if missing:
            sys.exit(f"{COLORS['FAIL']}Error: Missing required tools: {', '.join(missing)}{COLORS['ENDC']}")

    def run(self):
        try:
            if self.args.dry_run:
                log("DRY RUN MODE ACTIVE", "info")
                set_progress(100, "Dry Run Complete")
                sys.exit(0)

            self.welcome()
            self.tools_check()
            self.ensure_network_logic()
            self.partition_handler()
            self.install_base()
            if not shutil.which("arch-chroot"): self.setup_chroot_mounts()
            self.configure_system()
            self.setup_users()
            self.install_bootloader()
            self.cleanup()
            set_progress(100, "Installation Successful!")
            log("Installation Successfully Completed.", "success")
        except Exception as e:
            log(f"Critical Failure: {e}", "error")
            sys.exit(1)

    def welcome(self):
        title_name = "Blue Archive Linux" if self.target_os == "bal" else self.target_os.capitalize()
        log(f"Chimera Installer - {title_name} Edition", "HEADER")
        log(f"Target Mode: {self.args.disk_mode.upper()} | Boot Mode: {'UEFI' if self.uefi else 'BIOS'}", "info")
        log(f"Kernel: {self.args.kernel} | Zram: {self.args.zram} ({self.args.zram_comp})", "info")
        log(f"Hostname: {self.args.hostname} | Timezone: {self.args.timezone}", "info")

    def ensure_network_logic(self):
        if self.args.online:
            if not check_connection():
                raise RuntimeError("No Internet Connection. Required for Online mode.")

    def partition_handler(self):
        set_progress(20, "Partitioning Disk...")
        log("Preparing Partitions...", "info")

        if os.path.exists(MOUNT_POINT) and os.path.ismount(MOUNT_POINT):
            self.run_cmd(["umount", "-R", MOUNT_POINT], check=False)

        if self.args.disk_mode == "auto":
            try:
                swaps = subprocess.check_output(["lsblk", "-nlo", "NAME,FSTYPE", self.args.disk], stderr=subprocess.DEVNULL).decode().splitlines()
                for line in swaps:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == "swap":
                        self.run_cmd(["swapoff", f"/dev/{parts[0].strip()}"], check=False)
            except Exception: pass
            self._auto_partition_disk()
        else:
            if self.args.swap:
                self.run_cmd(["swapoff", self.args.swap], check=False)

        if not self.args.rootfs:
            sys.exit(f"{COLORS['FAIL']}Error: Root partition (--rootfs) is required in manual mode.{COLORS['ENDC']}")

        self.run_cmd(["mkfs.ext4", "-F", self.args.rootfs])
        os.makedirs(MOUNT_POINT, exist_ok=True)
        self.run_cmd(["mount", self.args.rootfs, MOUNT_POINT])

        if self.args.boot:
            path = f"{MOUNT_POINT}/boot/efi" if self.uefi else f"{MOUNT_POINT}/boot"
            os.makedirs(path, exist_ok=True)
            if self.uefi: self.run_cmd(["mkfs.vfat", "-F32", self.args.boot])
            else: self.run_cmd(["mkfs.ext4", "-F", self.args.boot])
            self.run_cmd(["mount", self.args.boot, path])

        if self.args.swap:
            self.run_cmd(["mkswap", self.args.swap])

    def _auto_partition_disk(self):
        log(f"Wiping and partitioning {self.args.disk}...", "warn")
        label_type = "gpt" if self.uefi else "msdos"
        self.run_cmd(["wipefs", "--all", self.disk])
        self.run_cmd(["parted", "-s", self.disk, "mklabel", label_type])

        boot_part_end = "513MiB"
        self.run_cmd(["parted", "-s", self.disk, "mkpart", "primary", "1MiB", boot_part_end])
        current_end = boot_part_end

        if self.args.swap:
            size = self.args.swap.upper()
            mult = 1024 if "G" in size else 1
            mb_size = int(''.join(filter(str.isdigit, size))) * mult
            swap_end = f"{513 + mb_size}MiB"
            self.run_cmd(["parted", "-s", self.disk, "mkpart", "primary", current_end, swap_end])
            current_end = swap_end

        self.run_cmd(["parted", "-s", self.disk, "mkpart", "primary", current_end, "100%"])

        prefix = f"{self.disk}p" if any(self.disk.startswith(p) for p in ["/dev/nvme", "/dev/mmc", "/dev/loop"]) else self.disk

        self.args.boot = f"{prefix}1"
        if self.args.swap:
            self.args.swap = f"{prefix}2"
            self.args.rootfs = f"{prefix}3"
        else:
            self.args.rootfs = f"{prefix}2"

        if self.uefi: self.run_cmd(["parted", "-s", self.disk, "set", "1", "esp", "on"])
        else: self.run_cmd(["parted", "-s", self.disk, "set", "1", "boot", "on"])

        self.run_cmd(["partprobe", self.disk])
        self.run_cmd(["udevadm", "settle"])

    def install_base(self):
        set_progress(50, "Installing Base System...")
        log(f"Copying Base System (Preserving custom distro files)...", "info")
        excludes = [
            "--exclude=/proc/*", "--exclude=/sys/*", "--exclude=/dev/*",
            "--exclude=/run/*", "--exclude=/tmp/*", "--exclude=/mnt/*",
            f"--exclude={MOUNT_POINT}/*"
        ]

        if self.args.online:
            excludes.append("--exclude=/var/cache/pacman/pkg/*")

        self.run_cmd(["rsync", "-axAWS", "--numeric-ids"] + excludes + ["/", MOUNT_POINT])

        if shutil.which("genfstab"):
            with open(f"{MOUNT_POINT}/etc/fstab", "w") as f:
                subprocess.run(["genfstab", "-U", MOUNT_POINT], stdout=f, check=True)

    def setup_chroot_mounts(self):
        log("Mounting API filesystems...", "info")
        for m in ["dev", "proc", "sys"]:
            target = os.path.join(MOUNT_POINT, m)
            os.makedirs(target, exist_ok=True)
            self.run_cmd(["mount", "--rbind", f"/{m}", target], check=False)
            self.run_cmd(["mount", "--make-rslave", target], check=False)

        resolv_path = f"{MOUNT_POINT}/etc/resolv.conf"
        if os.path.lexists(resolv_path):
            os.remove(resolv_path)
        with open(resolv_path, "w") as f:
            f.write("nameserver 1.1.1.1\nnameserver 8.8.8.8\n")

    def configure_system(self):
        set_progress(75, "Configuring System...")
        log("Configuring System Locale & Time...", "info")

        with open(f"{MOUNT_POINT}/etc/hostname", "w") as f:
            f.write(f"{self.args.hostname}\n")

        if self.args.timezone:
            tz_path = os.path.abspath(f"/usr/share/zoneinfo/{self.args.timezone}")
            if os.path.exists(f"{MOUNT_POINT}{tz_path}"):
                self.run_cmd(["ln", "-sf", tz_path, "/etc/localtime"], chroot=True)
                self.run_cmd(["hwclock", "--systohc"], chroot=True, check=False)

        locale_str = f"{self.args.locale_lang} {self.args.locale_enc}"
        locale_file = f"{MOUNT_POINT}/etc/locale.gen"
        found_locale = False
        if os.path.exists(locale_file):
            with open(locale_file, "r") as f:
                lines = f.readlines()
            with open(locale_file, "w") as f:
                for line in lines:
                    if line.strip().lstrip("#").strip() == locale_str:
                        f.write(f"{locale_str}\n")
                        found_locale = True
                    else:
                        f.write(line)
            if not found_locale:
                with open(locale_file, "a") as f:
                    f.write(f"{locale_str}\n")

        self.run_cmd(["locale-gen"], chroot=True, check=False)

        with open(f"{MOUNT_POINT}/etc/locale.conf", "w") as f:
            f.write(f"LANG={self.args.locale_lang}\n")

        with open(f"{MOUNT_POINT}/etc/vconsole.conf", "w") as f:
            f.write(f"KEYMAP={self.args.keyboard}\nFONT={self.args.console_font}\n")

        if self.args.mirror_region and shutil.which("reflector", path=f"{MOUNT_POINT}/usr/bin"):
            log(f"Setting Pacman Mirror Region to {self.args.mirror_region}...", "info")
            self.run_cmd(["reflector", "--country", self.args.mirror_region, "--save", "/etc/pacman.d/mirrorlist", "--protocol", "https", "--latest", "5", "--download-timeout", "5"], chroot=True, check=False)

        self.run_cmd(["systemctl", "enable", "NetworkManager"], chroot=True, check=False)

        # Packages Setup
        pkgs = []
        if self.args.kernel and self.args.kernel != "linux":
            pkgs.extend([self.args.kernel, f"{self.args.kernel}-headers"])
        if self.args.zram: pkgs.append("zram-generator")

        if self.args.audio == "pipewire":
            # [FIX] Force removal of conflicting jack2 before pipewire-jack installation
            self.run_cmd(["pacman", "-Rdd", "--noconfirm", "jack2"], chroot=True, check=False)
            pkgs.extend(["pipewire", "pipewire-pulse", "pipewire-alsa", "pipewire-jack", "wireplumber"])
        elif self.args.audio == "pulseaudio":
            pkgs.extend(["pulseaudio", "pulseaudio-alsa"])

        if self.args.bluetooth: pkgs.extend(["bluez", "bluez-utils"])

        if self.args.online:
            log("Online Mode: Initializing keyring and upgrading system...", "info")
            self.run_cmd(["pacman-key", "--init"], chroot=True, check=False)
            self.run_cmd(["pacman-key", "--populate"], chroot=True, check=False)
            self.run_cmd(["pacman", "-Syuu", "--noconfirm"], chroot=True)

            if pkgs:
                self.run_cmd(["pacman", "-S", "--noconfirm", "--needed"] + pkgs, chroot=True)
        else:
            log("Offline Mode: Installing packages from local cache...", "info")
            if pkgs:
                self.run_cmd(["pacman", "-S", "--noconfirm", "--needed"] + pkgs, chroot=True, check=False)

        # Services
        if self.args.zram:
            zram_conf = f"{MOUNT_POINT}/etc/systemd/zram-generator.conf"
            with open(zram_conf, "w") as f:
                f.write(f"[zram0]\ncompression-algorithm = {self.args.zram_comp}\nswap-priority = 100\nfs-type = swap\n")
            self.run_cmd(["systemctl", "daemon-reload"], chroot=True, check=False)

        if self.args.bluetooth:
            self.run_cmd(["systemctl", "enable", "bluetooth"], chroot=True, check=False)

        # --- Target Specific Configuration ---
        if self.target_os == "bal":
            log("Setting up Blue Archive Linux (BAL) specifics...", "info")
            sddm_script = "/root/SilentSDDM/install.sh"
            chroot_sddm_script = f"{MOUNT_POINT}{sddm_script}"

            if os.path.exists(chroot_sddm_script):
                log("Running SilentSDDM installer script...", "info")
                os.chmod(chroot_sddm_script, 0o755)
                self.run_cmd([sddm_script], chroot=True, check=False)
            else:
                log(f"Notice: {sddm_script} not found on target filesystem.", "warn")

            log("Enabling SDDM display manager...", "info")
            self.run_cmd(["systemctl", "enable", "sddm"], chroot=True, check=False)

    def setup_users(self):
        log("Configuring Users...", "info")
        if self.root_pass:
            self.run_cmd(["chpasswd"], chroot=True, input_data=f"root:{self.root_pass}\n")

        if self.args.user:
            self.run_cmd(["useradd", "-m", "-G", "wheel", "-s", "/bin/bash", self.args.user], chroot=True)
            if self.user_pass:
                self.run_cmd(["chpasswd"], chroot=True, input_data=f"{self.args.user}:{self.user_pass}\n")

            sudoers_file = f"{MOUNT_POINT}/etc/sudoers.d/99_installer"
            os.makedirs(f"{MOUNT_POINT}/etc/sudoers.d", exist_ok=True)
            with open(sudoers_file, 'w') as f:
                f.write("%wheel ALL=(ALL:ALL) ALL\n")
            os.chmod(sudoers_file, 0o440)

    def install_bootloader(self):
        set_progress(90, "Installing Bootloader...")
        log("Installing GRUB Bootloader...", "info")
        grub_path = f"{MOUNT_POINT}/etc/default/grub"
        if os.path.exists(grub_path):
            with open(grub_path, 'r') as f: lines = f.readlines()
            with open(grub_path, 'w') as f:
                for line in lines:
                    if line.strip().startswith("GRUB_CMDLINE_LINUX_DEFAULT="):
                        f.write(line.replace("quiet", "").replace("  ", " "))
                    else:
                        f.write(line)

        bootloader_id = "BlueArchiveLinux" if self.target_os == "bal" else self.args.target.capitalize()
        cmd = ["grub-install", f"--target={'x86_64-efi' if self.uefi else 'i386-pc'}", f"--bootloader-id={bootloader_id}", "--recheck"]
        if self.uefi: cmd.append("--efi-directory=/boot/efi")
        else: cmd.append(self.disk)

        self.run_cmd(cmd, chroot=True)
        self.run_cmd(["grub-mkconfig", "-o", "/boot/grub/grub.cfg"], chroot=True)

    def cleanup(self):
        self.run_cmd(["umount", "-R", MOUNT_POINT], check=False)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--disk-mode", choices=["auto", "manual"], default="auto")
    parser.add_argument("--disk", help="Target disk for auto mode (e.g., /dev/sda)")
    parser.add_argument("--boot", help="Manual: Boot partition")
    parser.add_argument("--rootfs", help="Manual: Root partition")
    parser.add_argument("--swap", help="Swap partition or size")

    parser.add_argument("--keyboard", default="us")
    parser.add_argument("--locale-lang", default="en_US.UTF-8")
    parser.add_argument("--locale-enc", default="UTF-8")
    parser.add_argument("--console-font", default="default8x16")
    parser.add_argument("--mirror-region", default="Worldwide")

    parser.add_argument("--kernel", default="linux")
    parser.add_argument("--zram", action="store_true")
    parser.add_argument("--zram-comp", default="lz4")
    parser.add_argument("--audio", choices=["pipewire", "pulseaudio", "none"], default="pipewire")
    parser.add_argument("--bluetooth", action="store_true")
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--hostname", default=socket.gethostname())

    parser.add_argument("--user", help="Username to create")
    parser.add_argument("--target", choices=["arch", "bal"], default="arch")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--i-am-very-stupid", action="store_true")

    args = parser.parse_args()
    if os.geteuid() != 0: sys.exit("Run as root.")

    lock_file = "/var/lock/chimera_installer.lock"
    try:
        lock_fd = open(lock_file, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f"{COLORS['FAIL']}Error: Another instance is running.{COLORS['ENDC']}")

    ChimeraInstaller(args).run()

if __name__ == "__main__":
    main()
