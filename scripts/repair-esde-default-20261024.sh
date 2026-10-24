#!/usr/bin/env bash
# Restore the ES-DE shadPS4 core if deleted, using a SHA-pinned previously tested native build.
# Deliberately do not change ~/.local/bin/shadps4-esde (Sunshine/session guard).
set -Eeuo pipefail

ROOT="${HOME}/projects/shadps4"
APP="${HOME}/Applications/shadps4"
TARGET="${APP}/shadps4"
GUARD="${HOME}/.local/bin/shadps4-esde"
REV="53b27cbc75b3511efca0c258c129241477f0aae8"
SHA="ffabf54a07828a1d05cfd41c659176a5770a066d74d492b75361b38b5473e20a"
SRC1="${ROOT}/GhostOfTsushima/candidates/${REV}-${SHA}/shadps4"
SRC2="${ROOT}/GhostOfTsushima/fresh-build/got-descriptor-identity-20261024-200312/shadps4"
SRC=""
echo "ESDE_REPAIR_STAGE=CHECK_PREVIOUS_DEFAULT"
if [[ -e "${TARGET}" || -L "${TARGET}" ]]; then
  echo "Previous ES-DE core: $(ls -ld "${TARGET}")"
else
  echo "Previous ES-DE core: MISSING (can be recreated)"
fi
echo "ES-DE session launcher: ${GUARD}"

for candidate in "${SRC1}" "${SRC2}"; do
  if [[ -f "${candidate}" && -x "${candidate}" ]]; then
    got="$(sha256sum "${candidate}" | awk '{print $1}')"
    if [[ "${got}" == "${SHA}" ]]; then
      SRC="${candidate}"
      break
    fi
    echo "Candidate hash mismatch; skipping: ${candidate}" >&2
  fi
done
if [[ -z "${SRC}" ]]; then
  echo "ERROR: Neither archived candidate nor its original build matches the verified SHA256." >&2
  echo "Checked: ${SRC1}" >&2
  echo "         ${SRC2}" >&2
  echo "No ES-DE executable was modified." >&2
  exit 2
fi

if [[ -d "${TARGET}" && ! -L "${TARGET}" ]]; then
  echo "ERROR: ES-DE core target is a directory; refusing to overwrite it." >&2
  exit 3
fi

echo "ESDE_REPAIR_STAGE=STAGE_VERIFIED_NATIVE_CORE"
RELEASE="${APP}/releases/got-descriptor-identity-${REV:0:10}-${SHA:0:12}"
ROLLBACK_ROOT="${ROOT}/ESDE/rollback"
mkdir -p "${RELEASE}" "${ROLLBACK_ROOT}"
STAGED="${RELEASE}/.shadps4.$.tmp"
NEWLINK=""
cleanup() {
  if [[ -n "${STAGED}" ]]; then rm -f -- "${STAGED}"; fi
  if [[ -n "${NEWLINK}" ]]; then rm -f -- "${NEWLINK}"; fi
}
trap cleanup EXIT
install -m 755 -- "${SRC}" "${STAGED}"
echo "${SHA}  ${STAGED}" | sha256sum --check --status
mv -Tf -- "${STAGED}" "${RELEASE}/shadps4"
STAGED=""
printf 'source=%s\nrevision=%s\nsha256=%s\n' "${SRC}" "${REV}" "${SHA}" > "${RELEASE}/install-info.txt"

echo "ESDE_REPAIR_STAGE=BACKUP_AND_SWITCH"
BACKUP=""
if [[ -e "${TARGET}" || -L "${TARGET}" ]]; then
  BACKUP="${ROLLBACK_ROOT}/before-got-$(date +%Y%m%d-%H%M%S)-$"
  mkdir -p "${BACKUP}"
  cp -a -- "${TARGET}" "${BACKUP}/shadps4.previous"
fi

NEWLINK="${APP}/.shadps4-link-$"
ln -s -- "${RELEASE}/shadps4" "${NEWLINK}"
mv -Tf -- "${NEWLINK}" "${TARGET}"
NEWLINK=""
echo "${SHA}  ${TARGET}" | sha256sum --check --status

echo
echo "ESDE_DEFAULT_REPAIR=SUCCESS"
echo "Installed core: ${TARGET}"
echo "Resolved binary: $(readlink -f "${TARGET}")"
echo "SHA256: ${SHA}"
echo "Previous executable backup: ${BACKUP:-none (target was missing)}"
if [[ -x "${GUARD}" ]]; then
  echo "ES-DE session guard: PRESERVED and executable"
else
  echo "WARNING: ES-DE session guard is missing/not executable: ${GUARD}"
  echo "The core has been restored, but the separate ES-DE guard still needs repair."
fi
echo "Other versioned releases, saves, configs, installed games and SSH unchanged."
