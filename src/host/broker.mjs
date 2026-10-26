import {openSync,closeSync,fstatSync,lstatSync,readSync,constants} from 'node:fs';
import {randomInt} from 'node:crypto';
import {SDK_ARITIES} from '../runtime/policy.mjs';

export class ResourceBroker {
  #resources=new Map(); #handles=new Map(); #closed=false; #events=[]; #limits;
  static create(grants={},limits={}) { return new ResourceBroker(grants,limits); }
  constructor(grants,limits) {
    this.#limits={maxFileBytes:262144,maxResources:16,maxHandles:32,maxLogs:4096,...limits};
    try {
      const entries=Object.entries(grants);
      if(entries.length>this.#limits.maxResources)throw new Error('TOO_MANY_RESOURCES');
      for(const [key,file] of entries) {
        const id=Number(key);
        if(!Number.isInteger(id)||id<1||id>65535||String(id)!==key||typeof file!=='string')throw new Error('INVALID_GRANT');
        const info=lstatSync(file);
        if(info.isSymbolicLink())throw new Error('SYMLINK_DENIED');
        if(!info.isFile())throw new Error('NOT_REGULAR_FILE');
        const fd=openSync(file,constants.O_RDONLY|(constants.O_NOFOLLOW??0)|(constants.O_NONBLOCK??0));
        try {
          const stat=fstatSync(fd);
          if(!stat.isFile())throw new Error('NOT_REGULAR_FILE');
          if(stat.size>this.#limits.maxFileBytes)throw new Error('RESOURCE_TOO_LARGE');
          this.#resources.set(id,{fd,size:stat.size});
        } catch(e) {closeSync(fd);throw e;}
      }
    } catch(e) {this.close();throw e;}
  }
  get events(){return this.#events.map(e=>({...e}));}
  dispatch(method,args) {
    if(this.#closed||!Object.hasOwn(SDK_ARITIES,method)||!Array.isArray(args)||args.length!==SDK_ARITIES[method]||
      args.some(n=>!Number.isInteger(n)||n<-2147483648||n>2147483647))return -2;
    if(method==='log') {
      if(this.#events.length>=this.#limits.maxLogs)return -4;
      this.#events.push({code:args[0],value:args[1]});return 0;
    }
    if(method==='file_open') {
      const resource=this.#resources.get(args[0]);if(!resource)return -1;
      if(this.#handles.size>=this.#limits.maxHandles)return -4;
      let handle;do{handle=randomInt(1,2147483647);}while(this.#handles.has(handle));
      this.#handles.set(handle,resource);return handle;
    }
    const resource=this.#handles.get(args[0]);if(!resource)return -2;
    if(method==='file_length')return resource.size;
    if(method==='file_close'){this.#handles.delete(args[0]);return 0;}
    const offset=args[1];if(offset<0||offset>=resource.size)return -2;
    const byte=Buffer.allocUnsafe(1);
    try {return readSync(resource.fd,byte,0,1,offset)===1?byte[0]:-3;}catch{return -3;}
  }
  close() {
    if(this.#closed)return;this.#closed=true;
    for(const {fd} of this.#resources.values()) {try{closeSync(fd);}catch{/* best-effort cleanup */}}
    this.#resources.clear();this.#handles.clear();
  }
}
