"""Offline evidence audit for the paired prompt campaign; never invokes a model or scorer."""
from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path

from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite

from agent_tradeoffs.agent import LocalAgent
from agent_tradeoffs.config import stable_hash
from agent_tradeoffs.prompt_defense import (ROOT, NAME, DEPLOYMENT_SHA256, digest,
    selected_config, verify_frozen)
from agent_tradeoffs.prompt_policy import prompt_identity
from agent_tradeoffs.reporting import atomic_json
from agent_tradeoffs.runner import make_injections

AUDIT_SOURCE = ROOT / 'scripts/audit_phase1_results.py'
spec = importlib.util.spec_from_file_location('original_raw_audit', AUDIT_SOURCE)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
require = base.require


def audit(directory: Path, *, complete: bool = True) -> dict:
    path = directory / 'manifest.json'
    manifest_raw = path.read_bytes()
    manifest = json.loads(manifest_raw)
    inputs = manifest['identity_inputs']
    verify_frozen(inputs)
    plan = inputs['plan']
    require(stable_hash(manifest['runtime']['semantic_identity']) == DEPLOYMENT_SHA256,
            'Semantic deployment differs from original F16')
    for observation in manifest.get('resume_runtime_observations',[]):
        require(stable_hash(observation['semantic_identity']) == DEPLOYMENT_SHA256,
                'Mixed semantic runtime identity on resume')
    require(manifest['identity'] == stable_hash({'inputs':inputs,
        'runtime':manifest['runtime']['semantic_identity']}), 'Campaign identity mismatch')
    require(manifest['episodes'] == plan['schedule'], 'Manifest schedule differs from frozen plan')
    if complete:
        require(manifest['status'] in {'finalized','finalized_with_errors'}, 'Campaign not finalized')
    expected_ids = {entry['episode_id'] for entry in plan['schedule']}
    require(len(expected_ids) == 600, 'Not exactly600 unique scheduled episodes')
    actual_ids = {p.parent.name for p in (directory/'episodes').glob('*/result.json')}
    require(actual_ids <= expected_ids, 'Unexpected result outside frozen schedule')
    if complete:
        require(actual_ids == expected_ids, 'Not all600 results finalized')
    suites = {}
    schemas = {}
    for name in plan['configurations']:
        suite = get_suite('v1.2.2', name)
        suites[name] = suite
        runtime = FunctionsRuntime(suite.tools)
        schemas[name] = stable_hash([{'type':'function','function':{'name':fn.name,
            'description':fn.description,'parameters':fn.parameters.model_json_schema()}}
            for fn in runtime.functions.values()])
    cohort = expected_ids if complete else {entry['episode_id'] for entry in
        plan['schedule'][:manifest.get('finalized_episodes',0)]}
    require(cohort <= actual_ids, 'Manifest finalized prefix lacks results')
    counts = Counter()
    measurement = Counter()
    inventory = []
    for entry in plan['schedule']:
        if entry['episode_id'] not in cohort:
            continue
        episode = directory/'episodes'/entry['episode_id']
        result_path = episode/'result.json'
        if not result_path.is_file():
            continue
        result = json.loads(result_path.read_text())
        require(result['identity'] == manifest['identity'], 'Result identity differs')
        require(all(result.get(k)==v for k,v in entry.items()), 'Result entry fields differ')
        require(result['status'] in {'completed','error'}, 'Result not terminal')
        config = selected_config(plan,entry)
        require(result['tool_calls'] <= config.runner.max_tool_steps, 'Executed tool cap exceeded')
        require(result['duration_seconds'] >= 0, 'Negative duration')
        if result['status'] == 'error':
            require(result['utility'] is None and result['attack_success'] is None,
                    'Error scores not unknown')
            require(bool(result.get('error')), 'Error lacks type/message')
        else:
            require(isinstance(result['utility'],bool), 'Completed U not boolean')
            require(isinstance(result['attack_success'],bool) if entry['condition']=='attack'
                    else result['attack_success'] is None, 'Completed A invalid')
        journal = base.compression_records(episode)
        raw_trace, trace_record = base.artifact_bytes(episode,'trace.json',journal,
            compressed_required=complete)
        require(raw_trace is not None, 'Missing trace')
        trace = json.loads(raw_trace)
        require(trace['identity']==manifest['identity'], 'Trace identity differs')
        require(all(trace.get(k)==v for k,v in entry.items()), 'Trace entry fields differ')
        for key in ('usage','requests','tool_calls','tool_errors','finish_reason',
                    'observed_injection_vectors'):
            require(trace[key] == result[key], f'Trace/result differs: {key}')
        require(trace['system_prompt'] == prompt_identity(entry['arm']), 'Trace prompt identity differs')
        suite = suites[entry['suite']]
        user = suite.get_user_task_by_id(entry['user_task_id'])
        require(trace['user_prompt'] == user.PROMPT, 'User objective differs')
        if entry['condition'] == 'attack':
            target = suite.get_injection_task_by_id(entry['injection_task_id'])
            agent = LocalAgent(config,episode,client=object())
            payloads, combined = make_injections(suite,agent,entry['attack_style'],user,target)
            require(trace['attack_payloads'] == payloads and trace['injections'] == combined,
                    'Actual attack text differs from original fixed append template')
            require(trace['injection_goal'] == target.GOAL, 'Attack objective differs')
            require(trace['attack_variant'] == entry['attack_style'], 'Attack template differs')
            require(trace['injection_defaults'] == {v:suite.get_injection_vector_defaults()[v]
                    for v in combined}, 'Preserved source content differs')
        else:
            require(all(trace[k] == {} for k in ('attack_payloads','injections','injection_defaults')),
                    'Clean episode contains injected content')
            require(trace['injection_goal'] is None and trace['attack_variant'] is None,
                    'Clean episode contains attack metadata')
        request_raw, request_record = base.artifact_bytes(episode,'requests.jsonl',journal,
            compressed_required=complete)
        stats, schema = base.request_check(request_raw,trace,result,config)
        measurement.update(stats)
        if schema is not None:
            require(schema==schemas[entry['suite']], 'Actual tool schemas differ from frozen suite')
        if request_raw is not None:
            for event in map(json.loads,request_raw.decode().splitlines()):
                if event['event'] != 'request':
                    continue
                require(event['payload']['messages'][0] == {'role':'system',
                    'content':plan['prompts'][entry['arm']]['text']}, 'Actual system policy differs')
                timeout = event['timeout_seconds']
                require(0 < timeout <= config.runner.request_timeout_seconds, 'Request budget differs')
        memory = result['server_memory']
        allowed_pids = {snapshot['metadata_snapshot']['pid'] for snapshot in
            [manifest['runtime'], *manifest.get('resume_runtime_observations',[])]}
        require(memory['pid'] in allowed_pids and memory['scope']=='server_process_rss',
                'RSS process identity or scope differs')
        require(memory['samples'] > 0 and memory['peak_rss_bytes'] > 0, 'Missing actual server RSS')
        counts[(entry['arm'],entry['condition'],result['status'])] += 1
        inventory.append({'episode_id':entry['episode_id'],'result_sha256':digest(result_path),
            'trace_original_sha256':base.digest(raw_trace),
            'request_original_sha256':base.digest(request_raw) if request_raw is not None else None,
            'compression_journal_sha256':digest(episode/'compression.json')})
    require(sum(counts.values()) == len(cohort), 'Finalized count mismatch')
    return {'schema_version':1,'passed':True,'complete':complete,'campaign_identity':manifest['identity'],
        'campaign_manifest_sha256':base.digest(manifest_raw),'audit_source_sha256':digest(Path(__file__)),
        'base_audit_source_sha256':digest(AUDIT_SOURCE),'finalized_episodes':len(inventory),
        'counts':[{'arm':k[0],'condition':k[1],'status':k[2],'episodes':v}
                  for k,v in sorted(counts.items())],
        'request_measurements':dict(measurement),'expected_tool_schema_sha256':schemas,
        'inventory':inventory}


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--partial', action='store_true')
    args=parser.parse_args()
    destination=ROOT/'artifacts/prompt-defense-queue'/(
        'raw-evidence-partial-audit.json' if args.partial else 'raw-evidence-complete-audit.json')
    report=audit(ROOT/'artifacts'/NAME,complete=not args.partial)
    atomic_json(destination,report)
    print(json.dumps({'passed':report['passed'],'complete':report['complete'],
                      'finalized_episodes':report['finalized_episodes'],'report':str(destination)}))
