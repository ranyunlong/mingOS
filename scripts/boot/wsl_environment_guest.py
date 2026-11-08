"""Linux binary receiver. Failed destinations are deliberately retained."""
import hashlib
import os
from pathlib import PurePosixPath
import re

def receive_file(path, sha256, size, mode, stream):
    if not isinstance(path,str) or not path.startswith('/') or any(x in path for x in ('\x00','\\')):
        raise ValueError('absolute guest path required')
    parts=path.split('/')[1:]
    if not parts or any(p in ('','.','..') for p in parts): raise ValueError('invalid path')
    if not re.fullmatch('[0-9a-f]{64}',sha256) or type(size) is not int or size<0 or type(mode) is not int or mode & ~0o777:
        raise ValueError('invalid transfer pin or mode')
    parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            os.close(parent); parent=child
        fd=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        try:
            digest=hashlib.sha256(); count=0
            while True:
                data=stream.read(min(1024*1024,size-count+1))
                if not data: break
                count+=len(data)
                if count>size: raise ValueError('transfer too long')
                digest.update(data); view=memoryview(data)
                while view:
                    written=os.write(fd,view)
                    if written<=0: raise OSError('write made no progress')
                    view=view[written:]
            if count!=size or digest.hexdigest()!=sha256: raise ValueError('transfer size/hash mismatch')
            os.fchmod(fd,mode); os.fsync(fd)
        finally: os.close(fd)
        os.fsync(parent)
    finally: os.close(parent)
    return {'path':path,'sha256':sha256,'bytes':size,'mode':mode}
