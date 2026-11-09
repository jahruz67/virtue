# Keep Python sources portable across Fedora Python versions. The payload has no
# bundled native libraries; runtime dependencies are declared explicitly below.
%global __os_install_post %{nil}

Name:           virtue-desktop
Version:        %{virtue_version}
Release:        1
Summary:        Windows virtual machine desktop (engineering preview)
License:        MIT
BuildArch:      x86_64
AutoReqProv:    no

Requires:       python3 >= 3.11
Requires:       python3-pyside6 >= 6.8.3
Requires:       qemu-system-x86 >= 8.0
Requires:       qemu-ui-gtk
Requires:       qemu-img
Requires:       edk2-ovmf
Requires:       swtpm
Requires:       xdg-utils
Requires:       flatpak
Requires:       vulkan-tools
Requires:       swtpm-tools
Requires:       /usr/bin/pkexec
Requires:       mate-polkit
Requires:       /usr/bin/setfacl
Requires:       /usr/bin/xorriso
Requires:       /usr/bin/wimlib-imagex
Requires:       ( /usr/bin/7z or /usr/bin/7zz )
Requires:       mesa-vulkan-drivers

%description
Linux-first desktop for real QEMU/KVM lifecycle, private VM storage, recovery,
and required Yttrium graphics with automatic guest setup. Windows media and licenses are not included.
Uses Fedora's system Python and PySide6 packages; Qt is not bundled.

%prep

%build

%install
mkdir -p "%{buildroot}"
cp -a "%{virtue_payload}/." "%{buildroot}/"

%files
%defattr(-,root,root,-)
/opt/virtue
/usr/bin/virtue
/usr/libexec/virtue-setup-host
/usr/lib/udev/rules.d/70-virtue-kvm.rules
/usr/share/applications/virtue.desktop
/usr/share/icons/hicolor/scalable/apps/virtue.svg
