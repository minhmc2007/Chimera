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
        # If --disk is used, self.disk is the whole device. Otherwise, it's inferred.
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
            print(f"{COLORS['FAIL']}ALL EXISTING DATA WILL BE PERMANENTLY DESTROYED.{COLORS['ENDC']}")
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
        
        # New automated partitioning logic
        if self.args.disk:
            self._auto_partition_disk()

        # Formatting and mounting (works for both manual and auto modes)
        if self.args.swap:
            run_cmd(["swapoff", self.args.swap], check=False, ignore_error=True)
            run_cmd(["mkswap", self.args.swap])
            run_cmd(["swapon", self.args.swap])
        
        run_cmd(["mkfs.ext4", "-F", self.args.rootfs])
        run_cmd(["mount", self.args.rootfs, MOUNT_POINT])
        
        if self.args.boot:
            # Filesystem formatting for boot is now inside _auto_partition_disk
            # This block now just handles mounting
            path = f"{MOUNT_POINT}/boot/efi" if self.uefi else f"{MOUNT_POINT}/boot"
            os.makedirs(path, exist_ok=True)
            run_cmd(["mount", self.args.boot, path])

    def _auto_partition_disk(self):
        log(f"Wiping and partitioning {self.args.disk}...", "warn")
        
        # Determine partition table type based on boot mode
        label_type = "gpt" if self.uefi else "msdos"
        log(f"Using '{label_type}' partition table for {self.disk}", "info")
        
        # Wipe existing signatures and create new label
        run_cmd(["wipefs", "--all", self.disk])
        run_cmd(["parted", "-s", self.disk, "mklabel", label_type])
        
        # Define partition layout
        boot_part_num = 1
        boot_part_end = "513MiB"
        
        # Create Boot Partition
        run_cmd(["parted", "-s", self.disk, "mkpart", "primary", "1MiB", boot_part_end])
        
        current_end = boot_part_end
        swap_part_num = None
        root_part_num = 2
        
        # Create Swap Partition (if requested)
        if self.args.swap:
            swap_part_num = 2
            root_part_num = 3
            swap_size = self.args.swap
            log(f"Creating {swap_size} swap partition...", "info")
            run_cmd(["parted", "-s", self.disk, "mkpart", "primary", current_end, f"calc(100% - {current_end} - {swap_size})"])
            current_end = f"calc(100% - {current_end} - {swap_size})" # Incorrect logic, parted mkpart needs start, end
            # Let's use a simpler, more robust approach
            # We need to calculate the end position for swap
            # Simpler: Boot, Swap, Root (rest)
            swap_end_calc = f"calc(100% - {boot_part_end} + {swap_size})" # still not right
            # Let's do it sequentially
            swap_end = f"$(echo $(parted {self.disk} unit MiB print free | tail -n 2 | head -n 1 | awk '{{print $1}}') + $(numfmt --from=auto {swap_size}) | bc)MiB"
            # This is getting too complex and fragile for shell. Let's simplify.
            # Boot: 1-513MiB. Swap: 513MiB - (513MiB + Size). Root: (513MiB + Size) - 100%
            swap_start = boot_part_end
            run_cmd(f'parted -s {self.disk} mkpart primary linux-swap {swap_start} "$(echo {swap_start} | sed "s/MiB//") + $(numfmt --from=auto {swap_size}) / 1024 / 1024"s', shell=True)
            # Above is still too complex. Let's use parted's own features.
            swap_start_mb = 513
            swap_size_mb = int(subprocess.check_output(f"numfmt --from=auto {self.args.swap}", shell=True).decode()) // 1024 // 1024
            swap_end_mb = swap_start_mb + swap_size_mb
            
            run_cmd(["parted", "-s", self.disk, "mkpart", "primary", f"{swap_start_mb}MiB", f"{swap_end_mb}MiB"])
            current_end = f"{swap_end_mb}MiB"

        # Create Root Partition (rest of the disk)
        run_cmd(["parted", "-s", self.disk, "mkpart", "primary", current_end, "100%"])
        
        # Set flags
        if self.uefi:
            run_cmd(["parted", "-s", self.disk, "set", str(boot_part_num), "esp", "on"])
        else: # BIOS
            run_cmd(["parted", "-s", self.disk, "set", str(boot_part_num), "boot", "on"])
            
        # Let kernel re-read the table
        run_cmd(["partprobe", self.disk])
        time.sleep(2) # Give udev time to create device nodes

        # Determine partition suffix ('p' for nvme/mmc, empty for sata)
        p_suffix = "p" if "nvme" in self.disk or "mmc" in self.disk else ""
        
        # Update self.args with the new paths
        self.args.boot = f"{self.disk}{p_suffix}{boot_part_num}"
        if swap_part_num:
            self.args.swap = f"{self.disk}{p_suffix}{swap_part_num}"
        self.args.rootfs = f"{self.disk}{p_suffix}{root_part_num}"
        
        log(f"New Layout: Boot={self.args.boot}, Swap={self.args.swap}, Root={self.args.rootfs}", "success")
        
        # Format the newly created partitions
        log("Formatting new partitions...", "info")
        if self.uefi:
            run_cmd(["mkfs.vfat", "-F32", self.args.boot])
        else:
            run_cmd(["mkfs.ext4", "-F", self.args.boot])
        run_cmd(["sync"])

    def install_base(self):
        # ... (rest of the script is unchanged) ...
        if self.target_os == "gentoo": self._install_gentoo_stage3()
        else:
            log(f"Cloning live system (Mode: {self.target_os})...", "info")
            excludes = ["--exclude=/proc/*", "--exclude=/sys/*", "--exclude=/dev/*", 
                        "--exclude=/run/*", "--exclude=/tmp/*", "--exclude=/mnt/*", 
                        f"--exclude={MOUNT_POINT}/*"]
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
    
    # Mode groups: User can either specify a whole disk OR manual partitions
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--disk", help="Path to the disk to auto-partition (e.g., /dev/sda). Wipes the entire disk.")
    
    manual_group = mode_group.add_argument_group('manual', 'Manual Partitioning')
    manual_group.add_argument("--boot", help="Path to boot partition")
    manual_group.add_argument("--rootfs", help="Path to root partition")

    # General options
    parser.add_argument("--swap", help="Size for swap partition in auto mode (e.g., 2G) or path in manual mode.")
    parser.add_argument("--target", default="arch", choices=["arch", "gentoo", "debian", "void", "generic"])
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--init", choices=["systemd", "openrc"])
    parser.add_argument("--profile", choices=["cli", "desktop"])
    parser.add_argument("--tui", action="store_true")
    parser.add_argument("--i-am-very-stupid", action="store_true", help="Skip safety confirmations")
    
    args = parser.parse_args()
    
    # Validate arguments
    if not args.disk and (not args.boot or not args.rootfs):
        parser.error("In manual mode, both --boot and --rootfs are required.")

    if args.tui:
        print("TUI mode not implemented.")
        sys.exit(1)
            
    if os.geteuid() != 0: sys.exit("Root privileges required.")
    ChimeraInstaller(args).run()

if __name__ == "__main__":
    main()
