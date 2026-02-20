# Maintainer: minhmc2007 <your-email@example.com>
pkgname=chimera-installer
pkgver=1.0.0
pkgrel=1
pkgdesc="A custom Linux installer written in Python and PySide6"
arch=('any')
url="https://github.com/minhmc2007/Chimera" # Change this to your actual repo if applicable
license=('MIT') # Assuming MIT based on the LICENSE file presence
depends=('python' 'pyside6')
makedepends=()

# If you are building this locally inside the Chimera folder, you can just use local sources:
source=("local://chimera.py"
        "local://chimera-gui.py"
        "local://logo.png"
        "local://backg.png"
        "local://README.md"
        "local://LICENSE")
sha256sums=('SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP')

# NOTE: If you push this to GitHub, change the source array to:
# source=("git+https://github.com/minhmc2007/Chimera.git")
# And change the package() function to `cd "$srcdir/Chimera"` first.

package() {
    # 1. Create necessary system directories
    install -dm755 "$pkgdir/usr/share/chimera"
    install -dm755 "$pkgdir/usr/bin"
    install -dm755 "$pkgdir/usr/share/applications"
    install -dm755 "$pkgdir/usr/share/licenses/$pkgname"
    install -dm755 "$pkgdir/usr/share/doc/$pkgname"
    install -dm755 "$pkgdir/usr/share/pixmaps"

    # 2. Install application files to /usr/share/chimera
    install -m755 "$srcdir/chimera.py" "$pkgdir/usr/share/chimera/chimera.py"
    install -m755 "$srcdir/chimera-gui.py" "$pkgdir/usr/share/chimera/chimera-gui.py"
    install -m644 "$srcdir/logo.png" "$pkgdir/usr/share/chimera/logo.png"
    install -m644 "$srcdir/backg.png" "$pkgdir/usr/share/pixmaps/backg.png"

    # 3. Create executable wrappers in /usr/bin
    # This ensures the Python script's working directory has access to the image files.
    
    # CLI wrapper
    cat <<EOF > "$pkgdir/usr/bin/chimera"
#!/bin/sh
cd /usr/share/chimera
exec python3 chimera.py "\$@"
EOF
    chmod +x "$pkgdir/usr/bin/chimera"

    # GUI wrapper
    cat <<EOF > "$pkgdir/usr/bin/chimera-gui"
#!/bin/sh
cd /usr/share/chimera
exec python3 chimera-gui.py "\$@"
EOF
    chmod +x "$pkgdir/usr/bin/chimera-gui"

    # 4. Create Desktop Entry for GUI
    cat <<EOF > "$pkgdir/usr/share/applications/chimera-gui.desktop"
[Desktop Entry]
Name=Chimera System Installer
Comment=Install the operating system to disk
Exec=/usr/bin/chimera-gui
Icon=/usr/share/chimera/logo.png
Terminal=false
Type=Application
Categories=System;Settings;
EOF

    # 5. Install Documentation and License
    install -m644 "$srcdir/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
    install -m644 "$srcdir/README.md" "$pkgdir/usr/share/doc/$pkgname/README.md"
}
