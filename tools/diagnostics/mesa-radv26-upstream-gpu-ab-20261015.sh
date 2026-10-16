#!/usr/bin/env bash
# Upstream driver validation, NOT a game-specific workaround:
# one unchanged shadPS4 binary, official Mesa 26.2.5 RADV built in HOME.
# Never replace system Mesa, run apt/sudo, alter X11/Sunshine, or stop SSH.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
LOG_ROOT="$HOME/projects/shadps4/logs"
TREE="$ROOT/tests/got-generic-srt-680029f3"
EMULATOR="$TREE/build/shadps4"
EMULATOR_SOURCE="$TREE/source"
BASE_PROFILE="$ROOT/play/np-init-affinity-main-20261015-195552"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
EMU_SHA="b30e0d1391213975e36717ab3b769e71470da56a3d8bba3431befa0389faba64"
EMU_COMMIT="2af10e40512da8bef86684a762c5626bcf0a5955"
GAME_SHA="0324f71fd64f80dfe2ad3adbd7e8c00155c4b110f4703354f58ffbd269a00de6"
MESA_VERSION="26.2.5"
MESA_SHA="794b3d1729075dae7ebd47c7283b7ca0cdb5dec668f7f11cc0a5d82f6138a0a5"
MESA_WORK="$ROOT/tests/mesa-upstream-radv-26.2.5"
MESA_TARBALL="$MESA_WORK/mesa-26.2.5.tar.xz"
MESA_SOURCE="$MESA_WORK/src/mesa-26.2.5"
MESA_BUILD="$MESA_WORK/build"
MESA_PREFIX="$MESA_WORK/install"
mkdir -p "$LOG_ROOT"
if [[ "$#" == 0 ]]; then
    RUN="$LOG_ROOT/got-upstream-radv26-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$RUN/info" "$RUN/screenshots"
    touch "$RUN/build.log" "$RUN/game.log"
    nohup bash "$0" --worker "$RUN" >"$RUN/runner.log" 2>&1 </dev/null &
    echo "WORKER_PID=$!"
    echo "FOLLOW=tail -f $RUN/runner.log"
    echo "ARCHIVE=$RUN.tar.gz"
    echo "NO_SYSTEM_DRIVER_CHANGE; NO_EMULATOR_PATCHES; SSH=UNCHANGED"
    exit 0
