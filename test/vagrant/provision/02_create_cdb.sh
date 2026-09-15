#!/usr/bin/env bash
set -euo pipefail

ORACLE_BASE=/u01/app/oracle
ORACLE_HOME=/u01/app/oracle/product/19.0.0/dbhome_1
DBCA_TEMPLATE=/vagrant/provision/response_files/dbca.rsp

if pgrep -f 'ora_pmon_CDB1' >/dev/null 2>&1; then
  echo "CDB1 instance already running — skipping dbca."
  exit 0
fi

SYS_PW=$(openssl rand -base64 12 | tr -dc 'A-Za-z0-9' | cut -c1-14)
install -o oracle -g oinstall -m 600 /dev/null "$ORACLE_BASE/.cdb1_sys_password"
echo "$SYS_PW" > "$ORACLE_BASE/.cdb1_sys_password"

sed "s/__SYS_PASSWORD__/${SYS_PW}/g" "$DBCA_TEMPLATE" > /tmp/dbca.rsp
chown oracle:oinstall /tmp/dbca.rsp
chmod 600 /tmp/dbca.rsp

sudo -u oracle bash -c "
  export ORACLE_HOME=$ORACLE_HOME
  export ORACLE_BASE=$ORACLE_BASE
  export PATH=\$ORACLE_HOME/bin:\$PATH
  \$ORACLE_HOME/bin/dbca -silent -createDatabase -responseFile /tmp/dbca.rsp
"
rm -f /tmp/dbca.rsp

if ! grep -q '^CDB1:' /etc/oratab 2>/dev/null; then
  echo "CDB1:$ORACLE_HOME:Y" >> /etc/oratab
fi

echo "CDB1 created. SYS/SYSTEM password stored at $ORACLE_BASE/.cdb1_sys_password (oracle-owned, mode 600)."
