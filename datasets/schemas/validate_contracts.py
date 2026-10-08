#!/usr/bin/env python3
import json, os, hashlib, sys
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'datasets'

def chash(o): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def st(v):
    if v is None:return 'null'
    if isinstance(v,bool):return 'boolean'
    if isinstance(v,int) and not isinstance(v,bool):return 'integer'
    if isinstance(v,float):return 'number'
    if isinstance(v,str):return 'string'
    return type(v).__name__
def shape(v):
    if isinstance(v,dict): return {'object':{k:shape(v[k]) for k in sorted(v)}}
    if isinstance(v,list):
        uniq={json.dumps(shape(x),sort_keys=True,separators=(',',':')) for x in v}
        return {'array':[json.loads(x) for x in sorted(uniq)]}
    return {'scalar':st(v)}

def load_json(p): return json.loads(p.read_text(encoding='utf-8'))
def rel_to_data(p): return p.relative_to(DATA).as_posix()
contracts=load_json(DATA/'schemas/contracts.json')
errors=[]; records=0
strict=set()
for c in contracts['contracts']:
    dp=ROOT/c['path']; sp=ROOT/c['schema']; strict.add(rel_to_data(dp))
    schema=load_json(sp); v=Draft202012Validator(schema,format_checker=FormatChecker())
    if c['mode']=='json': objs=[load_json(dp)]
    elif c['mode']=='jsonl': objs=[json.loads(x) for x in dp.read_text(encoding='utf-8').splitlines() if x.strip()]
    else: errors.append(f"unsupported mode {c['mode']} for {c['path']}"); continue
    records += len(objs)
    for i,o in enumerate(objs,1):
        for e in v.iter_errors(o): errors.append(f"{c['path']}#{i}: {e.message}")
shape_paths=set()
for c in contracts.get('shape_contracts',[]):
    dp=ROOT/c['path']; rel=rel_to_data(dp); shape_paths.add(rel)
    o=load_json(dp)
    top='array' if isinstance(o,list) else 'object' if isinstance(o,dict) else st(o)
    if top!=c['top_level_type']: errors.append(f"{c['path']}: top-level type drift {top} != {c['top_level_type']}")
    if isinstance(o,dict) and sorted(o.keys())!=c.get('required_top_level_keys'):
        errors.append(f"{c['path']}: top-level key drift")
    actual=chash(shape(o))
    if actual!=c['shape_sha256']: errors.append(f"{c['path']}: recursive JSON shape drift")
# Coverage check: all non-schema .json/.jsonl must be classified.
all_docs=set()
for p in DATA.rglob('*'):
    if not p.is_file() or p.suffix not in ('.json','.jsonl'): continue
    rel=rel_to_data(p)
    if rel.startswith('schemas/') or rel.startswith('evaluation/schemas/'): continue
    all_docs.add(rel)
classified=strict|shape_paths
unclassified=sorted(all_docs-classified)
if unclassified: errors.extend('UNCLASSIFIED '+x for x in unclassified)
extra=sorted(classified-all_docs)
if extra: errors.extend('MISSING '+x for x in extra)
# Snapshot hash integrity.
snaps={}
sp=DATA/'knowledge/sources/verification_snapshots.jsonl'
for line in sp.read_text(encoding='utf-8').splitlines():
    if not line.strip(): continue
    o=json.loads(line); expected=o['bundle_sha256']; payload=dict(o); payload.pop('bundle_sha256')
    if chash(payload)!=expected: errors.append(f"snapshot hash mismatch {o['snapshot_id']}")
    snaps[o['snapshot_id']]=expected
for line in (DATA/'knowledge/sources/verified_sources.jsonl').read_text(encoding='utf-8').splitlines():
    o=json.loads(line)
    if snaps.get(o['evidence_snapshot_id'])!=o['evidence_snapshot_sha256']:
        errors.append(f"source snapshot link/hash mismatch {o['artifact_id']}")
# Domain-review safety gate consistency.
for line in (DATA/'knowledge/canonical/facts.jsonl').read_text(encoding='utf-8').splitlines():
    f=json.loads(line); d=f['domain_review']
    if d['required'] and d['status']!='approved' and d['runtime_gate']!='staff_confirmation_required_until_approved':
        errors.append(f"unsafe domain review gate {f['canonical_fact_id']}")
print(json.dumps({'status':'PASS' if not errors else 'FAIL','strict_contracts':len(contracts['contracts']),'shape_contracts':len(contracts.get('shape_contracts',[])),'classified_documents':len(classified),'documents_in_scope':len(all_docs),'validated_objects':records,'errors':errors[:200]},indent=2))
sys.exit(1 if errors else 0)
