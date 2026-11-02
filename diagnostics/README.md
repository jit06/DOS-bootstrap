# BIOS boot diagnostics

Use the diagnostics in sequence. `MBR OK` checks that the BIOS loads and executes sector zero and can display text. `MBR READ` checks whether BIOS INT 13h can read the first sector of the active partition using the CHS address stored in its MBR entry. Both halt deliberately and do not boot DOS.

`MBR/VBR` adds a controlled handoff: the MBR reads a diagnostic VBR and jumps to it; the VBR displays `VBR OK` and halts. This isolates VBR execution before investigating the operating-system bootloader.

## 1. MBR OK

This diagnostic replaces **all of LBA 0**, including the MBR boot code and partition table, with a sector that prints `MBR OK` using BIOS video services and then deliberately halts. It does not read partitions or any other disk sector. A successful message shows that the BIOS loaded and executed this sector; it does not validate the IDE controller, later disk reads, DOS or the rest of the card. Once halted, keyboard input and Ctrl+Alt+Del are intentionally disabled; reset or power-cycle the PC.

**The partition table will be erased.** Data beyond LBA 0 is not intentionally written, but the existing installation will no longer be accessible through its old partition table. Back up anything needed first. The target must be a whole 512-byte-sector block device with no mounted partitions, active swap or holders. The writer requires root, an interactive terminal, and NASM or GNU binutils (`as`, `ld`, `objcopy`).

Before running it, identify the CompactFlash by its size, model, serial number and connection. `/dev/sdc` is an example and may refer to a different device on another boot:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TYPE,MOUNTPOINTS
sudo swapon --show --noheadings --raw --output NAME
sudo ./diagnostics/write-mbr-ok.sh /dev/sdc
```

Read the displayed identity again. To authorize the write, type the exact confirmation requested by the script, for example `ERASE /dev/sdc` if that is the resolved target path. The script rechecks for busy devices immediately before writing exactly 512 bytes, then reads back and compares the sector. This is separate from `setup_disk.sh` and `package.sh`. Reinitialize the card and reinstall its contents after the experiment.

## Optional QEMU check

The following creates a new temporary 16 MiB image, writes the diagnostic sector to it, and starts QEMU with snapshot mode. It does not use an existing disk image. Install NASM first:

```bash
diag_dir=$(mktemp -d)
nasm -f bin diagnostics/mbr-ok.asm -o "$diag_dir/mbr.bin"
truncate -s 16M "$diag_dir/mbr-test.img"
dd if="$diag_dir/mbr.bin" of="$diag_dir/mbr-test.img" bs=512 count=1 conv=notrunc
qemu-system-i386 -machine pc -cpu 486 -m 16 \
  -drive file="$diag_dir/mbr-test.img",format=raw,if=ide -snapshot -boot c
```

Expect `MBR OK` and a halted guest; close QEMU to stop it. If NASM is unavailable, generate the sector with GNU binutils instead:

```bash
as --32 diagnostics/mbr-ok.S -o "$diag_dir/mbr.o"
ld -m elf_i386 -Ttext 0x7c00 -e _start "$diag_dir/mbr.o" -o "$diag_dir/mbr.elf"
objcopy -O binary -j .text "$diag_dir/mbr.elf" "$diag_dir/mbr.bin"
```

Run the `truncate`, `dd` and QEMU commands above after the alternative build. Remove the temporary directory when finished.

## 2. MBR READ

The first diagnostic erases the partition table. A disk affected by `MBR OK` needs a valid partition layout recreated before `MBR READ` can be installed. Recreating the layout formats the disk and destroys its current contents. Confirm the device identity and the target BIOS geometry first; replace `HEADS` and `SECTORS` below with the correct values:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TYPE,MOUNTPOINTS
sudo ./setup_disk.sh --disk /dev/sdc --heads HEADS --sectors SECTORS
sudo ./diagnostics/write-mbr-read.sh /dev/sdc
```

`/dev/sdc` is only an example; check it every time. The reader requires a valid MBR with exactly one valid active partition. It preserves the disk signature, reserved bytes, all partition entries and the boot signature, replaces only the boot-code region, and verifies the entire sector after writing. It does not write the partition or its VBR. Installation requires Python 3, GNU binutils (`as`, `ld`, `objcopy`), root and an interactive terminal. Confirm by typing the exact `WRITE /dev/sdc` prompt, using the resolved path displayed by the tool.

Boot the physical PC from the card and observe:

- **`MBR START` only:** execution began, but the diagnostic did not reach the disk-read call. An invalid partition is reported separately if validation completes.
- **`READING...` with no subsequent result:** the BIOS INT 13h call did not return, or execution stopped before reporting its return.
- **`DISK READ OK`:** INT 13h reported that the active partition's first sector was read; the diagnostic does not validate the contents.
- **`DISK READ ERROR AH=XX`:** INT 13h returned with carry set; `XX` is the BIOS status code in hexadecimal.

After a result, the program intentionally disables interrupts and halts. Reset or power-cycle to continue. It preserves the BIOS boot drive number and does not execute the VBR.

To restore a normal DOS-bootstrap installation after either diagnostic, identify the card, rerun `setup_disk.sh` with the correct target BIOS geometry, then run `package.sh --disk /dev/sdc --install dos622,benchmark` as root. This recreates and formats the layout and reinstalls the selected packages; it destroys existing card contents. Use the appropriate package selection and device path for your setup.

For automated disposable-image checks, run `python3 tests/test_mbr_read.py`; the QEMU test is skipped when QEMU is unavailable. It never accesses a physical disk.

## 3. Experimental DOS/Windows NT MBR

