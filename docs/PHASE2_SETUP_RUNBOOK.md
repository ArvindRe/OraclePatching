# Runbook: Set Up the Phase 2 Test Environment

Stands up a local, disposable Oracle 19c CDB (via Vagrant + QEMU) so the
`cpu_patch_precheck.yml` / `cpu_patch_apply.yml` playbooks in this repo can
be run against a real database instead of just syntax-checked. Follow the
steps in order; each one says what to expect before moving on.

**Time estimate:** 1–3 hours hands-on, depending on download speed and
machine. If running on an Apple Silicon Mac (arm64), also budget extra time
for the DB install/CDB creation steps (30 min–a few hours) — Oracle 19c is
x86_64-only, so the VM runs under software emulation on ARM, not hardware
acceleration. **If you have access to a native x86_64 Linux machine or
cloud VM, use that instead — same steps, just faster.**

---

## 1. Install the tooling

```bash
brew install --cask vagrant
brew install qemu
vagrant plugin install vagrant-qemu
```

The `vagrant` cask install asks for your sudo password interactively —
enter it when prompted.

**Verify:**
```bash
vagrant --version      # 2.4.x or later
qemu-system-x86_64 --version
vagrant plugin list     # should list vagrant-qemu
```

---

## 2. Download Oracle Database 19c

Go to:
**https://www.oracle.com/database/technologies/oracle-database-software-downloads.html**

1. Find the **Oracle Database 19c** section, select platform **Linux
   x86-64**.
2. Sign in with (or create) a free Oracle account and accept the license
   agreement — this step can't be skipped or automated.
3. Download **`LINUX.X64_193000_db_home.zip`** (~3GB). This is the 19.3
   base-release "DB Home" zip — you do **not** need Grid Infrastructure,
   RAC, or any other file on that page for this setup.

Place the downloaded file at:
```
test/vagrant/media/LINUX.X64_193000_db_home.zip
```

*(Optional, only needed later for the actual patch-apply test, not to
create the CDB): download a real — even old/superseded — CPU/RU patch zip
from **support.oracle.com** (My Oracle Support, requires a support account)
by patch number. Set that up separately per the main `README.md` when
you're ready for that step.)*

---

## 3. Bring up the VM

```bash
cd test/vagrant
vagrant up
```

This runs three provisioning scripts in order — watch the output, each one
prints what it's doing:

| Step | What it does | Typical time (native / emulated) |
|---|---|---|
| `00_os_prereqs.sh` | OS packages, kernel params, `oracle` user, swap | 2–5 min / 5–15 min |
| `01_install_db_software.sh` | Unzips media, silent Oracle software install | 10–20 min / 30 min–2 hr |
| `02_create_cdb.sh` | Silent `dbca` creates CDB `CDB1` + PDB `PDB1` | 15–30 min / 30 min–2 hr |

**If it fails partway**, fix the issue and re-run just `vagrant provision`
(not `vagrant up`) — each script skips work it's already done.

**If `vagrant up` hangs or fails at copying `test/vagrant/media/` into the
VM**, that's a known sync issue — see "Troubleshooting" below for the
manual `scp` workaround.

---

## 4. Confirm the CDB is up

```bash
vagrant ssh
```
Once inside the VM:
```bash
sudo su - oracle
export ORACLE_HOME=/u01/app/oracle/product/19.0.0/dbhome_1
export ORACLE_SID=CDB1
export PATH=$ORACLE_HOME/bin:$PATH
sqlplus / as sysdba
```
At the `SQL>` prompt:
```sql
SELECT name, open_mode FROM v$database;
SELECT name, open_mode FROM v$pdbs;
```
Expect `CDB1` in `READ WRITE` and `PDB1` in `READ WRITE`. Exit with `exit`
twice to leave `sqlplus` and the VM.

---

## 5. Run the patching playbooks against it

From the repo root (not `test/vagrant/`):

```bash
vagrant -C test/vagrant/ ssh-config   # confirm the private key path printed here
                                       # matches inventories/vagrant_test/group_vars/oracle_db_hosts.yml —
                                       # update that file if it doesn't
```

```bash
ansible-playbook -i inventories/vagrant_test/hosts.yml \
  playbooks/cpu_patch_precheck.yml -e @vars/patches/EXAMPLE_PATCH.yml
```

This should run cleanly (`failed=0`) — it's read-only and only stages
whatever patch zip `vars/patches/EXAMPLE_PATCH.yml` points to. Set up a
real patch definition (per step 2's optional download) before attempting
`cpu_patch_apply.yml`.

---

## 6. Tear down when done

```bash
cd test/vagrant
vagrant destroy -f
```

Your downloaded media zip in `test/vagrant/media/` is untouched — reused
automatically on the next `vagrant up`.

---

## Troubleshooting

**`media/` doesn't sync into the VM** (install script reports "no db_home
zip found"):
```bash
cd test/vagrant
vagrant ssh-config   # note the identity file path
scp -P 2222 -i .vagrant/machines/default/qemu/private_key \
  media/LINUX.X64_193000_db_home.zip vagrant@127.0.0.1:/tmp/
vagrant provision
```

**Install or `dbca` fails on disk space:**
```bash
find ~/.vagrant.d/boxes -path '*oraclelinux*' -name box.img
qemu-img resize <path-from-above> +30G
```
Then `vagrant up` again — `00_os_prereqs.sh` will attempt to grow the
filesystem to use the extra space.

**Anything else:** check `test/vagrant/README.md`'s "Known rough edges"
section, then `/u01/app/oraInventory/logs/` inside the VM for the actual
Oracle installer/dbca log output.