fi
[[ "$#" == 2 && "$1" == --worker ]] || exit 2
RUN="$2"
PLAY="$ROOT/play/$(basename "$RUN")"
ARCHIVE="$RUN.tar.gz"
collect_radv() {
    set +e
    if [[ -d "$PLAY" ]]; then
        while IFS= read -r -d '' dir; do
            local id
            id="$(basename "$dir")"
            for name in app_info.log gpu_info.log registers.log vm_fault.log \
                        bo_ranges.log pipeline.log trace.log umr_waves.log umr_ring.log \
                        dmesg.log bo_history.log addr_binding_report.log; do
                if [[ -f "$dir/$name" ]]; then
                    tail -c 4000000 "$dir/$name" >"$RUN/info/$id-$name" 2>/dev/null
                fi
            done
            for shader in "$dir"/*.spv; do
                if [[ -f "$shader" ]]; then
                    cp -a "$shader" "$RUN/info/$id-$(basename "$shader")" 2>/dev/null
                fi
            done
        done < <(find "$PLAY" -maxdepth 5 -type d -name 'radv_dumps_*' -print0 2>/dev/null)
    fi
}
package() {
    set +e
    collect_radv
    [[ ! -f "$RUN/game.log" ]] || tail -c 11000000 "$RUN/game.log" >"$RUN/info/game-last.log" 2>/dev/null
    [[ ! -f "$RUN/runner.log" ]] || tail -c 1000000 "$RUN/runner.log" >"$RUN/info/runner-last.log" 2>/dev/null
    [[ ! -f "$RUN/build.log" ]] || tail -c 1000000 "$RUN/build.log" >"$RUN/info/build-last.log" 2>/dev/null
    local partial="$ARCHIVE.$BASHPID.part"
    if tar -czf "$partial" -C "$RUN" info screenshots 2>/dev/null; then
        mv -f "$partial" "$ARCHIVE"
        echo "ARCHIVE=$ARCHIVE"
    else
        rm -f "$partial"
        echo "ARCHIVE_FAILED=$RUN"
    fi
}
trap package EXIT
exec 9>"$LOG_ROOT/.got-upstream-radv26.lock"
flock -n 9 || { echo "ANOTHER_DRIVER_VALIDATION_RUNNING; EXITING"; exit 4; }
for tool in curl sha256sum tar python3 meson ninja pkg-config git vulkaninfo; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "MISSING_BUILD_PREREQUISITE=$tool; NO_SYSTEM_CHANGES; NOT_LAUNCHED"; exit 4
    }
done
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
    echo "ANOTHER_EMULATOR_RUNNING; NOTHING_STOPPED"; exit 4
fi
[[ -x "$EMULATOR" && -f "$GAME" && -d "$BASE_PROFILE/user" ]] || {
    echo "BASELINE_FILE_MISSING; GAME_NOT_LAUNCHED"; exit 4
}
actual_bin="$(sha256sum "$EMULATOR" | cut -d' ' -f1)"
actual_game="$(sha256sum "$GAME" | cut -d' ' -f1)"
actual_source="$(git -C "$EMULATOR_SOURCE" rev-parse HEAD)"
{
    echo "EMULATOR_SHA=$actual_bin"
    echo "EMULATOR_COMMIT=$actual_source"
    echo "GAME_SHA=$actual_game"
    echo "SYSTEM_DRIVER_FROM_PREVIOUS_RUN=RADV_25.0.7"
    echo "TARGET_DRIVER=OFFICIAL_MESA_$MESA_VERSION"
    echo "SOURCE_TARBALL_SHA=$MESA_SHA"
    echo "SOURCE_URL=https://archive.mesa3d.org/mesa-26.2.5.tar.xz"
    echo "EMU_SOURCE_CHANGES=NONE"
    echo "GAME_SPECIFIC_SHADERS_OR_RETURNS=NONE"
    echo "MESA_GLOBAL_INSTALL=NONE"
} >"$RUN/info/provenance.txt"
if [[ "$actual_bin" != "$EMU_SHA" || "$actual_game" != "$GAME_SHA" ||
      "$actual_source" != "$EMU_COMMIT" ]]; then
    echo "CHANGED_EMULATOR_OR_GAME; REFUSED_UNCONTROLLED_COMPARISON"; exit 4
fi
if [[ -e "$PLAY" || -L "$PLAY" ]]; then
    echo "PROFILE_COLLISION; REFUSING_OVERWRITE"; exit 4
fi
space_kib="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
if [[ ! "$space_kib" =~ ^[0-9]+$ ]] || ((space_kib < 5242880)); then
    echo "AT_LEAST_5GiB_FREE_REQUIRED_FOR_ISOLATED_MESA_BUILD"; exit 4
fi
mkdir -p "$MESA_WORK" "$MESA_WORK/src"
if [[ ! -e "$MESA_TARBALL" ]]; then
    echo "DOWNLOADING_OFFICIAL_MESA_26.2.5_WITH_IPV4"
    temp="$MESA_TARBALL.$BASHPID.part"
    curl -4 -fLsS --retry 4 --retry-delay 3 --connect-timeout 20 \
         --max-time 900 -o "$temp" \
         https://archive.mesa3d.org/mesa-26.2.5.tar.xz \
         >>"$RUN/build.log" 2>&1 || {
        rm -f "$temp"
        echo "MESA_DOWNLOAD_FAILED; ORIGINAL_SYSTEM_DRIVER_UNCHANGED"
        exit 5
    }
    echo "$MESA_SHA  $temp" | sha256sum -c - >>"$RUN/build.log" 2>&1 || {
        rm -f "$temp"
        echo "MESA_DOWNLOAD_SHA256_MISMATCH; SOURCE_REJECTED"; exit 5
    }
    mv "$temp" "$MESA_TARBALL"
fi
echo "$MESA_SHA  $MESA_TARBALL" | sha256sum -c - >>"$RUN/build.log" 2>&1 || {
    echo "MESA_SOURCE_ARCHIVE_CHANGED; NOT_LAUNCHED"; exit 5
}
if [[ ! -d "$MESA_SOURCE" ]]; then
    echo "EXTRACT_VERIFIED_MESA_RELEASE"
    tar -xf "$MESA_TARBALL" -C "$MESA_WORK/src" >>"$RUN/build.log" 2>&1
fi
[[ "$(cat "$MESA_SOURCE/VERSION")" == "$MESA_VERSION" ]] || {
    echo "MESA_VERSION_UNEXPECTED; NOT_LAUNCHED"; exit 5
}

# Mesa 26.2 needs a native glslangValidator to compile its generic Vulkan
# shaders. Debian's glslang-tools package can be downloaded and extracted
# without administrator privileges or changing the system package database.
# This is a BUILD TOOL only; the installed RADV source remains untouched.
TOOLS_ROOT="$MESA_WORK/local-build-tools"
if ! command -v glslangValidator >/dev/null 2>&1; then
    for tool in apt-get dpkg-deb; do
        command -v "$tool" >/dev/null 2>&1 || {
            echo "MESA_LOCAL_BUILD_TOOL_HELPER_MISSING=$tool; NOT_LAUNCHED"; exit 5
        }
    done
    mkdir -p "$TOOLS_ROOT/debs" "$TOOLS_ROOT/usr"
    if [[ ! -x "$TOOLS_ROOT/usr/bin/glslangValidator" ]]; then
        echo "FETCH_DEBIAN_BUILD_TOOLS_WITH_IPV4_NO_INSTALL"
        (
            cd "$TOOLS_ROOT/debs"
            apt-get -o Acquire::ForceIPv4=true download glslang-tools spirv-tools
        ) >>"$RUN/build.log" 2>&1 || {
            echo "APT_DOWNLOAD_BUILD_TOOLS_FAILED; NO_SYSTEM_CHANGES"; exit 5
        }
        for package in glslang-tools spirv-tools; do
            found=false
            for deb in "$TOOLS_ROOT/debs/$package"_*.deb; do
                [[ ! -f "$deb" ]] && continue
                found=true
                dpkg-deb -x "$deb" "$TOOLS_ROOT" >>"$RUN/build.log" 2>&1 || {
                    echo "BUILD_TOOL_EXTRACT_FAILED=$package"; exit 5
                }
            done
            if [[ "$found" != true ]]; then
                echo "BUILD_TOOL_PACKAGE_NOT_FOUND=$package"; exit 5
            fi
        done
    fi
    [[ -x "$TOOLS_ROOT/usr/bin/glslangValidator" ]] || {
        echo "LOCAL_GLSLANG_VALIDATOR_MISSING; NOT_LAUNCHED"; exit 5
    }
    export PATH="$TOOLS_ROOT/usr/bin:$PATH"
    echo "GSLANG_VALIDATOR_FROM_LOCAL_DEBIAN_TOOLS=$TOOLS_ROOT/usr/bin/glslangValidator"
else
    echo "GSLANG_VALIDATOR_FROM_EXISTING_PATH=$(command -v glslangValidator)"
fi
glslangValidator --version >"$RUN/info/glslang-version.txt" 2>&1 || {
    echo "GSLANG_VALIDATOR_CANNOT_RUN; NOT_LAUNCHED"; exit 5
}
echo "GSLANG_VALIDATOR_VERIFIED=YES"
{
    echo "GSLANG_PROVENANCE=$(command -v glslangValidator)"
    echo "BUILD_TOOL_DEPENDENCY_ONLY=YES"
    echo "ROOT_PACKAGE_INSTALL=NONE"
    echo "PATH_ONLY_SCOPED_TO_WORKER=YES"
} >"$RUN/info/build-tool-provenance.txt"

# Mesa only accepts Python >= 3.10 if it can import Mako >= 0.8,
# PyYAML and version parsing (packaging or distutils).
# Obtain the missing build-only Python modules without modifying the system.
PY_SITE="$TOOLS_ROOT/usr/lib/python3/dist-packages"
mkdir -p "$TOOLS_ROOT/debs" "$PY_SITE"
if ! command -v dpkg-deb >/dev/null 2>&1 ||
   ! command -v apt-get >/dev/null 2>&1; then
    echo "DEBIAN_PYTHON_BUILD_TOOL_HELPER_MISSING; NOT_LAUNCHED"
    exit 5
fi
for package in python3-packaging python3-markupsafe python3-mako python3-yaml; do
    case "$package" in
        python3-packaging) module_path="$PY_SITE/packaging/__init__.py" ;;
        python3-markupsafe) module_path="$PY_SITE/markupsafe/__init__.py" ;;
        python3-mako) module_path="$PY_SITE/mako/__init__.py" ;;
        python3-yaml) module_path="$PY_SITE/yaml/__init__.py" ;;
    esac
    if [[ -f "$module_path" ]]; then
        echo "PRIVATE_PYTHON_MODULE_ALREADY_EXTRACTED=$package"
        continue
    fi
    echo "DOWNLOAD_PRIVATE_PYTHON_MODULE=$package"
    (
        cd "$TOOLS_ROOT/debs"
        apt-get -o Acquire::ForceIPv4=true download "$package"
    ) >>"$RUN/build.log" 2>&1 || {
        echo "PYTHON_BUILD_DEPENDENCY_DOWNLOAD_FAILED=$package; NO_SYSTEM_CHANGES"
        exit 5
    }
    picked=""
    for deb in "$TOOLS_ROOT/debs/$package"_*.deb; do
        [[ -f "$deb" ]] || continue
        picked="$deb"
        break
    done
    [[ -n "$picked" ]] || {
        echo "PYTHON_BUILD_DEPENDENCY_DEB_NOT_FOUND=$package"
        exit 5
    }
    dpkg-deb -x "$picked" "$TOOLS_ROOT" >>"$RUN/build.log" 2>&1 || {
        echo "PYTHON_BUILD_DEPENDENCY_EXTRACTION_FAILED=$package"
        exit 5
    }
    [[ -f "$module_path" ]] || {
        echo "PYTHON_BUILD_DEPENDENCY_MODULE_NOT_EXTRACTED=$package"
        exit 5
    }
done

# All Meson child Python code generators inherit this private module path.
# Include the worker's prior PYTHONPATH when set.
if [[ -n "$(printenv PYTHONPATH || true)" ]]; then
    export PYTHONPATH="$PY_SITE:$(printenv PYTHONPATH)"
else
    export PYTHONPATH="$PY_SITE"
fi
python_ok=false
: >"$RUN/info/mesa-python-preflight.txt"
for candidate in python3.14 python3.13 python3; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    {
        echo "CANDIDATE=$candidate"
        "$candidate" - <<'PY'
import sys
from packaging.version import Version
import mako
import yaml

assert sys.version_info >= (3, 10), sys.version
assert Version(mako.__version__) >= Version("0.8.0"), mako.__version__
print("PYTHON=" + sys.version.split()[0])
print("MAKO=" + mako.__version__)
print("YAML=" + yaml.__version__)
print("PACKAGING=IMPORT_PASS")
print("MESA_PYTHON_DEPENDENCIES=PASS")
PY
    } >>"$RUN/info/mesa-python-preflight.txt" 2>&1 && python_ok=true
done
if [[ "$python_ok" != true ]]; then
    echo "MESA_PYTHON_PRECHECK_FAILED; NO_GAME_LAUNCH"
    tail -n 90 "$RUN/info/mesa-python-preflight.txt"
    exit 5
fi
echo "PRIVATE_MESA_PYTHON_BUILD_DEPENDENCIES=PASS"
{
    echo "PYTHON_MODULES_FROM=$PY_SITE"
    echo "PYTHONPATH_APPLIED_TO_WORKER_ONLY=YES"
    echo "SOURCE_TARBALL_UNMODIFIED=YES"
    echo "DPKG_INSTALLATION=NONE"
    echo "SELECTED_PYTHONS:"
    grep -A 6 '^CANDIDATE=' "$RUN/info/mesa-python-preflight.txt" || true
} >>"$RUN/info/build-tool-provenance.txt"

# Mesa 26.2 RADV requires libdrm >= 2.4.133; Debian Trixie provides
# 2.4.124. Build official 2.4.133 privately and use it for this Meson build
# and its runtime only. Never modify system libdrm or package manager state.
DRM_VERSION="2.4.133"
DRM_SHA256="fc68f9d0ba2ea63c9432a299e14fea09fad7a8a66e8039fcd7802ca59f77b4f5"
DRM_ROOT="$MESA_WORK/libdrm-upstream-$DRM_VERSION"
DRM_ARCHIVE="$DRM_ROOT/libdrm-$DRM_VERSION.tar.xz"
DRM_SOURCE="$DRM_ROOT/src/libdrm-$DRM_VERSION"
DRM_BUILD="$DRM_ROOT/build"
DRM_PREFIX="$DRM_ROOT/install"
{
    echo "SYSTEM_LIBDRM_VERSION_BEFORE=$(pkg-config --modversion libdrm 2>/dev/null || echo absent)"
    echo "MINIMUM_MESA26_AMDGPU_LIBDRM=2.4.133"
    echo "PRIVATE_LIBDRM_SOURCE=https://dri.freedesktop.org/libdrm/libdrm-$DRM_VERSION.tar.xz"
    echo "PRIVATE_LIBDRM_SHA256=$DRM_SHA256"
} >"$RUN/info/libdrm-provenance.txt"
mkdir -p "$DRM_ROOT/src"
if [[ ! -f "$DRM_ARCHIVE" ]]; then
    echo "DOWNLOAD_OFFICIAL_LIBDRM_${DRM_VERSION}_IPV4"
    tmp="$DRM_ARCHIVE.$BASHPID.part"
    curl -4 -fLsS --retry 4 --retry-delay 3 --connect-timeout 20 \
        --max-time 400 -o "$tmp" \
        "https://dri.freedesktop.org/libdrm/libdrm-$DRM_VERSION.tar.xz" \
        >>"$RUN/build.log" 2>&1 || {
        rm -f -- "$tmp"
        echo "LIBDRM_SOURCE_DOWNLOAD_FAILED; NO_SYSTEM_CHANGES"
        exit 5
    }
    echo "$DRM_SHA256  $tmp" | sha256sum -c - >>"$RUN/build.log" 2>&1 || {
        rm -f -- "$tmp"
        echo "LIBDRM_SOURCE_SHA_MISMATCH; REFUSED"
        exit 5
    }
    mv -- "$tmp" "$DRM_ARCHIVE"
fi
echo "$DRM_SHA256  $DRM_ARCHIVE" | sha256sum -c - >>"$RUN/build.log" 2>&1 || {
    echo "PREVIOUS_LIBDRM_SOURCE_SHA_MISMATCH; REFUSED"
    exit 5
}
if [[ ! -d "$DRM_SOURCE" ]]; then
    echo "EXTRACT_VERIFIED_OFFICIAL_LIBDRM_$DRM_VERSION"
    tar -xf "$DRM_ARCHIVE" -C "$DRM_ROOT/src" >>"$RUN/build.log" 2>&1
fi
[[ -f "$DRM_SOURCE/meson.build" ]] || {
    echo "LIBDRM_SOURCE_TREE_INVALID; NOT_LAUNCHED"
    exit 5
}
if [[ -d "$DRM_BUILD" && ! -f "$DRM_BUILD/build.ninja" ]]; then
    saved="$DRM_ROOT/failed-meson-setup-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    mv -- "$DRM_BUILD" "$saved"
    echo "INCOMPLETE_LIBDRM_MESON_SETUP_SAVED=$saved"
fi
if [[ ! -f "$DRM_BUILD/build.ninja" ]]; then
    echo "CONFIGURE_PRIVATE_LIBDRM_$DRM_VERSION"
    meson setup "$DRM_BUILD" "$DRM_SOURCE" \
        --prefix="$DRM_PREFIX" --libdir=lib -Dbuildtype=release \
        -Damdgpu=enabled -Dintel=disabled -Dradeon=disabled \
        -Dnouveau=disabled -Dvmwgfx=disabled \
        -Dtests=false -Dvalgrind=disabled -Dman-pages=disabled \
        -Dcairo-tests=disabled \
        >>"$RUN/build.log" 2>&1 || {
        echo "LIBDRM_PRIVATE_CONFIGURE_FAILED; NO_SYSTEM_CHANGES"
        exit 5
    }
fi
echo "BUILD_PRIVATE_LIBDRM_$DRM_VERSION"
meson compile -C "$DRM_BUILD" -j 4 >>"$RUN/build.log" 2>&1 || {
    echo "LIBDRM_PRIVATE_BUILD_FAILED; NO_SYSTEM_CHANGES"
    exit 5
}
meson install -C "$DRM_BUILD" --no-rebuild >>"$RUN/build.log" 2>&1 || {
    echo "LIBDRM_PRIVATE_INSTALL_FAILED; NO_SYSTEM_CHANGES"
    exit 5
}
[[ -f "$DRM_PREFIX/lib/pkgconfig/libdrm.pc" &&
   -f "$DRM_PREFIX/lib/pkgconfig/libdrm_amdgpu.pc" ]] || {
    echo "PRIVATE_LIBDRM_PC_FILES_NOT_FOUND; NO_GAME_LAUNCH"
    exit 5
}
if [[ -n "$(printenv PKG_CONFIG_PATH || true)" ]]; then
    export PKG_CONFIG_PATH="$DRM_PREFIX/lib/pkgconfig:$(printenv PKG_CONFIG_PATH)"
else
    export PKG_CONFIG_PATH="$DRM_PREFIX/lib/pkgconfig"
fi
if [[ -n "$(printenv LD_LIBRARY_PATH || true)" ]]; then
    export LD_LIBRARY_PATH="$DRM_PREFIX/lib:$(printenv LD_LIBRARY_PATH)"
else
    export LD_LIBRARY_PATH="$DRM_PREFIX/lib"
fi
for module in libdrm libdrm_amdgpu; do
    version="$(pkg-config --modversion "$module" 2>/dev/null || true)"
    if [[ "$version" != "$DRM_VERSION" ]]; then
        echo "WRONG_PRIVATE_LIBDRM_VERSION=$module/$version; NO_GAME_LAUNCH"
        exit 5
    fi
    echo "$module=$version" >>"$RUN/info/libdrm-provenance.txt"
done
echo "PRIVATE_LIBDRM_VERSION_VERIFIED=$DRM_VERSION"
{
    echo "PRIVATE_LIBDRM_PREFIX=$DRM_PREFIX"
    echo "PRIVATE_LIBDRM_PKGCONFIG=$DRM_PREFIX/lib/pkgconfig"
    echo "PRIVATE_LIBDRM_RUNTIME_PATH=$DRM_PREFIX/lib"
    echo "SYSTEM_LIBDRM_UNMODIFIED=YES"
} >>"$RUN/info/libdrm-provenance.txt"

# Mesa 26.2 Vulkan X11 WSI requires the full XCB extension developer set.
# Use only Debian Trixie .deb DOWNLOADS and PRIVATE EXTRACTIONS, never install.
# This leaves the system's X11 libraries, Mesa and package database unchanged.
X11_PRIVATE_PC="$TOOLS_ROOT/private-pkgconfig"
X11_PRIVATE_LIB="$TOOLS_ROOT/usr/lib/$(cc -dumpmachine)"
mkdir -p "$TOOLS_ROOT/debs" "$X11_PRIVATE_PC"
if [[ -n "$(printenv PKG_CONFIG_PATH || true)" ]]; then
    export PKG_CONFIG_PATH="$X11_PRIVATE_PC:$(printenv PKG_CONFIG_PATH)"
else
    export PKG_CONFIG_PATH="$X11_PRIVATE_PC"
fi
printf 'FULL_X11_VULKAN_DEPENDENCY_PRECHECK=START\n' >"$RUN/info/mesa-x11-dependencies.txt"
while read -r module package; do
    [[ -n "$module" ]] || continue
    if pkg-config --exists "$module"; then
        printf '%s=ALREADY_AVAILABLE version=%s\n' "$module" \
            "$(pkg-config --modversion "$module")" \
            >>"$RUN/info/mesa-x11-dependencies.txt"
        continue
    fi
    echo "DOWNLOAD_MISSING_PRIVATE_X11_DEVELOPMENT_PACKAGE=$package"
    selected=""
    for deb in "$TOOLS_ROOT/debs/$package"_*.deb; do
        [[ -f "$deb" ]] || continue
        selected="$deb"
        break
    done
    if [[ -z "$selected" ]]; then
        (
            cd "$TOOLS_ROOT/debs"
            apt-get -o Acquire::ForceIPv4=true download "$package"
        ) >>"$RUN/build.log" 2>&1 || {
            echo "X11_PRIVATE_DEB_DOWNLOAD_FAILED=$package; NO_SYSTEM_INSTALL"
            exit 5
        }
        for deb in "$TOOLS_ROOT/debs/$package"_*.deb; do
            [[ -f "$deb" ]] || continue
            selected="$deb"
            break
        done
    fi
    [[ -n "$selected" && -f "$selected" ]] || {
        echo "X11_PRIVATE_DEB_NOT_FOUND=$package"
        exit 5
    }
    dpkg-deb -x "$selected" "$TOOLS_ROOT" >>"$RUN/build.log" 2>&1 || {
        echo "X11_PRIVATE_DEB_EXTRACT_FAILED=$package"
        exit 5
    }
    printf '%s=PRIVATE_DEB_EXTRACTED package=%s\n' "$module" "$package" \
        >>"$RUN/info/mesa-x11-dependencies.txt"
done <<'X11_PRIVATE_REQUIRED'
xcb libxcb1-dev
xcb-randr libxcb-randr0-dev
xcb-render libxcb-render0-dev
xcb-shape libxcb-shape0-dev
x11 libx11-dev
x11-xcb libx11-xcb-dev
xcb-dri3 libxcb-dri3-dev
xcb-dri2 libxcb-dri2-0-dev
xcb-present libxcb-present-dev
xcb-shm libxcb-shm0-dev
xcb-sync libxcb-sync-dev
xcb-xfixes libxcb-xfixes0-dev
xshmfence libxshmfence-dev
libelf libelf-dev
X11_PRIVATE_REQUIRED

# XCB extension headers extracted above include "xcb.h" relative to their
# own xcb/ directory. Merely finding the *system* xcb.pc is insufficient:
# -I$TOOLS_ROOT/usr/include selects our extracted shm.h/randr.h first, and
# these require xcb.h, xcbext.h and xproto.h in the SAME private directory.
# Always extract libxcb1-dev into the private prefix, even if pkg-config
# already reports that the host has the base libxcb development package.
XCB_BASE_PKG="libxcb1-dev"
XCB_HEADER="$TOOLS_ROOT/usr/include/xcb/xcb.h"
if [[ ! -f "$XCB_HEADER" ||
      ! -f "$TOOLS_ROOT/usr/include/xcb/xcbext.h" ||
      ! -f "$TOOLS_ROOT/usr/include/xcb/xproto.h" ]]; then
    echo "POPULATE_PRIVATE_XCB_BASE_HEADERS"
    xcb_deb=""
    for candidate in "$TOOLS_ROOT/debs/$XCB_BASE_PKG"_*.deb; do
        [[ -f "$candidate" ]] || continue
        xcb_deb="$candidate"
        break
    done
    if [[ -z "$xcb_deb" ]]; then
        (
            cd "$TOOLS_ROOT/debs"
            apt-get -o Acquire::ForceIPv4=true download "$XCB_BASE_PKG"
        ) >>"$RUN/build.log" 2>&1 || {
            echo "PRIVATE_XCB_BASE_DOWNLOAD_FAILED; MESA_BUILD_NOT_STARTED"
            exit 5
        }
        for candidate in "$TOOLS_ROOT/debs/$XCB_BASE_PKG"_*.deb; do
            [[ -f "$candidate" ]] || continue
            xcb_deb="$candidate"
            break
        done
    fi
    [[ -n "$xcb_deb" && -f "$xcb_deb" ]] || {
        echo "PRIVATE_XCB_BASE_DEB_NOT_FOUND; MESA_BUILD_NOT_STARTED"
        exit 5
    }
    dpkg-deb -x "$xcb_deb" "$TOOLS_ROOT" >>"$RUN/build.log" 2>&1 || {
        echo "PRIVATE_XCB_BASE_EXTRACTION_FAILED; MESA_BUILD_NOT_STARTED"
        exit 5
    }
fi
for required in xcb.h xcbext.h xproto.h; do
    [[ -f "$TOOLS_ROOT/usr/include/xcb/$required" ]] || {
        echo "PRIVATE_XCB_FOUNDATION_HEADER_MISSING=$required; MESA_BUILD_NOT_STARTED"
        exit 5
    }
done
printf 'PRIVATE_XCB_CORE_HEADERS=PASS\n' >>"$RUN/info/mesa-x11-dependencies.txt"

# Debian .pc entries embed prefix=/usr. Copy them to our own pkgconfig path,
# rewriting only the copies to reference extracted private headers and libs.
# The official downloaded packages and all installed system files stay intact.
python3 - "$TOOLS_ROOT/usr" "$X11_PRIVATE_PC" <<'PY'
from pathlib import Path
import re
import sys

private_usr = Path(sys.argv[1])
private_pc = Path(sys.argv[2])
count = 0
for source in sorted(private_usr.glob("lib/*/pkgconfig/*.pc")):
    data = source.read_text(encoding="utf-8")
    data = re.sub(r"(?m)^prefix=/usr$", "prefix=" + str(private_usr), data)
    (private_pc / source.name).write_text(data, encoding="utf-8")
    count += 1
print("PRIVATE_X11_PKGCONFIG_COPIES=" + str(count))
PY
# Development packages do not download their runtime dependencies when
# extracted with dpkg-deb -x. Make sure the two newly required libraries have
# a real runtime target, either already on Debian or within this private tree.
# Download the matching official Debian runtime .deb only when neither exists.
for runtime_spec in \
    "libxcb-render.so.0 libxcb-render0" \
    "libxcb-shape.so.0 libxcb-shape0"; do
    read -r soname package <<<"$runtime_spec"
    if [[ -e "$X11_PRIVATE_LIB/$soname" ||
          -e "/usr/lib/$(cc -dumpmachine)/$soname" ]]; then
        echo "X11_RUNTIME_LIBRARY_AVAILABLE=$soname"
        continue
    fi
    echo "DOWNLOAD_PRIVATE_X11_RUNTIME_PACKAGE=$package"
    runtime_deb=""
    for deb in "$TOOLS_ROOT/debs/$package"_*.deb; do
        [[ -f "$deb" ]] || continue
        runtime_deb="$deb"
        break
    done
    if [[ -z "$runtime_deb" ]]; then
        (
            cd "$TOOLS_ROOT/debs"
            apt-get -o Acquire::ForceIPv4=true download "$package"
        ) >>"$RUN/build.log" 2>&1 || {
            echo "X11_PRIVATE_RUNTIME_DOWNLOAD_FAILED=$package"
            exit 5
        }
        for deb in "$TOOLS_ROOT/debs/$package"_*.deb; do
            [[ -f "$deb" ]] || continue
            runtime_deb="$deb"
            break
        done
    fi
    [[ -n "$runtime_deb" && -f "$runtime_deb" ]] || {
        echo "X11_PRIVATE_RUNTIME_DEB_MISSING=$package"
        exit 5
    }
    dpkg-deb -x "$runtime_deb" "$TOOLS_ROOT" >>"$RUN/build.log" 2>&1 || {
        echo "X11_PRIVATE_RUNTIME_EXTRACTION_FAILED=$package"
        exit 5
    }
    [[ -e "$X11_PRIVATE_LIB/$soname" ]] || {
        echo "X11_PRIVATE_RUNTIME_UNRESOLVED=$soname"
        exit 5
    }
    echo "X11_RUNTIME_PACKAGE_EXTRACTED=$package"
done

# Development Debian packages provide unversioned lib*.so linker symlinks,
# while already-installed runtime packages provide versioned lib*.so.N files.
# Since extraction is private, complete ONLY broken private linker symlinks
# with read-only copies of their real runtime files, when present on the host.
# No system libraries are modified, replaced or injected into other processes.
python3 - "$X11_PRIVATE_LIB" <<'PY'
from pathlib import Path
import os
import shutil
import sys

private = Path(sys.argv[1])
public = Path("/usr/lib") / private.name
count = 0
if private.exists():
    for link in sorted(private.glob("lib*.so")):
        if not link.is_symlink() or link.exists():
            continue
        target = Path(os.readlink(link))
        if target.is_absolute():
            raise SystemExit("Unexpected absolute private linker target: " + str(link))
        source = public / target
        if not source.is_file():
            raise SystemExit("Missing runtime library for private linker symlink: " + str(link))
        destination = private / target
        if destination.exists():
            continue
        shutil.copy2(source.resolve(), destination)
        count += 1
print("PRIVATE_LINKER_RUNTIME_COPIES=" + str(count))
PY
if [[ -n "$(printenv LD_LIBRARY_PATH || true)" ]]; then
    export LD_LIBRARY_PATH="$DRM_PREFIX/lib:$X11_PRIVATE_LIB:$(printenv LD_LIBRARY_PATH)"
else
    export LD_LIBRARY_PATH="$DRM_PREFIX/lib:$X11_PRIVATE_LIB"
fi

# Debian libxcb-randr0-dev requires libxcb-render0-dev, and
# libxcb-xfixes0-dev additionally requires libxcb-shape0-dev. Both are
# downloaded up front above, rather than discovered by repeated build errors.
# Verify ALL required packages now to avoid serial Meson failures.
while read -r module package; do
    [[ -n "$module" ]] || continue
    if ! pkg-config --exists "$module"; then
        echo "MESA_REQUIRED_X11_MODULE_UNRESOLVED=$module; NO_MESA_BUILD"
        pkg-config --print-errors --exists "$module" \
            >>"$RUN/info/mesa-x11-dependencies.txt" 2>&1 || true
        exit 5
    fi
    printf '%s=VERIFIED version=%s\n' "$module" \
        "$(pkg-config --modversion "$module")" \
        >>"$RUN/info/mesa-x11-dependencies.txt"
done <<'X11_PRIVATE_RECHECK'
xcb libxcb1-dev
xcb-randr libxcb-randr0-dev
xcb-render libxcb-render0-dev
xcb-shape libxcb-shape0-dev
x11 libx11-dev
x11-xcb libx11-xcb-dev
xcb-dri3 libxcb-dri3-dev
xcb-dri2 libxcb-dri2-0-dev
xcb-present libxcb-present-dev
xcb-shm libxcb-shm0-dev
xcb-sync libxcb-sync-dev
xcb-xfixes libxcb-xfixes0-dev
xshmfence libxshmfence-dev
libelf libelf-dev
X11_PRIVATE_RECHECK
if [[ "$(pkg-config --modversion libdrm)" != "2.4.133" ||
      "$(pkg-config --modversion libdrm_amdgpu)" != "2.4.133" ]]; then
    echo "MESA_PRIVATE_LIBDRM_LOST_AFTER_X11_SETUP"
    exit 5
fi
# Catch partial header trees before resuming Ninja at the failed X11
# translation unit. The previously missing #include "xcb.h" must now work,
# as must all existing XCB extension headers and X11 presentation interfaces.
cat >"$RUN/info/mesa-xcb-header-smoke.c" <<'C'
#include <xcb/xcb.h>
#include <xcb/xcbext.h>
#include <xcb/xproto.h>
#include <xcb/shm.h>
#include <xcb/randr.h>
#include <xcb/render.h>
#include <xcb/shape.h>
#include <xcb/present.h>
#include <xcb/xfixes.h>
#include <xcb/dri2.h>
#include <xcb/dri3.h>
#include <xcb/sync.h>
#include <X11/Xlib.h>
#include <X11/Xlib-xcb.h>
int main(void) { return 0; }
C
if ! cc -std=c11 -fsyntax-only -I"$TOOLS_ROOT/usr/include" \
        $(pkg-config --cflags \
          xcb xcb-shm xcb-randr xcb-render xcb-shape xcb-present \
          xcb-xfixes xcb-dri2 xcb-dri3 xcb-sync x11 x11-xcb) \
        "$RUN/info/mesa-xcb-header-smoke.c" \
        >"$RUN/info/mesa-xcb-header-smoke.log" 2>&1; then
    echo "PRIVATE_MESA_XCB_HEADERS_INCOMPLETE; MESA_BUILD_NOT_STARTED"
    cat "$RUN/info/mesa-xcb-header-smoke.log"
    exit 5
fi
echo "PRIVATE_MESA_XCB_HEADER_SMOKE=PASS"
rm -f -- "$RUN/info/mesa-xcb-header-smoke.c"

# Mesa's final RADV link calls "-lelf" directly. A working libelf.pc
# and libelf headers are NOT enough: Debian's libelf-dev supplies the linker
# libelf.so entry, separate from libelf1t64's runtime libelf.so.1.
# The previously configured Meson build cached "-lelf" with no private -L.
# Supply the private directory through GCC's LIBRARY_PATH (link-time only)
# without reconfiguring or touching the nearly completed 626/627 build.
ELF_LINKER_DIR="$TOOLS_ROOT/usr/lib/$(cc -dumpmachine)"
ELF_LINKER_SO="$ELF_LINKER_DIR/libelf.so"
mkdir -p "$ELF_LINKER_DIR"
if [[ ! -e "$ELF_LINKER_SO" ||
      ! -f "$TOOLS_ROOT/usr/include/libelf.h" ]]; then
    echo "FETCH_PRIVATE_LIBELF_DEVELOPMENT_LINKER"
    elfdev_deb=""
    for candidate in "$TOOLS_ROOT/debs/libelf-dev"_*.deb; do
        [[ -f "$candidate" ]] || continue
        elfdev_deb="$candidate"
        break
    done
    if [[ -z "$elfdev_deb" ]]; then
        (
            cd "$TOOLS_ROOT/debs"
            apt-get -o Acquire::ForceIPv4=true download libelf-dev
        ) >>"$RUN/build.log" 2>&1 || {
            echo "LIBELF_DEV_DOWNLOAD_FAILED; MESA_BUILD_NOT_STARTED"
            exit 5
        }
        for candidate in "$TOOLS_ROOT/debs/libelf-dev"_*.deb; do
            [[ -f "$candidate" ]] || continue
            elfdev_deb="$candidate"
            break
        done
    fi
    [[ -n "$elfdev_deb" && -f "$elfdev_deb" ]] || {
        echo "LIBELF_DEV_DEB_NOT_FOUND"
        exit 5
    }
    dpkg-deb -x "$elfdev_deb" "$TOOLS_ROOT" >>"$RUN/build.log" 2>&1 || {
        echo "LIBELF_DEV_EXTRACTION_FAILED"
        exit 5
    }
fi
# libelf.so is a development symlink to the runtime SONAME. Complete the
# private dependency using Debian's matching runtime without changing /usr.
if [[ ! -e "$ELF_LINKER_SO" ]]; then
    if [[ ! -e "$ELF_LINKER_DIR/libelf.so.1" ]]; then
        elf_runtime_deb=""
        for candidate in "$TOOLS_ROOT/debs/libelf1t64"_*.deb; do
            [[ -f "$candidate" ]] || continue
            elf_runtime_deb="$candidate"
            break
        done
        if [[ -z "$elf_runtime_deb" ]]; then
            echo "FETCH_PRIVATE_LIBELF_RUNTIME_SONAME"
            (
                cd "$TOOLS_ROOT/debs"
                apt-get -o Acquire::ForceIPv4=true download libelf1t64
            ) >>"$RUN/build.log" 2>&1 || {
                echo "LIBELF_RUNTIME_DOWNLOAD_FAILED"
                exit 5
            }
            for candidate in "$TOOLS_ROOT/debs/libelf1t64"_*.deb; do
                [[ -f "$candidate" ]] || continue
                elf_runtime_deb="$candidate"
                break
            done
        fi
        [[ -n "$elf_runtime_deb" && -f "$elf_runtime_deb" ]] || {
            echo "LIBELF_RUNTIME_DEB_NOT_FOUND"
            exit 5
        }
        dpkg-deb -x "$elf_runtime_deb" "$TOOLS_ROOT" >>"$RUN/build.log" 2>&1 || {
            echo "LIBELF_RUNTIME_EXTRACTION_FAILED"
            exit 5
        }
    fi
fi
if [[ ! -e "$ELF_LINKER_SO" ||
      ! -f "$TOOLS_ROOT/usr/include/libelf.h" ]]; then
    echo "PRIVATE_LIBELF_LINKER_UNRESOLVED; MESA_BUILD_NOT_STARTED"
    ls -la "$ELF_LINKER_DIR"/libelf* >>"$RUN/info/libelf-link-provenance.txt" 2>&1 || true
    exit 5
fi
# GCC's LIBRARY_PATH is used by cc and c++ at link time, even when Ninja's
# already-generated link command has no extra -L. Do not touch Meson cache.
if [[ -n "$(printenv LIBRARY_PATH || true)" ]]; then
    export LIBRARY_PATH="$ELF_LINKER_DIR:$(printenv LIBRARY_PATH)"
else
    export LIBRARY_PATH="$ELF_LINKER_DIR"
fi
if [[ -n "$(printenv LD_LIBRARY_PATH || true)" ]]; then
    export LD_LIBRARY_PATH="$ELF_LINKER_DIR:$(printenv LD_LIBRARY_PATH)"
else
    export LD_LIBRARY_PATH="$ELF_LINKER_DIR"
fi
{
    echo "DEVELOPMENT_LINKER=$ELF_LINKER_SO"
    echo "RUNTIME_SONAME=$(readlink "$ELF_LINKER_SO" || true)"
    echo "PACKAGE_VERSION=$(pkg-config --modversion libelf)"
    echo "LIBRARY_PATH_FOR_NINJA_LINKER=$LIBRARY_PATH"
    echo "PRIVATE_DEV_HEADERS=$TOOLS_ROOT/usr/include"
    echo "NO_SYSTEM_LINKER_CONFIGURATION_MODIFIED=YES"
} >"$RUN/info/libelf-link-provenance.txt"
# This reproduces the exact "-lelf" search pattern that failed in RADV.
# The test explicitly CALLS elf_version, so --as-needed cannot hide a
# missing libelf. Run the result to prove that runtime resolution works.
if ! printf '#include <libelf.h>\nint main(void){return elf_version(EV_CURRENT)==EV_NONE;}\n' | \
    cc -I"$TOOLS_ROOT/usr/include" -x c - -x none \
       -o "$RUN/info/private-libelf-link-test" -lelf \
       >"$RUN/info/libelf-link-smoke.log" 2>&1; then
    echo "PRIVATE_LIBELF_LINK_SMOKE_FAILED; MESA_BUILD_NOT_STARTED"
    cat "$RUN/info/libelf-link-smoke.log"
    exit 5
fi
if ! "$RUN/info/private-libelf-link-test" \
    >>"$RUN/info/libelf-link-smoke.log" 2>&1; then
    echo "PRIVATE_LIBELF_RUNTIME_SMOKE_FAILED; MESA_BUILD_NOT_STARTED"
    exit 5
fi
rm -f -- "$RUN/info/private-libelf-link-test"
echo "PRIVATE_LIBELF_LINK_AND_RUNTIME=PASS"

# Check a real native link, not just Meson's pkg-config discovery.
if ! printf 'int main(void){return 0;}\n' | \
    cc -x c - -x none -o "$RUN/info/private-x11-link-test" \
        $(pkg-config --cflags --libs \
           xcb-randr xcb-render xcb-shape xcb-dri3 xcb-present \
           xcb-shm xcb-sync xcb-xfixes x11-xcb xshmfence libelf) \
        >>"$RUN/build.log" 2>&1; then
    echo "MESA_PRIVATE_X11_LINK_TEST_FAILED; NO_MESA_BUILD"
    tail -n 45 "$RUN/build.log"
    exit 5
fi
rm -f -- "$RUN/info/private-x11-link-test"
echo "PRIVATE_MESA_X11_VULKAN_DEPENDENCIES=PASS"

# Mesa's compiler generators may require Bison and Flex later in the build.
# Debian reports these absent in the previous Meson log; satisfy both now.
# Extract executable tools locally and set only worker-local search variables.
for generator in bison flex; do
    if command -v "$generator" >/dev/null 2>&1; then
        echo "MESA_COMPILER_GENERATOR_PRESENT=$generator"
        continue
    fi
    localdeb=""
    for deb in "$TOOLS_ROOT/debs/$generator"_*.deb; do
        [[ -f "$deb" ]] || continue
        localdeb="$deb"
        break
    done
    if [[ -z "$localdeb" ]]; then
        echo "DOWNLOAD_PRIVATE_MESA_COMPILER_GENERATOR=$generator"
        (
            cd "$TOOLS_ROOT/debs"
            apt-get -o Acquire::ForceIPv4=true download "$generator"
        ) >>"$RUN/build.log" 2>&1 || {
            echo "MESA_GENERATOR_DOWNLOAD_FAILED=$generator; NO_SYSTEM_INSTALL"
            exit 5
        }
        for deb in "$TOOLS_ROOT/debs/$generator"_*.deb; do
            [[ -f "$deb" ]] || continue
            localdeb="$deb"
            break
        done
    fi
    [[ -n "$localdeb" && -f "$localdeb" ]] || {
        echo "MESA_GENERATOR_DEB_MISSING=$generator"
        exit 5
    }
    dpkg-deb -x "$localdeb" "$TOOLS_ROOT" >>"$RUN/build.log" 2>&1 || {
        echo "MESA_GENERATOR_EXTRACT_FAILED=$generator"
        exit 5
    }
done
export PATH="$TOOLS_ROOT/usr/bin:$PATH"
if [[ -d "$TOOLS_ROOT/usr/share/bison" ]]; then
    export BISON_PKGDATADIR="$TOOLS_ROOT/usr/share/bison"
fi
for generator in bison flex; do
    if ! command -v "$generator" >/dev/null 2>&1 ||
       ! "$generator" --version >>"$RUN/info/mesa-generators.txt" 2>&1; then
        echo "MESA_GENERATOR_VERIFICATION_FAILED=$generator"
        exit 5
    fi
done
echo "MESA_BISON_FLEX_BUILD_GENERATORS=PASS"
{
    echo "BUILD_TOOLS_FROM_PRIVATE_DEBIAN_DEBS=YES"
    echo "PRIVATE_X11_PKGCONFIG=$X11_PRIVATE_PC"
    echo "PRIVATE_X11_LINKER_PATH=$X11_PRIVATE_LIB"
    echo "OFFICIAL_MESA_SOURCE_CHANGED=NO"
    echo "SYSTEM_PACKAGES_INSTALLED=NONE"
} >>"$RUN/info/mesa-x11-dependencies.txt"

# The preceding run stopped midway through Meson setup. Preserve the failed
# configuration and its logs, then start a clean build directory. No completed
# build or emulator checkpoint is ever replaced by this recovery path.
if [[ -d "$MESA_BUILD" && ! -f "$MESA_BUILD/build.ninja" ]]; then
    failed="$MESA_WORK/failed-meson-setup-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    mv -- "$MESA_BUILD" "$failed"
    echo "INCOMPLETE_MESON_SETUP_SAVED=$failed"
fi
if [[ ! -f "$MESA_BUILD/build.ninja" ]]; then
    echo "CONFIGURE_OFFICIAL_MESA_RADV_ONLY"
    meson setup "$MESA_BUILD" "$MESA_SOURCE" \
          --prefix="$MESA_PREFIX" --libdir=lib \
          -Dbuildtype=release -Dgallium-drivers= -Dvulkan-drivers=amd \
          -Dplatforms=x11 -Dglx=disabled -Degl=disabled -Dgbm=disabled \
          >>"$RUN/build.log" 2>&1 || {
        echo "MESA_CONFIGURE_FAILED; SEE_BUILD_LOG; NO_SYSTEM_CHANGES"; exit 5
    }
fi
echo "COMPILE_UNMODIFIED_OFFICIAL_MESA_26.2.5_RADV"
meson compile -C "$MESA_BUILD" -j 4 >>"$RUN/build.log" 2>&1 || {
    echo "MESA_BUILD_FAILED; SEE_BUILD_LOG; NO_SYSTEM_CHANGES"; exit 5
}
meson install -C "$MESA_BUILD" --no-rebuild >>"$RUN/build.log" 2>&1 || {
    echo "MESA_LOCAL_INSTALL_FAILED; NO_SYSTEM_CHANGES"; exit 5
}
icd="$(find "$MESA_PREFIX" -type f -path '*/vulkan/icd.d/*radeon*.json' -print -quit)"
driver="$(find "$MESA_PREFIX" -type f -name 'libvulkan_radeon.so' -print -quit)"
[[ -n "$icd" && -f "$icd" && -n "$driver" && -s "$driver" ]] || {
    echo "RADV_ONLY_BUILD_MISSING_ICD_OR_LIBRARY; NO_GAME_LAUNCH"; exit 5
}
ICD_LOCAL="$RUN/info/upstream-mesa-26.2.5-icd.json"
python3 - "$icd" "$driver" "$ICD_LOCAL" <<'PY'
import json
import sys
from pathlib import Path
src, driver, dst = map(Path, sys.argv[1:])
obj = json.loads(src.read_text(encoding="utf-8"))
if "ICD" not in obj or not obj["ICD"].get("library_path"):
    raise SystemExit("Invalid upstream Mesa ICD manifest")
obj["ICD"]["library_path"] = str(driver.resolve())
dst.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
PY
echo "VERIFYING_SIDE_BY_SIDE_DRIVER_WITH_VULKANINFO"
VK_DRIVER_FILES="$ICD_LOCAL" timeout 25s vulkaninfo --summary \
    >"$RUN/info/vulkaninfo-upstream.txt" 2>&1 || {
    echo "SIDE_BY_SIDE_VULKAN_LOADER_FAILED; NO_GAME_LAUNCH"; exit 5
}
if ! grep -q '26\.2\.5' "$RUN/info/vulkaninfo-upstream.txt" ||
   ! grep -q 'AMD Radeon RX 7900 XTX' "$RUN/info/vulkaninfo-upstream.txt"; then
    echo "WRONG_MESA_VERSION_OR_GPU; REFUSING_INVALID_AB_COMPARISON"
    grep -E 'driverName|driverInfo|deviceName' "$RUN/info/vulkaninfo-upstream.txt" || true
    exit 5
fi
echo "UPSTREAM_MESA_26_2_5_DRIVER_VERIFIED=YES"
echo "PRIVATE_MESA_DIR=$MESA_PREFIX"
mkdir -p "$PLAY"
cp -a "$BASE_PROFILE/user" "$PLAY/user"
for name in config.json users.json keys input_config save_data savedata; do
    [[ ! -e "$BASE_PROFILE/$name" ]] || cp -a "$BASE_PROFILE/$name" "$PLAY/$name"
done
cp -a "$EMULATOR" "$PLAY/shadps4"
cd "$PLAY"
unset VK_ICD_FILENAMES SHADPS4_GHOST_BINDLESS_EXPERIMENT
export VK_DRIVER_FILES="$ICD_LOCAL"
export HOME="$PLAY"
export RADV_DEBUG="hang,noumr"
export MESA_SHADER_CACHE_DISABLE=true
if [[ -z "$(printenv DISPLAY || true)" ]]; then export DISPLAY=:0; fi
date +%s >"$RUN/info/start-epoch.txt"
echo "UNCHANGED_UPSTREAM_EMULATOR; CLEAN_UPSTREAM_RADV_$MESA_VERSION; NORMAL_SPEED; GAMEPAD_MANUAL"
./shadps4 --game "$GAME" --fullscreen true >"$RUN/game.log" 2>&1 &
GAME_PID=$!
echo "GAME_PID=$GAME_PID"
(
    last=0
    for mark in 3 6 9 12 16 20 30 45 60 90 120 180; do
        sleep "$((mark-last))"
        last="$mark"
        kill -0 "$GAME_PID" 2>/dev/null || break
        echo "$mark $(ps -p "$GAME_PID" -o etimes=,time=,pcpu=,rss=,stat= 2>/dev/null || true)" \
            >>"$RUN/info/runtime-samples.txt"
        if command -v xdotool >/dev/null 2>&1 && command -v import >/dev/null 2>&1; then
            window="$(xdotool search --onlyvisible --pid "$GAME_PID" 2>/dev/null | tail -n 1 || true)"
            if [[ -n "$window" ]]; then
                timeout 4s import -window "$window" \
                    "$RUN/screenshots/$(printf '%03d' "$mark")s.png" 2>/dev/null || true
            fi
        fi
        if [[ "$mark" == 120 ]]; then
            echo "120_SECONDS_REACHED; GAME_CONTINUES"
            package
        fi
    done
) &
SHOT_PID=$!
set +e
wait "$GAME_PID"
RC=$?
kill "$SHOT_PID" 2>/dev/null || true
wait "$SHOT_PID" 2>/dev/null || true
set -e
elapsed="$(($(date +%s)-$(cat "$RUN/info/start-epoch.txt")))"
{
    echo "GAME_EXIT_CODE=$RC"
    echo "GAME_ELAPSED_SECONDS=$elapsed"
    echo "GPU_HANGS=$(grep -c 'GPU hang detected' "$RUN/game.log" || true)"
    echo "GRAPHICS_PIPELINES=$(grep -c 'Compiling graphics pipeline' "$RUN/game.log" || true)"
    echo "COMPUTE_PIPELINES=$(grep -c 'Compiling compute pipeline' "$RUN/game.log" || true)"
    echo "NP_TOOLKIT_CALLBACKS=$(grep -cE 'sceUserServiceRegisterEventCallback|sceNpRegisterStateCallbackForToolkit' "$RUN/game.log" || true)"
    echo "OLD_DRIVER_AB_RESULT=GPU_HANG_AT_ABOUT_20_SECONDS"
    echo "NO_SYSTEM_DRIVER_INSTALL=YES"
    echo "NO_GAME_SPECIFIC_EMULATOR_FIX=YES"
} >"$RUN/info/result.txt"
echo "GAME_EXIT=$RC ELAPSED=$elapsed"
echo "UPSTREAM_MESA_AB_CAPTURE_COMPLETE; ALL_PREVIOUS_BINARIES_AND_SSH_UNCHANGED"
