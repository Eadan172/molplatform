#!/usr/bin/env python3
"""No-spend Tamarind connectivity + schema + validation check.
Add --submit only when you explicitly want to launch a billable cloud job.
"""
import argparse, json, uuid, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.tamarind.client import TamarindClient, TamarindError

SEQUENCE = 'MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQ'

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--tool',default='alphafold')
    p.add_argument('--sequence',default=SEQUENCE)
    p.add_argument('--submit',action='store_true',help='Actually submit a cloud compute job (may incur charges).')
    p.add_argument('--job-name',default=None)
    args=p.parse_args()
    c=TamarindClient()
    try:
        print('[1/4] Connectivity:', json.dumps(c.ping(),ensure_ascii=False))
        print('[2/4] Tool schema:')
        schema=c.tool_schema(args.tool)
        print(json.dumps({'name':schema.get('name'),'displayName':schema.get('displayName'),'settings':schema.get('settings')},ensure_ascii=False,indent=2))
        settings={'sequence':args.sequence}
        name=args.job_name or f'molplatform-smoke-{uuid.uuid4().hex[:8]}'
        print('[3/4] No-cost validation...')
        checked=c.validate(args.tool,settings,name)
        print(json.dumps(checked,ensure_ascii=False,indent=2))
        if not checked.get('valid') or checked.get('mutatedFields'):
            raise SystemExit('Validation failed or mutated fields; no job was submitted.')
        print('[4/4] Validation passed.')
        if not args.submit:
            print('SAFE MODE: no cloud job submitted. Re-run with --submit to execute a billable job.')
            return
        answer=input(f'This will submit {args.tool} as {name}. Type SUBMIT to continue: ')
        if answer != 'SUBMIT': print('Cancelled; no job submitted.'); return
        result=c.submit(name,args.tool,checked.get('normalized',settings))
        print('SUBMITTED:',json.dumps(result,ensure_ascii=False,indent=2))
        print('Status command: GET /api/v4/tamarind/jobs/'+name)
    except TamarindError as e:
        raise SystemExit(str(e))

if __name__=='__main__': main()
