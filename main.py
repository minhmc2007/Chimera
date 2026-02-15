#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
import shutil
import time
import urllib.request
import socket
import re

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
    print(f"{color}{icon} {msg}{COLORS['ENDC']}")

def run_cmd(cmd, shell=False, check=True, chroot=False, ignore_error=False):
    """Executes commands on host or inside chroot."""
    if chroot:
        if isinstance(cmd, list): cmd = " ".join(cmd)
        # Use sh -c to ensure complex pipes/redirects work inside chroot
        cmd = f"chroot {MOUNT_POINT} /bin/sh -c '{cmd}'"

    try:
        subprocess.run(cmd, shell=shell or chroot, check=check, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True
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
    except:
        return False

def get_blk_value(device, field):
    """Wraps lsblk to get specific fields (UUID, FSTYPE, PARTTYPE)."""
    try:
        return subprocess.check_output(["lsblk", "-no", field, device]).decode().strip()
    except:
        return ""

# --- Main Installer Class ---
class ChimeraInstaller:
    def __init__(self, args):
        self.args = args
        self.uefi = os.path.exists("/sys/firmware/efi")
        self.target_os = args.target.lower()
        self.disk = self._detect_disk(args.rootfs)

    def _detect_disk(self, partition):
        """Intelligently finds parent disk (e.g., nvme0n1p1 -> nvme0n1)."""
        try:
            return "/dev/" + subprocess.check_output(["lsblk", "-no", "pkname", partition]).decode().strip()
        except:
            return partition.rstrip("0123456789")

    def run(self):
        try:
            self.welcome()
            self.safety_check()

            # Network is only for Gentoo or explicit --online flag.
            # Generic mode explicitly FORBIDS network usage.
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
        print(f"{COLORS['WARN']}    It WILL NOT be as stable as Calamares or Ubiquity.{COLORS['ENDC']}")
        print(f"{COLORS['WARN']}    Use at your own risk. undefined behavior may occur.{COLORS['ENDC']}")
        time.sleep(2)

    def safety_check(self):
        # 1. EFI Partition Validation
        if self.uefi and self.args.boot:
            ptype = get_blk_value(self.args.boot, "PARTTYPE").lower()
            # EF00 (MBR) or C12A7328... (GPT)
            if ptype and ptype not in ["c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "ef"]:
                log(f"WARNING: {self.args.boot} is NOT marked as an EFI System Partition!", "warn")
                log("Bios might refuse to boot from it.", "warn")
                time.sleep(2)

        # 2. User Confirmation
        if self.args.i_am_very_stupid:
            log("Skipping confirmation (--i-am-very-stupid active)", "warn")
            return

        print(f"\n{COLORS['FAIL']}WARNING: DESTRUCTIVE OPERATION{COLORS['ENDC']}")
        print(f"  - Root: {self.args.rootfs} (Format EXT4)")
        print(f"  - Boot: {self.args.boot} (Format/Mount)")
        if self.args.swap: print(f"  - Swap: {self.args.swap} (Format SWAP)")

        if input(f"\nType 'YES' to destroy data on these partitions: ") != "YES":
            sys.exit("Aborted.")

    def ensure_network(self):
        if not check_connection():
            log("Network required. Launching nmtui...", "warn")
            if shutil.which("nmtui"): subprocess.run(["nmtui"])
            else: input("No nmtui. Connect manually then press Enter...")

            if not check_connection():
                raise RuntimeError("No Internet Connection (DNS Failed).")

    def partition_handler(self):
        log("Preparing Partitions...", "info")
        run_cmd(["umount", "-R", MOUNT_POINT], check=False, ignore_error=True)

        # Swap
        if self.args.swap:
            run_cmd(["mkswap", self.args.swap])
            run_cmd(["swapon", self.args.swap])

        # Root
        run_cmd(["mkfs.ext4", "-F", self.args.rootfs])
        run_cmd(["mount", self.args.rootfs, MOUNT_POINT])

        # Boot
        if self.args.boot:
            fstype = get_blk_value(self.args.boot, "FSTYPE")
            should_format = True

            # Reuse existing EFI partition if possible
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
            # Generic, Arch, Debian, Void all use rsync from live ISO
            log(f"Cloning live system (Mode: {self.target_os})...", "info")
            excludes = ["--exclude=/proc/*", "--exclude=/sys/*", "--exclude=/dev/*",
                        "--exclude=/run/*", "--exclude=/tmp/*", "--exclude=/mnt/*",
                        f"--exclude={MOUNT_POINT}/*", "--exclude=/etc/machine-id",
                        "--exclude=/etc/udev/rules.d/70-persistent-net.rules",
                        "--exclude=/var/cache/pacman/pkg/*", "--exclude=/var/cache/apt/archives/*"]

            # For Generic mode, we want a pure copy, but still exclude runtime junk
            subprocess.call(["rsync", "-axHAWXS", "--numeric-ids", "--info=progress2"] + excludes + ["/", MOUNT_POINT])

    def _install_gentoo_stage3(self):
        init = self.args.init or "openrc"
        # Folder pattern: current-stage3-amd64-{desktop|nomultilib}-{openrc|systemd}
        profile_str = "desktop" if self.args.profile == "desktop" else ""

        folder_mid = "amd64"
        if self.args.profile == "desktop": folder_mid += "-desktop"
        folder_name = f"current-stage3-{folder_mid}-{init}"

        base_dir = f"{GENTOO_BASE}/{folder_name}"
        txt_file = f"latest-stage3-{folder_mid}-{init}.txt"

        log(f"Querying Gentoo mirrors for {folder_name}...", "info")

        try:
            # Fetch listing
            with urllib.request.urlopen(f"{base_dir}/{txt_file}") as response:
                content = response.read().decode('utf-8')

            # Parse for .tar.xz
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
            subprocess.run(["tar", "xpvf", dest, "--xattrs-include='*.*'", "--numeric-owner", "-C", MOUNT_POINT], check=True)
            os.remove(dest)

        except Exception as e:
            raise RuntimeError(f"Gentoo Fetch Failed: {e}")

    def setup_chroot_mounts(self):
        log("Mounting API filesystems...", "info")
        for m in ["dev", "proc", "sys"]:
            target = os.path.join(MOUNT_POINT, m)
            run_cmd(["mount", "--rbind", f"/{m}", target])
            run_cmd(["mount", "--make-rslave", target])

        # DNS is only needed if we plan to use network
        if self.target_os != "generic":
            shutil.copy("/etc/resolv.conf", f"{MOUNT_POINT}/etc/resolv.conf")

    def configure_system(self):
        log("Configuring System...", "info")

        # 1. Fstab
        self._gen_fstab()

        # 2. Generic Mode Bypass
        if self.target_os == "generic":
            log("Generic Mode: Skipping package updates and kernel install.", "warn")
            log("Generic Mode: Assuming kernel/initramfs exist in copied rootfs.", "warn")
            return

        # 3. Gentoo Specifics
        if self.target_os == "gentoo":
            log("Gentoo: Installing Kernel & Firmware...", "info")
            run_cmd("emerge-webrsync", chroot=True)
            run_cmd("emerge --noreplace sys-kernel/gentoo-kernel-bin sys-kernel/linux-firmware sys-boot/grub", chroot=True)
            if self.uefi:
                run_cmd("emerge --noreplace sys-boot/refind", chroot=True)

        # 4. Online Updates (Arch/Debian/Void)
        elif self.args.online:
            if os.path.exists(f"{MOUNT_POINT}/usr/bin/pacman"): run_cmd("pacman -Syu --noconfirm", chroot=True)
            elif os.path.exists(f"{MOUNT_POINT}/usr/bin/apt"): run_cmd("apt update && apt upgrade -y", chroot=True)

        # 5. Kernel Verification (Safety Net)
        if not self._kernel_exists():
            log("WARNING: No Kernel detected in /boot!", "warn")
            if check_connection():
                log("Attempting emergency kernel install...", "info")
                self._emergency_install_kernel()
            else:
                log("System is likely unbootable. No Kernel found.", "error")

    def _kernel_exists(self):
        import glob
        return (len(glob.glob(f"{MOUNT_POINT}/boot/vmlinuz*")) > 0 or
                len(glob.glob(f"{MOUNT_POINT}/boot/kernel*")) > 0)

    def _emergency_install_kernel(self):
        if os.path.exists(f"{MOUNT_POINT}/usr/bin/pacman"): run_cmd("pacman -S --noconfirm linux linux-firmware", chroot=True)
        elif os.path.exists(f"{MOUNT_POINT}/usr/bin/apt"): run_cmd("apt install -y linux-image-amd64 linux-firmware", chroot=True)
        elif os.path.exists(f"{MOUNT_POINT}/usr/bin/xbps-install"): run_cmd("xbps-install -y linux", chroot=True)

    def _gen_fstab(self):
        if shutil.which("genfstab"):
            with open(f"{MOUNT_POINT}/etc/fstab", "w") as f:
                subprocess.run(["genfstab", "-U", MOUNT_POINT], stdout=f)
        else:
            log("Generating fstab manually...", "warn")
            with open(f"{MOUNT_POINT}/etc/fstab", "w") as f:
                f.write(f"UUID={get_blk_value(self.args.rootfs, 'UUID')} / ext4 defaults 0 1\n")
                if self.args.boot:
                    mount = '/boot/efi' if self.uefi else '/boot'
                    f.write(f"UUID={get_blk_value(self.args.boot, 'UUID')} {mount} vfat defaults 0 2\n")
                if self.args.swap:
                    f.write(f"UUID={get_blk_value(self.args.swap, 'UUID')} none swap defaults 0 0\n")

    def install_bootloader(self):
        log("Installing Bootloader...", "info")

        # Inject tools if missing (Only if not generic and online)
        if self.target_os != "generic" and self.target_os != "gentoo" and self.args.online:
             self._emergency_install_boottools()

        installed = False

        # UEFI rEFInd
        if self.uefi and os.path.exists(f"{MOUNT_POINT}/usr/bin/refind-install"):
            if run_cmd("refind-install", chroot=True, check=False): installed = True

        # GRUB
        if not installed:
            target = "x86_64-efi" if self.uefi else "i386-pc"
            efi_arg = "--efi-directory=/boot/efi" if self.uefi else ""
            disk_arg = "" if self.uefi else self.disk

            # In Generic mode, we assume the binaries exist in the copied rootfs
            cmd = f"grub-install --target={target} {efi_arg} --bootloader-id=Chimera --recheck {disk_arg}"

            if run_cmd(cmd, chroot=True, check=False):
                run_cmd("grub-mkconfig -o /boot/grub/grub.cfg", chroot=True)
            else:
                log("Bootloader Install Failed.", "error")
                if self.target_os == "generic":
                    log("Generic Mode Hint: Ensure 'grub' and 'efibootmgr' are installed in the Live ISO source.", "warn")

    def _emergency_install_boottools(self):
        if os.path.exists(f"{MOUNT_POINT}/usr/bin/pacman"): run_cmd("pacman -S --noconfirm grub efibootmgr", chroot=True)
        elif os.path.exists(f"{MOUNT_POINT}/usr/bin/apt"): run_cmd("apt install -y grub-efi-amd64 efibootmgr", chroot=True)

    def cleanup(self):
        # Generate machine-id (Crucial for systemd in Generic/Copy modes)
        if not os.path.exists(f"{MOUNT_POINT}/etc/machine-id"):
            run_cmd("systemd-machine-id-setup", chroot=True, check=False, ignore_error=True)

        log("Cleaning up mounts...", "info")
        run_cmd(["umount", "-R", MOUNT_POINT], check=False, ignore_error=True)

# --- Entry Point ---
def main():
    parser = argparse.ArgumentParser(description="Chimera Universal Installer")
    parser.add_argument("--boot", default="/dev/sda1")
    parser.add_argument("--swap")
    parser.add_argument("--rootfs", default="/dev/sda3")
    parser.add_argument("--target", default="arch", choices=["arch", "gentoo", "debian", "void", "generic"])
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--init", choices=["systemd", "openrc"])
    parser.add_argument("--profile", choices=["cli", "desktop"])
    parser.add_argument("--tui", action="store_true")
    parser.add_argument("--i-am-very-stupid", action="store_true", help="Skip safety confirmations")

    # Simple TUI Stub
    if "--tui" in sys.argv:
        print("TUI mode not fully implemented in this snippet. Please use CLI args.")
        # In full version, will be here run_tui() here

    args = parser.parse_args()
    if os.geteuid() != 0: sys.exit("Root privileges required.")

    ChimeraInstaller(args).run()

if __name__ == "__main__":
    main()
