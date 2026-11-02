# DOS-bootstrap package reference

Packages describe sources and file mappings. Installation stages all selected components, applies an optional trusted post-script, and transfers the staged files to existing FAT16 partitions. See [Documentation.md](Documentation.md#generic-package-workflow) for host requirements and safety details.

## Supported languages

The package-definition convention is English only. The installer does not filter or translate the contents of a source archive.

## Naming convention

- Use a lowercase package name, such as `dos622` or `benchmark`.
- A named package uses `packages/NAME.list` and a `packages/NAME/` directory.
- Each component has a unique name within the package and a matching `COMPONENT.desc` in that directory. Component names can contain letters, digits, `_`, `-` and `.`; preserve capitalization between list and description.
- For multipart media, use a distinct component for each part, such as `Dos622-Disk1`, `Dos622-Disk2` and `Dos622-Disk3`.

## Package organization

| Package | Purpose | Source |
| --- | --- | --- |
| `dos622` | MS-DOS 6.22 system files, utilities, and native boot installation | Three MS-DOS 6.22 floppy images; users must have appropriate rights. |
| `benchmark` | DOS benchmark pack under `C:\\BENCH` | Phil's DOS Benchmark Pack archive; its own terms apply. |

## Package definitions

A `.list` contains one component name and local path or HTTP(S) URL per line. Use shell-style quotes for paths with spaces. Blank lines and comments are ignored. Local relative source paths are interpreted from the caller's working directory.

```text
Editor "local files/editor.zip"
Tools https://example.org/tools.zip
```

The matching `.desc` has one source path and destination per line, both relative to their respective extracted component and logical FAT volume. Quotes protect spaces; paths are parsed as data, not executed.

```text
"BIN/EDITOR.EXE" "C:APPS/EDITOR.EXE"
"DOCS/" "D:MANUALS/"
```

Supported sources are ZIP archives, unpartitioned FAT12 `.img` or `.ima` floppy images, ordinary files, and local directories. FAT12 reads include hidden and system files. Later components replace matching staged paths case-insensitively. Microsoft KWAJ files are detected by signature and expanded before post-processing; an explicit destination must supply the usable filename. Unsupported compression, unsafe paths, or extraction failures stop installation.

## Acquisition and dependencies

HTTP(S) sources are downloaded using `wget` and cached by URL. Local files are read directly. `--clean-on-close` removes selected downloaded cache files after successful work. Ordinary sources are not authenticated by a mandatory checksum; the native `dos622` installer additionally validates disk 1's FAT12 structure, boot-code fingerprint and original system files. The archive.org URLs do not grant a license to use MS-DOS.

Host dependencies are checked by the shell entry points. Installation also requires the libmspack runtime for KWAJ support. Package-to-package dependencies are not automatically resolved: select required packages explicitly in the intended order. In `dos622`, the first component supplies the validated native boot code and original system files.

## Generation and installation

Generate initial editable descriptions without touching a target disk:

```bash
./generate_package.sh --package-list mypackages.list --packages-path packages/mypackages
```

The generator enumerates supported sources and writes one description per component under `C:COMPONENT/`; it overwrites existing descriptions of those names. Review the generated paths and compressed-file destinations before installing. Install a named set or an explicit list and directory:

```bash
./package.sh --disk disk.img --install dos622,benchmark
./package.sh --disk disk.img --package-list mypackages.list --packages-path packages/mypackages
```

The target must already have the matching initialized FAT16 layout. The installer stages selected packages and components in order, runs `--post-script` if supplied, validates the target, and transfers files. Only `dos622` performs the narrowly scoped native MS-DOS boot installation; ordinary packages do not write a VBR.
