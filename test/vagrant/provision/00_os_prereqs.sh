#!/usr/bin/env bash
set -euo pipefail

ORACLE_BASE=/u01/app/oracle
ORACLE_HOME=/u01/app/oracle/product/19.0.0/dbhome_1

dnf -y install oracle-database-preinstall-19c unzip cloud-utils-growpart

mkdir -p "$ORACLE_HOME"
chown -R oracle:oinstall /u01
chmod -R 775 /u01

if [ ! -f /swapfile ]; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Best-effort root filesystem grow — the base box ships a small disk and
# vagrant-qemu has no reliable primary-disk-size Vagrantfile option
# (https://github.com/ppggff/vagrant-qemu/issues/66). If this doesn't grow
# enough for the DB software + a CDB, see README.md's manual qemu-img
# resize fallback.
ROOT_DEV=$(findmnt -no SOURCE /)
if [[ "$ROOT_DEV" == /dev/mapper/* ]]; then
  VG_LV=$(lvs --noheadings -o vg_name,lv_name "$ROOT_DEV" 2>/dev/null | xargs || true)
  PV_DEV=$(pvs --noheadings -o pv_name 2>/dev/null | xargs || true)
  if [ -n "$PV_DEV" ]; then
    DISK="/dev/$(lsblk -no PKNAME "$PV_DEV" 2>/dev/null | head -1)"
    PART_NUM="${PV_DEV##*[a-z]}"
    [ -n "${DISK#/dev/}" ] && growpart "$DISK" "$PART_NUM" || true
    pvresize "$PV_DEV" || true
  fi
  [ -n "$VG_LV" ] && lvextend -l +100%FREE "$ROOT_DEV" || true
  xfs_growfs / || true
fi

# Local disposable test VM only — never do this on anything that isn't a
# throwaway Vagrant box.
echo 'vagrant ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/91-vagrant-test-only
chmod 440 /etc/sudoers.d/91-vagrant-test-only
