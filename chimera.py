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
DEBIAN_RELEASE = "trixie" # Stable

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

def check_connection():
    # Use multiple fallback endpoints to prevent failure on strictly filtered networks
    for host, port in [("8.8.8.8", 53), ("1.1.1.1", 53), ("google.com", 443)]:
        try:
            socket.create_connection((host, port), timeout=3)
            return True
        except OSError:
            continue
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
        self.debug = args.debug
        self.uefi = os.path.exists("/sys/firmware/efi")
        self.target_os = args.target.lower()
        self.disk = args.disk if args.disk else self._detect_disk(args.rootfs)

        if self.target_os == "gentoo":
            sys.exit(f"{COLORS['FAIL']}Error: Gentoo target is currently not implemented.{COLORS['ENDC']}")

        # Secure password handling via Environment Variable or secure prompt
        self.password = os.environ.get("CHIMERA_PASS", self.args.passwd)
        if self.args.passwd:
            log("Warning: Passing password via --passwd is insecure (visible in process list). Consider using CHIMERA_PASS env var or interactive prompt.", "warn")
            
        if self.args.user and not self.password:
            self.password = getpass.getpass(f"Enter password for root and new user '{self.args.user}': ")
            if not self.password:
                sys.exit(f"{COLORS['FAIL']}Error: Password cannot be empty when creating a user.{COLORS['ENDC']}")
            
        if self.args.user and not re.match(r"^[a-z_][a-z0-9_-]{0,31}$", self.args.user):
            sys.exit(f"{COLORS['FAIL']}Error: Invalid username format.{COLORS['ENDC']}")
            
        if self.args.timezone:
            tz_path = os.path.abspath(f"/usr/share/zoneinfo/{self.args.timezone}")
            if not tz_path.startswith("/usr/share/zoneinfo/"):
                sys.exit(f"{COLORS['FAIL']}Error: Invalid timezone path.{COLORS['ENDC']}")

        if self.target_os == "arch" and self.args.online and not shutil.which("pacstrap"):
            sys.exit(f"{COLORS['FAIL']}Error: 'pacstrap' not found. Install 'arch-install-scripts'.{COLORS['ENDC']}")
        if self.target_os == "debian" and self.args.online and not shutil.which("debootstrap"):
            sys.exit(f"{COLORS['FAIL']}Error: 'debootstrap' not found. Please install it.{COLORS['ENDC']}")

        if self.args.swap:
            if self.args.disk:
                if not re.match(r"^\d+[MGmg]$", self.args.swap):
                    sys.exit(f"{COLORS['FAIL']}Error: --swap must be a size (e.g. 2G, 512M) in auto-partition mode.{COLORS['ENDC']}")
            else:
                if not os.path.exists(self.args.swap):
                    sys.exit(f"{COLORS['FAIL']}Error: Swap partition {self.args.swap} not found.{COLORS['ENDC']}")

        if not self.disk and not self.uefi:
            sys.exit(f"{COLORS['FAIL']}Error: Could not detect root disk for bootloader. Please provide --disk.{COLORS['ENDC']}")

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
            
        if not show_output:
            kwargs['stdout'] = subprocess.PIPE
            kwargs['stderr'] = subprocess.PIPE

        proc = subprocess.run(cmd, **kwargs)
        
        if proc.returncode != 0:
            if check:
                log(f"Command Failed: {cmd}", "error")
                if not show_output and proc.stderr:
                    print(f"{COLORS['FAIL']}STDERR: {proc.stderr.decode().strip()}{COLORS['ENDC']}")
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
            
        if not shutil.which("arch-chroot") and not shutil.which("chroot"):
            sys.exit(f"{COLORS['FAIL']}Error: Missing chroot environment (chroot or arch-chroot).{COLORS['ENDC']}")

    def is_host_disk(self, disk_path):
        try:
            root_source = subprocess.check_output(["findmnt", "/", "-o", "SOURCE", "-n"], stderr=subprocess.DEVNULL).decode().strip()
            if not root_source: return False
            
            root_source = os.path.realpath(root_source)
            target_path = os.path.realpath(disk_path)
            
            if root_source == target_path: return True
                
            parent = subprocess.check_output(["lsblk", "-no", "pkname", root_source], stderr=subprocess.DEVNULL).decode().strip()
            if parent and os.path.realpath(f"/dev/{parent}") == target_path:
                return True
        except Exception:
            pass
        return False

    def run(self):
        try:
            if self.args.dry_run:
                self.tools_check()
                log("DRY RUN MODE. The following actions would be taken:", "info")
                if self.args.disk: log(f" - Wipe and partition {self.args.disk}", "info")
                else: log(f" - Use existing partitions: Root={self.args.rootfs}, Boot={self.args.boot}, Swap={self.args.swap}", "info")
                log(f" - Install {self.target_os.capitalize()} via {'online' if self.args.online else 'offline'} mode", "info")
                log(f" - Configure bootloader, users, and locales", "info")
                sys.exit(0)

            self.welcome()
            self.tools_check()
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
            if not self.args.dry_run:
                self.cleanup()

    def welcome(self):
        if shutil.which("clear"): subprocess.run(["clear"])
        log(f"Chimera Installer - {self.target_os.upper()} Edition", "HEADER")
        log(f"Target Disk: {self.disk} | Boot Mode: {'UEFI' if self.uefi else 'BIOS'}", "info")
        
        if self.debug:
            log("Debug Mode: ON (Verbose output enabled)", "DEBUG")
            log("Current Disk Layout:", "DEBUG")
            subprocess.run(["lsblk"])
            print("-" * 40)

        if self.args.user: log(f"User Setup: {self.args.user}", "info")
        if self.password: log("Password securely configured for Root (and User).", "info")
        if self.args.timezone: log(f"Timezone: {self.args.timezone}", "info")
        
        if (self.target_os in ["arch", "debian", "bal"] and not self.args.online) or self.target_os == "bal":
            print(f"\n{COLORS['WARN']}WARNING: Offline/Clone Install Mode Active.{COLORS['ENDC']}")

    def safety_check(self):
        if self.args.disk and self.is_host_disk(self.args.disk):
            sys.exit(f"{COLORS['FAIL']}CRITICAL: You are targeting the active host disk for wiping. Aborting.{COLORS['ENDC']}")
            
        if not self.args.disk and self.args.rootfs and self.is_host_disk(self.args.rootfs):
            sys.exit(f"{COLORS['FAIL']}CRITICAL: Target rootfs is on the active host disk. Aborting.{COLORS['ENDC']}")

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
        if self.args.online:
            if not check_connection():
                log("Network required. Trying nmtui...", "warn")
                if shutil.which("nmtui"): subprocess.run(["nmtui"])
                if not check_connection(): raise RuntimeError("No Internet Connection.")

    def partition_handler(self):
        log("Preparing Partitions...", "info")
        self.run_cmd(["umount", "-R", MOUNT_POINT], check=False)
        
        if self.args.disk:
            # Selectively clear swap signatures located ON the target disk only (-l removes tree drawing artifacts)
            try:
                swaps = subprocess.check_output(["lsblk", "-nlo", "NAME,FSTYPE", self.args.disk], stderr=subprocess.DEVNULL).decode().splitlines()
                for line in swaps:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == "swap":
                        dev_name = parts[0].strip()
                        self.run_cmd(["swapoff", f"/dev/{dev_name}"], check=False)
            except Exception: pass
            
            self._auto_partition_disk()
        else:
            if self.args.swap:
                self.run_cmd(["swapoff", self.args.swap], check=False)

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
            # We purposely do not "swapon" it on the host to avoid swapping host workloads into the target's partition

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
        
        if any(self.disk.startswith(prefix) for prefix in ["/dev/nvme", "/dev/mmc", "/dev/loop", "/dev/md"]):
            prefix = f"{self.disk}p"
        else:
            prefix = self.disk
        
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
        log(f"Layout: Boot={self.args.boot}, Root={self.args.rootfs}", "success")

    def install_base(self):
        log(f"Installing Base System ({self.target_os})...", "info")
        
        if self.target_os == "arch" and self.args.online:
            self._install_arch_pacstrap()
        elif self.target_os == "debian" and self.args.online:
            self._install_debian_debootstrap()
        else:
            log("Mode: Offline/Clone. Running Rsync...", "warn")
            excludes = ["--exclude=/proc/*", "--exclude=/sys/*", "--exclude=/dev/*", 
                        "--exclude=/run/*", "--exclude=/tmp/*", "--exclude=/mnt/*", 
                        f"--exclude={MOUNT_POINT}/*"]
            self.run_cmd(["rsync", "-axHAWXS", "--numeric-ids", "--info=progress2"] + excludes + ["/", MOUNT_POINT])

        log("Generating fstab...", "info")
        self._gen_fstab()

    def _install_arch_pacstrap(self):
        log("Running pacstrap...", "info")
        pkgs = ["base", "linux", "linux-firmware", "base-devel", "nano", "networkmanager", "grub", "efibootmgr", "sudo"]
        if self.args.profile == "desktop": pkgs.extend(["plasma-meta", "konsole", "dolphin", "sddm"])
        self.run_cmd(["pacstrap", "-K", MOUNT_POINT] + pkgs, stream=True)

    def _install_debian_debootstrap(self):
        log(f"Running debootstrap ({DEBIAN_RELEASE})...", "info")
        # Note: debootstrap is hardcoded to --arch amd64. 
        # This aligns with other x86_64 bootloader paths hardcoded in the original project structure.
        self.run_cmd(["debootstrap", "--arch", "amd64", DEBIAN_RELEASE, MOUNT_POINT, "http://deb.debian.org/debian"], stream=True)
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
            self.run_cmd(["mount", "--rbind", f"/{m}", target], check=False)
            self.run_cmd(["mount", "--make-rslave", target], check=False)
        with open(f"{MOUNT_POINT}/etc/resolv.conf", "w") as f:
            f.write("nameserver 1.1.1.1\nnameserver 8.8.8.8\n")

    def _setup_locales(self):
        self.run_cmd(["sed", "-i", "s/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/", "/etc/locale.gen"], chroot=True)
        if not self.run_cmd(["grep", "-q", "^en_US.UTF-8 UTF-8", "/etc/locale.gen"], chroot=True, check=False):
            self.run_cmd(["sh", "-c", "echo 'en_US.UTF-8 UTF-8' >> /etc/locale.gen"], chroot=True)
        self.run_cmd(["locale-gen"], chroot=True)

    def configure_system(self):
        log("Configuring System...", "info")
        
        log(f"Setting hostname to '{self.target_os}'...", "info")
        with open(f"{MOUNT_POINT}/etc/hostname", "w") as f:
            f.write(f"{self.target_os}\n")
        
        if self.args.timezone:
            tz_path = os.path.abspath(f"/usr/share/zoneinfo/{self.args.timezone}")
            if not os.path.exists(f"{MOUNT_POINT}{tz_path}"):
                log(f"Timezone {self.args.timezone} not found in target!", "warn")
            else:
                log(f"Setting timezone to {self.args.timezone}...", "info")
                self.run_cmd(["ln", "-sf", tz_path, "/etc/localtime"], chroot=True)
                self.run_cmd(["hwclock", "--systohc"], chroot=True, check=False)
        else:
            log("No timezone specified (UTC default).", "info")

        if self.target_os in ["arch", "bal"]:
            self._setup_locales()
            self.run_cmd(["systemctl", "enable", "NetworkManager"], chroot=True, check=False)
            
            if not self.args.online or self.target_os == "bal":
                log("Offline Mode: Extracting Kernel...", "warn")
                kernel_dst = f"{MOUNT_POINT}/boot/vmlinuz-linux"
                os.makedirs(os.path.dirname(kernel_dst), exist_ok=True)
                search_patterns = ["/usr/lib/modules/*/vmlinuz", "/boot/vmlinuz-linux", "/run/archiso/bootmnt/arch/boot/x86_64/vmlinuz-linux"]
                
                def kernel_version(path):
                    match = re.search(r'(\d+)\.(\d+)\.(\d+)', path)
                    return tuple(map(int, match.groups())) if match else (0,0,0)

                kernel_src = None
                for pattern in search_patterns:
                    matches = glob.glob(pattern)
                    if matches:
                        matches.sort(key=kernel_version, reverse=True)
                        kernel_src = matches[0]
                        break
                
                if kernel_src and os.path.exists(kernel_src):
                    log(f"Found kernel: {kernel_src}", "success")
                    shutil.copy(kernel_src, kernel_dst)
                    os.chmod(kernel_dst, 0o644)
                else:
                    raise RuntimeError("CRITICAL: Kernel not found! Cannot proceed with initramfs creation.")

                log("Sanitizing mkinitcpio presets...", "info")
                preset_dir = f"{MOUNT_POINT}/etc/mkinitcpio.d"
                if os.path.exists(preset_dir):
                    for preset in glob.glob(f"{preset_dir}/*.preset"):
                        try:
                            with open(preset, 'r') as f: content = f.read()
                            if "archiso.conf" in content:
                                content = content.replace("/etc/mkinitcpio.conf.d/archiso.conf", "/etc/mkinitcpio.conf")
                                with open(preset, 'w') as f: f.write(content)
                        except OSError as e: log(f"Failed to clean up {preset}: {e}", "warn")

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
                except OSError as e: log(f"Failed to reset mkinitcpio HOOKS: {e}", "warn")

                if os.path.exists(f"{MOUNT_POINT}/etc/mkinitcpio.conf.d/archiso.conf"):
                    os.remove(f"{MOUNT_POINT}/etc/mkinitcpio.conf.d/archiso.conf")

                log("Rebuilding initramfs...", "info")
                self.run_cmd(["mkinitcpio", "-P"], chroot=True, stream=True)

            if self.target_os == "bal":
                log("Applying Blue Archive Linux (BAL) specifics...", "info")
                self.run_cmd(["systemctl", "enable", "sddm"], chroot=True, check=False)
                log("Running /root/SilentSDDM/install.sh...", "info")
                self.run_cmd(["bash", "/root/SilentSDDM/install.sh"], chroot=True, stream=True)
                self.run_cmd(["touch", "/etc/bal-installed"], chroot=True, check=False)

        elif self.target_os == "debian":
            if not shutil.which("arch-chroot"): self.setup_chroot_mounts()
            env = {"DEBIAN_FRONTEND": "noninteractive"}
            self.run_cmd(["apt-get", "update"], chroot=True, env=env)
            pkgs = ["linux-image-amd64", "linux-headers-amd64", "locales", "network-manager", "sudo"]
            if self.uefi: pkgs.append("grub-efi-amd64")
            else: pkgs.append("grub-pc")
            
            self.run_cmd(["apt-get", "install", "-y"] + pkgs, chroot=True, env=env, stream=True)
            self._setup_locales()

    def setup_users(self):
        pwd = self.password
        if pwd:
            log("Setting ROOT password...", "info")
            self.run_cmd(["chpasswd"], chroot=True, input_data=f"root:{pwd}\n")
        else:
            log("No password provided. Defaulting ROOT password to 'root'.", "warn")
            self.run_cmd(["chpasswd"], chroot=True, input_data="root:root\n")

        if self.args.user:
            user = self.args.user
            log(f"Creating user '{user}'...", "info")
            sudo_group = "sudo" if self.target_os == "debian" else "wheel"
            self.run_cmd(["useradd", "-m", "-G", sudo_group, "-s", "/bin/bash", user], chroot=True)

            if pwd:
                log(f"Setting password for user '{user}'...", "info")
                self.run_cmd(["chpasswd"], chroot=True, input_data=f"{user}:{pwd}\n")

            log("Configuring sudo access...", "info")
            sudoers_file = f"{MOUNT_POINT}/etc/sudoers.d/99_installer"
            os.makedirs(f"{MOUNT_POINT}/etc/sudoers.d", exist_ok=True)
            with open(sudoers_file, 'w') as f:
                f.write(f"%{sudo_group} ALL=(ALL:ALL) ALL\n")
            os.chmod(sudoers_file, 0o440)
            
            try:
                self.run_cmd(["visudo", "-c", "-f", "/etc/sudoers.d/99_installer"], chroot=True)
                log(f"User '{user}' added to {sudo_group} group with sudo access.", "success")
            except subprocess.CalledProcessError:
                os.remove(sudoers_file)
                raise RuntimeError("Failed to validate sudoers template layout. Installation aborted!")

    def install_bal_extras(self):
        if self.target_os == "bal" and self.args.online:
            if not self.args.user:
                log("Skipping BAL Online Extras: No user provided.", "warn")
                return

            log("BAL Online Mode: Initializing Keyring...", "HEADER")
            self.run_cmd(["pacman-key", "--init"], chroot=True)
            self.run_cmd(["pacman-key", "--populate"], chroot=True)

    def run_custom_scripts(self):
        if not self.args.run: return
        log(f"Running Post-Install Command: {self.args.run}", "warn")
        self.run_cmd(self.args.run, chroot=True, stream=True)

    def _gen_fstab(self):
        if shutil.which("genfstab"):
            with open(f"{MOUNT_POINT}/etc/fstab", "w") as f:
                subprocess.run(["genfstab", "-U", MOUNT_POINT], stdout=f, check=True)
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
                if self.args.swap:
                    swap_uuid = get_blk_value(self.args.swap, 'UUID')
                    if swap_uuid:
                        f.write(f"UUID={swap_uuid} none swap sw 0 0\n")

    def install_bootloader(self):
        log("Installing Bootloader...", "info")
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
                except OSError: pass
            
            try:
                with open(grub_path, 'r') as f: lines = f.readlines()
                with open(grub_path, 'w') as f:
                    for line in lines:
                        if line.strip().startswith("GRUB_DISTRIBUTOR="):
                            f.write(f"GRUB_DISTRIBUTOR={shlex.quote(pretty_name)}\n")
                        elif self.target_os in ["arch", "bal"] and line.strip().startswith("GRUB_CMDLINE_LINUX_DEFAULT="):
                            f.write(line.replace("quiet", "").replace("  ", " "))
                        else:
                            f.write(line)
            except OSError as e:
                log(f"Failed to edit grub config: {e}", "warn")

        boot_id = self.target_os 
        if self.target_os == "debian":
            if self.uefi:
                self.run_cmd(["grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi", f"--bootloader-id={boot_id}", "--recheck"], chroot=True)
            else:
                self.run_cmd(["grub-install", "--target=i386-pc", self.disk], chroot=True)
            self.run_cmd(["update-grub"], chroot=True)
        else:
            cmd = ["grub-install", f"--target={'x86_64-efi' if self.uefi else 'i386-pc'}", f"--bootloader-id={boot_id}", "--recheck"]
            if self.uefi: cmd.append("--efi-directory=/boot/efi")
            else: cmd.append(self.disk)
            
            self.run_cmd(cmd, chroot=True)
            self.run_cmd(["grub-mkconfig", "-o", "/boot/grub/grub.cfg"], chroot=True)

    def finalize(self):
        self.run_cmd(["systemd-machine-id-setup"], chroot=True, check=False)

    def cleanup(self):
        log("Cleaning up...", "info")
        self.run_cmd(["umount", "-R", MOUNT_POINT], check=False)

