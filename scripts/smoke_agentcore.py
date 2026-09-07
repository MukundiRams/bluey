#!/usr/bin/env python3
"""Invoke a deployed Bluey Harness directly and print its response."""
import argparse, boto3, uuid

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--stage',default='dev')
    p.add_argument('--harness',choices=['main','credit','account-opening'],default='main')
    p.add_argument('--prompt',default='Hello. Please tell me briefly what you can help me with.')
    p.add_argument('--region',default='us-east-1')
    a=p.parse_args()
    s=boto3.Session(region_name=a.region)
    ssm=s.client('ssm'); ac=s.client('bedrock-agentcore')
    key=f'/bluey/{a.stage}/agentcore/{a.harness}-harness-arn'
    arn=ssm.get_parameter(Name=key)['Parameter']['Value']
    sid=str(uuid.uuid4())
    r=ac.invoke_harness(harnessArn=arn,runtimeSessionId=sid,messages=[{'role':'user','content':[{'text':a.prompt}]}])
    text=''
    for chunk in r.get('stream',[]):
        delta=chunk.get('contentBlockDelta',{}).get('delta',{})
        text += delta.get('text','')
    print(text)
if __name__=='__main__': main()
