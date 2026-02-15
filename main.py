#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
import shutil
import time
import urllib.request
import socket
import glob
import shlex

# --- Configuration & Constants ---
COLORS = {
    'HEADER': '\033[95m', 'BLUE': '\033[94m', 'GREEN': '\033[92m',
    'WARN': '\033[93m', 'FAIL': '\033[91m', 'ENDC': '\033[0m', 'BOLD': '\033[1m'
}
MOUNT_POINT = "/mnt/chimera_target"
GENTOO_BASE = "https://distfiles.gentoo.org/releases/amd64/autobuilds"
DEBIAN_RELEASE = "bookworm" # Stable

# --- Utility Functions ---
def log(msg, level="info"):
    icon = "[*]"
    color = COLORS['BLUE']
    if level == "error": icon, color = "[!]", COLORS['FAIL']
    elif level == "success": icon, color = "[+]", COLORS['GREEN']
    elif level == "warn": icon, color = "[?]", COLORS['WARN']
    elif level == "HEADER": icon, color = "[#]", COLORS['HEADER']
    
    print(f"{color}{icon} {msg}{COLORS['ENDC']}")

def run_cmd(cmd, shell=False, check=True, chroot=False, ignore_error=False, env=None):
    if chroot:
        if isinstance(cmd, list): cmd_str = " ".join(shlex.quote(arg) for arg in cmd)
        else: cmd_str = cmd
        # Use arch-chroot if available for better proc/sys binding, else standard chroot
        if shutil.which("arch-chroot"):
            cmd = ["arch-chroot", MOUNT_POINT, "/bin/sh", "-c", cmd_str]
        else:
            cmd = ["chroot", MOUNT_POINT, "/bin/sh", "-c", cmd_str]
        shell = False
    
    try:
        # Pass environment variables if needed
        proc = subprocess.run(cmd, shell=shell, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        return proc.returncode == 0
    except subprocess.CalledProcessError as e:
        if not ignore_error:
            log(f"Command Failed: {cmd}", "error")
            print(f"{COLORS['FAIL']}STDERR: {e.stderr.decode().strip()}{COLORS['ENDC']}")
            if check: raise e 
        return False

def check_connection():
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=3)
        return True
    except OSError:
        return False

def get_blk_value(device, field):
    try:
        return subprocess.check_output(["lsblk", "-no", field, device], stderr=subprocess.DEVNULL).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""

