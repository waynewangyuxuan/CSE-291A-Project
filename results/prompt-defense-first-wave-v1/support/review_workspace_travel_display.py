"""Read-only display of complete messages/raw responses and raw dictionary state differences."""
import importlib.util,json,sys
from pathlib import Path
p=Path(__file__).with_name('review_workspace_travel.py')
spec=importlib.util.spec_from_file_location('targeted_review',p);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)

def differences(a,b,p=''):
 if isinstance(a,dict) and isinstance(b,dict):
  for k in sorted(set(a)|set(b)):
   q=p+'/'+str(k)
   if k not in a:yield [q,'ADDED',b[k]]
   elif k not in b:yield [q,'REMOVED',a[k]]
   else:yield from differences(a[k],b[k],q)
 elif isinstance(a,list) and isinstance(b,list) and all(isinstance(x,dict) and 'id_' in x for x in a+b):
  if [x['id_'] for x in a] != [x['id_'] for x in b]:yield[p+'/id_order','CHANGED',[x['id_'] for x in a],[x['id_'] for x in b]]
  yield from differences({str(x['id_']):x for x in a},{str(x['id_']):x for x in b},p+'/by_id')
 elif a!=b:yield[p,'CHANGED',a,b]

for e in h.TARGET:
 if e['suite']!=sys.argv[1] or e['user_task_id']!='user_task_'+sys.argv[2]:continue
 if not (h.D/'episodes'/e['episode_id']/'result.json').exists():continue
 r,t,ev,a=h.audit(e)
 print('\nEPISODE',e['episode_id'],'STATUS',r['status'],'U',r['utility'],'A',r['attack_success'],'ERROR',r.get('error'))
 print('TASK',t['user_prompt'])
 print('TRACE_KEYS',list(t))
 for i,m in enumerate(t['messages']):
  if m.get('role')!='system':print('messages['+str(i)+']',json.dumps(m,ensure_ascii=False))
 for event in ev:
  if event['event']!='request':print('RAW request['+str(event['index'])+']',json.dumps(event,ensure_ascii=False))
 print('ENVIRONMENT_KEYS',list(t.get('initial_environment',{})))
 changes=list(differences(t.get('initial_environment'),t.get('final_environment')))
 print('RAW_DICTIONARY_STATE_DIFF',json.dumps(changes,ensure_ascii=False))
 print('AUDIT',json.dumps(a,ensure_ascii=False))
