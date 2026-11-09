# Building and packaging

Pushing a `v*` tag (for example, `v0.1.0`) runs the **Test and package** workflow.
After its Linux and Windows jobs succeed, it creates a GitHub prerelease with the
Linux-built DEB and RPM, their SHA-256 checksums, Python wheel, and source archive attached.
Update the versions in `pyproject.toml` and `tools/build_deb.py` before tagging
a new version. Rerunning the workflow for the same tag updates its release assets.
Branch pushes and pull requests upload workflow artifacts without publishing a release.

For pip and DEB builds, Python 3.11–3.13, pip, and a virtual environment are required. Qt is pinned to
PySide6 Essentials 6.8.3 and shiboken6 6.8.3. Guest execution uses distribution
QEMU 8+ with GTK; the Yttrium profile uses the separately installed upstream
Flatpak host bundle. `resources/yttrium-stack.json` records researched revisions
and upstream asset checksums; it is not a claim that hardware was tested here.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[test]'
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python -m virtue --diagnose
.venv/bin/python -m build
.venv/bin/python tools/build_deb.py
sudo apt install ./dist/virtue-desktop_0.1.0_amd64.deb
virtue
```

The DEB includes the application and official dynamically linked Linux Qt wheels
under `/opt/virtue/lib`, plus setup guides and guest scripts under
`/opt/virtue/share`. QEMU, TPM, UEFI, Vulkan and GPU drivers are distribution/host
dependencies. The package launcher uses Python 3.11–3.13's ABI-compatible Qt
bindings; Python 3.14 is excluded because this pinned Qt version predates it.
The DEB targets amd64 Ubuntu 24.04+ / Debian 12+; actual install-and-launch
validation on each distribution is still required. It is an engineering preview.

The DEB builder can run on Windows: it downloads Linux wheels without executing
them and writes standard ar/control/data archives. This proves packaging structure,
not Linux runtime compatibility. Cache the wheelhouse and pass `--offline` to reuse
it. Archive mtimes, ownership and gzip timestamps are normalized; the exact Qt
wheel hashes are embedded in `bundled-wheels.json`. The application wheel can vary
with source tree content and build backend version, so bit-identical builds require
an identical checkout and build environment. No downloaded binaries are committed.

For Fedora x86_64, install the RPM from the release with DNF:

```sh
sudo dnf install ./virtue-desktop-0.1.0-1.x86_64.rpm
virtue
```

The RPM uses Fedora's system Python (3.11+) and `python3-pyside6` (6.8.3+),
including its Qt libraries, rather than bundling the Python-version-limited Qt
wheels used by the DEB. DNF installs QEMU, UEFI, TPM and desktop dependencies.
Building the RPM requires Linux, Python 3.11+, `build`, and `rpmbuild`:

```sh
sudo dnf install rpm-build python3-pip
python3 -m venv .venv
.venv/bin/python -m pip install build
.venv/bin/python tools/build_rpm.py
```

On Ubuntu, the builder package is `rpm`. The RPM is an engineering preview;
building it on CI does not validate installation, Fedora's system Qt, or VM
execution on Fedora. Ensure `/dev/kvm` permissions on the target host.
Distribution names/firmware layouts vary; diagnostics reads QEMU firmware
descriptors and recognizes common matched firmware pairs. The frontend runs
under Wayland and X11 using Qt's platform plugins; QEMU uses GTK.

The built wheel supports desktop launch and diagnostics on Windows for development,
but VM execution deliberately raises an actionable Linux/KVM requirement. No Windows
host hypervisor backend or macOS guest backend is claimed.

