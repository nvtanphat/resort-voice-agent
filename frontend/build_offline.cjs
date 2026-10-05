/** Deterministic full Siêu Web React + Tailwind build from npm-locked dependencies.
 * All TSX modules are compiled; never patch a previously shipped bundle. */
const fs=require('fs'),path=require('path'),cp=require('child_process'),crypto=require('crypto'),ts=require('typescript');
require.resolve('react/jsx-runtime');
require.resolve('react-dom/client');
require.resolve('tailwindcss/lib/cli.js');
const base=__dirname;

function sourceDigest(){
 const files=[];
 const walk=dir=>{for(const name of fs.readdirSync(dir).sort()){const file=path.join(dir,name),stat=fs.statSync(file);if(stat.isDirectory())walk(file);else if(/\.(?:ts|tsx|css)$/.test(name))files.push(file);}};
 walk(path.join(base,'src'));files.push(path.join(base,'package-lock.json'));
 const hash=crypto.createHash('sha256');
 for(const file of files.sort()){hash.update(path.relative(base,file).replaceAll('\\','/'));hash.update('\0');hash.update(fs.readFileSync(file));hash.update('\0');}
 return hash.digest('hex');
}
const entry=path.join(base,'src/main.tsx');
const dir=path.join(base,'..','web');fs.mkdirSync(dir,{recursive:true});
const ids=new Map(),mod=[];
function resolve(spec,parent){
 if(spec.endsWith('.css'))return null;
 if(spec.startsWith('.')){
  let filename=path.resolve(path.dirname(parent),spec);
  if(!path.extname(filename)){
   filename=['.tsx','.ts','.js','.jsx','.json'].map(ext=>filename+ext).find(f=>fs.existsSync(f));
  }
  if(!filename||!fs.existsSync(filename)) throw Error(`Missing ${spec} from ${parent}`);
  return filename;
 }
 return require.resolve(spec,{paths:[path.dirname(parent),base]});
}
function add(filename){
 if(ids.has(filename))return ids.get(filename);
 const id=mod.length;ids.set(filename,id);mod.push(null);
 let code=fs.readFileSync(filename,'utf8');
 if(/\.json$/.test(filename)) code=`module.exports=${code};`;
 if(/\.(tsx?|jsx)$/.test(filename)) {
   code=ts.transpileModule(code,{fileName:filename,compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,
    target:ts.ScriptTarget.ES2020,esModuleInterop:true}}).outputText;
 }
 const dependencies={};
 for(const match of code.matchAll(/\brequire\((['"])([^'"]+)\1\)/g)){
  const spec=match[2];if(dependencies[spec]!==undefined)continue;
  const file=resolve(spec,filename);dependencies[spec]=file===null?null:add(file);
 }
 mod[id]={code,dependencies,filename:path.relative(base,filename)};
 return id;
}
const main=add(entry);
const factories=mod.map((m,id)=>`${JSON.stringify(id)}:[function(module,exports,require,process){\n${m.code}\n},${JSON.stringify(m.dependencies)}]`).join(',\n');
const sourceSha=sourceDigest();
const output=`/* source-sha256:${sourceSha} */\n/* Concierge Kiosk guest UI. React and TSX bundled from the user-provided Siêu Web UI plus real Concierge backend client. */\n(function(){
'use strict';
const modules={${factories}};const cache={};const process={env:{NODE_ENV:'production'}};
function load(id){if(cache[id])return cache[id].exports;const row=modules[id];if(!row)throw Error('Unknown frontend module '+id);
const module={exports:{}};cache[id]=module;
row[0](module,module.exports,function(spec){const dep=row[1][spec];if(dep===null)return {};if(dep===undefined)throw Error('Unbundled '+spec);return load(dep);},process);return module.exports;}
load(${main});})();`;
fs.writeFileSync(path.join(dir,'guest.js'),output);
const cssCLI=require.resolve('tailwindcss/lib/cli.js');
cp.execFileSync(process.execPath,[cssCLI,'-c','tailwind.config.js','-i','src/index.css','-o','../web/guest.css','--minify'],{cwd:base,stdio:'pipe'});
console.log('Bundled modules',mod.length,'bytes',Buffer.byteLength(output),'and generated stylesheet');
