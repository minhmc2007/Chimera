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
    """Executes commands on host or inside chroot with proper error handling."""
    if chroot:
        # Securely construct chroot command
        if isinstance(cmd, list):
            # Join list into string for sh -c with quoting
            cmd_str = " ".join(shlex.quote(arg) for arg in cmd)
        else:
            cmd_str = cmd
            
        # Use list for the outer command to avoid shell injection there
        cmd = ["chroot", MOUNT_POINT, "/bin/sh", "-c", cmd_str]
        # Force shell=False for the outer call since we constructed the list
        shell = False

    try:
        # Capture process to check return code manually if needed
        proc = subprocess.run(
            cmd, 
            shell=shell, 
            check=check, 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.PIPE
        )
        return proc.returncode == 0
    except subprocess.CalledProcessError as e:
        if not ignore_error:
            log(f"Command Failed: {cmd}", "error")
            print(f"{COLORS['FAIL']}STDERR: {e.stderr.decode().strip()}{COLORS['ENDC']}")
            if check: raise e 
        return False

def check_connection():
    """Verifies DNS resolution to ensure package managers will work."""
    try:
        socket.gethostbyname("gentoo.org")
        return True
    except socket.gaierror:
        return False

def get_blk_value(device, field):
    """Wraps lsblk to get specific fields (UUID, FSTYPE, PARTTYPE)."""
    try:
        return subprocess.check_output(
            ["lsblk", "-no", field, device], 
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""

# --- Main Installer Class ---
class ChimeraInstaller:
    def __init__(self, args):
        self.args = args
        self.uefi = os.path.exists("/sys/firmware/efi")
        self.target_os = args.target.lower()
        self.disk = self._detect_disk(args.rootfs)

    def _detect_disk(self, partition):
        """Intelligently finds parent disk using lsblk."""
        try:
            parent = subprocess.check_output(
                ["lsblk", "-no", "pkname", partition], 
                stderr=subprocess.PIPE
            ).decode().strip()
            
            if not parent:
                raise ValueError("lsblk returned empty parent name")
                
            return f"/dev/{parent}"
        except (subprocess.CalledProcessError, ValueError, FileNotFoundError) as e:
            raise RuntimeError(f"Could not determine parent disk for {partition}: {e}")

    def run(self):
        try:
            self.welcome()
            self.safety_check()
            
            # Network logic
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
        
        # Stability Warning
        print(f"\n{COLORS['WARN']}⚠️  DISCLAIMER: Chimera is an under-development hobby project.{COLORS['ENDC']}")
        time.sleep(1)

    def safety_check(self):
        # 1. EFI Partition Validation
        if self.uefi and self.args.boot:
            ptype = get_blk_value(self.args.boot, "PARTTYPE").lower()
            if ptype and ptype not in ["c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "ef"]:
                log(f"WARNING: {self.args.boot} is NOT marked as an EFI System Partition!", "warn")
                time.sleep(2)

        # 2. User Confirmation
        if self.args.i_am_very_stupid:
            log("Skipping confirmation (--i-am-very-stupid active)", "warn")
            return

        print(f"\n{COLORS['FAIL']}WARNING: DESTRUCTIVE OPERATION{COLORS['ENDC']}")
        print(f"  - Root: {self.args.rootfs} (Format EXT4)")
        if self.args.boot:
            print(f"  - Boot: {self.args.boot} (Format/Mount)")
        if self.args.swap: 
            print(f"  - Swap: {self.args.swap} (Format SWAP)")
        
        if input(f"\nType 'YES' to destroy data on these partitions: ") != "YES":
            sys.exit("Aborted.")

    def ensure_network(self):
        if not check_connection():
            log("Network required. Launching nmtui...", "warn")
            if shutil.which("nmtui"): 
                subprocess.run(["nmtui"], check=False)
            else: 
                input("No nmtui. Connect manually then press Enter...")
            
            if not check_connection():
                raise RuntimeError("No Internet Connection (DNS Failed).")

    def partition_handler(self):
        log("Preparing Partitions...", "info")
        run_cmd(["umount", "-R", MOUNT_POINT], check=False, ignore_error=True)

        if self.args.swap:
            run_cmd(["mkswap", self.args.swap])
            run_cmd(["swapon", self.args.swap])

        run_cmd(["mkfs.ext4", "-F", self.args.rootfs])
        run_cmd(["mount", self.args.rootfs, MOUNT_POINT])

        if self.args.boot:
            fstype = get_blk_value(self.args.boot, "FSTYPE")
            should_format = True

            if fstype == "vfat" and not self.args.i_am_very_stupid:
                choice = input(f"{COLORS['WARN']}{self.args.boot} is already FAT32. Format it? (y/N): {COLORS['ENDC']}").lower()
                if choice != 'y':
                    should_format = False
                    log("Preserving existing boot partition data.", "success")

            if should_format:
                run_cmd(["mkfs.vfat", "-F32", self.args.boot])
            
            path = f"{MOUNT_POINT}/boot/efi" if self.uefi else f"{MOUNT_POINT}/boot"
            os.makedirs(path, exist_ok=True)
            run_cmd(["mount", self.args.boot, path])

    def install_base(self):
        if self.target_os == "gentoo":
            self._install_gentoo_stage3()
        else:
            log(f"Cloning live system (Mode: {self.target_os})...", "info")
            excludes = ["--exclude=/proc/*", "--exclude=/sys/*", "--exclude=/dev/*", 
                        "--exclude=/run/*", "--exclude=/tmp/*", "--exclude=/mnt/*", 
                        f"--exclude={MOUNT_POINT}/*", "--exclude=/etc/machine-id",
                        "--exclude=/etc/udev/rules.d/70-persistent-net.rules",
                        "--exclude=/var/cache/pacman/pkg/*", "--exclude=/var/cache/apt/archives/*"]
            
            subprocess.run(
                ["rsync", "-axHAWXS", "--numeric-ids", "--info=progress2"] + excludes + ["/", MOUNT_POINT],
                check=False
            )

    def _install_gentoo_stage3(self):
        init = self.args.init or "openrc"
        folder_mid = "amd64"
        if self.args.profile == "desktop": folder_mid += "-desktop"
        folder_name = f"current-stage3-{folder_mid}-{init}"
        
        base_dir = f"{GENTOO_BASE}/{folder_name}"
        txt_file = f"latest-stage3-{folder_mid}-{init}.txt"
        
        log(f"Querying Gentoo mirrors for {folder_name}...", "info")
        
        try:
            with urllib.request.urlopen(f"{base_dir}/{txt_file}") as response:
                content = response.read().decode('utf-8')
            
            tarball_path = None
            for line in content.splitlines():
                if ".tar.xz" in line and not line.strip().startswith("#"):
                    tarball_path = line.split()[0]
                    break
            
            if not tarball_path:
                raise ValueError("Could not find valid stage3 tarball in mirror listing.")

            full_url = f"{base_dir}/{tarball_path}"
            dest = os.path.join(MOUNT_POINT, "stage3.tar.xz")
            
            log(f"Downloading: {tarball_path}", "info")
            urllib.request.urlretrieve(full_url, dest)
            
            log("Extracting Stage3...", "info")
            subprocess.run(
                ["tar", "xpvf", dest, "--xattrs-include=*.*", "--numeric-owner", "-C", MOUNT_POINT], 
                check=True
            )
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
            shutil.copy("/etc/resolv.conf", f"{MOUNT_POINT}/etc/resolv.conf")

    def configure_system(self):
        log("Configuring System...", "info")
        self._gen_fstab()

        if self.target_os == "generic":
            log("Generic Mode: Skipping package updates and kernel install.", "warn")
            return

        if self.target_os == "gentoo":
            log("Gentoo: Installing Kernel & Firmware...", "info")
            run_cmd("emerge-webrsync", chroot=True)
            run_cmd("emerge --noreplace sys-kernel/gentoo-kernel-bin sys-kernel/linux-firmware sys-boot/grub", chroot=True)
            if self.uefi: 
                run_cmd("emerge --noreplace sys-boot/refind", chroot=True)
        
        elif self.args.online:
            if os.path.exists(f"{MOUNT_POINT}/usr/bin/pacman"): 
                run_cmd("pacman -Syu --noconfirm", chroot=True)
            elif os.path.exists(f"{MOUNT_POINT}/usr/bin/apt"): 
                run_cmd("apt update && apt upgrade -y", chroot=True)
            elif os.path.exists(f"{MOUNT_POINT}/usr/bin/xbps-install"): 
                run_cmd("xbps-install -Suy", chroot=True)

        if not self._kernel_exists():
            log("WARNING: No Kernel detected in /boot!", "warn")
            if check_connection():
                log("Attempting emergency kernel install...", "info")
                self._emergency_install_kernel()
            else:
                log("System is likely unbootable. No Kernel found.", "error")

    def _kernel_exists(self):
        return (len(glob.glob(f"{MOUNT_POINT}/boot/vmlinuz*")) > 0 or 
                len(glob.glob(f"{MOUNT_POINT}/boot/kernel*")) > 0)

    def _emergency_install_kernel(self):
        if os.path.exists(f"{MOUNT_POINT}/usr/bin/pacman"): 
            run_cmd("pacman -S --noconfirm linux linux-firmware", chroot=True)
        elif os.path.exists(f"{MOUNT_POINT}/usr/bin/apt"): 
            run_cmd("apt install -y linux-image-amd64 linux-firmware", chroot=True)
        elif os.path.exists(f"{MOUNT_POINT}/usr/bin/xbps-install"): 
            run_cmd("xbps-install -y linux", chroot=True)

    def _gen_fstab(self):
        if shutil.which("genfstab"):
            with open(f"{MOUNT_POINT}/etc/fstab", "w") as f:
                # Issue 2 Fix: Add check=True to prevent silent failures
                subprocess.run(["genfstab", "-U", MOUNT_POINT], stdout=f, check=True)
        else:
            log("Generating fstab manually...", "warn")
            
            root_uuid = get_blk_value(self.args.rootfs, 'UUID')
            if not root_uuid:
                raise RuntimeError(f"Could not retrieve UUID for rootfs {self.args.rootfs}")
                
            with open(f"{MOUNT_POINT}/etc/fstab", "w") as f:
                f.write(f"UUID={root_uuid} / ext4 defaults 0 1\n")
                
                if self.args.boot:
                    boot_uuid = get_blk_value(self.args.boot, 'UUID')
                    if boot_uuid:
                        mount = '/boot/efi' if self.uefi else '/boot'
                        f.write(f"UUID={boot_uuid} {mount} vfat defaults 0 2\n")
                        
                if self.args.swap:
                    swap_uuid = get_blk_value(self.args.swap, 'UUID')
                    if swap_uuid:
                        f.write(f"UUID={swap_uuid} none swap defaults 0 0\n")

    def install_bootloader(self):
        log("Installing Bootloader...", "info")
        
        if self.target_os != "generic" and self.target_os != "gentoo" and self.args.online:
             self._emergency_install_boottools()

        installed = False
        
        if self.uefi and os.path.exists(f"{MOUNT_POINT}/usr/bin/refind-install"):
            if run_cmd("refind-install", chroot=True, check=False): installed = True
        
        if not installed:
            # Issue 1 Fix: Use list-based command construction
            target = "x86_64-efi" if self.uefi else "i386-pc"
            
            cmd = ["grub-install", f"--target={target}", "--bootloader-id=Chimera", "--recheck"]
            
            if self.uefi:
                cmd.append("--efi-directory=/boot/efi")
            else:
                # For BIOS, target the disk
                cmd.append(self.disk)
            
            if run_cmd(cmd, chroot=True, check=False):
                run_cmd("grub-mkconfig -o /boot/grub/grub.cfg", chroot=True)
            else:
                log("Bootloader Install Failed.", "error")

    def _emergency_install_boottools(self):
        if os.path.exists(f"{MOUNT_POINT}/usr/bin/pacman"): 
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
    parser = argparse.ArgumentParser(description="Chimera Universal Installer")
    
    # Issue 4 Fix: Remove required=True to allow TUI handling
    parser.add_argument("--boot", help="Path to boot partition (e.g., /dev/sda1)")
    parser.add_argument("--rootfs", help="Path to root partition (e.g., /dev/sda3)")
    parser.add_argument("--swap", help="Path to swap partition")
    parser.add_argument("--target", default="arch", choices=["arch", "gentoo", "debian", "void", "generic"])
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--init", choices=["systemd", "openrc"])
    parser.add_argument("--profile", choices=["cli", "desktop"])
    parser.add_argument("--tui", action="store_true")
    parser.add_argument("--i-am-very-stupid", action="store_true", help="Skip safety confirmations")

    args = parser.parse_args()
    
    if args.tui:
        print("TUI mode not implemented in this version. Use CLI arguments.")
        sys.exit(1)
    else:
        # Manual validation for CLI mode
        if not args.boot or not args.rootfs:
            parser.error("the following arguments are required: --boot, --rootfs")
            
    if os.geteuid() != 0: sys.exit("Root privileges required.")
    
    ChimeraInstaller(args).run()

if __name__ == "__main__":
    main()
