#!/usr/bin/python3
"""Guest-only passive virtual evdev observer; no exclusive grabs or host inputs."""
import argparse,json,os,re,select,stat,struct,time
from pathlib import Path
EVENT=struct.Struct('<qqHHi')
def decode(block):
    if len(block)%EVENT.size:raise ValueError('partial evdev event')
    return [dict(zip(('seconds','microseconds','type','code','value'),EVENT.unpack_from(block,n))) for n in range(0,len(block),EVENT.size)]
def capture(descriptors, write, deadline):
    count=0
    while time.monotonic()<deadline and count<4096:
        ready,_,_=select.select(list(descriptors),[],[],min(0.2,max(0,deadline-time.monotonic())))
        for item in ready:
            if count>=4096:break
            for event in decode(os.read(item,EVENT.size*128)):
                if count>=4096:break
                write(dict(record='kernel-input-event',**descriptors[item],**event));count+=1
                if count>=4096:break
    return count
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boot-id',required=True);parser.add_argument('--duration-seconds',type=int,default=900)
    args=parser.parse_args()
    if not 1<=args.duration_seconds<=900:raise ValueError('duration bound')
    cmdline=Path('/proc/cmdline').read_text()
    if os.geteuid()!=0 or 'rdinit=/init' not in cmdline or 'mingos.data_uuid=1bb342cc-5448-4ae6-8bd6-108a15b2cafc' not in cmdline:
        raise RuntimeError('requires task-owned guest kernel and data disk identity')
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=args.boot_id:raise RuntimeError('current boot mismatch')
    output=Path('/run/evidence/evdev-input.log')
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    descriptors={};count=0
    with os.fdopen(fd,'w',buffering=1) as log:
        def write(value):log.write(json.dumps(value)+'\n')
        try:
            for node in sorted(Path('/dev/input').glob('event*')):
                if not re.fullmatch('event[0-9]+',node.name):continue
                name=(Path('/sys/class/input')/node.name/'device/name').read_text().strip()
                if name not in ('QEMU Virtio Keyboard','QEMU Virtio Tablet'):continue
                info=node.lstat()
                if not stat.S_ISCHR(info.st_mode) or os.major(info.st_rdev)!=13:raise RuntimeError('unexpected input node')
                item=os.open(node,os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC|os.O_NOFOLLOW)
                descriptors[item]={'device':str(node),'name':name}
            if {d['name'] for d in descriptors.values()}!={'QEMU Virtio Keyboard','QEMU Virtio Tablet'}:raise RuntimeError('expected virtual input devices missing')
            write({'type':'observer-start','bootId':args.boot_id,'exclusiveGrab':False,'devices':list(descriptors.values())})
            deadline=time.monotonic()+args.duration_seconds
            count=capture(descriptors,write,deadline)
            write({'type':'observer-stop','events':count,'bounded':True})
        finally:
            for item in descriptors:os.close(item)
            log.flush();os.fsync(log.fileno())
if __name__=='__main__':main()