`write-mbr-dos.sh` tests the existing host `ms-sys --mbrdos` implementation; it does not change `setup_disk.sh`. The [ms-sys documentation](https://ms-sys.sourceforge.net/) calls this a DOS/Windows NT MBR, and its [2.8.0 source](https://fossies.org/linux/privat/ms-sys-2.8.0.tar.gz/ms-sys-2.8.0/inc/mbr_dos.h) identifies the embedded bytes as the MBR used from DOS 3.3 onward and by Windows 95A. Inspection of the 2.8.0 output shows that it selects the active entry, uses BIOS CHS reads to load its VBR, checks the VBR signature, and transfers control. It uses BIOS drive `0x80`, so use it for the first hard disk. Unlike the current Syslinux MBR, it does not support LBA reads past cylinder 1023. Actual hardware behavior needs testing.

The ms-sys project is distributed under the GNU GPL ([project page](https://ms-sys.sourceforge.net/)). Its source describes the DOS MBR bytes as originating from DOS; their separate provenance or redistribution rights have not been independently established. This repository does not bundle those bytes: the utility uses the locally installed `ms-sys`. The user must independently have rights to any MS-DOS files used for booting.

The tool requires a valid MBR with one active partition, a whole unmounted block device, root and exact interactive confirmation. It runs `ms-sys` against a temporary sector, checks that bytes 440–511 are unchanged, rechecks the target, writes only LBA 0 and verifies the readback. The VBR and DOS files are not changed. QEMU testing does not establish compatibility with a physical BIOS.

Check the disk's identity before **every** command; `/dev/sdc` is an example. If it already has the correct active FAT16 partition, VBR and DOS files, keep that layout. If you need to prepare it first, the following commands destroy existing contents; substitute the target BIOS geometry:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TYPE,MOUNTPOINTS
sudo ./setup_disk.sh --disk /dev/sdc --heads HEADS --sectors SECTORS
sudo ./package.sh --disk /dev/sdc --install dos622
```

Then install the experimental MBR, verify the printed identity and type the displayed confirmation, such as `WRITE /dev/sdc`:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TYPE,MOUNTPOINTS
sudo ./diagnostics/write-mbr-dos.sh /dev/sdc
```

Boot the 486 with the card as its first hard disk and record the result. To restore the normal DOS-bootstrap Syslinux MBR without reformatting or reinstalling DOS, reconnect the card to Linux, recheck its identity, and run:

```bash
sudo ./diagnostics/write-mbr-dos.sh --restore-syslinux /dev/sdc
```

This restoration also requires the exact displayed confirmation and preserves the partition table, VBR and files. It regenerates the normal Syslinux boot code; it does not restore an arbitrary earlier diagnostic sector byte for byte. The regression test is `python3 tests/test_mbr_dos.py`; its QEMU test is skipped when QEMU is unavailable.

## 4. MBR-to-VBR handoff test

This test **replaces both the MBR and the active FAT16 VBR**. Its MBR preserves the disk signature and partition table; its VBR preserves the FAT16 BPB and extended BPB. The installer checks the active entry, FAT16 metadata, partition boundaries, and agreement between the partition's CHS address and its BPB geometry. It cannot determine whether that geometry matches the physical BIOS. The card needs an existing valid active FAT16 partition. If its partition table was erased by `MBR OK`, reinitialize it first with `setup_disk.sh`; reinitialization formats the card and destroys its contents.

Identify the CompactFlash before running the test. `/dev/sdc` is an example only. Select a **new** backup directory on storage separate from the card; the script refuses an existing directory and saves both original sectors there before modifying the card. Keep the backup until restoration is complete. The directory contains boot-sector code and should be protected from accidental disclosure or deletion.

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TYPE,MOUNTPOINTS
sudo swapon --show --noheadings --raw --output NAME
sudo ./diagnostics/write-mbr-vbr-test.sh --backup-dir /var/tmp/cf486-handoff-backup /dev/sdc
```

Confirm the displayed identity by typing the exact requested text, such as `WRITE /dev/sdc`. The tool refuses mounted devices, active swap and held devices, checks the original sectors, flushes the backup, writes only the active VBR and MBR sectors, and reads each back. A write or readback failure can leave one or both sectors changed; retain the backup and restore it before using the card. This requires Python 3 and GNU binutils.

Boot the disk as the first hard disk. Expected output, in order, is `MBR START`, `READING...`, `DISK READ OK`, `JUMPING TO VBR`, and `VBR OK`. A stop after `JUMPING TO VBR` narrows the failure to the handoff or VBR execution. `VBR OK` confirms this diagnostic VBR executed; it does not establish that the DOS bootloader or remaining disk reads work. The final halt is deliberate and interrupts are disabled; reset or power-cycle the PC.

To restore **both** backed-up sectors, reconnect the same card to Linux, verify its identity again, and confirm the displayed request:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TYPE,MOUNTPOINTS
sudo ./diagnostics/write-mbr-vbr-test.sh --restore /var/tmp/cf486-handoff-backup /dev/sdc
```

Restoration checks the backup hashes and that the card's partition metadata and BPB still match. It restores the exact sectors that existed at installation time, which may themselves be earlier diagnostic sectors. Do not repartition or reformat between installation and restoration. Restoring only the MBR is **not sufficient**: the diagnostic VBR also has to be replaced.

If the original backup is unavailable, the normal Syslinux MBR can be restored using `write-mbr-dos.sh --restore-syslinux`, and the native Microsoft VBR reinstalled with `sudo ./package.sh --disk /dev/sdc --install dos622` provided the partition layout and DOS system files still satisfy the package checks. Verify the device path and BIOS geometry first; package installation does not recreate missing partitions. If those checks fail, recreate the disk layout and reinstall DOS, which destroys the existing contents.

Run `python3 tests/test_mbr_vbr.py` for disposable-image checks, including QEMU handoff when QEMU is installed. No physical disk is used by these tests.
