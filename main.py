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

# --- Utility Functions ---
def log(msg, level="info"):
    icon = "[*]"
    color = COLORS['BLUE']
    if level == "error": icon, color = "[!]", COLORS['FAIL']
    elif level == "success": icon, color = "[+]", COLORS['GREEN']
    elif level == "warn": icon, color = "[?]", COLORS['WARN']
    elif level == "HEADER": icon, color = "[#]", COLORS['HEADER']
    
    print(f"{color}{icon} {msg}{COLORS['ENDC']}")

def run_cmd(cmd, shell=False, check=True, chroot=False, ignore_error=False):
    if chroot:
        if isinstance(cmd, list): cmd_str = " ".join(shlex.quote(arg) for arg in cmd)
        else: cmd_str = cmd
        cmd = ["chroot", MOUNT_POINT, "/bin/sh", "-c", cmd_str]
        shell = False
    try:
        proc = subprocess.run(cmd, shell=shell, check=check, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return proc.returncode == 0
    except subprocess.CalledProcessError as e:
        if not ignore_error:
            log(f"Command Failed: {cmd}", "error")
            print(f"{COLORS['FAIL']}STDERR: {e.stderr.decode().strip()}{COLORS['ENDC']}")
            if check: raise e 
        return False

def check_connection():
    try:
        socket.gethostbyname("gentoo.org")
        return True
    except socket.gaierror:
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
        self.arch_keyring_initialized = False

    def _detect_disk(self, partition):
        try:
            parent = subprocess.check_output(["lsblk", "-no", "pkname", partition], stderr=subprocess.PIPE).decode().strip()
            if not parent: raise ValueError("lsblk returned empty parent name")
            return f"/dev/{parent}"
        except (subprocess.CalledProcessError, ValueError, FileNotFoundError) as e:
            raise RuntimeError(f"Could not determine parent disk for {partition}: {e}")

    def run(self):
        try:
            self.welcome()
            self.safety_check()
            if self.target_os != "generic" and (self.target_os == "gentoo" or self.args.online):
                self.ensure_network()
            self.partition_handler()
            self.install_base()
            self.setup_chroot_mounts()
            self.configure_system()
            self.install_bootloader()
            log("Installation Successfully Completed.", "success")
        except Exception as e:
            log(f"Critical Failure: {e}", "error")
            sys.exit(1)
        finally:
            self.cleanup()

    def welcome(self):
        os.system("clear")
        log(f"Chimera Installer - Blue Archive Linux ({self.target_os})", "HEADER")
        log(f"Target Disk: {self.disk} | Boot Mode: {'UEFI' if self.uefi else 'BIOS'}", "info")
        print(f"\n{COLORS['WARN']}⚠️  DISCLAIMER: Chimera is an under-development hobby project.{COLORS['ENDC']}")
        time.sleep(1)

    def safety_check(self):
        if self.args.i_am_very_stupid:
            log("Skipping confirmation (--i-am-very-stupid active)", "warn")
            return

        if self.args.disk:
            print(f"\n{COLORS['FAIL']}!!!!!!!!!! WARNING: AUTOMATED DISK MODE !!!!!!!!!!{COLORS['ENDC']}")
            print(f"{COLORS['FAIL']}THE ENTIRE DISK {self.args.disk} WILL BE WIPED AND REPARTITIONED.{COLORS['ENDC']}")
        else:
            print(f"\n{COLORS['FAIL']}WARNING: DESTRUCTIVE OPERATION{COLORS['ENDC']}")
            if self.args.rootfs: print(f"  - Root: {self.args.rootfs} (Format EXT4)")
            if self.args.boot: print(f"  - Boot: {self.args.boot} (Format/Mount)")
            if self.args.swap: print(f"  - Swap: {self.args.swap} (Format SWAP)")

        if input(f"\nType 'YES' to proceed: ") != "YES":
            sys.exit("Aborted by user.")

    def ensure_network(self):
        if not check_connection():
            log("Network required. Launching nmtui...", "warn")
            if shutil.which("nmtui"): subprocess.run(["nmtui"], check=False)
            else: input("No nmtui. Connect manually then press Enter...")
            if not check_connection(): raise RuntimeError("No Internet Connection.")

    def partition_handler(self):
        log("Preparing Partitions...", "info")
        run_cmd(["umount", "-R", MOUNT_POINT], check=False, ignore_error=True)
        os.makedirs(MOUNT_POINT, exist_ok=True)
        
        if self.args.disk:
            self._auto_partition_disk()

        if self.args.swap:
            run_cmd(["swapoff", self.args.swap], check=False, ignore_error=True)
            run_cmd(["mkswap", self.args.swap])
            run_cmd(["swapon", self.args.swap])
        
        run_cmd(["mkfs.ext4", "-F", self.args.rootfs])
        run_cmd(["mount", self.args.rootfs, MOUNT_POINT])
        
        if self.args.boot:
            path = f"{MOUNT_POINT}/boot/efi" if self.uefi else f"{MOUNT_POINT}/boot"
            os.makedirs(path, exist_ok=True)
            run_cmd(["mount", self.args.boot, path])

    def _auto_partition_disk(self):
        log(f"Wiping and partitioning {self.args.disk}...", "warn")
        label_type = "gpt" if self.uefi else "msdos"
        run_cmd(["wipefs", "--all", self.disk])
        run_cmd(["parted", "-s", self.disk, "mklabel", label_type])
        
        boot_part_end = "513MiB"
        run_cmd(["parted", "-s", self.disk, "mkpart", "primary", "1MiB", boot_part_end])
        current_end = boot_part_end
        
        if self.args.swap:
            swap_start_mb = 513
            swap_size_str = self.args.swap.upper()
            multiplier = 1
            if swap_size_str.endswith('G'): multiplier = 1024
            elif swap_size_str.endswith('M'): multiplier = 1
            swap_size_mb = int(swap_size_str.rstrip('GM')) * multiplier
            swap_end_mb = swap_start_mb + swap_size_mb
            
            run_cmd(["parted", "-s", self.disk, "mkpart", "primary", f"{swap_start_mb}MiB", f"{swap_end_mb}MiB"])
            current_end = f"{swap_end_mb}MiB"

        run_cmd(["parted", "-s", self.disk, "mkpart", "primary", current_end, "100%"])
        
        p_suffix = "p" if "nvme" in self.disk or "mmc" in self.disk else ""
        boot_part_num = 1
        swap_part_num = 2 if self.args.swap else None
        root_part_num = 3 if self.args.swap else 2

        if self.uefi:
            run_cmd(["parted", "-s", self.disk, "set", str(boot_part_num), "esp", "on"])
        else: 
            run_cmd(["parted", "-s", self.disk, "set", str(boot_part_num), "boot", "on"])
            
        run_cmd(["partprobe", self.disk])
        time.sleep(2)

        self.args.boot = f"{self.disk}{p_suffix}{boot_part_num}"
        if swap_part_num: self.args.swap = f"{self.disk}{p_suffix}{swap_part_num}"
        self.args.rootfs = f"{self.disk}{p_suffix}{root_part_num}"
        
        log(f"New Layout: Boot={self.args.boot}, Swap={self.args.swap}, Root={self.args.rootfs}", "success")
        
        if self.uefi: run_cmd(["mkfs.vfat", "-F32", self.args.boot])
        else: run_cmd(["mkfs.ext4", "-F", self.args.boot])
        run_cmd(["sync"])

    def install_base(self):
        if self.target_os == "gentoo": self._install_gentoo_stage3()
        else:
            log(f"Cloning live system (Mode: {self.target_os})...", "info")
            excludes = ["--exclude=/proc/*", "--exclude=/sys/*", "--exclude=/dev/*", "--exclude=/run/*", "--exclude=/tmp/*", "--exclude=/mnt/*", f"--exclude={MOUNT_POINT}/*"]
            subprocess.run(["rsync", "-axHAWXS", "--numeric-ids", "--info=progress2"] + excludes + ["/", MOUNT_POINT], check=False)

    def _install_gentoo_stage3(self):
        init = self.args.init or "openrc"
        folder_mid = "amd64" + ("-desktop" if self.args.profile == "desktop" else "")
        folder_name = f"current-stage3-{folder_mid}-{init}"
        base_dir = f"{GENTOO_BASE}/{folder_name}"
        txt_file = f"latest-stage3-{folder_mid}-{init}.txt"
        log(f"Querying Gentoo mirrors for {folder_name}...", "info")
        try:
            with urllib.request.urlopen(f"{base_dir}/{txt_file}") as response:
                content = response.read().decode('utf-8')
            tarball_path = next((line.split()[0] for line in content.splitlines() if ".tar.xz" in line and not line.strip().startswith("#")), None)
            if not tarball_path: raise ValueError("Invalid mirror listing.")
            full_url = f"{base_dir}/{tarball_path}"
            dest = os.path.join(MOUNT_POINT, "stage3.tar.xz")
            log(f"Downloading: {tarball_path}", "info")
            urllib.request.urlretrieve(full_url, dest)
            log("Extracting Stage3...", "info")
            subprocess.run(["tar", "xpvf", dest, "--xattrs-include=*.*", "--numeric-owner", "-C", MOUNT_POINT], check=True)
            os.remove(dest)
        except Exception as e:
            raise RuntimeError(f"Gentoo Fetch Failed: {e}")

    def setup_chroot_mounts(self):
        log("Mounting API filesystems...", "info")
        for m in ["dev", "proc", "sys"]:
            target = os.path.join(MOUNT_POINT, m)
            run_cmd(["mount", "--rbind", f"/{m}", target])
            run_cmd(["mount", "--make-rslave", target])
        if self.target_os != "generic":
            resolv_dest = f"{MOUNT_POINT}/etc/resolv.conf"
            if os.path.exists(resolv_dest) or os.path.islink(resolv_dest):
                os.remove(resolv_dest)
            shutil.copy("/etc/resolv.conf", resolv_dest)

    def configure_system(self):
        log("Configuring System...", "info")
        self._gen_fstab()
        
        # --- FIX: Sanitize Arch/Generic-Arch Initramfs ---
        if os.path.exists(f"{MOUNT_POINT}/usr/bin/mkinitcpio"):
            self._fix_arch_initramfs()

        if self.target_os == "generic": return
        if self.target_os == "gentoo":
            log("Gentoo: Installing Kernel & Firmware...", "info")
            run_cmd("emerge-webrsync", chroot=True)
            run_cmd("emerge --noreplace sys-kernel/gentoo-kernel-bin sys-kernel/linux-firmware sys-boot/grub", chroot=True)
            if self.uefi: run_cmd("emerge --noreplace sys-boot/refind", chroot=True)
        elif self.args.online:
            if os.path.exists(f"{MOUNT_POINT}/usr/bin/pacman"):
                self._initialize_arch_keyring()
                run_cmd("pacman -Syu --noconfirm", chroot=True)
            elif os.path.exists(f"{MOUNT_POINT}/usr/bin/apt"): run_cmd("apt update && apt upgrade -y", chroot=True)
            elif os.path.exists(f"{MOUNT_POINT}/usr/bin/xbps-install"): run_cmd("xbps-install -Suy", chroot=True)
        if not self._kernel_exists() and check_connection():
            log("WARNING: No Kernel detected in /boot!", "warn")
            self._emergency_install_kernel()

    # --- FIX: New method to remove archiso hooks and rebuild ---
    def _fix_arch_initramfs(self):
        log("Sanitizing initramfs for physical disk boot...", "info")
        
        # 1. Remove the ISO-specific drop-in config
        iso_conf = f"{MOUNT_POINT}/etc/mkinitcpio.conf.d/archiso.conf"
        if os.path.exists(iso_conf):
            log(f"Removing live media config: {iso_conf}", "success")
            os.remove(iso_conf)
        
        # 2. Rebuild the images (using the clean /etc/mkinitcpio.conf)
        log("Rebuilding initramfs (this may take a moment)...", "info")
        if not run_cmd("mkinitcpio -P", chroot=True):
            log("Failed to rebuild initramfs.", "error")

    def _initialize_arch_keyring(self):
        if self.arch_keyring_initialized: return
        log("Arch Linux detected. Initializing pacman keyring...", "info")
        run_cmd("pacman-key --init", chroot=True)
        run_cmd("pacman-key --populate archlinux", chroot=True)
        self.arch_keyring_initialized = True

    def _kernel_exists(self):
        return bool(glob.glob(f"{MOUNT_POINT}/boot/vmlinuz*") or glob.glob(f"{MOUNT_POINT}/boot/kernel*"))

    def _emergency_install_kernel(self):
        log("Attempting emergency kernel install...", "info")
        if os.path.exists(f"{MOUNT_POINT}/usr/bin/pacman"):
            self._initialize_arch_keyring()
            run_cmd("pacman -S --noconfirm linux linux-firmware", chroot=True)
        elif os.path.exists(f"{MOUNT_POINT}/usr/bin/apt"): 
            run_cmd("apt install -y linux-image-amd64", chroot=True)

    def _gen_fstab(self):
        if shutil.which("genfstab"):
            with open(f"{MOUNT_POINT}/etc/fstab", "w") as f:
                subprocess.run(["genfstab", "-U", MOUNT_POINT], stdout=f, check=True)
        else:
            log("Generating fstab manually...", "warn")
            root_uuid = get_blk_value(self.args.rootfs, 'UUID')
            if not root_uuid: raise RuntimeError("Missing Root UUID")
            with open(f"{MOUNT_POINT}/etc/fstab", "w") as f:
                f.write(f"UUID={root_uuid} / ext4 defaults 0 1\n")
                if self.args.boot:
                    boot_uuid = get_blk_value(self.args.boot, 'UUID')
                    if boot_uuid:
                        fs_type = "vfat" if self.uefi else "ext4"
                        mount_point = '/boot/efi' if self.uefi else '/boot'
                        f.write(f"UUID={boot_uuid} {mount_point} {fs_type} defaults 0 2\n")
                if self.args.swap:
                    swap_uuid = get_blk_value(self.args.swap, 'UUID')
                    if swap_uuid: f.write(f"UUID={swap_uuid} none swap defaults 0 0\n")

    def install_bootloader(self):
        log("Installing Bootloader...", "info")
        if self.target_os not in ["generic", "gentoo"] and self.args.online:
             self._emergency_install_boottools()
        installed = False
        if self.uefi and os.path.exists(f"{MOUNT_POINT}/usr/bin/refind-install"):
            if run_cmd("refind-install", chroot=True, check=False): installed = True
        if not installed:
            target = "x86_64-efi" if self.uefi else "i386-pc"
            cmd = ["grub-install", f"--target={target}", "--bootloader-id=Chimera", "--recheck"]
            if self.uefi: cmd.append("--efi-directory=/boot/efi")
            else: cmd.append(self.disk)
            if run_cmd(cmd, chroot=True, check=False):
                run_cmd("grub-mkconfig -o /boot/grub/grub.cfg", chroot=True)
            else:
                log("Bootloader Install Failed.", "error")

    def _emergency_install_boottools(self):
        if os.path.exists(f"{MOUNT_POINT}/usr/bin/pacman"):
            self._initialize_arch_keyring()
            run_cmd("pacman -S --noconfirm grub efibootmgr", chroot=True)
        elif os.path.exists(f"{MOUNT_POINT}/usr/bin/apt"): 
            run_cmd("apt install -y grub-efi-amd64 efibootmgr", chroot=True)

    def cleanup(self):
        if not os.path.exists(f"{MOUNT_POINT}/etc/machine-id"):
            run_cmd("systemd-machine-id-setup", chroot=True, check=False, ignore_error=True)
        log("Cleaning up mounts...", "info")
        run_cmd(["umount", "-R", MOUNT_POINT], check=False, ignore_error=True)

# --- Entry Point ---
def main():
    parser = argparse.ArgumentParser(description="Chimera: A Universal Linux Installer")
    parser.add_argument("--disk", help="Automated Mode: Path to the disk to wipe and auto-partition (e.g., /dev/sda).")
    parser.add_argument("--boot", help="Manual Mode: Path to pre-existing boot partition.")
    parser.add_argument("--rootfs", help="Manual Mode: Path to pre-existing root partition.")
    parser.add_argument("--swap", help="Size for swap in auto mode (e.g., 2G) or path in manual mode.")
    parser.add_argument("--target", default="arch", choices=["arch", "gentoo", "debian", "void", "generic"])
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--init", choices=["systemd", "openrc"])
    parser.add_argument("--profile", choices=["cli", "desktop"])
    parser.add_argument("--tui", action="store_true")
    parser.add_argument("--i-am-very-stupid", action="store_true", help="Skip safety confirmations")
    
    args = parser.parse_args()
    
    if args.disk and (args.boot or args.rootfs):
        parser.error("Cannot use --disk with manual partitioning flags (--boot, --rootfs).")
    
    if not args.disk and (not args.boot or not args.rootfs):
        parser.error("You must specify either --disk (for automated install) or both --boot and --rootfs (for manual install).")

    if args.tui:
        print("TUI mode not implemented.")
        sys.exit(1)
            
    if os.geteuid() != 0: sys.exit("Root privileges required.")
    ChimeraInstaller(args).run()

if __name__ == "__main__":
    main()
