import {mkdir,writeFile} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {deflateSync} from 'node:zlib';
const width=1024,height=640;const raw=Buffer.alloc((width*3+1)*height);
for(let y=0;y<height;y++)for(let x=0;x<width;x++){
 let t=y/(height-1);let r=27+210*t,g=43+120*t,b=72+32*t;const d=Math.hypot(x-740,y-181);const glow=Math.max(0,1-d/190)*0.42;r=r*(1-glow)+255*glow;g=g*(1-glow)+205*glow;b=b*(1-glow)+139*glow;
 if(d<43){r=255;g=233;b=181;}
 const horizon=355;if(y>horizon){const v=(y-horizon)/(height-horizon);r=74-40*v;g=103-43*v;b=117-40*v;const wave=Math.sin(x/19+y/3)*Math.sin(y/6);r+=wave*5;g+=wave*6;b+=wave*7;const refl=Math.max(0,1-Math.abs(x-740)/(25+(y-horizon)*0.65));if(((x*31+y*17)%23)<5){r+=refl*97;g+=refl*57;b+=refl*23;}}
 const ridge=327+Math.sin(x/85)*23+Math.sin(x/37)*10;if(x<600&&y>ridge&&y<393+Math.sin(x/120)*8){r=64;g=78;b=91;}
 const cliff=475-x*0.34+Math.sin(x/24)*13;if(x<433&&y>cliff){r=30+(y%11)*0.4;g=52+(x%17)*0.2;b=54;}
 const noise=((x*73+y*151)%7)-3;const i=y*(width*3+1)+1+x*3;raw[i]=Math.max(0,Math.min(255,r+noise));raw[i+1]=Math.max(0,Math.min(255,g+noise));raw[i+2]=Math.max(0,Math.min(255,b+noise));
}
function crc(bytes){let c=0xffffffff;for(const v of bytes){c^=v;for(let j=0;j<8;j++)c=(c>>>1)^((c&1)?0xedb88320:0);}return(c^0xffffffff)>>>0;}
function chunk(type,data){const name=Buffer.from(type),out=Buffer.alloc(data.length+12);out.writeUInt32BE(data.length);name.copy(out,4);data.copy(out,8);out.writeUInt32BE(crc(Buffer.concat([name,data])),data.length+8);return out;}
const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(width);ihdr.writeUInt32BE(height,4);ihdr[8]=8;ihdr[9]=2;const png=Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ihdr),chunk('IDAT',deflateSync(raw,{level:9})),chunk('IEND',Buffer.alloc(0))]);
const dir=fileURLToPath(new URL('../samples/image-viewer/data/',import.meta.url));await mkdir(dir,{recursive:true});await writeFile(dir+'coastal.png',png);console.log('Generated original deterministic coastal fixture: '+png.length+' bytes');
