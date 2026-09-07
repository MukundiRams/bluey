#!/usr/bin/env python3
"""Start ingestion jobs for the two Bluey managed Knowledge Base data sources."""
import argparse, boto3

def main():
    p=argparse.ArgumentParser(); p.add_argument('--region',default='us-east-1'); p.add_argument('--main-kb',required=True); p.add_argument('--main-source',required=True); p.add_argument('--credit-kb',required=True); p.add_argument('--credit-source',required=True); a=p.parse_args()
    c=boto3.client('bedrock-agent',region_name=a.region)
    for kb,ds in [(a.main_kb,a.main_source),(a.credit_kb,a.credit_source)]:
        r=c.start_ingestion_job(knowledgeBaseId=kb,dataSourceId=ds)
        print(kb, r['ingestionJob']['ingestionJobId'])
if __name__=='__main__': main()
