#!/usr/bin/env bash
set -euo pipefail

ORACLE_BASE=/u01/app/oracle
ORACLE_HOME=/u01/app/oracle/product/19.0.0/dbhome_1
MEDIA_DIR="${MEDIA_DIR:-/vagrant/media}"

if [ -f "$ORACLE_HOME/bin/oracle" ]; then
  echo "Oracle software already installed at $ORACLE_HOME — skipping."
  exit 0
fi

ZIP=$(find "$MEDIA_DIR" /tmp -maxdepth 1 -iname 'LINUX.X64_*_db_home.zip' 2>/dev/null | head -1)
if [ -z "$ZIP" ]; then
  echo "No Oracle 19c db_home zip found in $MEDIA_DIR or /tmp." >&2
  echo "Download 'Oracle Database 19c (19.3) for Linux x86-64' yourself from" >&2
  echo "Oracle (requires an Oracle account + license click-through — this" >&2
  echo "can't be automated) and drop it in test/vagrant/media/, or scp it" >&2
  echo "to the VM's /tmp — see README.md." >&2
  exit 1
fi

sudo -u oracle mkdir -p "$ORACLE_HOME"
sudo -u oracle unzip -q "$ZIP" -d "$ORACLE_HOME"

install -o oracle -g oinstall -m 600 \
  /vagrant/provision/response_files/db_install.rsp /tmp/db_install.rsp

set +e
sudo -u oracle "$ORACLE_HOME/runInstaller" -silent -ignorePrereqFailure \
  -waitforcompletion -responseFile /tmp/db_install.rsp
INSTALLER_RC=$?
set -e
rm -f /tmp/db_install.rsp

# runInstaller exits 6 for "success with configuration warnings" — still a
# usable install; anything else is a real failure.
if [ "$INSTALLER_RC" -ne 0 ] && [ "$INSTALLER_RC" -ne 6 ]; then
  echo "runInstaller exited $INSTALLER_RC — check $ORACLE_BASE/oraInventory/logs" >&2
  exit "$INSTALLER_RC"
fi

if [ -f "$ORACLE_BASE/oraInventory/orainstRoot.sh" ]; then
  "$ORACLE_BASE/oraInventory/orainstRoot.sh"
fi
"$ORACLE_HOME/root.sh"
