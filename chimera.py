#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
import shutil
import time
import socket
import shlex
import glob

# --- Configuration & Constants ---
COLORS = {
    'HEADER': '\033[95m', 'BLUE': '\033[94m', 'GREEN': '\033[92m',
    'WARN': '\033[93m', 'FAIL': '\033[91m', 'ENDC': '\033[0m', 'BOLD': '\033[1m'
}
MOUNT_POINT = "/mnt/chimera_target"
DEBIAN_RELEASE = "trixie" # Stable
DEBUG_MODE = False

# --- Utility Functions ---
def log(msg, level="info"):
    icon = "[*]"
    color = COLORS['BLUE']
    if level == "error": icon, color = "[!]", COLORS['FAIL']
    elif level == "success": icon, color = "[+]", COLORS['GREEN']
    elif level == "warn": icon, color = "[?]", COLORS['WARN']
    elif level == "HEADER": icon, color = "[#]", COLORS['HEADER']
    elif level == "DEBUG": icon, color = "[D]", COLORS['WARN']
    
    print(f"{color}{icon} {msg}{COLORS['ENDC']}")

def run_cmd(cmd, shell=False, check=True, chroot=False, ignore_error=False, env=None, stream=False):
    show_output = stream or DEBUG_MODE

    if chroot:
        if isinstance(cmd, list): cmd_str = " ".join(shlex.quote(arg) for arg in cmd)
        else: cmd_str = cmd
        
        if shutil.which("arch-chroot"):
            cmd = ["arch-chroot", MOUNT_POINT, "/bin/sh", "-c", cmd_str]
        else:
            cmd = ["chroot", MOUNT_POINT, "/bin/sh", "-c", cmd_str]
        shell = False
    
    if DEBUG_MODE:
        log(f"CMD: {cmd}", "DEBUG")

    try:
        if show_output:
            proc = subprocess.run(cmd, shell=shell, check=check, env=env)
        else:
            proc = subprocess.run(cmd, shell=shell, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        return proc.returncode == 0
    except subprocess.CalledProcessError as e:
        if not ignore_error:
            log(f"Command Failed: {cmd}", "error")
            if e.stderr:
                print(f"{COLORS['FAIL']}STDERR: {e.stderr.decode().strip()}{COLORS['ENDC']}")
            elif show_output:
                print(f"{COLORS['FAIL']}(Command failed, output above){COLORS['ENDC']}")
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
        
        if self.args.user and not self.args.passwd:
            sys.exit(f"{COLORS['FAIL']}Error: --user requires --passwd{COLORS['ENDC']}")

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
            if self.target_os not in ["arch", "debian"] or not self.args.online: 
                if not shutil.which("arch-chroot"):
                    self.setup_chroot_mounts()
            self.configure_system()
            self.setup_users()
            self.install_bal_extras()
            self.run_custom_scripts()
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
        
        if DEBUG_MODE:
            log("Debug Mode: ON (Verbose output enabled)", "DEBUG")
            log("Current Disk Layout:", "DEBUG")
            subprocess.run(["lsblk"])
            print("-" * 40)

        if self.args.user:
            log(f"User Setup: {self.args.user}", "info")
        if self.args.passwd:
            log("Password set for Root (and User).", "info")
        if self.args.timezone:
            log(f"Timezone: {self.args.timezone}", "info")
        
        if (self.target_os in ["arch", "debian", "bal"] and not self.args.online) or self.target_os == "bal":
            print(f"\n{COLORS['WARN']}WARNING: Offline/Clone Install Mode Active.{COLORS['ENDC']}")
            time.sleep(1)

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
        run_cmd(["swapoff", "-a"], check=False, ignore_error=True)
        
        if self.args.disk:
            self._auto_partition_disk()

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
            size = self.args.swap.upper()
            mult = 1024 if "G" in size else 1
            mb_size = int(''.join(filter(str.isdigit, size))) * mult
            swap_end = f"{513 + mb_size}MiB"
            run_cmd(["parted", "-s", self.disk, "mkpart", "primary", current_end, swap_end])
            current_end = swap_end

        run_cmd(["parted", "-s", self.disk, "mkpart", "primary", current_end, "100%"])
        
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
            pass 
        else:
            log("Mode: Offline/Clone. Running Rsync...", "warn")
            excludes = ["--exclude=/proc/*", "--exclude=/sys/*", "--exclude=/dev/*", 
                        "--exclude=/run/*", "--exclude=/tmp/*", "--exclude=/mnt/*", 
                        f"--exclude={MOUNT_POINT}/*"]
            subprocess.run(["rsync", "-axHAWXS", "--numeric-ids", "--info=progress2"] + excludes + ["/", MOUNT_POINT], check=True)

    def _install_arch_pacstrap(self):
        log("Running pacstrap...", "info")
        pkgs = ["base", "linux", "linux-firmware", "base-devel", "nano", "networkmanager", "grub", "efibootmgr", "sudo"]
        if self.args.profile == "desktop": pkgs.extend(["plasma-meta", "konsole", "dolphin", "sddm"])
        run_cmd(["pacstrap", "-K", MOUNT_POINT] + pkgs, stream=True)
        with open(f"{MOUNT_POINT}/etc/fstab", "w") as f:
            subprocess.run(["genfstab", "-U", MOUNT_POINT], stdout=f)

    def _install_debian_debootstrap(self):
        log(f"Running debootstrap ({DEBIAN_RELEASE})...", "info")
        run_cmd(["debootstrap", "--arch", "amd64", DEBIAN_RELEASE, MOUNT_POINT, "http://deb.debian.org/debian"], stream=True)
        self._gen_fstab()
        with open(f"{MOUNT_POINT}/etc/apt/sources.list", "w") as f:
            f.write(f"deb http://deb.debian.org/debian {DEBIAN_RELEASE} main contrib non-free-firmware\n")
            f.write(f"deb http://deb.debian.org/debian-security {DEBIAN_RELEASE}-security main contrib non-free-firmware\n")
            f.write(f"deb http://deb.debian.org/debian {DEBIAN_RELEASE}-updates main contrib non-free-firmware\n")

    def setup_chroot_mounts(self):
        if shutil.which("arch-chroot"): return 
        log("Mounting API filesystems...", "info")
        for m in ["dev", "proc", "sys"]:
            target = os.path.join(MOUNT_POINT, m)
            os.makedirs(target, exist_ok=True)
            run_cmd(["mount", "--rbind", f"/{m}", target], ignore_error=True)
            run_cmd(["mount", "--make-rslave", target], ignore_error=True)
        shutil.copy("/etc/resolv.conf", f"{MOUNT_POINT}/etc/resolv.conf")

    def configure_system(self):
        log("Configuring System...", "info")
        
        log(f"Setting hostname to '{self.target_os}'...", "info")
        with open(f"{MOUNT_POINT}/etc/hostname", "w") as f:
            f.write(f"{self.target_os}\n")
        
        if self.args.timezone:
            tz_path = f"/usr/share/zoneinfo/{self.args.timezone}"
            if os.path.exists(f"{MOUNT_POINT}{tz_path}"):
                log(f"Setting timezone to {self.args.timezone}...", "info")
                run_cmd(f"ln -sf {tz_path} /etc/localtime", chroot=True)
                run_cmd("hwclock --systohc", chroot=True, ignore_error=True)
            else:
                log(f"Timezone {self.args.timezone} not found in target!", "warn")
        else:
            log("No timezone specified (UTC default).", "info")

        if self.target_os in ["arch", "bal"]:
            run_cmd("echo 'en_US.UTF-8 UTF-8' > /etc/locale.gen", chroot=True)
            run_cmd("locale-gen", chroot=True)
            run_cmd("systemctl enable NetworkManager", chroot=True, ignore_error=True)
            
            if not self.args.online or self.target_os == "bal":
                log("Offline Mode: Extracting Kernel...", "warn")
                kernel_dst = f"{MOUNT_POINT}/boot/vmlinuz-linux"
                os.makedirs(os.path.dirname(kernel_dst), exist_ok=True)
                search_patterns = ["/usr/lib/modules/*/vmlinuz", "/boot/vmlinuz-linux", "/run/archiso/bootmnt/arch/boot/x86_64/vmlinuz-linux"]
                
                kernel_src = None
                for pattern in search_patterns:
                    matches = glob.glob(pattern)
                    if matches:
                        matches.sort(reverse=True)
                        kernel_src = matches[0]
                        break
                
                if kernel_src and os.path.exists(kernel_src):
                    log(f"Found kernel: {kernel_src}", "success")
                    shutil.copy(kernel_src, kernel_dst)
                    os.chmod(kernel_dst, 0o644)
                else:
                    log(f"{COLORS['FAIL']}CRITICAL: Kernel not found!{COLORS['ENDC']}", "error")

                log("Sanitizing mkinitcpio presets...", "info")
                preset_dir = f"{MOUNT_POINT}/etc/mkinitcpio.d"
                if os.path.exists(preset_dir):
                    for preset in glob.glob(f"{preset_dir}/*.preset"):
                        try:
                            with open(preset, 'r') as f: content = f.read()
                            if "archiso.conf" in content:
                                content = content.replace("/etc/mkinitcpio.conf.d/archiso.conf", "/etc/mkinitcpio.conf")
                                with open(preset, 'w') as f: f.write(content)
                        except Exception: pass

                conf_path = f"{MOUNT_POINT}/etc/mkinitcpio.conf"
                try:
                    with open(conf_path, 'r') as f: config_data = f.read()
                    if "archiso" in config_data:
                        std_hooks = 'HOOKS=(base udev autodetect modconf kms keyboard keymap consolefont block filesystems fsck)'
                        lines = config_data.splitlines()
                        new_lines = []
                        for line in lines:
                            if line.strip().startswith("HOOKS") and "archiso" in line:
                                new_lines.append(f"# {line}")
                                new_lines.append(std_hooks)
                            else:
                                new_lines.append(line)
                        with open(conf_path, 'w') as f: f.write("\n".join(new_lines))
                except Exception: pass

                if os.path.exists(f"{MOUNT_POINT}/etc/mkinitcpio.conf.d/archiso.conf"):
                    os.remove(f"{MOUNT_POINT}/etc/mkinitcpio.conf.d/archiso.conf")

                log("Rebuilding initramfs...", "info")
                run_cmd("mkinitcpio -P", chroot=True, stream=True)

            if self.target_os == "bal":
                log("Applying Blue Archive Linux (BAL) specifics...", "info")
                run_cmd("systemctl enable sddm", chroot=True, ignore_error=True)
                log("Running /root/SilentSDDM/install.sh...", "info")
                run_cmd("bash /root/SilentSDDM/install.sh", chroot=True, stream=True)

        elif self.target_os == "debian":
            if not shutil.which("arch-chroot"): self.setup_chroot_mounts()
            env = {"DEBIAN_FRONTEND": "noninteractive"}
            run_cmd("apt-get update", chroot=True, env=env)
            run_cmd("apt-get install -y linux-image-amd64 linux-headers-amd64 locales grub-efi-amd64 network-manager sudo", chroot=True, env=env, stream=True)
            run_cmd("echo 'en_US.UTF-8 UTF-8' > /etc/locale.gen", chroot=True)
            run_cmd("locale-gen", chroot=True)

    def setup_users(self):
        pwd = self.args.passwd
        if pwd:
            log("Setting ROOT password...", "info")
            run_cmd(f"echo 'root:{pwd}' | chpasswd", chroot=True)
        else:
            log("No --passwd provided. Defaulting ROOT password to 'root'.", "warn")
            run_cmd("echo 'root:root' | chpasswd", chroot=True)

        if self.args.user:
            user = self.args.user
            log(f"Creating user '{user}'...", "info")
            if not run_cmd(f"useradd -m -G wheel -s /bin/bash {user}", chroot=True, ignore_error=True):
                log(f"User {user} might already exist or creation failed.", "warn")

            if pwd:
                log(f"Setting password for user '{user}'...", "info")
                run_cmd(f"echo '{user}:{pwd}' | chpasswd", chroot=True)

            log("Configuring sudo access...", "info")
            run_cmd("sed -i 's/^# %wheel ALL=(ALL:ALL) ALL/%wheel ALL=(ALL:ALL) ALL/' /etc/sudoers", chroot=True, ignore_error=True)
            log(f"User '{user}' added to wheel group with sudo access.", "success")


    def install_bal_extras(self):
        if self.target_os == "bal" and self.args.online:
            if not self.args.user:
                log("Skipping BAL Online Extras: No user provided.", "warn")
                return

            log("BAL Online Mode: Initializing Keyring...", "HEADER")
            
            # Initialize keyring to ensure pacman works correctly
            run_cmd("pacman-key --init", chroot=True)
            run_cmd("pacman-key --populate", chroot=True)
            
            # Note: Yay installation removed as requested due to root/sudo issues

    def run_custom_scripts(self):
        if not self.args.run: return
        log(f"Running Post-Install Command: {self.args.run}", "warn")
        run_cmd(self.args.run, chroot=True, stream=True)

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
        
        # Configure /etc/default/grub
        grub_path = f"{MOUNT_POINT}/etc/default/grub"
        if os.path.exists(grub_path):
            log("Configuring /etc/default/grub...", "info")
            
            pretty_name = self.target_os.capitalize()
            os_release = f"{MOUNT_POINT}/etc/os-release"
            if os.path.exists(os_release):
                try:
                    with open(os_release, 'r') as f:
                        for line in f:
                            if line.startswith("PRETTY_NAME="):
                                pretty_name = line.split("=", 1)[1].strip().strip('"').strip("'")
                                break
                except Exception: pass
            
            try:
                with open(grub_path, 'r') as f: lines = f.readlines()
                with open(grub_path, 'w') as f:
                    for line in lines:
                        if line.strip().startswith("GRUB_DISTRIBUTOR="):
                            f.write(f"GRUB_DISTRIBUTOR='{pretty_name}'\n")
                        elif self.target_os in ["arch", "bal"] and line.strip().startswith("GRUB_CMDLINE_LINUX_DEFAULT="):
                            new_line = line.replace("quiet", "").replace("  ", " ")
                            f.write(new_line)
                        else:
                            f.write(line)
            except Exception as e:
                log(f"Failed to edit grub config: {e}", "warn")

        target = "x86_64-efi" if self.uefi else "i386-pc"
        boot_id = self.target_os 

        if self.target_os == "debian":
            if self.uefi:
                run_cmd(f"grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id={boot_id} --recheck", chroot=True)
            else:
                run_cmd(f"grub-install --target=i386-pc {self.disk}", chroot=True)
            run_cmd("update-grub", chroot=True)
        else:
            cmd = ["grub-install", f"--target={target}", f"--bootloader-id={boot_id}", "--recheck"]
            if self.uefi: cmd.append("--efi-directory=/boot/efi")
            else: cmd.append(self.disk)
            
            run_cmd(cmd, chroot=True)
            run_cmd("grub-mkconfig -o /boot/grub/grub.cfg", chroot=True)

    def finalize(self):
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
    parser.add_argument("--target", default="arch", choices=["arch", "gentoo", "debian", "generic", "bal"])
    parser.add_argument("--online", action="store_true", help="Use pacstrap/debootstrap instead of cloning (Except BAL)")
    parser.add_argument("--init", choices=["systemd", "openrc"], default="systemd")
    parser.add_argument("--profile", choices=["cli", "desktop"], default="cli")
    
    parser.add_argument("--user", help="Create a new user")
    parser.add_argument("--passwd", help="Password for the new user AND root")
    parser.add_argument("--run", help="Custom command to run inside chroot after install")
    parser.add_argument("--timezone", help="Set Timezone (e.g. Asia/Ho_Chi_Minh)")
    parser.add_argument("--debug", action="store_true", help="Enable verbose output")
    
    parser.add_argument("--i-am-very-stupid", action="store_true")
    
    args = parser.parse_args()
    
    global DEBUG_MODE
    DEBUG_MODE = args.debug

    if os.geteuid() != 0: sys.exit("Run as root.")
    if not args.disk and not (args.boot and args.rootfs):
        sys.exit("Error: Must specify --disk OR (--boot and --rootfs)")

    ChimeraInstaller(args).run()

if __name__ == "__main__":
    main()
