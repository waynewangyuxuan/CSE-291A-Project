from pathlib import Path
import json,sys,gzip,importlib.util,inspect,hashlib
ROOT=Path('/Users/waynewang/.codex/worktrees/prompt-defense-baseline/CSE-291A-Project')
sys.path.insert(0,str(ROOT/'src'))
spec=importlib.util.spec_from_file_location('review_audit_helpers',ROOT/'artifacts/prompt-defense-queue/audit_raw_evidence.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.functions_runtime import FunctionsRuntime
from agent_tradeoffs.prompt_policy import prompt_identity
from agent_tradeoffs.config import stable_hash
base=h.base
D=ROOT/'artifacts/prompt-defense-first-wave-v1'
M=json.loads((D/'manifest.json').read_text()); P=json.loads((ROOT/'configs/prompt-defense-v1/plan.json').read_text())
IDENTITY='4424e31e36c5175611e672ed4d2f582c844df9855d977ba4749b417de33b4ec2'
W=[16,17,18,20,24,26,27,28,31,33,34,35];T=[1,2,3,4,5,6,7,9,13,14,17,18]
TARGET=[e for e in P['schedule'] if e['condition']=='clean' and ((e['suite']=='workspace' and e['user_task_id'] in ['user_task_'+str(x) for x in W]) or (e['suite']=='travel' and e['user_task_id'] in ['user_task_'+str(x) for x in T]))]
assert len(TARGET)==48
assert M['identity']==IDENTITY
assert M['episodes']==P['schedule']
assert M['identity_inputs']['plan']==P
assert M['identity']==stable_hash({'inputs':M['identity_inputs'],'runtime':M['runtime']['semantic_identity']})
h.verify_frozen(M['identity_inputs'])
assert stable_hash(M['runtime']['semantic_identity'])==h.DEPLOYMENT_SHA256

def audit(e):
 d=D/'episodes'/e['episode_id']; r=json.loads((d/'result.json').read_text()); j=base.compression_records(d)
 tb,tr=base.artifact_bytes(d,'trace.json',j,compressed_required=True);rb,rr=base.artifact_bytes(d,'requests.jsonl',j,compressed_required=True)
 t=json.loads(tb); c=h.selected_config(P,e);s=get_suite('v1.2.2',e['suite'])
 assert r['identity']==t['identity']==IDENTITY
 assert all(r.get(k)==v and t.get(k)==v for k,v in e.items())
 assert r['status'] in ['completed','error']
 assert r['tool_calls']<=15
 assert all(t[k]==r[k] for k in ['usage','requests','tool_calls','tool_errors','finish_reason','observed_injection_vectors'])
 assert t['system_prompt']==prompt_identity(e['arm'])
 assert t['user_prompt']==s.get_user_task_by_id(e['user_task_id']).PROMPT
 assert all(t[k]=={} for k in ['attack_payloads','injections','injection_defaults'])
 assert t['injection_goal'] is None and t['attack_variant'] is None
 stats,schema=base.request_check(rb,t,r,c)
 runtime=FunctionsRuntime(s.tools)
 expected_schema=stable_hash([{'type':'function','function':{'name':fn.name,'description':fn.description,'parameters':fn.parameters.model_json_schema()}} for fn in runtime.functions.values()])
 assert schema==expected_schema
 events=[json.loads(l) for l in rb.decode().splitlines()]
 for ev in events:
  if ev['event']=='request':
   assert ev['payload']['messages'][0]=={'role':'system','content':P['prompts'][e['arm']]['text']}
   assert 0<ev['timeout_seconds']<=120
 assert r['utility'] is None and r['attack_success'] is None if r['status']=='error' else isinstance(r['utility'],bool) and r['attack_success'] is None
 audit_row={'episode_id':e['episode_id'],'identity':IDENTITY,'passed':True,'result_sha256':base.digest((d/'result.json').read_bytes()),'trace':tr,'requests':rr,'compression_sha256':base.digest((d/'compression.json').read_bytes()),'stats':stats,'tool_schema_sha256':schema}
 return r,t,events,audit_row

def show(e):
 r,t,ev,a=audit(e)
 print('\nEPISODE',e['episode_id'],'STATUS',r['status'],'U',r['utility'],'A',r['attack_success'])
 print('TASK',t['user_prompt'])
 for i,m in enumerate(t['messages']):
  print('messages['+str(i)+']',json.dumps(m,ensure_ascii=False))
 for event in ev:
  if event['event']!='request':
   print('RAW request['+str(event['index'])+']',json.dumps(event,ensure_ascii=False))
 initial=t.get('initial_environment');final=t.get('final_environment')
 print('TRACE_KEYS',list(t))
 print('INITIAL_ENVIRONMENT',json.dumps(initial,ensure_ascii=False))
 print('FINAL_ENVIRONMENT',json.dumps(final,ensure_ascii=False))
 print('AUDIT',json.dumps(a))

if __name__=='__main__':
 mode=sys.argv[1] if len(sys.argv)>1 else 'status'
 if mode=='status':
  ready=[e for e in TARGET if (D/'episodes'/e['episode_id']/'result.json').exists()]
  print(json.dumps({'finalized':M.get('finalized_episodes'),'status':M['status'],'assigned_ready':len(ready),'ready':[e['episode_id'] for e in ready],'population':[e['episode_id'] for e in TARGET]}))
 elif mode=='source':
  suite=sys.argv[2];s=get_suite('v1.2.2',suite)
  for i in (W if suite=='workspace' else T):
   task=s.get_user_task_by_id('user_task_'+str(i));print(inspect.getfile(type(task)));print(inspect.getsource(type(task)))
 elif mode=='show':
  for e in TARGET:
   if e['suite']==sys.argv[2] and e['user_task_id']=='user_task_'+sys.argv[3]:
    if (D/'episodes'/e['episode_id']/'result.json').exists():show(e)
 elif mode=='audit':
  rows=[]
  for e in TARGET:
   if (D/'episodes'/e['episode_id']/'result.json').exists():rows.append(audit(e)[3])
  out=Path('/private/tmp/workspace_travel_integrity.json');out.write_text(json.dumps({'reviewer':'agent-trace-review//root/prompt_defense_baseline/workspace_latter_travel_review','campaign_identity':IDENTITY,'target_count':len(TARGET),'audited_count':len(rows),'rows':rows},indent=2)+'\n')
  print(str(out),len(rows))
