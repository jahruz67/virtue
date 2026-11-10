# Third-party components

Virtue source is MIT licensed. It invokes separately installed virtualization
components rather than incorporating their source or distributing Windows media.
The DEB bundles the official PySide6 Essentials/shiboken6 Linux wheels unchanged
except unpacking them into an application-private import directory. Their license
texts remain included in the wheel `.dist-info` and Qt license directories.
The Fedora RPM uses distribution-provided PySide6/Qt instead of bundling these
wheels; the distribution packages supply their own licenses and source material.

* Qt 6 / PySide6 / shiboken6: LGPLv3/GPLv3/commercial terms as applicable. Qt is
  dynamically linked, with no restriction in Virtue against replacing or debugging
  those libraries. Obtain the exact 6.8.3 source and license material from
  https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/ and
  https://download.qt.io/archive/qt/6.8/6.8.3/single/. Qt modules and third-party
  libraries retain their own notices. See https://doc.qt.io/qt-6/licensing.html.
* QEMU: GPLv2 and component-specific licenses; installed by the host package manager.
  https://www.qemu.org/docs/master/about/license.html
* OVMF / EDK II: BSD-2-Clause-Patent and component-specific licenses; host package.
  https://github.com/tianocore/edk2
* swtpm / libtpms: BSD-style licenses; host packages.
  https://github.com/stefanberger/swtpm and https://github.com/stefanberger/libtpms
* Yttrium driver: BSD-3-Clause, MPL-2.0 for marked viogpu3d changes,
  and inherited component notices.
  https://github.com/arehnman/yttrium-virtio-gpu
* Mesa / virglrenderer / Yttrium host bundle: component-specific open-source
  licenses. They are installed separately; their sources and exact researched
  revisions are linked in `resources/yttrium-stack.json` and `docs/yttrium.md`.

Windows, SOLIDWORKS, benchmarks and drivers must be obtained under their own terms.
No Microsoft installer, product key, proprietary CAD package or activation bypass
is shipped. Redistribution of modified third-party bundles requires honoring
their notices and source obligations independently of Virtue's source license.

The Mac workflow builds its native QEMU/virglrenderer, Neptune/DXMT and
ANGLE/libepoxy components and bundles ARM firmware, TPM and media tools.
`resources/macos-runtime.json` records its source revisions and the SHA-256
hashes of the published Triton/ARM VirtIO media. The runtime builder records
the actual revisions and Homebrew dependency metadata, includes license notices,
and publishes a native source archive alongside the app. These binaries are not
included in this source checkout. The Mac app builder verifies the runtime
inventory and checksums; it does not substitute Linux artifacts or download
proprietary D3DMetal. See
`docs/macos-validation.md` for the runtime contract and upstream references.