# --- Entry Point ---
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--disk", help="Auto Partition Mode (e.g., /dev/sda)")
    parser.add_argument("--boot", help="Manual: Boot partition")
    parser.add_argument("--rootfs", help="Manual: Root partition")
    parser.add_argument("--swap", help="Swap size (Auto) or partition (Manual)")
    parser.add_argument("--target", default="arch", choices=["arch", "gentoo", "debian", "generic", "bal"])
    parser.add_argument("--online", action="store_true", help="Use pacstrap/debootstrap instead of cloning (Except BAL)")
    parser.add_argument("--profile", choices=["cli", "desktop"], default="cli")
    
    parser.add_argument("--user", help="Create a new user")
    parser.add_argument("--passwd", help="Password for the new user AND root (Avoid using, use CHIMERA_PASS env)")
    parser.add_argument("--run", help="Custom command to run inside chroot after install")
    parser.add_argument("--timezone", help="Set Timezone (e.g. Asia/Ho_Chi_Minh)")
    parser.add_argument("--debug", action="store_true", help="Enable verbose output")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without modifying disks")
    
    parser.add_argument("--i-am-very-stupid", action="store_true")
    
    args = parser.parse_args()

    if os.geteuid() != 0: sys.exit("Run as root.")
    if not args.disk and not (args.boot and args.rootfs):
        sys.exit("Error: Must specify --disk OR (--boot and --rootfs)")

    # Implement locking to block concurrent script executions corrupting disks
    lock_file = "/var/lock/chimera_installer.lock"
    try:
        lock_fd = open(lock_file, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f"{COLORS['FAIL']}Error: Another instance of Chimera Installer is currently running.{COLORS['ENDC']}")
    except Exception as e:
        log(f"Warning: Could not acquire lock: {e}", "warn")

    ChimeraInstaller(args).run()

if __name__ == "__main__":
    main()
