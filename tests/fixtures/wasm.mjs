// Minimal binaries exercise policy and failure paths; Kotlin is built separately.
const uleb=n=>{const a=[];do{let b=n&127;n>>>=7;if(n)b|=128;a.push(b);}while(n);return a;};
const text=s=>{const b=[...new TextEncoder().encode(s)];return [...uleb(b.length),...b];};
const section=(id,bytes)=>[id,...uleb(bytes.length),...bytes];
export function moduleBytes({imports=[],instructions=[0x0b],memoryImport=false}={}) {
  const types=[[0x60,0,0],[0x60,1,0x7f,1,0x7f],[0x60,2,0x7f,0x7f,1,0x7f]];
  const entries=imports.map(([mod,name,type=1])=>[...text(mod),...text(name),0,...uleb(type)]);
  if(memoryImport)entries.push([...text('ming_v1'),...text('memory'),2,0,1]);
  const body=[0,...instructions];
  return new Uint8Array([0,97,115,109,1,0,0,0,
    ...section(1,[...uleb(types.length),...types.flat()]),
    ...(entries.length?section(2,[...uleb(entries.length),...entries.flat()]):[]),
    ...section(3,[1,0]),
    ...section(7,[1,...text('_start'),0,...uleb(imports.length)]),
    ...section(10,[1,...uleb(body.length),...body])]);
}
export const loopBytes=()=>moduleBytes({instructions:[0x03,0x40,0x0c,0,0x0b,0x0b]});
export const trapBytes=()=>moduleBytes({instructions:[0,0x0b]});
