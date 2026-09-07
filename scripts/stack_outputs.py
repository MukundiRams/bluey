#!/usr/bin/env python3
import argparse, boto3

def main():
 p=argparse.ArgumentParser(); p.add_argument('--stage',default='dev'); p.add_argument('--region',default='us-east-1'); a=p.parse_args()
 c=boto3.client('cloudformation',region_name=a.region)
 for stack in [f'BlueyApi-{a.stage}',f'BlueyKnowledge-{a.stage}',f'BlueyAgentCore-{a.stage}']:
  try:
   outs=c.describe_stacks(StackName=stack)['Stacks'][0].get('Outputs',[])
   print(f'[{stack}]')
   for x in outs: print(f"{x['OutputKey']}={x['OutputValue']}")
  except c.exceptions.ClientError: pass
if __name__=='__main__': main()
