"""Bounded receipt parser for the instrumented MingHost Wayland client only."""
import re
POINTER_SYNC_POLICY = {"policy":"wayland-client-pointer-frame-v1", "coordinateTolerancePixels":1,
    "pointerWaitMs":5000, "requireCurrentBootSessionDesktopPid":True, "requireAfterAbsLogOffset":True,
    "requireSameRootSurface":True, "allowEnterOrMotion":True, "leaveInvalidates":True}
class PointerReceipt:
    def __init__(self):
        self.buffer=b'';self.offset=0;self.pointer=None;self.surface=None
        self.x=self.y=None;self.entered=False;self.dirty=False;self.sequence=0
    def record(self):
        self.sequence+=1
        return {'pointer':self.pointer,'surface':self.surface,'entered':self.entered,'x':self.x,'y':self.y,'sequence':self.sequence,'logOffset':self.offset}
    def feed(self,data):
        self.buffer+=data;records=[]
        while b'\n' in self.buffer:
            raw,self.buffer=self.buffer.split(b'\n',1)
            if len(raw)>65536:raise ValueError('Wayland diagnostic line limit')
            self.offset+=len(raw)+1
            line=raw.decode('utf-8',errors='replace')
            created=re.search(r'get_pointer\(new id wl_pointer[#@](\d+)\)',line)
            if created:
                had=self.entered;self.pointer=int(created[1]);self.surface=None;self.entered=False;self.dirty=False;self.x=self.y=None
                if had:records.append(self.record())
                continue
            match=re.search(r'wl_pointer[#@](\d+)\.(enter|motion|leave|frame|release)\((.*)\)',line)
            if not match or int(match[1])!=self.pointer:continue
            kind,args=match[2],match[3]
            if kind=='enter':
                point=re.fullmatch(r'\d+,\s*wl_surface[#@](\d+),\s*(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)',args)
                if point:self.surface=int(point[1]);self.x=float(point[2]);self.y=float(point[3]);self.entered=True;self.dirty=True
            elif kind=='motion' and self.entered:
                point=re.fullmatch(r'\d+,\s*(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)',args)
                if point:self.x=float(point[1]);self.y=float(point[2]);self.dirty=True
            elif kind in ('leave','release'):
                self.entered=False;self.dirty=False;self.x=self.y=None;records.append(self.record())
            elif kind=='frame' and self.dirty:
                self.dirty=False;records.append(self.record())
        if len(self.buffer)>65536:raise ValueError('Wayland diagnostic line limit')
        return records
