import assert from 'node:assert/strict';
export function run(module, {events = [], contract = false, endlessIdle = false,
    createFailureAt = -1, createReply = -4, textFailureAt = -1, metricsFailureAt = -1,
    submitFailureAt = -1, closeReply = 0, textMode = false, app = false, appTailFrames = 0, boundaryCommit = '', boundaryOverflow = false, appPointer = [4,10], idleCalls = 0, idleWaits = 0, frameErrorMode = 0, bulkEvents = null, bulkPressFence = false, wakeSizes = null, extra = {}}  = {}) {
  let instance, nextStyle = 0x40000001;
  const styles = new Map(), layouts = new Map();
  const state = {creates: [], styles: [], closes: [], texts: [], textCloses: [],
    metrics: [], frames: [], logs: [], polls: 0, focuses: [], inputs: [], peakLayouts: 0};
  const bytes = (p, n) => new Uint8Array(instance.exports.memory.buffer, p, n);
  const view = () => new DataView(instance.exports.memory.buffer);
  const imports = {
    ming_v1: {log(code, value) {
      if (code === 70) assert.equal(state.creates.length, 0, 'invalid styles stay local');
      if (code === 71) assert.equal(state.frames.length, 0, 'invalid builders stay local');
      state.logs.push([code, value]); return 0;
    }},
    ming_v2: {
      random_fill(p, n) { bytes(p, n).fill(37); return 0; },
      event_next(p, n) {
        assert.equal(n, 16);
        const e = events[state.polls++] ?? [endlessIdle || state.polls <= idleCalls ? 8 : 0, 0, 0, 0];
        e.forEach((v, i) => view().setInt32(p + i * 4, v, true)); return 16;
      },
    },
    ming_v3: {
      text_layout_create(p, n, font, em, flags) {
        const i = state.texts.length;
        if (i === textFailureAt) return -5;
        assert.equal(font, 1); assert.ok(flags === 0 || (textMode && flags === 1));
        const text = new TextDecoder('utf8', {fatal: true}).decode(bytes(p, n));
        const handle = 0x30000001 + state.texts.length;
        const width = textMode ? Array.from(text).reduce((n,ch)=>n+(ch.codePointAt(0)>65535?em*1.5:ch.codePointAt(0)>127?em*1.25+48:em/2+48),0) : text.length*em;
        const metrics = [width, em * 3 / 4, em / 4, 0, em, 0, -em * 3 / 4,
          width, em / 4, text.length, 0, 7];
        assert.ok(layouts.size<64,'parent TextService 64 live layouts');layouts.set(handle, metrics);state.peakLayouts=Math.max(state.peakLayouts,layouts.size); state.texts.push({handle, text}); return handle;
      },
      text_layout_metrics(h, p, n) {
        assert.equal(n, 48); const i = state.metrics.length; state.metrics.push(h);
        if (i === metricsFailureAt || !layouts.has(h)) return -2;
        layouts.get(h).forEach((v, i) => view().setInt32(p + i * 4, v, true)); return 48;
      },
      text_layout_close(h) {
        state.textCloses.push(h); assert.ok(layouts.delete(h), 'text closed exactly once'); return closeReply;
      },
    },
    ming_v4: {
      shadow_style_create(radius, blur) {
        const i = state.creates.length; state.creates.push([radius, blur]);
        assert.ok(radius >= 0 && radius <= 64 && blur >= 0 && blur <= 16);
        if (i === createFailureAt) return createReply;
        if (styles.size === 8) return -4;
        const handle = nextStyle++; styles.set(handle, {radius, blur});
        state.styles.push({handle, radius, blur}); return handle;
      },
      shadow_style_close(h) {
        state.closes.push(h);
        if (!styles.delete(h)) { assert.ok(contract, 'style closed exactly once'); return -2; }
        return closeReply;
      },
      frame_submit(p, n) {
        const data = Buffer.from(bytes(p, n));
        assert.equal(data.readInt32LE(0), 0x4d474632); assert.equal(data.readInt32LE(4), 1);
        assert.equal(data.readInt32LE(20), 0);
        const width = data.readInt32LE(8), height = data.readInt32LE(12), count = data.readInt32LE(16);
        assert.ok(width >= 1 && width <= 4096 && height >= 1 && height <= 4096);
        assert.ok(width * height <= 4194304 && count <= 256); assert.equal(n, 24 + count * 32);
        const commands = Array.from({length: count}, (_, i) =>
          Array.from({length: 8}, (_, j) => data.readInt32LE(24 + i * 32 + j * 4)));
        const i = state.frames.length;
        state.frames.push({width, height, commands, styles: state.styles.length, texts: state.texts.length});
        if (commands.some(c => c[0] === 7 && !styles.has(c[1]) || c[0] === 5 && !layouts.has(c[1]))) return -2;
        return i === submitFailureAt ? -4 : 0;
      },
    },
  };
  let time=0n,rootSequence=9000n,source=100n,epoch=1n,inputSequence=0n,waitingPending=false,appStep=0,waits=0,clockReads=0,tailFrames=0,activeFocus=false;const boundaryCancels=[];let bulkSeeded=false,bulkWait=0,sizeWait=0;
  const packet=(kind,text='',a=0,b=0,c=0,mods=0)=>{if(kind===5)epoch++;const t=Buffer.from(text),p=Buffer.alloc(56+t.length);p.writeUInt32LE(1,0);p.writeUInt32LE(kind,4);p.writeBigUInt64LE(++inputSequence,8);p.writeBigUInt64LE(epoch,16);p.writeBigUInt64LE(kind===5?0n:source,24);p.writeInt32LE(a,32);p.writeInt32LE(b,36);p.writeInt32LE(c,40);p.writeInt32LE(mods,44);p.writeUInt32LE(t.length,48);t.copy(p,56);return p;};
  const focusPackets=(tab=false)=>{const list=[];if(tab&&boundaryCommit&&boundaryOverflow)list.push(packet(5,'',3));if(tab&&boundaryCommit)list.push(packet(1,boundaryCommit));list.push(packet(5,'',1));list.push(tab?packet(3,'',9,1):packet(4,'',appPointer[0],appPointer[1],1));activeFocus=false;const first=list.shift();boundaryCancels.push(...list);return first;};
  const appInputs=[()=>focusPackets(false),()=>packet(1,'中文😀'),()=>packet(2,'候选',0,6),()=>packet(5,'',3),()=>focusPackets(true),()=>packet(1,'B')];
  const submit=imports.ming_v4.frame_submit;imports.ming_v4.frame_submit=(p,n)=>{const code=submit(p,n);if(code===0)source++;return code;};
  imports.ming_input_v1={
    poll(p,n){assert.equal(n,568);if(bulkEvents&&!bulkSeeded){bulkSeeded=true;for(const e of bulkEvents){if(bulkPressFence&&e.kind===4&&e.c===1){boundaryCancels.push(packet(5,'',1));activeFocus=false;}boundaryCancels.push(packet(e.kind,e.text??'',e.a??0,e.b??0,e.c??0,e.mods??0));}}if(boundaryCancels.length){const data=boundaryCancels.shift();bytes(p,data.length).set(data);state.inputs.push(data.toString('hex'));return data.length;}if(!app||waitingPending||appStep>=appInputs.length){waitingPending=false;return 0;}if(!activeFocus&&[1,2,5].includes(appStep))return 0;waitingPending=true;const data=appInputs[appStep++]();bytes(p,data.length).set(data);state.inputs.push(data.toString('hex'));return data.length;},
    text_focus(p,n){assert.equal(n,24);const values=Array.from({length:6},(_,i)=>view().getInt32(p+i*4,true));assert.equal(values[0],1);if(values[1]===0){assert.deepEqual(values,[1,0,0,0,0,0]);if(activeFocus){if(boundaryCommit&&boundaryOverflow)boundaryCancels.push(packet(5,'',3));if(boundaryCommit)boundaryCancels.push(packet(1,boundaryCommit));boundaryCancels.push(packet(5,'',1));}activeFocus=false;}else{activeFocus=true;const f=state.frames.at(-1);assert.ok(f,'focus before accepted frame');assert.ok(values[2]>=0&&values[3]>=0&&values[4]>=1&&values[5]>=1&&values[2]+values[4]<=f.width&&values[3]+values[5]<=f.height);}state.focuses.push({values,frames:state.frames.length,epoch:String(epoch)});return 0;},
  };
  imports.ming_frame_v1={
    clock_monotonic_ns(p,n){assert.equal(n,8);if((frameErrorMode===2||frameErrorMode===4)&&clockReads++>0)return -3;view().setBigInt64(p,time,true);return 8;},
    wait(req,n,p,capacity){assert.equal(n,16);assert.equal(capacity,48);if(frameErrorMode===1||frameErrorMode===3)return -4;const flags=view().getInt32(req+4,true),deadline=view().getBigInt64(req+8,true);let kind=app&&(appStep<appInputs.length||tailFrames++<appTailFrames)?1:app?4:waits++>(idleWaits||3)?4:1;let resize=null;if(wakeSizes){const step=sizeWait++;if(step<wakeSizes.length*2){kind=step%2===0?3:1;if(kind===3)resize=wakeSizes[Math.floor(step/2)];}else kind=4;}else if(bulkEvents){kind=bulkWait++===0?1:4;}time+=1_000_000n;if(deadline>=0n&&deadline>time)time=deadline;const out=bytes(p,48);out.fill(0);view().setInt32(p,1,true);view().setInt32(p+4,kind,true);view().setBigInt64(p+8,time,true);view().setBigInt64(p+16,kind===1?rootSequence++:0n,true);if(resize){view().setInt32(p+24,1,true);view().setInt32(p+28,resize[0],true);view().setInt32(p+32,resize[1],true);}return 48;},
  };
  Object.assign(imports, extra);
  instance = new WebAssembly.Instance(module, imports); instance.exports._start();
  assert.equal(styles.size, 0, 'all owned styles close on exit');
  assert.equal(layouts.size, 0, 'all owned layouts close on exit');
  return state;
}
