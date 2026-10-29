"""Operational queue only: no endpoint access until the prior owner releases it."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from agent_tradeoffs.config import stable_hash
from agent_tradeoffs.prompt_defense import ROOT, NAME, frozen_identity, validate_plan, verify_frozen
from agent_tradeoffs.reporting import atomic_json

ORIGINAL = Path('/Users/waynewang/CSE-291A-Project')
QUEUE = ROOT / 'artifacts/prompt-defense-queue'
SENTINEL = ORIGINAL / 'artifacts/phase1-error-retries/handoff-to-prompt-defense.json'
PLAN = ROOT / 'configs/prompt-defense-v1/plan.json'
EXPECTED_PLAN_SHA = '32cad7d1c47e6b6e23a6117f927d55da8b5419a549b4ab24a64bf821fb3a6410'
EXPECTED_INPUTS_SHA = '32e41870d50ea041fed003ffdfc0b83f8daab59ae44c910091d33aee6129de43'
EXPECTED_RUNTIME_SHA = '4c3c5e1d45a1825df4628a6406d02ea459fb27f89415c3ef482ceefddbd72174'


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def now():
    return datetime.now(UTC).isoformat()


state = {'schema_version':1,'status':'waiting_for_explicit_retry_handoff','pid':os.getpid(),
    'created_at':now(),'sentinel':str(SENTINEL),'campaign_root':str(ROOT/'artifacts'/NAME),
    'plan_sha256':EXPECTED_PLAN_SHA,'inputs_identity':EXPECTED_INPUTS_SHA,
    'queue_script_sha256':sha(__file__),'inference_started':False}


def persist(**updates):
    state.update(updates,updated_at=now())
    atomic_json(QUEUE/'state.json',state)


def verify_prelaunch():
    if sha(PLAN) != EXPECTED_PLAN_SHA:
        raise ValueError('Reviewed plan changed before release')
    plan=json.loads(PLAN.read_text())
    validate_plan(plan)
    inputs=frozen_identity(plan,PLAN.parent)
    verify_frozen(inputs)
    if stable_hash(inputs) != EXPECTED_INPUTS_SHA:
        raise ValueError('Reviewed frozen inputs changed before release')
    if (ROOT/'artifacts'/NAME).exists():
        raise ValueError('New campaign already exists; explicit recovery needed')
    return plan


def verify_release(release):
    required={'status':'released','finalized_episodes':50,'driver_exited':True,
              'protected_guard_passed':True,'semantic_runtime_sha256':EXPECTED_RUNTIME_SHA}
    for key,value in required.items():
        if release.get(key) != value:
            raise ValueError(f'Incomplete owner release: {key}')
    metadata=Path(release['runtime_metadata_path']).resolve()
    if metadata != (ORIGINAL/'artifacts/phase1-error-retries/runtime/server.json').resolve():
        raise ValueError('Unexpected handoff metadata path')
    saved=json.loads(metadata.read_text())
    if saved['pid'] != release['pid']:
        raise ValueError('Handoff PID disagrees with metadata')
    manifest_path=Path(release['manifest_path']).resolve()
    if manifest_path != (ORIGINAL/'artifacts/phase1-error-retries/manifest.json').resolve():
        raise ValueError('Unexpected prior campaign path')
    manifest=json.loads(manifest_path.read_text())
    if manifest['status'] != 'finalized' or manifest.get('active_run') is not None:
        raise ValueError('Prior campaign not finalized')
    if sum(entry['summary']['finalized_episodes'] for entry in manifest['runs']) != 50:
        raise ValueError('Prior campaign does not contain all 50 attempts')
    if release.get('manifest_sha256') and sha(manifest_path) != release['manifest_sha256']:
        raise ValueError('Prior finalized manifest changed after release')
    if release.get('protected_guard_path'):
        proof=Path(release['protected_guard_path'])
        if release.get('protected_guard_sha256') and sha(proof) != release['protected_guard_sha256']:
            raise ValueError('Protected guard proof changed after release')
    # Independently verify the original protected inventory, without calling the model.
    original_plan=json.loads((ORIGINAL/'configs/phase1-error-retries/plan.json').read_text())
    inventory=ORIGINAL/original_plan['protected_inventory']['path']
    if sha(inventory) != original_plan['protected_inventory']['sha256']:
        raise ValueError('Original protected inventory changed')
    for item in json.loads(inventory.read_text())['files']:
        path=ORIGINAL/item['path']
        if not path.is_file() or path.stat().st_size != item['bytes'] or sha(path) != item['sha256']:
            raise ValueError(f'Original protected evidence changed: {path}')
    with (ORIGINAL/'artifacts/phase1-error-retries/.diagnostic.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    return saved


def launch():
    cmd=[sys.executable,'-m','agent_tradeoffs.prompt_defense','run']
    log=QUEUE/'collection.log'
    persist(status='starting_authorized_campaign',inference_started=True,command=cmd,
            collection_log=str(log))
    with log.open('ab',buffering=0) as output:
        child=subprocess.Popen(cmd,cwd=ROOT,stdout=output,stderr=subprocess.STDOUT)
        persist(status='collecting',driver_pid=child.pid)
        print(f'{now()} RELEASE ACCEPTED: driver PID {child.pid}; log {log}',flush=True)
        last=-1
        while child.poll() is None:
            manifest_path=ROOT/'artifacts'/NAME/'manifest.json'
            if manifest_path.exists():
                manifest=json.loads(manifest_path.read_text())
                finalized=manifest.get('finalized_episodes',0)
                persist(finalized_episodes=finalized,active_episode=manifest.get('active_episode'))
                if finalized != last:
                    print(f'{now()} finalized {finalized}/600 active={manifest.get("active_episode")}',flush=True)
                    last=finalized
            time.sleep(10)
        code=child.returncode
    persist(status='collection_finished' if code==0 else 'collection_failed',driver_exit_code=code)
    if code:
        raise RuntimeError(f'Frozen collection driver exited {code}; inspect preserved evidence')
    print(f'{now()} Collection finalized; running frozen offline analysis',flush=True)
    with (QUEUE/'analysis.log').open('ab',buffering=0) as output:
        code=subprocess.call([sys.executable,'-m','agent_tradeoffs.prompt_defense_analysis'],
            cwd=ROOT,stdout=output,stderr=subprocess.STDOUT)
    persist(status='awaiting_clean_review_and_independent_result_audit' if code==0 else 'analysis_failed',
            analysis_exit_code=code)
    if code:
        raise RuntimeError(f'Frozen analysis exited {code}; evidence retained')
    print(f'{now()} Frozen offline analysis complete; 120 clean reviews still required',flush=True)


try:
    verify_prelaunch()
    persist()
    print(f'{now()} Queue PID {os.getpid()} waiting for explicit owner release at {SENTINEL}; no model requests',flush=True)
    while not SENTINEL.exists():
        if (QUEUE/'PAUSE').exists():
            persist(status='paused_by_explicit_queue_marker')
            raise RuntimeError('Queue PAUSE marker present; no automatic launch')
        time.sleep(5)
    release=json.loads(SENTINEL.read_text())
    persist(status='validating_explicit_owner_release',release=release,sentinel_sha256=sha(SENTINEL))
    verify_release(release)
    verify_prelaunch()
    if (QUEUE/'PAUSE').exists():
        raise RuntimeError('Queue PAUSE marker present before launch')
    launch()
except BaseException as error:
    persist(status='failed',error_type=type(error).__name__,error=str(error))
    traceback.print_exc()
    raise