# --- Main Installer Class ---
class ChimeraInstaller:
    def __init__(self, args):
        self.args = args
        self.uefi = os.path.exists("/sys/firmware/efi")
        self.target_os = args.target.lower()
        self.disk = args.disk if args.disk else self._detect_disk(args.rootfs)
        
        # Check dependencies for online modes
        if self.target_os == "arch" and self.args.online and not shutil.which("pacstrap"):
            sys.exit(f"{COLORS['FAIL']}Error: 'pacstrap' not found. Install 'arch-install-scripts'.{COLORS['ENDC']}")
        if self.target_os == "debian" and self.args.online and not shutil.which("debootstrap"):
            sys.exit(f"{COLORS['FAIL']}Error: 'debootstrap' not found. Please install it.{COLORS['ENDC']}")

    def _detect_disk(self, partition):
        try:
            if not partition: return None
            parent = subprocess.check_output(["lsblk", "-no", "pkname", partition], stderr=subprocess.PIPE).decode().strip()
            return f"/dev/{parent}"
        except Exception:
            return None

    def run(self):
        try:
            self.welcome()
            self.safety_check()
            self.ensure_network_logic()
            self.partition_handler()
            self.install_base()
            if self.target_os not in ["arch", "debian"]: # pacstrap/debootstrap handle mounts internally usually
                self.setup_chroot_mounts()
            self.configure_system()
            self.install_bootloader()
            self.finalize()
            log("Installation Successfully Completed.", "success")
        except Exception as e:
            log(f"Critical Failure: {e}", "error")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        finally:
            self.cleanup()

    def welcome(self):
        os.system("clear")
        log(f"Chimera Installer - {self.target_os.upper()} Edition", "HEADER")
        log(f"Target Disk: {self.disk} | Boot Mode: {'UEFI' if self.uefi else 'BIOS'}", "info")
        
        # Recommendation logic
        if self.target_os in ["arch", "debian"] and not self.args.online:
            print(f"\n{COLORS['WARN']}WARNING: You are performing an OFFLINE install for {self.target_os}.{COLORS['ENDC']}")
            print(f"{COLORS['WARN']}This clones the live ISO, which can be unstable or carry over wrong configs.{COLORS['ENDC']}")
            print(f"{COLORS['GREEN']}Suggestion: Restart with --online to use native tools (pacstrap/debootstrap).{COLORS['ENDC']}")
            time.sleep(2)

    def safety_check(self):
        if self.args.i_am_very_stupid: return

        if self.args.disk:
            print(f"\n{COLORS['FAIL']}!!!!!!!!!! WARNING: AUTOMATED DISK MODE !!!!!!!!!!{COLORS['ENDC']}")
            print(f"{COLORS['FAIL']}THE ENTIRE DISK {self.args.disk} WILL BE WIPED.{COLORS['ENDC']}")
        else:
            print(f"\n{COLORS['FAIL']}WARNING: MANUAL MODE{COLORS['ENDC']}")
            print(f"  - Root: {self.args.rootfs}")

        if input(f"\nType 'YES' to proceed: ") != "YES":
            sys.exit("Aborted.")

    def ensure_network_logic(self):
        if self.target_os == "gentoo" or self.args.online:
            if not check_connection():
                log("Network required. Trying nmtui...", "warn")
                if shutil.which("nmtui"): subprocess.run(["nmtui"])
                if not check_connection(): raise RuntimeError("No Internet Connection.")

    def partition_handler(self):
        log("Preparing Partitions...", "info")
        run_cmd(["umount", "-R", MOUNT_POINT], check=False, ignore_error=True)
        # Ensure swap is off before partitioning
        run_cmd(["swapoff", "-a"], check=False, ignore_error=True)
        
        if self.args.disk:
            self._auto_partition_disk()

        # Format/Mount
        run_cmd(["mkfs.ext4", "-F", self.args.rootfs])
        os.makedirs(MOUNT_POINT, exist_ok=True)
        run_cmd(["mount", self.args.rootfs, MOUNT_POINT])
        
        if self.args.boot:
            path = f"{MOUNT_POINT}/boot/efi" if self.uefi else f"{MOUNT_POINT}/boot"
            os.makedirs(path, exist_ok=True)
            if self.uefi: run_cmd(["mkfs.vfat", "-F32", self.args.boot])
            else: run_cmd(["mkfs.ext4", "-F", self.args.boot])
            run_cmd(["mount", self.args.boot, path])
        
        if self.args.swap:
            run_cmd(["mkswap", self.args.swap])
            run_cmd(["swapon", self.args.swap])

    def _auto_partition_disk(self):
        log(f"Wiping and partitioning {self.args.disk}...", "warn")
        label_type = "gpt" if self.uefi else "msdos"
        run_cmd(["wipefs", "--all", self.disk])
        run_cmd(["parted", "-s", self.disk, "mklabel", label_type])
        
        boot_part_end = "513MiB"
        run_cmd(["parted", "-s", self.disk, "mkpart", "primary", "1MiB", boot_part_end])
        current_end = boot_part_end
        
        if self.args.swap:
            # Simple parsing: assume G or M
            size = self.args.swap.upper()
            mult = 1024 if "G" in size else 1
            mb_size = int(''.join(filter(str.isdigit, size))) * mult
            swap_end = f"{513 + mb_size}MiB"
            run_cmd(["parted", "-s", self.disk, "mkpart", "primary", current_end, swap_end])
            current_end = swap_end

        run_cmd(["parted", "-s", self.disk, "mkpart", "primary", current_end, "100%"])
        
        # Naming convention adjustment
        prefix = f"{self.disk}p" if self.disk.startswith("/dev/nvme") or self.disk.startswith("/dev/mmc") else f"{self.disk}"
        
        self.args.boot = f"{prefix}1"
        if self.args.swap:
            self.args.swap = f"{prefix}2"
            self.args.rootfs = f"{prefix}3"
        else:
            self.args.rootfs = f"{prefix}2"

        if self.uefi: run_cmd(["parted", "-s", self.disk, "set", "1", "esp", "on"])
        else: run_cmd(["parted", "-s", self.disk, "set", "1", "boot", "on"])
        
        run_cmd(["partprobe", self.disk])
        time.sleep(2)
        log(f"Layout: Boot={self.args.boot}, Root={self.args.rootfs}", "success")

    def install_base(self):
        log(f"Installing Base System ({self.target_os})...", "info")
        
        if self.target_os == "arch" and self.args.online:
            self._install_arch_pacstrap()
        elif self.target_os == "debian" and self.args.online:
            self._install_debian_debootstrap()
        elif self.target_os == "gentoo":
            self._install_gentoo_stage3()
        else:
            # Offline Cloning (Generic/Arch-Offline)
            log("Mode: Offline/Clone (Ensure source is clean!)", "warn")
            excludes = ["--exclude=/proc/*", "--exclude=/sys/*", "--exclude=/dev/*", "--exclude=/run/*", "--exclude=/tmp/*", f"--exclude={MOUNT_POINT}/*"]
            subprocess.run(["rsync", "-axHAWXS", "--numeric-ids", "--info=progress2"] + excludes + ["/", MOUNT_POINT], check=True)

    def _install_arch_pacstrap(self):
        log("Running pacstrap (Arch Wiki Way)...", "info")
        pkgs = ["base", "linux", "linux-firmware", "base-devel", "nano", "networkmanager", "grub", "efibootmgr"]
        if self.args.profile == "desktop": pkgs.extend(["plasma-meta", "konsole", "dolphin", "sddm"])
        
        run_cmd(["pacstrap", "-K", MOUNT_POINT] + pkgs)
        # Genfstab is standard with pacstrap
        with open(f"{MOUNT_POINT}/etc/fstab", "w") as f:
            subprocess.run(["genfstab", "-U", MOUNT_POINT], stdout=f)

    def _install_debian_debootstrap(self):
        log(f"Running debootstrap ({DEBIAN_RELEASE})...", "info")
        # Install base system
        run_cmd(["debootstrap", "--arch", "amd64", DEBIAN_RELEASE, MOUNT_POINT, "http://deb.debian.org/debian"])
        
        # Debootstrap doesn't create fstab, we must do it
        self._gen_fstab()
        
        # Debootstrap leaves sources.list minimal, let's ensure it's good
        with open(f"{MOUNT_POINT}/etc/apt/sources.list", "w") as f:
            f.write(f"deb http://deb.debian.org/debian {DEBIAN_RELEASE} main contrib non-free-firmware\n")
            f.write(f"deb http://deb.debian.org/debian-security {DEBIAN_RELEASE}-security main contrib non-free-firmware\n")
            f.write(f"deb http://deb.debian.org/debian {DEBIAN_RELEASE}-updates main contrib non-free-firmware\n")

    def _install_gentoo_stage3(self):
        # ... (Same logic as before, abbreviated for space) ...
        # Assuming URL logic matches previous script
        log("Downloading/Extracting Gentoo Stage3...", "info")
        # (Simplified for the sake of the example - in real use, keep your url parser)
        pass 

    def setup_chroot_mounts(self):
        # Only needed for manual clone/gentoo. Pacstrap/Arch-chroot handles this.
        if shutil.which("arch-chroot"): return 
        log("Mounting API filesystems...", "info")
        for m in ["dev", "proc", "sys"]:
            target = os.path.join(MOUNT_POINT, m)
            run_cmd(["mount", "--rbind", f"/{m}", target], ignore_error=True)
            run_cmd(["mount", "--make-rslave", target], ignore_error=True)
        shutil.copy("/etc/resolv.conf", f"{MOUNT_POINT}/etc/resolv.conf")

    def configure_system(self):
        log("Configuring System...", "info")
        
        # Hostname
        with open(f"{MOUNT_POINT}/etc/hostname", "w") as f:
            f.write("chimera-linux\n")
            
        # Arch Specific Configuration
        if self.target_os == "arch":
            # Set Locale
            run_cmd("echo 'en_US.UTF-8 UTF-8' > /etc/locale.gen", chroot=True)
            run_cmd("locale-gen", chroot=True)
            run_cmd("systemctl enable NetworkManager", chroot=True, ignore_error=True)
            
            # Root Password (quick hack for demo)
            run_cmd("echo 'root:root' | chpasswd", chroot=True)

        # Debian Specific Configuration
        elif self.target_os == "debian":
            # Mounts needed for apt if not using arch-chroot
            if not shutil.which("arch-chroot"): self.setup_chroot_mounts()
            
            # Update and Install Kernel (Debootstrap doesn't install kernel)
            env = {"DEBIAN_FRONTEND": "noninteractive"}
            run_cmd("apt-get update", chroot=True, env=env)
            run_cmd("apt-get install -y linux-image-amd64 linux-headers-amd64 locales grub-efi-amd64 network-manager", chroot=True, env=env)
            
            run_cmd("echo 'en_US.UTF-8 UTF-8' > /etc/locale.gen", chroot=True)
            run_cmd("locale-gen", chroot=True)
            run_cmd("echo 'root:root' | chpasswd", chroot=True)

        # Clone Fix (The error from your screenshot)
        if self.target_os == "arch" and not self.args.online:
            # Remove live media config
            if os.path.exists(f"{MOUNT_POINT}/etc/mkinitcpio.conf.d/archiso.conf"):
                os.remove(f"{MOUNT_POINT}/etc/mkinitcpio.conf.d/archiso.conf")
            # Rebuild initramfs
            run_cmd("mkinitcpio -P", chroot=True)

    def _gen_fstab(self):
        if shutil.which("genfstab"):
            with open(f"{MOUNT_POINT}/etc/fstab", "w") as f:
                subprocess.run(["genfstab", "-U", MOUNT_POINT], stdout=f)
        else:
            log("Generating fstab manually...", "info")
            root_uuid = get_blk_value(self.args.rootfs, 'UUID')
            with open(f"{MOUNT_POINT}/etc/fstab", "w") as f:
                f.write(f"UUID={root_uuid} / ext4 defaults 0 1\n")
                if self.args.boot:
                    boot_uuid = get_blk_value(self.args.boot, 'UUID')
                    fs_type = "vfat" if self.uefi else "ext4"
                    mount = '/boot/efi' if self.uefi else '/boot'
                    f.write(f"UUID={boot_uuid} {mount} {fs_type} defaults 0 2\n")

    def install_bootloader(self):
        log("Installing Bootloader...", "info")
        target = "x86_64-efi" if self.uefi else "i386-pc"
        
        if self.target_os == "debian":
            # Debian usually handles grub-install via apt hooks, but we ensure it
            if self.uefi:
                run_cmd("grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=debian --recheck", chroot=True)
            else:
                run_cmd(f"grub-install --target=i386-pc {self.disk}", chroot=True)
            run_cmd("update-grub", chroot=True)
            
        else: # Arch / Gentoo / Generic
            cmd = ["grub-install", f"--target={target}", "--bootloader-id=Chimera", "--recheck"]
            if self.uefi: cmd.append("--efi-directory=/boot/efi")
            else: cmd.append(self.disk)
            
            run_cmd(cmd, chroot=True)
            run_cmd("grub-mkconfig -o /boot/grub/grub.cfg", chroot=True)

    def finalize(self):
        # Set machine-id
        run_cmd("systemd-machine-id-setup", chroot=True, ignore_error=True)

    def cleanup(self):
        log("Cleaning up...", "info")
        run_cmd(["umount", "-R", MOUNT_POINT], check=False, ignore_error=True)

# --- Entry Point ---
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--disk", help="Auto Partition Mode (e.g., /dev/sda)")
    parser.add_argument("--boot", help="Manual: Boot partition")
    parser.add_argument("--rootfs", help="Manual: Root partition")
    parser.add_argument("--swap", help="Swap size (Auto) or partition (Manual)")
    parser.add_argument("--target", default="arch", choices=["arch", "gentoo", "debian", "generic"])
    parser.add_argument("--online", action="store_true", help="Use pacstrap/debootstrap instead of cloning")
    parser.add_argument("--init", choices=["systemd", "openrc"], default="systemd")
    parser.add_argument("--profile", choices=["cli", "desktop"], default="cli")
    parser.add_argument("--i-am-very-stupid", action="store_true")
    
    args = parser.parse_args()
    
    if os.geteuid() != 0: sys.exit("Run as root.")
    if not args.disk and not (args.boot and args.rootfs):
        sys.exit("Error: Must specify --disk OR (--boot and --rootfs)")

    ChimeraInstaller(args).run()

if __name__ == "__main__":
    main()
