# DOS-bootstrap

DOS-bootstrap prepares MBR/FAT16 disks and installs MS-DOS 6.22 and optional packages from Linux. It supports RAW images and physical disks for legacy BIOS PCs. Installation uses native Linux tools; QEMU is optional for testing.

**MS-DOS is not included.** The default package list downloads floppy images from archive.org, or you can use local media. You are responsible for having appropriate rights to use the images; their availability online does not grant such rights. The native boot installer accepts a specific verified 1.44 MB MS-DOS 6.22 disk-1 variant; other editions and OEM variants may be rejected. Third-party packages have their own licenses.

## Requirements

Use Linux with Bash, Python 3, `sfdisk`, `mkfs.fat`, `ms-sys` (with `--mbrsyslinux`), mtools, `wget`, and the `libmspack` runtime library. Standard Linux device-inspection utilities are also required. Preparing a physical device requires root. See [Documentation.md](Documentation.md) for details and compatibility limits.

## Quick start

From the repository directory, choose a **new** image filename. The following commands overwrite an existing image of the same name:

```bash
truncate -s 512M dos-test.img
./setup_disk.sh --disk dos-test.img --heads 32 --sectors 63
./package.sh --disk dos-test.img --install dos622
```

Use geometry matching the target BIOS: 32/63 is an example, not a universal setting. `setup_disk.sh` destroys all data on its target; an existing image can also be overwritten. For a physical disk, identify the whole device first and verify its BIOS geometry:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TYPE,MOUNTPOINTS
sudo ./setup_disk.sh --disk /dev/sdX --heads HEADS --sectors SECTORS
sudo ./package.sh --disk /dev/sdX --install dos622
```

Replace `/dev/sdX`, `HEADS` and `SECTORS` with the correct values. Setup checks for mounted or busy devices and asks for confirmation, but you are responsible for selecting the right disk. Back up any data you need before proceeding.

See [Documentation.md](Documentation.md) for partition configuration, packages, native boot requirements, tests and QEMU usage. [Boot diagnostics](diagnostics/README.md) provide separate, potentially destructive troubleshooting tools. Hardware compatibility must be validated on the target machine.

The application license is in [LICENSE](LICENSE). External tools, benchmark packages and MS-DOS media have separate licenses.
