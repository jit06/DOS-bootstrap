# DOS-bootstrap documentation

- [DOS-bootstrap documentation](#dos-bootstrap-documentation)
  - [Initialize a disk or raw image](#initialize-a-disk-or-raw-image)
  - [Generic package workflow](#generic-package-workflow)
    - [Package lists and sources](#package-lists-and-sources)
    - [Generate and edit descriptions](#generate-and-edit-descriptions)
    - [Install packages](#install-packages)
    - [MS-DOS 6.22 installation](#ms-dos-622-installation)
    - [Benchmark pack](#benchmark-pack)


## Initialize a disk or raw image

For isolated BIOS boot and disk-read troubleshooting, see the [standalone boot diagnostics](diagnostics/README.md). These are separate from normal disk and package installation.

`setup_disk.sh` creates a DOS/MBR partition table, writes generic Syslinux MBR boot code, and formats FAT16 primary partitions. It does not install an operating system or a DOS-specific volume boot record; its output is not yet a bootable MS-DOS installation. **All existing data on the selected target will be destroyed.**

Install the host commands `sfdisk`, `mkfs.fat`, and `ms-sys` (the latter must support `--mbrsyslinux`), together with the standard Linux utilities checked by the script. `ms-sys` is a normal host dependency; it is not downloaded or compiled by the project. Run as root for a block device; regular raw images can be prepared without root if writable.

```bash
# Create a disposable image, then initialize it:
truncate -s 512M disk.img
./setup_disk.sh --disk disk.img

# A physical whole disk requires interactive confirmation of its full path:
sudo ./setup_disk.sh --disk /dev/sdX --config partitions.config
```

Use `--help` for options. The target image must already exist. Physical disks must be whole devices with 512-byte logical sectors and must not be mounted, in use as swap, or held by another block device. Images attached to a loop device are rejected. Targets must have an even number of sectors: `mkfs.fat` can otherwise write an incorrect FAT total-sector count when formatting at an offset. The script validates the configuration and displays the resolved layout before writing; a physical disk additionally requires the full resolved path to be typed at the prompt. Do not use a disk containing data you need.

The default `partitions.config` is `C:`, a single FAT16 partition using the available space. Each noncomment line has the format `LETTER:FILESYSTEM:SIZE[:boot]`. For example, three partitions with the second active:

```text
C:fat16:128M
D:fat16:128M:boot
E:fat16:
```

Letters start at C and must be consecutive, up to F. They identify partitions in the configuration; DOS determines its own runtime drive letters. The optional fourth field `boot` marks the active partition; otherwise the first is active. Only one marker is allowed. The filesystem defaults to `fat16`, the only supported value. Sizes use binary `K`, `M`, or `G`; an omitted size uses remaining space and is allowed only for the last entry. For example, `C:::boot` specifies the default filesystem and remaining space with an explicit boot flag. Blank lines and comments beginning with `#` are ignored. Partition starts are aligned to 1 MiB, which may leave a small amount of disk space unused. A FAT16 partition cannot exceed 2 GiB and must contain a valid FAT16 cluster count. Unsupported layouts fail before the target is modified.

The BPB hidden-sector field is set to the partition start. By default, the script uses 63 sectors per track and selects the smallest head count in `16, 32, 64, 128, 255` for which the entire disk fits in 1024 cylinders. For example, a 512 MiB image selects 32/63. This is a compatibility heuristic based on capacity, not a measurement or guarantee of target BIOS geometry; BIOS translation rules vary. A disk larger than the largest automatic profile is rejected rather than truncated.

Use `--heads N --sectors N` together to override the heuristic with the **logical geometry exposed by the target BIOS**, with 1–255 heads and 1–63 sectors per track. The script displays whether the geometry was selected automatically or explicitly. The same values determine the FAT16 BPB and both MBR CHS addresses. Every partition must fit within the 1024-cylinder CHS encoding limit; an unrepresentable layout is rejected before writing. MS-DOS 6.22 uses the BPB geometry for BIOS CHS reads, so a mismatch with the BIOS logical geometry can prevent it from booting.

For the 512 MiB QEMU image and translation configuration below, the logical geometry is 32/63. Verify the geometry exposed by your target BIOS before preparing a physical disk, and specify it explicitly. If the disk or intended layout exceeds BIOS-addressable capacity, use a smaller disk or a layout within that capacity; do not assume the entire device is accessible. The options do not specify the BIOS cylinder count, which may be less than 1024: a successful boot alone does not validate access to the entire FAT16 partition. Test the intended address range on the target. Changing geometry on an existing installation is not supported; prepare a new image or reinitialize the disk after backing up its contents.

Run `python3 tests/test_setup_disk.py` for disposable-image tests. Most tests use an isolated `ms-sys` stand-in; an additional test validates MBR installation with the real tool when it is available.

For a disposable 512 MiB QEMU test, choose a new filename: `truncate` and setup overwrite an existing image. The example uses 32/63 geometry for this QEMU translation; it is not a general physical-BIOS setting.

```bash
truncate -s 512M dos622.img
./setup_disk.sh --disk dos622.img --heads 32 --sectors 63
./package.sh --disk dos622.img --install dos622,benchmark
qemu-system-i386 -machine pc -cpu 486 -m 16 \
  -drive file=dos622.img,format=raw,if=none,id=dosdisk \
  -device ide-hd,drive=dosdisk,bus=ide.0,unit=0,cyls=1040,heads=16,secs=63,bios-chs-trans=large \
  -boot c
```

QEMU is optional and is not an installation dependency. The package command runs after setup and does not create or format the disk.

## Configuration

`main.config` is sourced by the package entry points as trusted Bash configuration. It defines only package and cache locations; it does not select a disk. Environment values take precedence over these defaults:

| Setting | Default and use |
| --- | --- |
| `PACKAGES_BASE_DIR` | The repository's `packages` directory, resolved from the script location. Used for named `--install` packages. |
| `TMP_DOWNLOAD_DIR` | `$XDG_CACHE_HOME/dos-bootstrap/downloads`, or `$HOME/.cache/dos-bootstrap/downloads` when `XDG_CACHE_HOME` is unset. If both are unset, `${TMPDIR:-/tmp}/dos-bootstrap-$UID/downloads` is used. |
| `TMPDIR` | Standard environment variable for temporary work; when unset, the system temporary directory is used. It must name an existing writable directory. Temporary staging is cleaned up; the download cache persists unless `--clean-on-close` is used. |

Relative overrides and local package sources are resolved from the caller's working directory; paths containing spaces are supported. When using `sudo`, environment handling and the effective user's home directory may result in a different cache. Use an explicit approved `TMP_DOWNLOAD_DIR` when sharing a cache. An explicit `--disk`, `--config`, `--package-list` or `--packages-path` controls its respective operation; configuration never supplies a target disk. Treat `main.config` as code and edit it only from trusted sources.

For example, after preparing a disk, a cache path can be supplied explicitly to a root-run package installation:

```bash
sudo env TMP_DOWNLOAD_DIR="/path with spaces/cache" ./package.sh --disk /dev/sdX --install dos622
```

## Generic package workflow

`generate_package.sh` creates editable `.desc` mappings; it never installs packages. `package.sh` obtains the sources, extracts all selected components, builds an isolated staging tree, optionally runs `--post-script` there, and then transfers staged files to existing FAT16 partitions. It does not initialize, repartition, or format disks. A failed transfer can leave some files updated; the script reports the failure and stops.

### Package lists and sources

A `.list` file contains a component name and local path or HTTP(S) URL per line. Blank lines and comments are ignored. Quote paths containing spaces:

```text
editor   custom/editor.zip
tools    "custom/DOS utilities"
utility  https://example.org/utility.zip
```

ZIP files, FAT12 floppy images (`.img` and `.ima`), ordinary files and local directories are supported. FAT12 images are read with mtools without mounting; hidden and system files are included. The source must be an unpartitioned FAT12 image with 512-byte sectors and a valid BPB. HTTP(S) files are downloaded with `wget` and cached under `TMP_DOWNLOAD_DIR`; `--clean-on-close` removes the selected downloaded files. A failed download or extraction stops the operation. Cached files are keyed by URL, but are not authenticated by checksums. ADF and LHA are Amiga formats and are not supported; ISO and non-FAT12 disk-image extraction are not implemented.

Named sets use `packages/NAME.list` and `packages/NAME/`; select one or more in order with `--install NAME[,NAME...]`. Alternatively provide a list and description directory explicitly.

### Generate and edit descriptions

```bash
./generate_package.sh --package-list mypackages.list --packages-path packages/mypackages
```

For each entry the generator inspects its ZIP or directory and writes `component.desc` with one mapping per file, initially under `C:component/`. Existing descriptions of the same name are overwritten. Review and edit the generated mappings:

```text
"README.TXT"       "C:TOOLS/README.TXT"
"BIN/EDITOR.EXE"   "D:APPS/EDITOR.EXE"
"DOCS/"            "C:TOOLS/DOCS/"
```

Each line is a source path within the extracted component and a logical-volume destination. A directory mapping copies its contents recursively to the named destination. Paths are parsed as quoted text, not executed as shell commands. Absolute paths, `..`, symlinks, unsupported FAT characters and malformed mappings are rejected. Later components replace matching staged files, case-insensitively. File names can use FAT long names; MS-DOS 6.22 may expose their short aliases instead.

During staging, files with the Microsoft KWAJ signature are expanded using the host's `libmspack` shared library before later components and `--post-script` run. KWAJ methods 0–3 with a recorded uncompressed length are supported. Detection uses the file signature; an ordinary file ending in `_` is copied unchanged. Since KWAJ files do not always record the original filename, the description must specify it explicitly, for example:

```text
"EMM386.EX_" "C:DOS/EMM386.EXE"
```

An unresolved destination ending in `_`, an unsupported KWAJ method or missing length, an SZDD file, or decompression failure stops installation before target transfer. Directory mappings containing compressed files therefore need explicit file mappings when their destination names must change. Expanded files replace their staged destinations; compressed copies are not staged. Existing compressed files from a previous installation are not removed from the target.

### Install packages

`package.sh` installs packages onto an existing whole RAW image or physical disk. It does not create images, partition disks or format filesystems. Its options are:

| Option | Meaning |
| --- | --- |
| `-d`, `--disk PATH` | Required target whole disk or RAW image. |
| `-i`, `--install NAME[,NAME...]` | Install named packages in the supplied order. |
| `-l`, `--package-list FILE` | Use a specific component list. |
| `-p`, `--packages-path DIR` | Use its description directory; required with `--package-list`. |
| `-c`, `--config FILE` | Partition configuration; defaults to the repository's `partitions.config`. |
| `-s`, `--post-script FILE` | Source a trusted script after staging and before transfer. |
| `--clean-on-close` | Remove selected downloaded cache files after successful installation. |
| `-h`, `--help` | Show usage. |

`--install` cannot be combined with `--package-list` or `--packages-path`. A named package is resolved using `PACKAGES_BASE_DIR` in `main.config`.

```bash
# RAW image initialized with setup_disk.sh:
./package.sh --disk disk.img --config partitions.config \
  --package-list mypackages.list --packages-path packages/mypackages

# An initialized physical whole disk (requires access to the device):
sudo ./package.sh --disk /dev/sdX --config partitions.config --install mypackages
```

The partition configuration must match the target's partition count, start positions and sizes. `C:` and `D:` are logical identifiers assigned by that configuration, not guarantees of DOS drive-letter assignment. The installer checks the MBR and FAT16 metadata, refuses mounted or held physical devices and loop-attached images, and uses mtools with byte offsets to access the whole image or disk. `mcopy`, `mdir`, `mmd` and `mattrib` from mtools, Python 3, `wget`, the `libmspack` runtime library, and the Linux device-inspection tools must be installed on the host. The installer checks for libmspack before staging. Existing unrelated target files are retained; matching files are replaced. Existing targets with names that mtools cannot safely replace cause a failure.

Use `--post-script ./customize.sh` to source a user-supplied Bash script after staging and before transfer. Its working directory is `$STAGING_ROOT`, with one directory per selected logical volume (for example `C` and `D`). Logging functions are available. The script is trusted code; [pp-template.sh](pp-template.sh) is an example, not an automatic hook. A failing script or invalid staged path prevents transfer.

Ordinary packages do not modify a DOS boot sector. FAT source attributes are not generally preserved; the `dos622` installation below explicitly sets its required system-file attributes. Raw-image tests require mtools and can be run with `python3 tests/test_packages.py` and `python3 tests/test_dos622_boot.py`. Physical-device handling and booting require separate hardware validation.

### MS-DOS 6.22 installation

`--install dos622` obtains the three floppy images specified in `packages/dos622.list`. Its default sources are archive.org URLs, downloaded through the package cache. To use local original media instead, replace the URLs with local image paths while retaining the component names. Relative source paths are resolved from the current working directory. Users are responsible for having appropriate rights to use the media; hosting at archive.org does not grant those rights. The images and Microsoft binaries are not included in the repository.

For example, local copies can be listed as:

```text
Dos622-Disk1 media/dos-622-disk1.img
Dos622-Disk2 media/dos-622-disk2.img
Dos622-Disk3 media/dos-622-disk3.img
```

The installer recognizes the DOS-specific boot operation by the list filename `dos622.list` and the `Dos622-Disk1` component name, including when an explicit `--package-list` is used.

The individual `Dos622-Disk1`, `Dos622-Disk2` and `Dos622-Disk3` descriptions place `IO.SYS`, `MSDOS.SYS` and `COMMAND.COM` in the C: root, and the remaining files in `C:\\DOS`.

The 86 KWAJ-compressed files, including `HIMEM.SY_`, `EMM386.EX_`, `XCOPY.EX_` and `EDIT.HL_`, are expanded to their usable names using mappings from the media's `PACKING.LST`. The included DOS `EXPAND.EXE` is not executed.

The target must use the matching `partitions.config`, have C: as its only active partition, and have BIOS drive number `0x80` in its BPB.

The `dos622` package uses the original Microsoft MS-DOS 6.22 VBR. At installation time it validates the 1.44 MB disk-1 FAT12 image, its boot-code fingerprint and the three original system files. It takes the entry jump and boot-code bytes `0x3e–0x1fd` from that floppy and combines them with the target FAT16 BPB and extended BPB at bytes `0x03–0x3d`, retaining the target signature. The same code region was observed in a working hard-disk VBR. The supported source is the verified Microsoft 6.22 disk-1 variant; other DOS versions, OEM variants and directory-only sources are rejected.

No precompiled Microsoft VBR is shipped or needed in `contribs/`: it is extracted from the source media at runtime. No external binary currently needs to be downloaded or built into `contribs/`; an older local `contribs/` cache may contain unused historical material and is not part of the installation. [ms-sys](https://ms-sys.sourceforge.net/) is a GPL host tool; it provides the selected Syslinux MBR, while rights in other boot-record bytes it supplies should be assessed separately. [libmspack](https://github.com/kyz/libmspack) is an LGPL library used for decompression. mtools and all other host programs have their own licenses. Microsoft boot code and system files are not bundled; use of source media is subject to its applicable rights and licensing.

The package installs `IO.SYS` and `MSDOS.SYS` in the first two root-directory entries, applies their read-only, hidden and system attributes, and checks file contents, FAT chains and the first three contiguous `IO.SYS` sectors. It rejects occupied first entries and incompatible existing system files without relocating unrelated files. Only after other staged files are transferred does it install and verify the native VBR, preserving the target BPB, geometry, volume metadata and signature. `DRVSPACE.BIN` remains in `C:\\DOS`; the package does not attempt to duplicate every detail of `SYS C:`.

Existing unrelated files are retained. Existing system files are accepted for reinstall only when all three already match and satisfy the boot checks; incomplete or incompatible existing files are rejected. A failed file transfer can leave some files updated but does not install a new VBR. If the VBR write fails, the installer attempts to restore and verify the original sector.

The BPB geometry and drive assumptions must match the real BIOS. Native boot has been tested in QEMU and on one physical system; other BIOS and storage combinations require their own validation. The package does not configure the DOS environment or run Microsoft Setup, and placing the supplied files is not a substitute for validating a complete installation.

### Benchmark pack

`--install benchmark` downloads Phil's DOS Benchmark Pack v1.6 and installs its directory tree under `C:\\BENCH`. From that directory, `DOSBENCH.BAT` offers its menu, including TOPBENCH, CACHECHK, SpeedSys, 3DBench, Landmark and other included tools. Some tools have hardware or memory requirements beyond a particular configuration. The pack's binaries are not bundled in this repository; the pack and its components have their own licensing terms.

## Testing and troubleshooting

Run `python3 -m unittest discover -s tests` for the automated suite. Tests use disposable RAW images; QEMU checks are skipped if it is unavailable, and tests using original media are skipped if that media is unavailable. See [diagnostics/README.md](diagnostics/README.md) for separate boot diagnostics, their destructive effects and recovery procedures. QEMU is optional and is not an installation dependency.
