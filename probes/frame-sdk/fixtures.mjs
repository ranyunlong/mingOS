export const ENDIAN = 0x01234567fedcba98n;
export const MAX = 0x7fffffffffffffffn;
export const checks = [
 [701,'clock zero, mixed-endian words and Long.MAX_VALUE'],
 [702,'negative clock host statuses preserved'],
 [703,'incorrect clock result lengths rejected'],
 [704,'negative clock packet values rejected'],
 [705,'request flags/deadlines and FRAME/TIMER/CLOSE/CANCEL kinds'],
 [706,'all seven INPUT types and signed i32 payloads'],
 [707,'pending is code zero with no event'],
 [708,'negative wait host statuses preserved'],
 [709,'local negative deadlines make no host call'],
 [710,'incorrect positive wait lengths rejected'],
 [711,'event versions rejected'],
 [712,'unknown event kinds rejected'],
 [713,'negative event timestamps rejected'],
 [714,'u64 frame sequence outside nonnegative Long domain rejected'],
 [715,'FRAME sequence zero rejected'],
 [716,'all reserved words checked'],
 [717,'all non-INPUT payload words must be zero'],
 [718,'invalid INPUT types rejected'],
 [719,'valid wait after invalid packets; no owned resource handles'],
 [799,'all real Kotlin SDK boundary checks passed'],
];
const i64 = value => {const bytes=Buffer.alloc(8); bytes.writeBigInt64LE(BigInt.asIntN(64,value)); return bytes.toString('hex');};
export function packet({version=1,kind=1,now=ENDIAN,seq=1n,input=[0,0,0,0],reserved=0n}={}) {
 const bytes=Buffer.alloc(48);
 bytes.writeUInt32LE(version>>>0,0); bytes.writeUInt32LE(kind>>>0,4);
 bytes.writeBigInt64LE(BigInt.asIntN(64,now),8); bytes.writeBigUInt64LE(BigInt.asUintN(64,seq),16);
 input.forEach((word,index)=>bytes.writeInt32LE(word,24+index*4));
 bytes.writeBigUInt64LE(BigInt.asUintN(64,reserved),40);
 return bytes.toString('hex');
}
export const clockCases = [
 ...[0n,ENDIAN,MAX].map(value=>({label:'clock '+value,code:8,packet:i64(value)})),
 ...[-2,-3,-4,-99].map(code=>({label:'clock error '+code,code})),
 ...[0,1,4,7,9,16,2147483647].map(code=>({label:'clock invalid length '+code,code})),
 ...[-1n,-0x8000000000000000n].map(value=>({label:'clock negative value '+value,code:8,packet:i64(value)})),
];
const wait=(label,fields={},flags=0,deadline=-1n)=>({label,flags,deadline:deadline.toString(),code:48,packet:packet(fields)});
export const waitCases = [
 wait('close defaults',{kind:4,now:0n,seq:0n}),
 wait('frame wanted',{kind:1},1),
 wait('timer zero deadline',{kind:2,now:MAX,seq:0n},0,0n),
 wait('both flags and maximum deadline',{kind:1,now:MAX,seq:MAX},3,MAX),
 wait('cancel with mixed deadline',{kind:5,seq:ENDIAN},2,ENDIAN),
 ...Array.from({length:7},(_,i)=>wait('input type '+(i+1),{kind:3,now:ENDIAN+BigInt(i+1),seq:MAX,input:[i+1,-2147483648,2147483647,-123456789]},0,BigInt(i+1))),
 {label:'pending without output write',flags:2,deadline:'-1',code:0},
 ...[-2,-3,-4,-99].map(code=>({label:'wait error '+code,flags:0,deadline:'-1',code})),
 ...[1,8,16,47,49,2147483647].map(code=>({label:'wait invalid length '+code,flags:0,deadline:'-1',code})),
 ...[0,2,0xffffffff].map(version=>wait('bad version '+version,{version})),
 ...[0,6,0xffffffff].map(kind=>wait('bad kind '+kind,{kind})),
 ...[-1n,-0x8000000000000000n].map(now=>wait('bad timestamp '+now,{now})),
 ...[0x8000000000000000n,0xffffffffffffffffn].map(seq=>wait('bad sequence '+seq,{seq})),
 wait('frame sequence zero',{seq:0n}),
 ...[1n,0x100000000n,0xffffffffffffffffn].map(reserved=>wait('bad reserved '+reserved,{reserved})),
 ...[1,2,4,5].flatMap(kind=>Array.from({length:4},(_,index)=>{
  const input=[0,0,0,0]; input[index]=1;
  return wait('non-INPUT kind '+kind+' dirty payload word '+index,{kind,input});
 })),
 ...[0,8,-1,2147483647].map(type=>wait('bad input type '+type,{kind:3,input:[type,0,0,0]})),
 wait('recovery frame',{kind:1,seq:7n},3),
];
