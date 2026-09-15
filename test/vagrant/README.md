# Phase 2 Test Environment — Vagrant + QEMU

A local, disposable 19c CDB to run `cpu_patch_precheck.yml` /
`cpu_patch_apply.yml` against for real, per `docs/ROADMAP.md` Phase 2.
**This is a first-draft scaffold, not yet run end to end** — expect to
debug it on first use (see "Known rough edges" below).

## Status / honesty check

- Nothing here has been executed. Vagrant, QEMU, and the `vagrant-qemu`
  plugin were installed and confirmed working; the box has not been
  booted and Oracle has not been installed.
- Sized for an **8GB-RAM Apple Silicon Mac**: 4GB guest RAM, a 1.5GB DB
  SGA/PGA target, `-ignorePrereqFailure` on the installer. This is
  deliberately undersized for realistic performance testing — it exists
  to prove the *playbooks* work against a real CDB, not to benchmark
  patch-apply duration.
- Oracle Database 19c is **x86_64-only**. This Mac is arm64, so QEMU runs
  the guest under TCG software emulation (no hardware acceleration across
  architectures). Expect the DB software install + `dbca` to take a long
  time — likely over an hour, possibly several. If that's a blocker,
  run this same `test/vagrant/` directory on an x86_64 machine or cloud VM
  instead (native KVM/HVF acceleration there); nothing in it is
  Mac-specific except the provider choice.

## Prerequisites

Already done in this session, listed here for a fresh machine:

```bash
brew install --cask vagrant
brew install qemu
vagrant plugin install vagrant-qemu
```

You also need the actual Oracle Database 19c (19.3) software — **this
can't be downloaded on your behalf.** It requires an Oracle account and
clicking through Oracle's license agreement:

1. Go to Oracle's software download page and get
   `LINUX.X64_193000_db_home.zip` (19.3 base release — CDB creation only
   needs the base release; whatever CPU/RU you're testing gets applied
   *by the playbooks*, that's the point).
2. Place it at `test/vagrant/media/LINUX.X64_193000_db_home.zip`.
3. For the actual Phase 2 patch-apply test, also get a real (even old/
   superseded) CPU/RU patch zip and set it up per the main `README.md`
   (`vars/patches/<patch_id>.yml`, `patch_zip_local_path`).

`test/vagrant/media/` is gitignored — nothing under it (and no Oracle
media) ever gets pushed to GitHub.

## Bring it up

```bash
cd test/vagrant
vagrant up
```

This boots the VM and runs, in order:

1. `provision/00_os_prereqs.sh` — `oracle-database-preinstall-19c` (sets
   kernel params, creates the `oracle` user/`oinstall` group, ulimits),
   a 4GB swapfile, best-effort root-filesystem grow, and a `NOPASSWD`
   sudoers entry for the `vagrant` user (fine for a throwaway local VM,
   never do this anywhere else).
2. `provision/01_install_db_software.sh` — unzips the media, silent
   software-only install (`INSTALL_DB_SWONLY`), then `orainstRoot.sh` +
   `root.sh`.
3. `provision/02_create_cdb.sh` — silent `dbca` create of CDB `CDB1`
   with one PDB (`PDB1`), sized for the 4GB guest. Generates a random
   SYS/SYSTEM password at provision time, stored only inside the VM at
   `/u01/app/oracle/.cdb1_sys_password` (mode 600, oracle-owned) — never
   checked into git.

Each script is written to skip its work if already done, so
`vagrant provision` is safe to re-run after fixing something.

## Point the playbooks at it

Once `vagrant up` finishes cleanly:

```bash
cd test/vagrant && vagrant ssh-config   # confirm the private_key path matches
                                         # inventories/vagrant_test/group_vars/oracle_db_hosts.yml
cd ../..
ansible-playbook -i inventories/vagrant_test/hosts.yml \
  playbooks/cpu_patch_precheck.yml -e @vars/patches/<patch_id>.yml
```

## Known rough edges (read before filing a bug against yourself)

- **`synced_folder` for `./media`**: `vagrant-qemu`'s support for rsync
  sync is version-dependent and multi-GB transfers can be slow/flaky. If
  `vagrant up` fails at the sync step or the zip never appears at
  `/vagrant/media` inside the VM, work around it manually:
  ```bash
  scp -P 2222 -i .vagrant/machines/default/qemu/private_key \
    media/LINUX.X64_193000_db_home.zip vagrant@127.0.0.1:/tmp/
  ```
  `01_install_db_software.sh` already checks `/tmp` as a fallback.
- **Disk too small**: the base box ships a small disk, and `vagrant-qemu`
  has no reliable Vagrantfile option to set primary disk size
  ([ppggff/vagrant-qemu#66](https://github.com/ppggff/vagrant-qemu/issues/66)).
  `00_os_prereqs.sh` attempts a best-effort LVM grow; if the DB install or
  `dbca` still fails on space, resize the box image directly before
  `vagrant up`:
  ```bash
  find ~/.vagrant.d/boxes -path '*oraclelinux*' -name box.img
  qemu-img resize <path-from-above> +30G
  ```
  then re-run `00_os_prereqs.sh`'s growpart/lvextend/xfs_growfs steps by
  hand inside the VM (`vagrant ssh`) if provisioning already ran.
- **`ansible_ssh_private_key_file` path**: `vagrant ssh-config` is the
  source of truth if `inventories/vagrant_test/group_vars/oracle_db_hosts.yml`'s
  default path doesn't match your `vagrant-qemu` version's layout.
- **runInstaller exit code 6**: treated as success ("configuration
  warnings") by `01_install_db_software.sh` — check
  `/u01/app/oraInventory/logs` if the install seems incomplete despite a
  clean provision run.

## Tearing down

```bash
cd test/vagrant
vagrant destroy -f
```

Nothing durable lives outside the VM — `media/` is your own downloaded
zips (keep or delete as you like), everything else is disposable.
