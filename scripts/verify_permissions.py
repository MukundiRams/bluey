#!/usr/bin/env python3
"""Read-only verification of Bluey's critical authorization chain."""
import argparse, json
import boto3


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--region',default='us-east-1'); args=parser.parse_args()
    s=boto3.Session(region_name=args.region)
    iam=s.client('iam'); lam=s.client('lambda'); ac=s.client('bedrock-agentcore-control')
    roles=['HarnessRole']
    for r in iam.list_roles()['Roles']:
        if r['RoleName'].startswith('bluey-') and r['RoleName'].endswith('role'): roles.append(r['RoleName'])
    result={'roles':{}}
    for rn in roles:
        role=iam.get_role(RoleName=rn)['Role']
        policies=[]
        for pn in iam.list_role_policies(RoleName=rn)['PolicyNames']:
            policies.append(iam.get_role_policy(RoleName=rn,PolicyName=pn)['PolicyDocument'])
        result['roles'][rn]={'trust':role['AssumeRolePolicyDocument'],'inline':policies}
    gateways=ac.list_gateways().get('items',[]); result['gateways']=[]
    for g in gateways:
        gid=g['gatewayId']; gd=ac.get_gateway(gatewayIdentifier=gid)
        ts=ac.list_gateway_targets(gatewayIdentifier=gid).get('items',[])
        result['gateways'].append({'id':gid,'roleArn':gd.get('roleArn'),'targets':ts})
    result['lambda_policies']={}
    for n in ['bluey-sessions','bluey-credit-api','bluey-dynamodb-tool']:
        try: result['lambda_policies'][n]=lam.get_policy(FunctionName=n)['Policy']
        except lam.exceptions.ResourceNotFoundException: result['lambda_policies'][n]=None
    print(json.dumps(result,indent=2,default=str))

if __name__=='__main__': main()
