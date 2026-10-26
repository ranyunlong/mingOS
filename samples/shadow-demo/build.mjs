import {spawn} from 'node:child_process';
import {mkdir, readFile, writeFile, copyFile, readdir, access} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {adaptKotlinWasi} from '../../scripts/wasm-sdk-adapter.mjs';

const sample = fileURLToPath(new URL('./', import.meta.url));
const root = resolve(sample, '../..');
const evidence = join(root, 'artifacts/shadow-kotlin');
const contract = process.argv.includes('--contract');
const labelArg = process.argv.find(a => a.startsWith('--label='));
const label = labelArg ? labelArg.slice(8) : (contract ? 'contract-build' : 'demo-build');
if (!/^[a-z0-9-]+$/.test(label)) throw new Error('Invalid evidence label');
await mkdir(evidence, {recursive: true});
// Dedicated Gradle state keeps concurrent root builds and SDK output untouched.
const gradleHome = join(evidence, 'gradle-home');
const modules = join(gradleHome, 'caches/modules-2');
try { await access(modules); } catch {
  const {cp} = await import('node:fs/promises');
  await mkdir(join(gradleHome, 'caches'), {recursive: true});
  await cp(join(root, '.cache/gradle/caches/modules-2'), modules, {recursive: true});
}
const dists = join(root, '.cache/gradle/wrapper/dists/gradle-8.14.5-bin');
let gradle;
for (const dir of await readdir(dists)) {
  const candidate = join(dists, dir, 'gradle-8.14.5/lib/gradle-launcher-8.14.5.jar');
  try { await access(candidate); gradle = candidate; break; } catch { /* other dist */ }
}
if (!gradle) throw new Error('Cached Gradle 8.14.5 required');
const java = process.env.JAVA_HOME ? join(process.env.JAVA_HOME, 'bin', process.platform === 'win32' ? 'java.exe' : 'java') : 'java';
const args = ['-classpath', gradle, 'org.gradle.launcher.GradleMain', '-p', sample,
  '-g', gradleHome, '--project-cache-dir', join(evidence, 'gradle-project-cache'),
  '--no-daemon', '--offline', '--stacktrace', 'compileProductionExecutableKotlinWasmWasi'];
if (contract) args.push('-PshadowContract');
const javaTmp = join(evidence, 'java-tmp'); await mkdir(javaTmp, {recursive: true});
const env = {...process.env};
if (process.platform === 'win32') env.JAVA_TOOL_OPTIONS = [env.JAVA_TOOL_OPTIONS, '-Djdk.net.unixdomain.tmpdir="' + javaTmp + '"'].filter(Boolean).join(' ');
let output = '';
const code = await new Promise((done, reject) => {
  const child = spawn(java, args, {cwd: sample, env, windowsHide: true});
  for (const stream of [child.stdout, child.stderr]) stream.on('data', b => {output += b; process.stdout.write(b);});
  child.on('error', reject); child.on('exit', n => done(n ?? 1));
});
await writeFile(join(evidence, label + '.log'), output);
await writeFile(join(evidence, label + '.json'), JSON.stringify({code, contract, args, timestamp: new Date().toISOString()}, null, 2) + '\n');
if (code !== 0) process.exit(code);
const rawPath = join(sample, 'build', ...(contract ? ['contract'] : []), 'compileSync/wasmWasi/main/productionExecutable/kotlin/shadow-demo.wasm');
const raw = await readFile(rawPath);
const adapted = adaptKotlinWasi(raw);
const outputDir = contract ? join(evidence, 'contract') : join(root, 'artifacts/shadow-demo');
await mkdir(outputDir, {recursive: true});
await writeFile(join(outputDir, 'main.wasm'), adapted.bytes);
await copyFile(join(sample, 'app.json'), join(outputDir, 'app.json'));
const sha = b => createHash('sha256').update(b).digest('hex');
await writeFile(join(outputDir, 'build-info.json'), JSON.stringify({kotlin: '2.3.20', adapterVersion: 1,
  rawPath, rawSha256: sha(raw), packagedSha256: sha(adapted.bytes), rewrites: adapted.rewrites,
  imports: WebAssembly.Module.imports(new WebAssembly.Module(adapted.bytes))}, null, 2) + '\n');
console.log('Packaged real Kotlin/Wasm: ' + outputDir);
