#!/usr/bin/env python3
"""Validate, back up, and write only the MBR and active FAT16 VBR."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

from mbr_read_patch import patch

def geometry(mbr, vbr, sectors):
    patch(mbr, bytes(440), sectors)
    entries=[mbr[446+i*16:462+i*16] for i in range(4)]
    active=next(e for e in entries if e[0]==0x80)
    start,size=struct.unpack_from('<II',active,8)
    if active[4] not in (4,6,14): raise ValueError('Active partition must be FAT16 (type 04, 06 or 0e)')
    for e in entries:
        if e is active or not e[4]: continue
        other,length=struct.unpack_from('<II',e,8)
        if not other or not length or other+length>sectors or (other<start+size and start<other+length):
            raise ValueError('Invalid or overlapping partition layout')
    if len(vbr)!=512 or vbr[510:]!=b'\x55\xaa': raise ValueError('Invalid FAT16 boot sector signature')
    bps=struct.unpack_from('<H',vbr,11)[0]; spc=vbr[13]
    reserved=struct.unpack_from('<H',vbr,14)[0]; fats=vbr[16]
    roots=struct.unpack_from('<H',vbr,17)[0]; fatsize=struct.unpack_from('<H',vbr,22)[0]
    spt,heads=struct.unpack_from('<HH',vbr,24)
    hidden=struct.unpack_from('<I',vbr,28)[0]
    total=struct.unpack_from('<H',vbr,19)[0] or struct.unpack_from('<I',vbr,32)[0]
    if (bps!=512 or spc not in (1,2,4,8,16,32,64) or not reserved or fats not in (1,2)
        or not roots or not fatsize or not 1<=heads<=255 or not 1<=spt<=63
        or hidden!=start or total!=size or vbr[38] not in (0x28,0x29)):
        raise ValueError('Invalid or inconsistent FAT16 BPB')
    clusters=(size-reserved-fats*fatsize-(roots*32+511)//512)//spc
    if not 4085<=clusters<=65524 or fatsize*512//2<clusters+2:
        raise ValueError('Invalid FAT16 cluster count or FAT size')
    head=active[1]; sector=active[2]&63; cylinder=active[3]|((active[2]&0xc0)<<2)
    if head>=heads or sector>spt or ((cylinder*heads+head)*spt+sector-1)!=start:
        raise ValueError('Active partition CHS does not match its LBA and BPB geometry')
    return start

def read_sectors(fd, sectors):
    mbr=os.pread(fd,512,0)
    patch(mbr,bytes(440),sectors)
    active=next(mbr[446+i*16:462+i*16] for i in range(4) if mbr[446+i*16]==0x80)
    start=struct.unpack_from('<I',active,8)[0]
    vbr=os.pread(fd,512,start*512)
    geometry(mbr,vbr,sectors)
    return mbr,vbr,start

def write_checked(fd, data, offset, label):
    if os.pwrite(fd,data,offset)!=512: raise OSError(f'Short {label} write')
    os.fsync(fd)
    if os.pread(fd,512,offset)!=data: raise OSError(f'{label} readback verification failed')

def backup_write(directory, name, data):
    fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())

def run(mode, device, sectors, backup, mbr_code=None, vbr_code=None):
    fd=os.open(device,os.O_RDWR)
    try:
        current_mbr,current_vbr,start=read_sectors(fd,sectors)
        if mode=='check':
            print(f'Active FAT16 VBR: LBA {start}'); return
        directory=Path(backup)
        if mode=='install':
            mcode=Path(mbr_code).read_bytes(); vcode=Path(vbr_code).read_bytes()
            if len(mcode)!=440 or len(vcode)!=512 or vcode[:3]!=b'\xeb\x3c\x90' or vcode[510:]!=b'\x55\xaa':
                raise ValueError('Invalid diagnostic boot code')
            new_mbr=patch(current_mbr,mcode,sectors)
            new_vbr=vcode[:3]+current_vbr[3:62]+vcode[62:510]+current_vbr[510:]
            os.mkdir(directory,0o700)
            meta={'start':start,'sectors':sectors,'mbr_sha256':hashlib.sha256(current_mbr).hexdigest(),
                  'vbr_sha256':hashlib.sha256(current_vbr).hexdigest()}
            backup_write(directory,'mbr.bin',current_mbr)
            backup_write(directory,'vbr.bin',current_vbr)
            backup_write(directory,'metadata.json',(json.dumps(meta)+'\n').encode())
            dirfd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(dirfd)
            finally: os.close(dirfd)
            parentfd=os.open(directory.parent,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(parentfd)
            finally: os.close(parentfd)
            print(f'Original sectors backed up to {directory}',flush=True)
        else:
            meta=json.loads((directory/'metadata.json').read_text())
            new_mbr=(directory/'mbr.bin').read_bytes(); new_vbr=(directory/'vbr.bin').read_bytes()
            if (meta['start']!=start or meta['sectors']!=sectors or len(new_mbr)!=512 or len(new_vbr)!=512
                or hashlib.sha256(new_mbr).hexdigest()!=meta['mbr_sha256']
                or hashlib.sha256(new_vbr).hexdigest()!=meta['vbr_sha256']
                or new_mbr[440:]!=current_mbr[440:] or new_vbr[3:62]!=current_vbr[3:62]):
                raise ValueError('Backup does not match this disk layout and FAT16 BPB')
            geometry(new_mbr,new_vbr,sectors)
        try:
            write_checked(fd,new_vbr,start*512,'VBR')
            write_checked(fd,new_mbr,0,'MBR')
        except OSError as exc:
            raise OSError(f'{exc}; sectors may be partially changed; retain backup {directory}') from exc
        print(f'[ OK ] {mode} complete; MBR and VBR read back and verified')
    finally: os.close(fd)

def main():
    try:
        mode,device,sectors,backup,*codes=sys.argv[1:]
        if mode not in ('check','install','restore') or (mode=='install' and len(codes)!=2) or (mode!='install' and codes):
            raise ValueError('Invalid arguments')
        run(mode,device,int(sectors),backup,*codes)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        sys.exit(f'[ERROR] {exc}')

if __name__=='__main__': main()
