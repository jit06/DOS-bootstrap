# PCChips M915i restoration — working journal

Working notes for a possible future article on bluemind.org. This is a chronology and evidence log, not a finished article. Dates, photographs, benchmark outputs, and board details marked **to record** need verification before publication.

## Hardware and starting point

- Motherboard: PCChips M915i; AMI BIOS dated **1994-10-10**.
- Storage: CompactFlash on IDE, reported as **1,006,992 sectors** of 512 bytes; BIOS geometry **999 cylinders / 16 heads / 63 sectors**. The FAT16 active partition used in the boot investigation started at LBA 2048.
- CPU model and speed, RAM, graphics card, CompactFlash model, adapter, BIOS identifier beyond the date, and other configuration: **to record**.
- BIOS reported LBA enabled. The exact translation settings and photographs of the setup: **to record**.

## Boot investigation, in order

1. **Linux preparation and first failure.** DOS-bootstrap created an MBR/FAT16 disk and installed MS-DOS 6.22 with an alternative VBR. The resulting installation booted in QEMU but froze on the physical machine. Changing PIO modes, IDE Block Mode and 32-bit transfers, and replacing the CMOS battery did not resolve the reported freeze. Record the exact BIOS settings and visible screen state: **to record**.

2. **Geometry mismatch in an earlier QEMU image.** An image prepared with 255 heads and 63 sectors per track failed under a QEMU BIOS exposing 32/63. A trace showed an `IO.SYS` CHS read failing with AH=`01`; changing the BPB head count to 32 on a disposable copy let it reach the MS-DOS startup and date prompt. This established a geometry problem in that configuration, not the later physical-machine root cause. Disk initialization subsequently gained explicit BIOS geometry and consistent BPB and MBR CHS fields.

3. **QEMU comparison for the CompactFlash layout.** An exact RAW copy of the card booted under QEMU using 999/16/63 with `bios-chs-trans=none`, while the physical 486 froze. Both the Syslinux and traditional DOS MBR variants later booted the DOS installation in QEMU but froze physically. The matching QEMU result alone did not establish BIOS compatibility.

4. **Sector-zero diagnostic.** The permanent `MBR OK` diagnostic ran on the physical machine and displayed **`MBR OK`**. This established that BIOS could load sector zero, execute real-mode code, and display text. The diagnostic overwrote the complete first sector, including the partition table; the disk layout was recreated afterwards.

5. **BIOS read diagnostic.** The permanent INT 13h diagnostic displayed **`MBR START`**, **`READING...`**, and **`DISK READ OK`** on the physical machine. This established that one BIOS CHS read of the active partition's first sector returned successfully. It did not establish that all later reads or extended BIOS reads would succeed.

6. **MBR-to-VBR diagnostic.** A diagnostic MBR successfully read and transferred execution to a diagnostic VBR on the physical machine. The expected sequence includes `MBR START`, `READING...`, `DISK READ OK`, `JUMPING TO VBR`, and `VBR OK`; the exact captured screen transcript or photograph remains **to record**. This narrowed the investigation beyond a basic MBR-to-VBR transfer. Both sectors were backed up by the diagnostic installer.

7. **Other boot code.** The installation using DOSbootI booted in QEMU but froze on the physical 486. A traditional DOS/Windows NT MBR produced by `ms-sys --mbrdos` also booted the installation in QEMU and froze physically. Replacing the FAT16 VBR using `ms-sys --fat16` likewise froze physically. These results did not isolate the exact failing instruction or prove that one component alone caused the freeze.

8. **Microsoft `SYS C:` reference.** A disk was prepared with `setup_disk.sh --heads 16 --sectors 63`, cloned to `dos622-native.img`, and booted in QEMU from the original MS-DOS 6.22 floppy. After running `SYS C:`, the RAW image booted in QEMU. The image was then copied back to the CompactFlash and MS-DOS 6.22 booted successfully on the physical 486. The working reference retained a Syslinux-family MBR.

9. **Native VBR comparison.** Read-only inspection showed that bytes `0x3e–0x1fd` of the working hard-disk VBR matched the original MS-DOS 6.22 installation floppy. The BPBs differed as appropriate for hard disk and floppy. In the reference, `IO.SYS` and `MSDOS.SYS` were root entries 0 and 1, and the first three physical sectors of `IO.SYS` were contiguous. `SYS C:` also transferred system files and changed their layout, so the VBR difference alone was not a controlled proof of cause.

10. **Linux-native installation.** DOS-bootstrap was changed to obtain the Microsoft VBR code from the supplied original floppy at installation time, preserve the target BPB, and verify the system-file order and placement. A disposable 999/16/63 image reached the MS-DOS command prompt in QEMU, and the Linux-native installation subsequently booted successfully on the physical machine. The exact command transcript, boot photograph, and repeated boot results: **to record**.

The QEMU geometry used for the CompactFlash comparison can be reproduced with a suitable copy of the RAW image:

```bash
qemu-system-i386 -machine pc -cpu 486 -m 16 \
  -drive file=REFERENCE-COPY.img,format=raw,if=none,id=dosdisk \
  -device ide-hd,drive=dosdisk,bus=ide.0,unit=0,cyls=999,heads=16,secs=63,bios-chs-trans=none \
  -snapshot -boot c
```

Use a separate copy and substitute its path. QEMU's snapshot mode keeps guest writes off the base image. QEMU is for comparison, not an installation dependency.

## Benchmarks and L2 cache

Benchmark tools were run and the L2 cache chips were reported as potentially fake. The available record does not contain the tool versions, raw results, exact messages, cache-chip markings, electrical or board inspection, or test conditions. Those details are **to record** before treating the suspected fake cache as independently established or drawing a quantitative conclusion.

- Baseline benchmark commands, raw output, date and configuration: **to record**.
- Evidence for the cache finding, including photographs and any BIOS or software reports: **to record**.
- CPU and memory settings and whether cache was enabled: **to record**.
- Before/after results following installation of verified working cache: **to measure**.

## Hypotheses and unresolved questions

- DOSbootI attempts INT 13h extended reads before falling back to CHS. An incompatibility with the BIOS is plausible, but no physical trace established where it froze. Native `SYS C:` also changed system-file placement and other disk metadata.
- Fake or nonfunctional L2 cache is a reported suspicion; the underlying benchmark and physical evidence must be collected before confirmation.
- A successful boot does not validate every sector of the CompactFlash or every BIOS and IDE configuration.

## Next work

1. Identify the exact BIOS revision and investigate a suitable BIOS upgrade, its compatibility, and a recovery method before flashing.
2. Document the fitted cache circuitry and determine whether real, compatible L2 cache can be installed; record chip markings and jumper requirements rather than guessing them.
3. Repeat the same documented benchmark suite before and after any cache change; preserve raw output, tool versions, settings and photographs.
4. Record the complete system configuration and collect source photographs and exact observations for the future article.

Useful product commands and diagnostic recovery procedures are maintained in [Documentation.md](Documentation.md) and [diagnostics/README.md](diagnostics/README.md). They can overwrite disks; verify the target and back up data before use.
