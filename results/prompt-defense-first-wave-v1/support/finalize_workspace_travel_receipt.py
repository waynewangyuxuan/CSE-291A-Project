from pathlib import Path
from collections import Counter
from datetime import datetime,timezone
import csv,json,hashlib,sys
sys.path.insert(0,'/private/tmp')
import review_workspace_travel as h
ROOT=h.ROOT
QUEUE=ROOT/'artifacts/prompt-defense-queue'
REVIEWER='agent-trace-review/workspace_latter_travel_review'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
expected_helpers={'review_workspace_travel.py':'065587e95df0565a10cd8ff7affbf72e7849fbe4214b45bc9f4f94ab9c263cec','review_workspace_travel_display.py':'307b1b0c999bbe9fff2c65ad7b08e8e758fb7112bcdd700380441c365d874d2e'}
helpers=[]
for name,expected in expected_helpers.items():
 p=QUEUE/name
 assert sha(p)==expected and p.read_bytes()==(Path('/private/tmp')/name).read_bytes()
 helpers.append({'path':str(p.relative_to(ROOT)),'sha256':sha(p),'bytes':p.stat().st_size,'executed_copy_exact_bytes_verified':True})
fields=['episode_id','identity','suite','arm','user_task_id','review_status','refusal','failure_category','reviewer','evidence','notes','secondary_labels']
refusals={'yes','no','unclear'}
categories={'no_failure','unnecessary_refusal','task_understanding','tool_use','planning','infrastructure','other','unclear'}
fragments=[];allrows=[]
for suite,name in [('workspace','review-notes-workspace-latter12.csv'),('travel','review-notes-travel.csv')]:
 p=QUEUE/name
 with p.open(newline='') as f:
  reader=csv.DictReader(f);assert reader.fieldnames==fields;rows=list(reader)
 assert len(rows)==24
 expected=[e for e in h.TARGET if e['suite']==suite]
 assert [r['episode_id'] for r in rows]==[e['episode_id'] for e in expected]
 for r,e in zip(rows,expected):
  assert r['identity']==h.IDENTITY and r['suite']==suite and r['arm']==e['arm'] and r['user_task_id']==e['user_task_id']
  assert r['reviewer']==REVIEWER and r['review_status']=='reviewed'
  assert r['refusal'] in refusals and r['failure_category'] in categories
  assert not r['secondary_labels'] or set(r['secondary_labels'].split('|'))<=categories
  assert 'messages[' in r['evidence'] and 'request[' in r['evidence']
  assert all(v in r['notes'] for v in expected_helpers.values())
  result=json.loads((h.D/'episodes'/e['episode_id']/'result.json').read_text())
  if result['status']=='error':assert r['refusal']=='unclear' and r['failure_category']=='infrastructure' and result['utility'] is None and result['attack_success'] is None
  if r['failure_category']=='no_failure':assert r['refusal']=='no'
 fragments.append({'path':str(p.relative_to(ROOT)),'sha256':sha(p),'bytes':p.stat().st_size,'rows':len(rows),'refusal_counts':dict(Counter(r['refusal'] for r in rows)),'primary_failure_category_counts':dict(Counter(r['failure_category'] for r in rows))})
 allrows.extend(rows)
assert len(allrows)==len(set(r['episode_id'] for r in allrows))==48
assert {r['episode_id'] for r in allrows}=={e['episode_id'] for e in h.TARGET}
assert fragments[0]['sha256']=='755a1e2bc908e77a26edfd6cfa7090c4fc59a9d1eb984742ce4812a1ac5cae3c'
audit=json.loads(Path('/private/tmp/workspace_travel_integrity.json').read_text())
assert audit['campaign_identity']==h.IDENTITY and audit['target_count']==audit['audited_count']==48
assert [r['episode_id'] for r in audit['rows']]==[e['episode_id'] for e in h.TARGET]
assert all(r['passed'] for r in audit['rows'])
for row in audit['rows']:
 for k in ['trace','requests']:
  assert all(x in row[k] for x in ['path','original_sha256','compressed_sha256','original_bytes','compressed_bytes'])
receipt={
 'receipt_version':1,'reviewer':REVIEWER,'review_status':'complete','generated_utc':datetime.now(timezone.utc).isoformat(),
 'worktree':str(ROOT),'branch':'codex/prompt-defense-baseline','frozen_commit':'37964c8d3137d9092bc7daeba4e4e06289394877',
 'campaign_identity':h.IDENTITY,'scope':'Exactly Workspace latter 12 selected legitimate tasks and all 12 selected Travel tasks, both arms, clean condition only; predetermined 48 targets. This receipt is not an all-600 campaign audit.',
 'target_count':48,'audited_count':48,'qualitatively_reviewed_count':48,'target_schedule_indices':[e['index'] for e in h.TARGET],
 'helpers':helpers,'annotation_fragments':fragments,
 'assertions':{
  'exact_population_no_duplicates_or_out_of_scope_rows':True,'all_annotation_statuses_reviewed':True,
  'compressed_and_original_sha256_and_lengths_verified':True,'result_and_trace_metadata_identity_verified':True,
  'complete_actual_request_histories_and_accepted_trace_prefixes_verified':True,'frozen_system_prompts_and_decode_settings_verified':True,
  'frozen_tool_schema_identity_verified':True,'all_raw_completion_choices_tool_calls_results_and_final_answer_or_error_inspected':True,
  'relevant_raw_state_dictionaries_and_ordered_lists_inspected':True,'workspace_saved_states_never_pydantic_reconstructed':True,
  'official_utility_and_attack_success_preserved':True,'no_utility_or_security_scorers_invoked':True,'no_model_endpoint_or_inference_calls':True,
  'error_rows_kept_refusal_unclear_infrastructure':True},
 'interpretation_limits':'Qualitative agent trace review, not human annotation or a validated judge. Existing clean-refusal rubric only; no legitimate-success L or attack-attempt labels created. Tool-output/answer contradictions do not alone establish internal task misunderstanding. Official U/A are not changed by checker caveats. Travel7 year is underspecified; Travel17 deferrals remain refusal=unclear.',
 'refusal_counts':dict(Counter(r['refusal'] for r in allrows)),
 'primary_failure_category_counts':dict(Counter(r['failure_category'] for r in allrows)),
 'raw_integrity_rows':audit['rows']}
out=Path('/private/tmp/review-workspace-travel-audit-receipt.json')
out.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'receipt_path':str(out),'receipt_sha256':sha(out),'fragments':fragments,'helpers':helpers,'rows':48,'refusal_counts':receipt['refusal_counts'],'primary_counts':receipt['primary_failure_category_counts']},ensure_ascii=False,indent=2))
