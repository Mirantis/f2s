%define name f2s
%{!?version: %define version 0.0.1}
%{!?release: %define release 1}

Name: %{name}
Version: %{version}
Release: %{release}
Source0: %{name}-%{version}.tar.gz
Summary: Fuel 2 Solar stuff
URL:     http://mirantis.com
License: Apache-2
Group: Development/Libraries
BuildRoot: %{_tmppath}/%{name}-%{version}-buildroot
Prefix: %{_prefix}
BuildRequires:  git
BuildRequires: python-setuptools
BuildRequires: python-pbr
BuildArch: noarch

Requires:    python
Requires:    python-click >= 6
Requires:    python-fuelclient >= 9
Requires:    python-networkx >= 1.10
Requires:    PyYAML >= 3.1

%description
F2S script converts tasks.yaml + fuel-library actions into solar resources,
vrs, and events.

%prep
%setup -cq -n %{name}-%{version}

%build
cd %{_builddir}/%{name}-%{version} && PBR_VERSION=%{version} python setup.py build

%install
cd %{_builddir}/%{name}-%{version} && PBR_VERSION=%{version} python setup.py install --single-version-externally-managed -O1 --root=$RPM_BUILD_ROOT --record=%{_builddir}/%{name}-%{version}/INSTALLED_FILES

%clean
rm -rf $RPM_BUILD_ROOT

%files -f %{_builddir}/%{name}-%{version}/INSTALLED_FILES

